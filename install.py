#!/usr/bin/env python3
"""Installation-capability CLI. Does not launch REMORA or probe live devices."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parent
SCHEMA = "remora-build-plan/v1"
ENV_KEYS = ("PATH", "CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "LIBRARY_PATH",
            "LD_LIBRARY_PATH", "LOADEDMODULES", "MODULEPATH", "CUDA_HOME", "CUDA_PATH",
            "NVHPC", "I_MPI_ROOT", "OPAL_PREFIX", "MPICH_CC", "MPICH_CXX", "MPICH_FC",
            "OMPI_CC", "OMPI_CXX", "OMPI_FC", "I_MPI_CC", "I_MPI_CXX", "I_MPI_F77",
            "I_MPI_F90", "TACC_CUDA_INC", "TACC_CUDA_LIB")
TOOL_DEFAULTS = {"cc": "cc", "cxx": "c++", "fc": "gfortran", "mpicc": "mpicc",
                 "mpicxx": "mpicxx", "mpifc": "mpif77", "nvcc": "nvcc",
                 "make": "make", "bash": "bash", "readelf": "readelf", "mpip_python": "python2"}
ARCHIVES = {"mpstat": "extra/sysstat-11.5.3.tar.gz", "mpip": "extra/mpiP-3.4.1.tar.gz"}


class InstallError(Exception):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def seal(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def source_manifest():
    # Exclude generated binaries/objects and dependency expansion directories.
    paths = [ROOT / "install.py", ROOT / "install.sh"]
    for name in ("installer", "src", "docs"):
        if any(p.is_symlink() for p in (ROOT / name).rglob("*")):
            raise InstallError("source symlinks are not supported")
        paths.extend(p for p in (ROOT / name).rglob("*") if p.is_file()
                     and "binary_data_collectors" not in p.parts
                     and p.suffix not in (".o", ".pyc") and "__pycache__" not in p.parts)
    paths.extend(ROOT / name for name in ARCHIVES.values())
    if any(p.is_symlink() for p in paths):
        raise InstallError("source symlinks are not supported")
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(set(paths))}


def environment():
    return {key: os.environ.get(key) for key in ENV_KEYS}


def external_dependencies(config):
    """Pin explicitly configured NVML files; implicit system search stays a limitation."""
    paths = []
    if config["nvml_include"]:
        paths.append(Path(config["nvml_include"]) / "nvml.h")
    if config["nvml_lib"]:
        paths.extend(Path(config["nvml_lib"]).glob("libnvidia-ml.so*"))
    if config["cuda"]:
        paths.extend(Path(config["cuda"]) / name for name in ("version.json", "version.txt"))
    return {str(p): digest(p) for p in sorted(set(paths)) if p.is_file()}


def run(args, directory, logdir, label, timeout=60, env=None):
    """Retain both streams and kill only the owned process group on timeout."""
    logdir = Path(logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    receipt = {"command": [str(a) for a in args], "cwd": str(directory), "timeout_seconds": timeout}
    with (logdir / (label + ".stdout")).open("x") as out, (logdir / (label + ".stderr")).open("x") as err:
        proc = None
        try:
            proc = subprocess.Popen(args, cwd=directory, env=env, stdout=out, stderr=err,
                                    start_new_session=True)
            try:
                rc = proc.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise InstallError("{} timed out or was interrupted; inspect {}".format(label, logdir))
        except OSError as exc:
            err.write(str(exc) + "\n")
            rc = 127
        finally:
            receipt["exit_code"] = proc.returncode if proc is not None else 127
            save(logdir / (label + ".json"), receipt)
    if rc:
        raise InstallError("{} failed (exit {}); inspect {}".format(label, rc, logdir))
    return (logdir / (label + ".stdout")).read_text(errors="replace")


def tool(name, config, work):
    requested = config["tools"][name]
    path = shutil.which(requested)
    if not path:
        raise InstallError("{} not found: {}".format(name, requested))
    # Preserve wrapper invocation path; resolving an MPI symlink can change dispatch.
    path = str(Path(path).absolute())
    version = run([path, "--version"], work, work / "logs", name + "-version")
    version += (work / "logs" / (name + "-version.stderr")).read_text(errors="replace")
    return {"path": path, "sha256": digest(path), "version": version.strip() or "version reported on stderr/unknown"}


def extract(archive, directory):
    directory.mkdir(parents=True)
    with tarfile.open(archive) as tar:
        members = tar.getmembers()
        for member in members:
            dest = (directory / member.name).resolve()
            if (directory.resolve() not in dest.parents or not (member.isfile() or member.isdir())):
                raise InstallError("unsafe dependency archive member: " + member.name)
        tar.extractall(directory, members=members)
    roots = list(directory.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise InstallError("dependency archive must have one source directory")
    return roots[0]


def flags(config, name):
    return shlex.split(config["flags"][name])


def compile_probe(name, compiler, code, suffix, config, work, extra=(), language="cflags"):
    source = work / (name + suffix)
    source.write_text(code)
    dest = work / (name + ".probe")
    run([compiler, *flags(config, "cppflags"), *flags(config, language), str(source),
         *flags(config, "ldflags"), *extra, "-o", str(dest)], work, work / "logs", name)
    if not dest.is_file() or not dest.stat().st_size:
        raise InstallError(name + " did not produce a linked artifact")
    check_elf(dest)


def check_elf(path):
    with Path(path).open("rb") as stream:
        header = stream.read(20)
    if len(header) < 20 or header[:4] != b"\x7fELF" or header[5] not in (1, 2):
        raise InstallError("missing ELF artifact: " + str(path))
    endian = "little" if header[5] == 1 else "big"
    native = {"x86_64": 62, "aarch64": 183}.get(platform.machine())
    if native is None or int.from_bytes(header[18:20], endian) != native:
        raise InstallError("ELF architecture differs from native target: " + str(path))


def dependency_build(name, config, tools, work, stage):
    source = extract(ROOT / ARCHIVES[name], work / name)
    env = os.environ.copy()
    # Explicit configuration wins; inherited make flags cannot inject different targets/jobs.
    for key in ("MAKEFLAGS", "MFLAGS", "MAKEOVERRIDES", "CONFIG_SITE"):
        env.pop(key, None)
    env.update(CC=tools["cc"]["path"], CFLAGS=config["flags"]["cflags"],
               CPPFLAGS=config["flags"]["cppflags"], LDFLAGS=config["flags"]["ldflags"])
    command = [tools["bash"]["path"], "./configure"]
    if name == "mpip":
        # Bundled 3.4.1 generates Fortran wrappers with Python 2.
        defs = source / "Defs.mak.in"
        python_command = shlex.quote(tools["mpip_python"]["path"]).replace("$", "$$").replace("#", "\\#")
        defs.write_text(defs.read_text().replace("PYTHON  = python", "PYTHON  = " + python_command))
        env.update(CC=tools["mpicc"]["path"], F77=tools["mpifc"]["path"],
                   FFLAGS=config["flags"]["fflags"])
        command += ["--disable-demangling", "--disable-bfd", "--disable-libunwind",
                    "--prefix=" + str(stage)]
    run(command, source, work / "logs", name + "-configure", 180, env)
    targets = ["mpstat"] if name == "mpstat" else ["libmpiP.a"]
    run([tools["make"]["path"], "-j1", *targets], source, work / "logs", name + "-make", 240, env)
    if name == "mpip":
        run([tools["make"]["path"], "-j1", "shared"], source, work / "logs", name + "-shared", 240, env)
        for lib in ("libmpiP.a", "libmpiP.so"):
            if not (source / lib).is_file():
                raise InstallError("mpiP did not produce " + lib)
            (stage / "lib").mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / lib, stage / "lib" / lib)
        check_elf(stage / "lib/libmpiP.so")
    else:
        if not (source / "mpstat").is_file():
            raise InstallError("sysstat did not produce mpstat")
        (stage / "bin").mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / "mpstat", stage / "bin/mpstat")
        check_elf(stage / "bin/mpstat")


def gpu_flags(config):
    args = []
    includes = config["nvml_include"]
    libraries = config["nvml_lib"]
    if includes:
        args += ["-I", includes]
    if libraries:
        args += ["-L", libraries, "-Wl,-rpath," + libraries]
    return args + ["-lnvidia-ml"]


def collector(name, config, tools, work, stage):
    dest = stage / "bin/binary_data_collectors" / ("data_collect_" + name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    extra = gpu_flags(config) if name == "gpu" else []
    run([tools["cc"]["path"], *flags(config, "cppflags"), *flags(config, "cflags"),
         str(ROOT / "src/C_data_collectors_src" / ("data_collect_" + name + ".c")),
         *flags(config, "ldflags"), *extra, "-o", str(dest)], work, work / "logs", "collector-" + name)
    if not dest.is_file() or not dest.stat().st_size:
        raise InstallError("collector did not produce " + str(dest))
    check_elf(dest)


def registry():
    return json.loads((ROOT / "installer/modules.json").read_text())["modules"]


def expected(selected):
    result = ["bin/remora", "bin/config/modules"]
    for name in selected:
        spec = registry()[name]
        result.extend(spec.get("artifacts", []))
        if spec["kind"] == "collector":
            result.append("bin/binary_data_collectors/data_collect_" + name)
        if "entry" in spec:
            result.append("bin/modules/" + spec["entry"].split(",")[0])
    return sorted(set(result))


def check_platform():
    if platform.system() != "Linux":
        raise InstallError("REMORA installation probes require native Linux; runtime remains NOT_RUN")


def configure(args):
    build = Path(args.build_dir).absolute()
    if any(build.resolve() == (ROOT / name).resolve() or (ROOT / name).resolve() in build.resolve().parents
           for name in ("src", "docs", "extra", "installer")):
        raise InstallError("build directory cannot be inside source/dependency directories")
    if build.exists():
        raise InstallError("build directory must be new; use a separate directory for reconfigure")
    build.mkdir(parents=True, mode=0o700)
    config = {"tools": dict(TOOL_DEFAULTS), "flags": {key: "" for key in
              ("cflags", "cxxflags", "fflags", "cppflags", "ldflags")},
              "modules": {name: spec["default"] for name, spec in registry().items()},
              "prefix": str(Path(args.prefix).absolute()) if args.prefix else "",
              "nvml_include": "", "nvml_lib": "", "cuda": "", "requested_tools": []}
    if args.config:
        loaded = json.loads(Path(args.config).read_text())
        unknown = set(loaded) - set(config)
        if unknown:
            raise InstallError("unknown configuration keys: " + ", ".join(sorted(unknown)))
        for key in ("tools", "flags", "modules"):
            if set(loaded.get(key, {})) - set(config[key]):
                raise InstallError("unknown configuration " + key)
            config[key].update(loaded.get(key, {}))
        for key in ("prefix", "nvml_include", "nvml_lib", "cuda"):
            if key in loaded:
                config[key] = loaded[key]
        config["requested_tools"] = loaded.get("requested_tools", list(loaded.get("tools", {})))
    for key in TOOL_DEFAULTS:
        value = getattr(args, key)
        if value:
            config["tools"][key] = value
            if key not in config["requested_tools"]:
                config["requested_tools"].append(key)
        elif not args.config:
            env_key = {"mpifc": "MPIFC", "mpip_python": "MPIP_PYTHON"}.get(key, key.upper())
            config["tools"][key] = os.environ.get(env_key, config["tools"][key])
    for key in config["flags"]:
        value = getattr(args, key)
        if value is not None:
            config["flags"][key] = value
        elif not args.config:
            config["flags"][key] = os.environ.get(key.upper(), "")
    for choice in args.module:
        name, sep, value = choice.partition("=")
        if not sep or name not in config["modules"] or value not in ("on", "auto", "off"):
            raise InstallError("module must be NAME=on|auto|off; unknown choice: " + choice)
        config["modules"][name] = value
    for key in ("prefix", "cuda", "nvml_include", "nvml_lib"):
        value = getattr(args, key)
        if value:
            config[key] = value
    if not config["prefix"]:
        raise InstallError("--prefix or config prefix is required")
    config["prefix"] = str(Path(config["prefix"]).absolute())
    prefix = Path(config["prefix"])
    if prefix.exists() or prefix.is_symlink():
        raise InstallError("prefix must be a new version directory; existing installations are preserved")
    if prefix == build or prefix in build.parents or build in prefix.parents:
        raise InstallError("build and prefix directories must be separate")
    if prefix == ROOT or prefix in ROOT.parents or ROOT in prefix.parents:
        raise InstallError("prefix must be outside the source tree")
    if config["cuda"]:
        cuda = Path(config["cuda"]).absolute()
        config["cuda"] = str(cuda)
        if not config["nvml_include"]:
            config["nvml_include"] = str(cuda / "include")
        if "nvcc" not in config["requested_tools"]:
            config["tools"]["nvcc"] = str(cuda / "bin/nvcc")
    for key in ("nvml_include", "nvml_lib"):
        if config[key]:
            config[key] = str(Path(config[key]).absolute())
    if any(value not in ("on", "auto", "off") for value in config["modules"].values()):
        raise InstallError("invalid module mode in configuration")
    for key, value in config["tools"].items():
        if not isinstance(value, str) or not value or any(c in value for c in "\n\r"):
            raise InstallError("tool must be one executable path, not a shell command: " + key)
    save(build / "config.json", config)
    tools, failures, results = {}, {}, {}
    work = build / "checks"
    work.mkdir()

    def need(name):
        if name in failures:
            raise InstallError(failures[name])
        if name not in tools:
            try:
                tools[name] = tool(name, config, work)
                if name in ("cc", "cxx", "fc", "mpicc", "mpicxx", "mpifc", "nvcc"):
                    for option, label in (("-dumpmachine", "target"),):
                        try:
                            tools[name][label] = run([tools[name]["path"], option], work,
                                                    work / "logs", name + "-" + label).strip()
                        except InstallError:
                            tools[name][label] = "UNKNOWN"
                if name.startswith("mpi") and name != "mpip_python":
                    for index, option in enumerate(("--showme", "-show")):
                        try:
                            tools[name]["backend"] = run([tools[name]["path"], option], work,
                                                         work / "logs", name + "-backend-" + str(index)).strip()
                            backend = shlex.split(tools[name]["backend"])
                            backend_path = shutil.which(backend[0]) if backend else None
                            if backend_path:
                                tools[name]["backend_executable"] = {
                                    "path": backend_path, "sha256": digest(backend_path)}
                            break
                        except InstallError:
                            pass
            except InstallError as exc:
                failures[name] = str(exc)
                raise
        return tools[name]["path"]

    # Explicitly provided optional languages/toolchains are checked too.
    extra_checks = {}
    try:
        check_platform()
        bash = need("bash")
        scripts = [ROOT / "src/remora", *sorted((ROOT / "src/scripts").glob("*.sh"))]
        for index, script in enumerate(scripts):
            run([bash, "-n", str(script)], work, work / "logs", "base-script-" + str(index))
        extra_checks["base-scripts"] = {"status": "READY"}
    except InstallError as exc:
        extra_checks["base-scripts"] = {"status": "BLOCKED", "reason": str(exc)}
    for name, suffix, code, flag in (
        ("cxx", ".cpp", "int main(){return 0;}\n", "cxxflags"),
        ("fc", ".f90", "program probe\nend program probe\n", "fflags"),
        ("mpicc", ".c", "#include <mpi.h>\nint main(int n,char**v){MPI_Init(&n,&v);MPI_Finalize();}\n", "cflags"),
        ("mpifc", ".f", "      program probe\n      include 'mpif.h'\n      integer ierr\n      call MPI_Init(ierr)\n      call MPI_Finalize(ierr)\n      end\n", "fflags"),
        ("mpicxx", ".cpp", "#include <mpi.h>\nint main(int n,char**v){MPI_Init(&n,&v);MPI_Finalize();}\n", "cxxflags"),
        ("nvcc", ".cu", "__global__ void k(){}\nint main(){return 0;}\n", "cxxflags")):
        explicitly_set = (name in config["requested_tools"] or
                          (name == "nvcc" and bool(config["cuda"])))
        if explicitly_set:
            try:
                check_platform()
                extra = ["-ccbin", need("cxx")] if name == "nvcc" and "cxx" in config["requested_tools"] else []
                compile_probe(name + "-language", need(name), code, suffix, config, work, extra, language=flag)
                extra_checks[name] = {"status": "READY"}
            except InstallError as exc:
                extra_checks[name] = {"status": "BLOCKED", "reason": str(exc)}
    for name, spec in registry().items():
        mode = config["modules"][name]
        result = {"requested": mode, "runtime": "NOT_RUN"}
        if mode == "off":
            result.update(status="DISABLED", reason="disabled by configuration")
        else:
            try:
                check_platform()
                for dep in spec["requires"]:
                    if results[dep]["status"] != "READY":
                        raise InstallError("requires READY module: " + dep)
                need("bash")
                stage = work / (name + "-stage")
                stage.mkdir()
                if spec["kind"] != "shell":
                    cc = need("cc")
                    need("readelf")
                    if "cc-language" not in extra_checks:
                        try:
                            compile_probe("cc-language", cc, "int main(void){return 0;}\n", ".c", config, work)
                            extra_checks["cc-language"] = {"status": "READY"}
                        except InstallError as exc:
                            extra_checks["cc-language"] = {"status": "FAIL", "reason": str(exc)}
                    if extra_checks["cc-language"]["status"] != "READY":
                        raise InstallError(extra_checks["cc-language"]["reason"])
                if spec["kind"] == "mpip":
                    mpi = need("mpicc")
                    description = tools["mpicc"]["version"] + " " + tools["mpicc"].get("backend", "")
                    description += run([mpi, "-v"], work, work / "logs", "mpi-description")
                    # Some wrappers report implementation on stderr.
                    description += (work / "logs/mpi-description.stderr").read_text(errors="replace")
                    if not any(s in description.lower() for s in ("intel(r) mpi", "intel mpi", "mvapich")):
                        raise InstallError("legacy statistics backend supports Intel MPI/MVAPICH2 only")
                    need("mpifc")
                    need("mpip_python")
                    if "Python 2." not in (tools["mpip_python"]["version"] +
                                            (work / "logs/mpip_python-version.stderr").read_text()):
                        raise InstallError("bundled mpiP 3.4.1 requires Python 2")
                    compile_probe("mpi-c", mpi, "#include <mpi.h>\nint main(int n,char**v){MPI_Init(&n,&v);MPI_Finalize();}\n", ".c", config, work)
                    compile_probe("mpi-fortran", tools["mpifc"]["path"],
                                  "      program probe\n      include 'mpif.h'\n      integer ierr\n      call MPI_Init(ierr)\n      call MPI_Finalize(ierr)\n      end\n", ".f", config, work, language="fflags")
                if spec["kind"] in ("sysstat", "mpip"):
                    need("make")
                    dependency_build(name, config, tools, work, stage)
                elif spec["kind"] in ("collector", "nvml"):
                    if name == "gpu":
                        compile_probe("nvml", tools["cc"]["path"],
                                      "#include <nvml.h>\nint main(void){return (int)nvmlInit();}\n", ".c", config, work, gpu_flags(config))
                    collector(name, config, tools, work, stage)
                if "entry" in spec:
                    module_script = spec["entry"].split(",")[0]
                    run([tools["bash"]["path"], "-n", str(ROOT / "src/modules" / module_script)],
                        work, work / "logs", "shell-" + name)
                for index, path in enumerate(sorted(stage.rglob("*"))):
                    if path.is_file() and path.read_bytes()[:4] == b"\x7fELF":
                        run([tools["readelf"]["path"], "-h", "-d", str(path)], work,
                            work / "logs", name + "-elf-" + str(index))
                result.update(status="READY", reason="installation checks passed; runtime NOT_RUN")
            except InstallError as exc:
                result.update(status="BLOCKED" if mode == "on" else "SKIPPED", reason=str(exc))
        results[name] = result
    selected = [name for name, result in results.items() if result["status"] == "READY"]
    blocked = any(r["status"] == "BLOCKED" for r in list(results.values()) + list(extra_checks.values()))
    plan = {"schema": SCHEMA, "source": str(ROOT), "source_files": source_manifest(),
            "build_dir": str(build), "config": config, "tools": tools,
            "environment": environment(), "external_dependencies": external_dependencies(config),
            "platform": {"system": platform.system(), "machine": platform.machine()},
            "checks": extra_checks, "modules": results, "selected": selected,
            "expected_artifacts": expected(selected), "status": "BLOCKED" if blocked else "READY",
            "runtime_qualification": "NOT_RUN"}
    plan["plan_sha256"] = seal(plan)
    save(build / "plan.json", plan)
    show(plan)
    return 2 if blocked else 0


def read_plan(path):
    plan = json.loads(Path(path).read_text())
    claimed = plan.pop("plan_sha256", None)
    if plan.get("schema") != SCHEMA or seal(plan) != claimed:
        raise InstallError("plan schema/integrity mismatch; configure again")
    plan["plan_sha256"] = claimed
    return plan


def show(plan):
    print("Installation plan: " + plan["status"] + "; runtime NOT_RUN")
    print("Prefix: " + plan["config"]["prefix"])
    for name, identity in plan["tools"].items():
        print("Tool {}: {} [{}]".format(name, identity["path"], identity["version"].splitlines()[0]))
        if "backend" in identity:
            print("  wrapper backend: " + identity["backend"])
    print("{:<14} {:<10} {:<10} {}".format("Module", "Requested", "Result", "Reason"))
    for name, result in plan["modules"].items():
        print("{:<14} {:<10} {:<10} {}".format(name, result["requested"], result["status"], result["reason"]))
    for name, result in plan["checks"].items():
        if result["status"] == "BLOCKED":
            print("Toolchain check BLOCKED: {}: {}".format(name, result["reason"]))
    print("Selected modules: " + ", ".join(plan["selected"]))


def verify(plan):
    if plan["status"] != "READY":
        raise InstallError("plan is BLOCKED; configure again after correcting required features")
    if plan["source"] != str(ROOT) or plan["source_files"] != source_manifest():
        raise InstallError("source changed; configure again")
    if plan["environment"] != environment():
        raise InstallError("toolchain environment changed; configure again")
    if plan["external_dependencies"] != external_dependencies(plan["config"]):
        raise InstallError("configured dependency files changed; configure again")
    if plan["platform"] != {"system": platform.system(), "machine": platform.machine()}:
        raise InstallError("platform changed; configure again")
    for identity in plan["tools"].values():
        if not Path(identity["path"]).is_file() or digest(identity["path"]) != identity["sha256"]:
            raise InstallError("tool changed: " + identity["path"])
        backend = identity.get("backend_executable")
        if backend and (not Path(backend["path"]).is_file() or digest(backend["path"]) != backend["sha256"]):
            raise InstallError("MPI backend compiler changed: " + backend["path"])


def artifact_manifest(stage):
    return {str(p.relative_to(stage)): digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}


def build_plan(plan):
    verify(plan)
    work = Path(plan["build_dir"]) / "build"
    if work.exists():
        raise InstallError("build already attempted; preserve evidence and configure a new build directory")
    work.mkdir()
    stage = work / "stage"
    stage.mkdir()
    receipt = {"schema": "remora-build-result/v1", "plan_sha256": plan["plan_sha256"], "status": "FAIL",
               "modules": {name: "NOT_RUN" for name in plan["selected"]}}
    try:
        # Copy scripts/config/docs without generated legacy binaries or C sources.
        for source in (ROOT / "src").iterdir():
            if source.name in ("C_data_collectors_src", "binary_data_collectors"):
                continue
            dest = stage / "bin" / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, dest)
            else:
                shutil.copy2(source, dest)
        for name in ("modules_help", "modules_whatis", "remora_user_guide.pdf"):
            (stage / "docs").mkdir(exist_ok=True)
            shutil.copy2(ROOT / "docs" / name, stage / "docs" / name)
        entries = [registry()[name]["entry"] for name in plan["selected"] if "entry" in registry()[name]]
        (stage / "bin/config/modules").write_text("\n".join(entries) + "\n")
        for name in plan["selected"]:
            receipt["modules"][name] = "BUILDING"
            kind = registry()[name]["kind"]
            if kind in ("sysstat", "mpip"):
                dependency_build(name, plan["config"], plan["tools"], work, stage)
            elif kind in ("collector", "nvml"):
                collector(name, plan["config"], plan["tools"], work, stage)
            receipt["modules"][name] = "BUILT"
        for name in plan["expected_artifacts"]:
            path = stage / name
            if not path.is_file() or not path.stat().st_size:
                raise InstallError("missing/empty expected artifact: " + name)
        # Inspect dynamic dependencies without executing collector binaries.
        inspector = plan["tools"].get("readelf", {}).get("path")
        for index, path in enumerate(sorted(stage.rglob("*"))):
            if path.is_file() and path.read_bytes()[:4] == b"\x7fELF":
                check_elf(path)
                run([inspector, "-h", "-d", str(path)], work, work / "logs", "elf-" + str(index))
        receipt.update(status="BUILT", artifacts=artifact_manifest(stage), runtime_qualification="NOT_RUN")
        save(stage / "build-manifest.json", {"plan": plan, "build": receipt})
    except (InstallError, OSError) as exc:
        receipt["reason"] = str(exc)
        receipt["modules"] = {name: "FAIL" if status == "BUILDING" else status
                              for name, status in receipt["modules"].items()}
        raise
    finally:
        save(work / "result.json", receipt)
    print("BUILT; runtime NOT_RUN. Review logs, then run install with the same plan.")
    return 0


def install_plan(plan):
    verify(plan)
    work = Path(plan["build_dir"]) / "build"
    result = json.loads((work / "result.json").read_text())
    stage = work / "stage"
    if result["status"] != "BUILT" or result["plan_sha256"] != plan["plan_sha256"]:
        raise InstallError("no successful build for this plan")
    artifacts = artifact_manifest(stage)
    artifacts.pop("build-manifest.json", None)
    if artifacts != result["artifacts"]:
        raise InstallError("staged artifacts changed; refuse installation")
    if json.loads((stage / "build-manifest.json").read_text()) != {"plan": plan, "build": result}:
        raise InstallError("staged provenance changed")
    prefix = Path(plan["config"]["prefix"])
    prefix.parent.mkdir(parents=True, exist_ok=True)
    if prefix.exists() or prefix.is_symlink():
        raise InstallError("prefix exists; refusing to overwrite installation")
    # Copy on destination filesystem, then publish by rename under an exclusive lock.
    lock = prefix.parent / ("." + prefix.name + ".install-lock")
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise InstallError("installation lock exists; inspect before retrying: " + str(lock))
    candidate = None
    published = False
    try:
        candidate = Path(tempfile.mkdtemp(prefix="." + prefix.name + ".staging-", dir=prefix.parent))
        shutil.copytree(stage, candidate, dirs_exist_ok=True)
        if artifact_manifest(candidate) != artifact_manifest(stage):
            raise InstallError("destination copy checksum mismatch")
        if prefix.exists() or prefix.is_symlink():
            raise InstallError("prefix appeared during installation")
        candidate.rename(prefix)
        published = True
        save(work / "install-result.json", {"status": "INSTALLED", "prefix": str(prefix),
                                            "plan_sha256": plan["plan_sha256"], "runtime_qualification": "NOT_RUN"})
    except (InstallError, OSError, KeyboardInterrupt) as exc:
        save(work / "install-result.json", {"status": "FAIL", "reason": str(exc),
                                            "published": published, "candidate": str(candidate),
                                            "plan_sha256": plan["plan_sha256"]})
        raise
    finally:
        # A failed candidate is retained for diagnosis, never remove another prefix.
        lock.rmdir()
    print("INSTALLED: " + str(prefix) + "; runtime NOT_RUN")
    return 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("configure", help="compile/link installation checks; use allocated compute on HPC")
    c.add_argument("--build-dir", required=True)
    c.add_argument("--prefix")
    c.add_argument("--config", help="reuse/edit a prior config.json in a NEW build directory")
    for name in TOOL_DEFAULTS:
        c.add_argument("--" + name.replace("_", "-"), dest=name)
    for name in ("cflags", "cxxflags", "fflags", "cppflags", "ldflags"):
        c.add_argument("--" + name)
    for name in ("cuda", "nvml-include", "nvml-lib"):
        c.add_argument("--" + name)
    c.add_argument("--module", action="append", default=[], metavar="NAME=on|auto|off")
    for name in ("show", "build", "install"):
        s = sub.add_parser(name)
        s.add_argument("--plan", required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    previous_term = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.command == "configure":
            return configure(args)
        plan = read_plan(args.plan)
        if args.command == "show":
            show(plan)
            return 0
        return build_plan(plan) if args.command == "build" else install_plan(plan)
    except (InstallError, OSError, ValueError, KeyError, TypeError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("ERROR: installation interrupted; retained work must be reviewed", file=sys.stderr)
        return 130
    finally:
        signal.signal(signal.SIGTERM, previous_term)


if __name__ == "__main__":
    sys.exit(main())
