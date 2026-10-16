"""Installer contract/failure tests with synthetic tools, not HPC qualification."""
import io
import subprocess
import time
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1]
class InstallerData:
    ARCHIVES = {"mpstat":"extra/sysstat-11.5.3.tar.gz", "mpip":"extra/mpiP-3.5.tar.gz"}
    @staticmethod
    def registry():
        return json.loads((SOURCE / "installer/modules.json").read_text())["modules"]
    @staticmethod
    def read_plan(path):
        return json.loads(Path(path).read_text())
installer = InstallerData()



class InstallerTests(unittest.TestCase):
    def assertEqual(self, first, second, msg=None):
        return super().assertEqual(first, second, msg or getattr(self,"last_output",None))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="remora installer ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ("install.sh",):
            shutil.copy2(SOURCE / name, self.source / name)
        shutil.copytree(SOURCE / "installer", self.source / "installer")
        shutil.copytree(SOURCE / "runtime", self.source / "runtime")
        shutil.copytree(SOURCE / "build_support", self.source / "build_support")
        shutil.copytree(SOURCE / "third_party_packages", self.source / "third_party_packages")
        shutil.copytree(SOURCE / "src", self.source / "src")
        (self.source / "docs").mkdir()
        for name in ("modules_help", "modules_whatis", "remora_user_guide.pdf"):
            (self.source / "docs" / name).write_text("fixture\n")
        (self.source / "extra").mkdir()
        for name in installer.ARCHIVES.values():
            (self.source / name).write_bytes(b"archive fixture")
        self.tools = self.root / "tools"
        self.tools.mkdir()
        self.cc = self.tools / "compiler"
        self.cc.write_text("#!" + sys.executable + "\n" + r'''
import pathlib, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("Intel(R) MPI Synthetic compiler 1.0"); sys.exit(0)
if args == ["-dumpmachine"]:
    print("x86_64-linux-gnu"); sys.exit(0)
if args in (["--showme"], ["-show"]):
    print(str(pathlib.Path(__file__).parent / "compiler")); sys.exit(0)
if args == ["-v"]:
    print("Intel(R) MPI synthetic"); sys.exit(0)
if "-DSLOW" in args:
    import time; time.sleep(10)
if "-DFAIL_NVML" in args and any("nvml.c" in a for a in args):
    print("nvml.h missing", file=sys.stderr); sys.exit(1)
if pathlib.Path.cwd().name == "build" and (pathlib.Path(__file__).parent / "fail-build").exists():
    print("injected compile failure", file=sys.stderr); sys.exit(9)
if pathlib.Path.cwd().name == "build" and (pathlib.Path(__file__).parent / "slow-build").exists():
    import time
    (pathlib.Path(__file__).parent / "build-started").write_text("started")
    time.sleep(10)
out = pathlib.Path(args[args.index("-o") + 1])
header = bytearray(32); header[:4] = b"\x7fELF"; header[5] = 1
# Synthetic ELF header only; never execute these files.
header[18] = 62
if "-DWRONG_ARCH" in args: header[18:20] = (183).to_bytes(2, "little")
out.write_bytes(header); out.chmod(0o755)
''')
        self.cc.chmod(0o755)
        self.readelf = self.tools / "readelf"
        self.readelf.write_text("#!/bin/sh\nprintf '%s\\n' 'synthetic readelf: header/dynamic inspection'\n")
        self.readelf.chmod(0o755)
        self.make = self.tools / "make"
        self.make.write_text("#!" + sys.executable + "\n" + r'''
import pathlib, sys
args = sys.argv[1:]
if args == ["--version"]: print("Synthetic make"); sys.exit(0)
if "shared" in args and (pathlib.Path(__file__).parent / "fail-shared").exists(): sys.exit(8)
header = bytearray(32); header[:4] = b"\x7fELF"; header[5] = 1; header[18] = 62
if "mpstat" in args: pathlib.Path("mpstat").write_bytes(header)
if "libmpiP.a" in args: pathlib.Path("libmpiP.a").write_bytes(b"!<arch>\nfixture")
if "shared" in args: pathlib.Path("libmpiP.so").write_bytes(header)
''')
        self.make.chmod(0o755)
        self.mpip_python = self.tools / "python3"
        self.mpip_python.write_text("#!" + sys.executable + "\nimport os,sys\nos.execv(" + repr(sys.executable) + ", [" + repr(sys.executable) + "] + sys.argv[1:])\n")
        self.mpip_python.chmod(0o755)
        self.build = self.root / "build state"
        self.prefix = self.root / "installed version"
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.tools) + os.pathsep + self.env["PATH"]
        self.env["REMORA_FIXTURE_SYSTEM"] = "Linux"
        self.uname = self.tools / "uname"
        self.uname.write_text("#!/bin/sh\nif [ \"$1\" = -s ]; then printf '%s\\n' \"${REMORA_FIXTURE_SYSTEM:-Linux}\"; else echo x86_64; fi\n")
        self.uname.chmod(0o755)
        self.setsid = self.tools / "setsid"
        self.setsid.write_text("#!" + sys.executable + "\nimport os,sys\nos.setsid()\nos.execv(sys.argv[1],sys.argv[1:])\n")
        self.setsid.chmod(0o755)
        self.timeout = self.tools / "timeout"
        self.timeout.write_text("#!" + sys.executable + "\n" + r'''
import subprocess,sys
args=sys.argv[1:]
while args and args[0].startswith("--"): args.pop(0)
limit=float(args.pop(0)); proc=subprocess.Popen(args,close_fds=False)
try: sys.exit(proc.wait(timeout=limit))
except subprocess.TimeoutExpired:
    proc.terminate()
    try: proc.wait(timeout=3)
    except subprocess.TimeoutExpired: proc.kill(); proc.wait()
    sys.exit(124)
''')
        self.timeout.chmod(0o755)
        mv=self.tools/"mv"
        mv.write_text('''#!/bin/sh
if [ "$1" = -T ]; then
  shift; shift; shift
  if [ -n "${REMORA_PUBLISH_RACE:-}" ] && [ -f "$REMORA_PUBLISH_RACE" ]; then
    mkdir "$2"; echo original > "$2/keep"; exit 0
  fi
  [ ! -e "$2" ] && [ ! -L "$2" ] || exit 0
fi
exec /bin/mv "$@"
''')
        mv.chmod(0o755)

    def invoke(self, args):
        env = os.environ.copy()
        env.update(self.env)
        # Let a test inject an actual recorded environment drift.
        if "CPATH" in os.environ: env["CPATH"] = os.environ["CPATH"]
        result = subprocess.run(["/bin/bash", str(self.source / "install.sh"), *args],
                                cwd=self.root, env=env, capture_output=True, text=True)
        self.last_output = result.stdout + result.stderr
        self.last_stdout = result.stdout
        self.last_stderr = result.stderr
        if result.returncode not in (0,2,130): print(self.last_output)
        return result.returncode

    def config(self, choices=None, extra=()):
        modes = {name: "off" for name in installer.registry()}
        modes.update(choices or {"memory": "on"})
        args = ["configure", "--build-dir", str(self.build), "--prefix", str(self.prefix), "--cc", str(self.cc), "--readelf", str(self.readelf), "--make", str(self.make)]
        for name, value in modes.items():
            args += ["--module", name + "=" + value]
        return self.invoke(args + list(extra))

    def plan(self):
        return installer.read_plan(self.build / "plan.json")

    def build_install(self):
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 0)
        self.assertEqual(self.invoke(["install", "--plan", str(self.build / "plan.json")]), 0)

    def test_successful_selected_build_and_install_with_spaces(self):
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.plan()["selected"], ["memory"])
        self.build_install()
        self.assertEqual((self.prefix / "bin/config/modules").read_text(), "memory,MEMORY\n")
        self.assertTrue((self.prefix / "bin/binary_data_collectors/data_collect_memory").is_file())
        self.assertFalse((self.prefix / "bin/binary_data_collectors/data_collect_gpu").exists())
        self.assertEqual(json.loads((self.prefix / "build-manifest.json").read_text())["build"]["runtime_qualification"], "NOT_RUN")

    def assert_progress_order(self, phase, messages):
        position = 0
        for message in messages:
            marker = f"[{phase}] {message}"
            index = self.last_stdout.find(marker, position)
            self.assertGreaterEqual(index, 0, self.last_stdout)
            position = index + len(marker)

    def test_progress_on_stdout_for_all_installation_phases(self):
        self.assertEqual(self.config(), 0)
        self.assert_progress_order("configure", [
            "Starting configuration", "Checking platform and base scripts",
            "Checking module: memory", "Module memory: READY",
            "Recording source, dependency and environment checksums",
            "Configuration result: READY",
        ])
        self.assertEqual(self.last_stderr, "")
        plan = str(self.build / "plan.json")
        self.assertEqual(self.invoke(["build", "--plan", plan]), 0)
        self.assert_progress_order("build", [
            "Starting build; verifying plan", "Preparing staged scripts",
            "Building module: memory", "Module memory: BUILT",
            "Verifying expected artifacts", "Build complete; receipt:",
        ])
        self.assertEqual(self.last_stderr, "")
        self.assertEqual(self.invoke(["install", "--plan", plan]), 0)
        self.assert_progress_order("install", [
            "Starting installation; verifying plan", "Verifying staged artifacts",
            "Copying installation", "Verifying destination copy checksums",
            "Publishing installation", "Installation complete; receipt:",
        ])
        self.assertEqual(self.last_stderr, "")
        self.assertEqual(self.invoke(["show", "--plan", plan]), 0)
        self.assertNotIn("[show]", self.last_stdout)
        for log in (self.build / "checks/logs").glob("*.stdout"):
            self.assertNotIn("[configure]", log.read_text())

    def test_failure_progress_does_not_claim_success(self):
        self.assertEqual(self.config(), 0)
        (self.tools / "fail-build").touch()
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 2)
        self.assert_progress_order("build", [
            "Building module: memory", "Command collector-memory failed (exit 9)",
            "FAILED:",
        ])
        self.assertNotIn("[build] Build complete", self.last_stdout)
        self.assertNotIn("[build] Module memory: BUILT", self.last_stdout)
        self.assertIn("ERROR:", self.last_stderr)
        self.assertIn("injected compile failure", (self.build / "build/logs/collector-memory.stderr").read_text())

    def test_progress_arrives_before_long_command_finishes(self):
        import queue
        import threading

        self.assertEqual(self.config(), 0)
        (self.tools / "slow-build").touch()
        proc = subprocess.Popen(
            ["/bin/bash", str(self.source / "install.sh"), "build", "--plan", str(self.build / "plan.json")],
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        lines = queue.Queue()
        reader = threading.Thread(target=lambda: [lines.put(line) for line in proc.stdout], daemon=True)
        reader.start()
        try:
            deadline = time.monotonic() + 8
            output = ""
            while "[build] Building module: memory" not in output:
                output += lines.get(timeout=max(0.01, deadline - time.monotonic()))
            while not (self.tools / "build-started").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.tools / "build-started").exists())
            self.assertIsNone(proc.poll(), output)
        finally:
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=8)
            reader.join(timeout=2)
            proc.stdout.close()
            proc.stderr.close()
        self.assertEqual(proc.returncode, 130)

    def test_gpu_auto_skips_missing_header(self):
        self.assertEqual(self.config({"gpu": "auto"}, ["--cppflags=-DFAIL_NVML"]), 0)
        self.assertEqual(self.plan()["modules"]["gpu"]["status"], "SKIPPED")
        self.assertNotIn("gpu", self.plan()["selected"])

    def test_gpu_on_blocks_missing_header(self):
        self.assertEqual(self.config({"gpu": "on"}, ["--cppflags=-DFAIL_NVML"]), 2)
        self.assertEqual(self.plan()["status"], "BLOCKED")
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 2)
        self.assertFalse(self.prefix.exists())

    def test_gpu_off_never_runs_nvml_probe(self):
        self.assertEqual(self.config(extra=["--cppflags=-DFAIL_NVML"]), 0)
        self.assertFalse((self.build / "checks/nvml.c").exists())

    def test_cpu_depends_on_mpstat(self):
        self.assertEqual(self.config({"cpu": "on"}), 2)
        self.assertIn("mpstat", self.plan()["modules"]["cpu"]["reason"])

    def test_optional_languages_not_required(self):
        self.assertEqual(self.config(), 0)
        self.assertNotIn("fc", self.plan()["tools"])
        self.assertNotIn("cxx", self.plan()["tools"])

    def test_explicit_missing_fortran_blocks(self):
        self.assertEqual(self.config(extra=["--fc", str(self.tools / "missing")]), 2)
        self.assertEqual(self.plan()["checks"]["fc"]["status"], "BLOCKED")

    def test_supplied_mpi_wrappers_checked_without_mpip(self):
        self.assertEqual(self.config(extra=["--mpicc", str(self.cc), "--mpifc", str(self.cc)]), 0)
        self.assertEqual(self.plan()["checks"]["mpicc"]["status"], "READY")
        self.assertTrue((self.build / "checks/mpifc-language.f").exists())
        self.assertIn("include 'mpif.h'",(self.build/"checks/mpifc-language.f").read_text())

    def test_no_python_command_required_by_installer(self):
        marker=self.root/"python-invoked"
        self.env["REMORA_PYTHON_FORBIDDEN_MARKER"]=str(marker)
        for name in ("python","python3"):
            stub=self.tools/name
            stub.write_text('#!/bin/sh\necho forbidden > "$REMORA_PYTHON_FORBIDDEN_MARKER"\nexit 99\n')
            stub.chmod(0o755)
        self.assertEqual(self.config(),0)
        self.build_install()
        self.assertFalse(marker.exists())
        self.assertFalse((SOURCE/"install.py").exists())

    def test_flags_quote_spaces_without_shell_evaluation(self):
        marker=self.root/"must-not-exist"
        literal="$(touch "+str(marker)+")"
        value='-DNAME="hello world" -DLITERAL=\''+literal+"'"
        self.assertEqual(self.config(extra=["--cflags="+value]),0)
        command=json.loads((self.build/"checks/logs/cc-language.json").read_text())["command"]
        self.assertIn("-DNAME=hello world",command)
        self.assertIn("-DLITERAL="+literal,command)
        self.assertFalse(marker.exists())

    def test_unicode_paths_and_json_seal_roundtrip(self):
        self.build=self.root/"編譯紀錄"
        self.prefix=self.root/"安裝版本"
        self.assertEqual(self.config(),0)
        self.assertEqual(self.invoke(["show","--plan",str(self.build/"plan.json")]),0)
        self.build_install()

    def test_old_python_plan_requires_reconfigure(self):
        self.assertEqual(self.config(),0)
        path=self.build/"plan.json"; data=json.loads(path.read_text())
        data["schema"]="remora-build-plan/v1"; path.write_text(json.dumps(data))
        self.assertEqual(self.invoke(["show","--plan",str(path)]),2)
        self.assertIn("configure again",self.last_output)

    def test_source_alias_cannot_receive_build_output(self):
        alias=self.root/"source-alias"; alias.symlink_to(self.source/"src",target_is_directory=True)
        self.build=alias/"new-work"
        self.assertEqual(self.config(),2)
        self.assertFalse(self.build.exists())

    def test_prefix_created_during_publish_is_preserved(self):
        self.env["REMORA_PUBLISH_RACE"]=str(self.root/"race-marker")
        self.assertEqual(self.config(),0)
        self.assertEqual(self.invoke(["build","--plan",str(self.build/"plan.json")]),0)
        (self.root/"race-marker").touch()
        self.assertEqual(self.invoke(["install","--plan",str(self.build/"plan.json")]),2)
        self.assertEqual((self.prefix/"keep").read_text(),"original\n")
        self.assertTrue(list(self.prefix.parent.glob("."+self.prefix.name+".staging-*")))

    def test_required_mpip_does_not_silently_disable(self):
        self.assertEqual(self.config({"mpip": "on"}, ["--mpicc", str(self.cc)]), 2)
        self.assertEqual(self.plan()["modules"]["mpip"]["status"], "BLOCKED")

    def test_reconfigure_saved_config_keeps_optional_languages_optional(self):
        self.assertEqual(self.config(), 0)
        new = self.root / "reconfigured"
        self.assertEqual(self.invoke(["configure", "--build-dir", str(new), "--config", str(self.build / "config.json")]), 0)
        result = installer.read_plan(new / "plan.json")
        self.assertNotIn("fc", result["tools"])

    def test_plan_tampering_rejected(self):
        self.assertEqual(self.config(), 0)
        path = self.build / "plan.json"
        data = json.loads(path.read_text()); data["config"]["prefix"] = "/different"
        path.write_text(json.dumps(data))
        self.assertEqual(self.invoke(["build", "--plan", str(path)]), 2)

    def test_source_change_rejected(self):
        self.assertEqual(self.config(), 0)
        (self.source / "src/remora").write_text("changed")
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 2)

    def test_tool_change_rejected(self):
        self.assertEqual(self.config(), 0)
        self.cc.write_text(self.cc.read_text() + "\n# changed\n")
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 2)

    def test_environment_change_rejected(self):
        self.assertEqual(self.config(), 0)
        with patch.dict(os.environ, {"CPATH": "changed"}):
            self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 2)

    def test_failed_build_preserves_evidence_without_install(self):
        self.assertEqual(self.config(), 0)
        (self.tools / "fail-build").touch()
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 2)
        result = json.loads((self.build / "build/result.json").read_text())
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(self.invoke(["install", "--plan", str(self.build / "plan.json")]), 2)
        self.assertFalse(self.prefix.exists())
        self.assertTrue((self.build / "build/logs/collector-memory.stderr").is_file())

    def test_installed_prefix_not_overwritten(self):
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 0)
        self.prefix.mkdir(); (self.prefix / "keep").write_text("original")
        self.assertEqual(self.invoke(["install", "--plan", str(self.build / "plan.json")]), 2)
        self.assertEqual((self.prefix / "keep").read_text(), "original")

    def test_changed_artifact_not_installed(self):
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 0)
        (self.build / "build/stage/bin/remora").write_text("changed")
        self.assertEqual(self.invoke(["install", "--plan", str(self.build / "plan.json")]), 2)
        self.assertFalse(self.prefix.exists())

    def test_no_build_directory_reuse(self):
        self.assertEqual(self.config(), 0)
        before = (self.build / "plan.json").read_bytes()
        self.assertEqual(self.config(), 2)
        self.assertEqual(before, (self.build / "plan.json").read_bytes())

    def test_non_linux_is_blocked(self):
        self.env["REMORA_FIXTURE_SYSTEM"] = "Darwin"
        self.assertEqual(self.config(), 2)
        self.assertEqual(self.plan()["checks"]["base-scripts"]["status"], "BLOCKED")

    def test_dependency_failure_propagates(self):
        self.dependency_fixture("mpstat", configure_exit=7)
        self.assertEqual(self.config({"mpstat":"on"}),2)
        self.assertIn("mpstat-configure failed",self.plan()["modules"]["mpstat"]["reason"])

    def dependency_fixture(self, name, configure_exit=0):
        directory = self.root / (name + "-fixture")
        directory.mkdir()
        probe = 'echo "main(){ FF(); return 0; }" > flink.c\n' if name == "mpip" else ""
        (directory / "configure").write_text("#!/bin/sh\n" + probe + "exit " + str(configure_exit) + "\n")
        (directory / "Defs.mak.in").write_text("PYTHON  = python\n")
        with tarfile.open(self.source / installer.ARCHIVES[name], "w:gz") as tar:
            tar.add(directory, arcname=name + "-fixture")
        registry = self.source / "third_party_packages/packages.json"
        data = json.loads(registry.read_text())
        if "sha256" in data["packages"][name]:
            import hashlib
            data["packages"][name]["sha256"] = hashlib.sha256((self.source / installer.ARCHIVES[name]).read_bytes()).hexdigest()
            registry.write_text(json.dumps(data))

    def test_bundled_mpstat_probe_build_and_install(self):
        self.dependency_fixture("mpstat")
        self.assertEqual(self.config({"mpstat": "on", "cpu": "on"}), 0)
        self.build_install()
        self.assertTrue((self.prefix / "bin/mpstat").exists())

    def test_dependency_configure_failure_is_not_masked(self):
        self.dependency_fixture("mpstat", configure_exit=6)
        self.assertEqual(self.config({"mpstat": "on"}), 2)
        log = self.build / "checks/third_party_packages/mpstat/logs/mpstat-configure.json"
        self.assertEqual(json.loads(log.read_text())["exit_code"], 6)
        self.assertFalse((self.build / "checks/third_party_packages/mpstat/logs/mpstat-make.json").exists())

    def test_mpip_configure_static_shared_build_and_install(self):
        self.dependency_fixture("mpip")
        extra = ["--mpicc", str(self.cc), "--mpicxx", str(self.cc), "--mpifc", str(self.cc), "--mpip-python", str(self.mpip_python)]
        self.assertEqual(self.config({"mpip": "on"}, extra), 0)
        self.build_install()
        self.assertTrue((self.prefix / "lib/libmpiP.a").exists())
        self.assertTrue((self.prefix / "lib/libmpiP.so").exists())
        self.assertEqual((self.prefix / "bin/config/modules").read_text(), "impi_mpip,MPI\n")

    def test_mpip_shared_failure_blocks_before_build(self):
        self.dependency_fixture("mpip")
        (self.tools / "fail-shared").touch()
        extra = ["--mpicc", str(self.cc), "--mpicxx", str(self.cc), "--mpifc", str(self.cc), "--mpip-python", str(self.mpip_python)]
        self.assertEqual(self.config({"mpip": "on"}, extra), 2)
        self.assertFalse(self.prefix.exists())

    def test_wrong_architecture_compiler_rejected(self):
        self.assertEqual(self.config(extra=["--cflags=-DWRONG_ARCH"]), 2)
        self.assertIn("architecture", self.plan()["modules"]["memory"]["reason"])

    def test_failed_shared_c_probe_reports_all_modules(self):
        self.assertEqual(self.config({"memory": "on", "numa": "auto", "gpu": "auto"},
                                     ["--cflags=-DWRONG_ARCH"]), 2)
        self.assertEqual(self.plan()["modules"]["memory"]["status"], "BLOCKED")
        self.assertEqual(self.plan()["modules"]["numa"]["status"], "SKIPPED")
        self.assertEqual(self.plan()["modules"]["gpu"]["status"], "SKIPPED")

    def test_explicit_language_and_cuda_compilers_checked(self):
        self.assertEqual(self.config(extra=["--cxx", str(self.cc), "--fc", str(self.cc),
                                            "--nvcc", str(self.cc)]), 0)
        for name in ("cxx", "fc", "nvcc"):
            self.assertEqual(self.plan()["checks"][name]["status"], "READY")

    def test_explicit_nvml_dependency_drift_rejected(self):
        include = self.root / "nvml include"; include.mkdir()
        header = include / "nvml.h"; header.write_text("header v1")
        self.assertEqual(self.config({"gpu": "on"}, ["--nvml-include", str(include)]), 0)
        header.write_text("header v2")
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 2)

    def test_failed_destination_copy_preserves_existing_state(self):
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 0)
        cp = self.tools / "cp"
        cp.write_text("#!/bin/sh\nfor arg do case $arg in *.staging-*) exit 8;; esac; done\nexec /bin/cp \"$@\"\n")
        cp.chmod(0o755)
        self.assertEqual(self.invoke(["install", "--plan", str(self.build / "plan.json")]), 2)
        self.assertFalse(self.prefix.exists())
        self.assertTrue(list(self.prefix.parent.glob("." + self.prefix.name + ".staging-*")))
        self.assertFalse((self.prefix.parent / ("." + self.prefix.name + ".install-lock")).exists())

    def test_build_dir_cannot_modify_source(self):
        self.build = self.source / "src/work"
        self.assertEqual(self.config(), 2)
        self.assertFalse(self.build.exists())

    def test_interruption_returns_130_and_preserves_receipt(self):
        args = ["/bin/bash", str(self.source / "install.sh"), "configure", "--build-dir",str(self.build),
                "--prefix",str(self.prefix),"--cc",str(self.cc),"--readelf",str(self.readelf),"--cflags=-DSLOW"]
        proc = subprocess.Popen(args, cwd=self.root,env=self.env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        receipt=self.build / "checks/logs/cc-language.json"
        deadline=time.monotonic()+8
        while not receipt.exists() and proc.poll() is None and time.monotonic()<deadline: time.sleep(.02)
        self.assertTrue(receipt.exists())
        proc.terminate()
        self.assertEqual(proc.wait(timeout=8),130)
        self.assertEqual(json.loads(receipt.read_text())["exit_code"],130)
        self.assertFalse(self.prefix.exists())

    def test_timeout_recorded(self):
        script='source "$1"; SETSID="$2"; TIMEOUT="$3"; run "$4" "$4/logs" timeout 0.05 "$5" -c "import time; time.sleep(60)"'
        result=subprocess.run(["/bin/bash","-c",script,"test",str(self.source/"installer/common.sh"),
                               str(self.setsid),str(self.timeout),str(self.root),sys.executable],env=self.env,capture_output=True)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual(json.loads((self.root/"logs/timeout.json").read_text())["exit_code"],124)

    def test_archive_traversal_rejected(self):
        archive=self.source / installer.ARCHIVES["mpstat"]
        with tarfile.open(archive,"w:gz") as tar:
            member=tarfile.TarInfo("../escape"); member.size=1
            tar.addfile(member,io.BytesIO(b"x"))
        self.assertEqual(self.config({"mpstat":"on"}),2)
        self.assertFalse((self.build/"checks/escape").exists())
        self.assertIn("unsafe",self.plan()["modules"]["mpstat"]["reason"])


if __name__ == "__main__":
    unittest.main()
