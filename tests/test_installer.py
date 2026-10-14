"""Installer contract/failure tests with synthetic tools, not HPC qualification."""
import contextlib
import importlib.util
import io
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
spec = importlib.util.spec_from_file_location("remora_install", SOURCE / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="remora installer ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        for name in ("install.py", "install.sh"):
            shutil.copy2(SOURCE / name, self.source / name)
        shutil.copytree(SOURCE / "installer", self.source / "installer")
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
    print("Python 2.7 (synthetic)" if pathlib.Path(__file__).name == "python2" else "Intel(R) MPI Synthetic compiler 1.0"); sys.exit(0)
if args == ["-dumpmachine"]:
    print("x86_64-linux-gnu"); sys.exit(0)
if args in (["--showme"], ["-show"]):
    print(str(pathlib.Path(__file__).parent / "compiler")); sys.exit(0)
if args == ["-v"]:
    print("Intel(R) MPI synthetic"); sys.exit(0)
if "-DFAIL_NVML" in args and any("nvml.c" in a for a in args):
    print("nvml.h missing", file=sys.stderr); sys.exit(1)
if pathlib.Path.cwd().name == "build" and (pathlib.Path(__file__).parent / "fail-build").exists():
    print("injected compile failure", file=sys.stderr); sys.exit(9)
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
        self.python2 = self.tools / "python2"
        shutil.copy2(self.cc, self.python2)
        self.build = self.root / "build state"
        self.prefix = self.root / "installed version"
        self.addCleanup(patch.stopall)
        patch.object(installer, "ROOT", self.source).start()
        patch.object(installer.platform, "system", return_value="Linux").start()
        patch.object(installer.platform, "machine", return_value="x86_64").start()

    def invoke(self, args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return installer.main(args)

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
        with patch.object(installer.platform, "system", return_value="Darwin"):
            self.assertEqual(self.config(), 2)
            self.assertEqual(self.plan()["checks"]["base-scripts"]["status"], "BLOCKED")

    def test_dependency_failure_propagates(self):
        with patch.object(installer, "dependency_build", side_effect=installer.InstallError("F77 symbols failed")):
            self.assertEqual(self.config({"mpstat": "on"}), 2)
            self.assertIn("F77 symbols", self.plan()["modules"]["mpstat"]["reason"])

    def dependency_fixture(self, name, configure_exit=0):
        directory = self.root / (name + "-fixture")
        directory.mkdir()
        (directory / "configure").write_text("#!/bin/sh\nexit " + str(configure_exit) + "\n")
        (directory / "Defs.mak.in").write_text("PYTHON  = python\n")
        with tarfile.open(self.source / installer.ARCHIVES[name], "w:gz") as tar:
            tar.add(directory, arcname=name + "-fixture")

    def test_bundled_mpstat_probe_build_and_install(self):
        self.dependency_fixture("mpstat")
        self.assertEqual(self.config({"mpstat": "on", "cpu": "on"}), 0)
        self.build_install()
        self.assertTrue((self.prefix / "bin/mpstat").exists())

    def test_dependency_configure_failure_is_not_masked(self):
        self.dependency_fixture("mpstat", configure_exit=6)
        self.assertEqual(self.config({"mpstat": "on"}), 2)
        log = self.build / "checks/logs/mpstat-configure.json"
        self.assertEqual(json.loads(log.read_text())["exit_code"], 6)
        self.assertFalse((self.build / "checks/logs/mpstat-make.json").exists())

    def test_mpip_configure_static_shared_build_and_install(self):
        self.dependency_fixture("mpip")
        extra = ["--mpicc", str(self.cc), "--mpifc", str(self.cc), "--mpip-python", str(self.python2)]
        self.assertEqual(self.config({"mpip": "on"}, extra), 0)
        self.build_install()
        self.assertTrue((self.prefix / "lib/libmpiP.a").exists())
        self.assertTrue((self.prefix / "lib/libmpiP.so").exists())
        self.assertEqual((self.prefix / "bin/config/modules").read_text(), "impi_mpip,MPI\n")

    def test_mpip_shared_failure_blocks_before_build(self):
        self.dependency_fixture("mpip")
        (self.tools / "fail-shared").touch()
        extra = ["--mpicc", str(self.cc), "--mpifc", str(self.cc), "--mpip-python", str(self.python2)]
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
        with patch.object(installer.shutil, "copytree", side_effect=OSError("injected copy failure")):
            self.assertEqual(self.invoke(["install", "--plan", str(self.build / "plan.json")]), 2)
        self.assertFalse(self.prefix.exists())
        self.assertTrue(list(self.prefix.parent.glob("." + self.prefix.name + ".staging-*")))
        self.assertFalse((self.prefix.parent / ("." + self.prefix.name + ".install-lock")).exists())

    def test_build_dir_cannot_modify_source(self):
        self.build = self.source / "src/work"
        self.assertEqual(self.config(), 2)
        self.assertFalse(self.build.exists())

    def test_interruption_returns_130_and_restores_handler(self):
        import signal
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(installer, "configure", side_effect=KeyboardInterrupt):
            self.assertEqual(self.config(), 130)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)

    def test_timeout_recorded(self):
        with self.assertRaises(installer.InstallError):
            installer.run([sys.executable, "-c", "import time; time.sleep(60)"], self.root,
                          self.root / "logs", "timeout", timeout=0.05)
        self.assertIsNotNone(json.loads((self.root / "logs/timeout.json").read_text())["exit_code"])

    def test_archive_traversal_rejected(self):
        archive = self.root / "bad.tar"
        with tarfile.open(archive, "w") as tar:
            member = tarfile.TarInfo("../escape"); member.size = 1
            tar.addfile(member, io.BytesIO(b"x"))
        with self.assertRaises(installer.InstallError):
            installer.extract(archive, self.root / "extract")
        self.assertFalse((self.root / "escape").exists())


if __name__ == "__main__":
    unittest.main()
