"""Third-party boundary and compiler capability regressions; synthetic Linux only."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

import test_installer as fixtures


class ThirdPartyTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.InstallerTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.standalone = self.f.root / "dependency build"

    def rejection_compiler(self, warn=False, dynamic=False):
        source = self.f.cc.read_text()
        rule = '''
if "-pipe" in args and REJECT:
    print("nvc-Error-Unknown switch: -pipe", file=sys.stderr)
    sys.exit(2)
'''
        if warn:
            rule = '''
if "-pipe" in args:
    print("warning: ignoring unsupported option -pipe", file=sys.stderr)
'''
        rule = rule.replace("REJECT", '(pathlib.Path(__file__).parent / "reject-pipe").exists()' if dynamic else "True")
        source = source.replace('args = sys.argv[1:]\n', 'args = sys.argv[1:]\n' + rule, 1)
        self.f.cc.write_text(source)

    def child_result(self, name="mpstat", phase="checks"):
        return json.loads((self.f.build / phase / "third_party_packages" / name / "result.json").read_text())

    def invoke_standalone(self, config=None, name="mpstat"):
        cfg = self.f.root / "dependency-config.json"
        cfg.write_text(json.dumps(config or {"tools": {"cc": str(self.f.cc), "make": str(self.f.make), "readelf": str(self.f.readelf)}}))
        return subprocess.run(
            ["/bin/bash", str(self.f.source / "third_party_packages/install.sh"), "check", "--package", name,
             "--config", str(cfg), "--build-dir", str(self.standalone)],
            env=self.f.env, cwd=self.f.root, capture_output=True, text=True,
        )

    def test_old_jq_split_keeps_last_checksum_and_environment_value(self):
        # Emulate jq 1.5 omitting the final empty split field, using the host jq.
        actual_jq = shutil.which("jq")
        wrapper = self.f.tools / "jq"
        expression = r'split("\u0000")'
        compatibility = r'(split("\u0000") | if .[-1]=="" then .[0:-1] else . end)'
        wrapper.write_text("#!" + sys.executable + "\n" +
            "import os,sys\nargs=sys.argv[1:]\n" +
            "args=[arg.replace(" + repr(expression) + ", " + repr(compatibility) + ") for arg in args]\n" +
            "os.execv(" + repr(actual_jq) + ", [" + repr(actual_jq) + "] + args)\n")
        wrapper.chmod(0o755)
        self.f.env["TACC_CUDA_LIB"] = ""  # Empty final value must remain empty, not null.
        include = self.f.root / "external include"
        include.mkdir()
        header = include / "nvml.h"
        header.write_text("external manifest fixture")
        self.f.dependency_fixture("mpstat")
        self.assertEqual(self.f.config({"mpstat": "on", "cpu": "on"}, ["--nvml-include", str(include)]), 0, self.f.last_output)
        receipt = self.child_result()
        self.assertRegex(receipt["artifacts"]["bin/mpstat"], r"^[0-9a-f]{64}$")
        plan = self.f.plan()
        self.assertEqual(plan["environment"]["TACC_CUDA_LIB"], "")
        self.assertRegex(plan["external_dependencies"][str(header)], r"^[0-9a-f]{64}$")
        self.assertTrue(all(isinstance(value, str) and len(value) == 64 for value in plan["source_files"].values()))
        self.f.build_install()

    def test_standalone_does_not_load_remora_cli(self):
        self.f.dependency_fixture("mpstat")
        (self.f.source / "installer/cli.sh").rename(self.f.source / "installer/unused-cli.sh")
        result = self.invoke_standalone()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        receipt = json.loads((self.standalone / "result.json").read_text())
        self.assertEqual(receipt["status"], "BUILT")
        self.assertEqual(receipt["runtime_qualification"], "NOT_RUN")
        self.assertEqual(set(receipt["artifacts"]), {"bin/mpstat"})
        self.assertFalse(self.f.prefix.exists())

    def test_unselected_dependency_does_not_invoke_child(self):
        self.assertEqual(self.f.config(), 0, self.f.last_output)
        self.assertEqual(self.f.plan()["third_party_packages"], {})
        self.assertFalse((self.f.build / "checks/third_party_packages").exists())

    def test_rejected_sysstat_default_not_reintroduced_by_make(self):
        self.f.dependency_fixture("mpstat")
        self.rejection_compiler()
        make = self.f.make.read_text()
        make = make.replace('if "mpstat" in args:', '''
if "mpstat" in args:
    import shlex
    values = [arg.split("=",1)[1] for arg in args if arg.startswith("CFLAGS=")]
    # Upstream defaults contain -pipe unless the recipe overrides CFLAGS.
    if not values or "-pipe" in shlex.split(values[0]): sys.exit(9)
if "mpstat" in args:''')
        self.f.make.write_text(make)
        self.assertEqual(self.f.config({"mpstat": "on", "cpu": "on"}), 0, self.f.last_output)
        checked = self.child_result()
        self.assertTrue(any(p["flag"] == "-pipe" and p["status"] == "REJECTED" for p in checked["flag_probes"]))
        self.assertNotIn("-pipe", checked["effective_flags"]["cflags"])
        self.assertIn("[third-party-check] Omitting unsupported default -pipe", self.f.last_stdout)
        self.f.build_install()
        self.assertEqual(self.child_result(phase="build")["effective_flags"], checked["effective_flags"])
        self.assertIn("third_party_packages", json.loads((self.f.prefix / "build-manifest.json").read_text())["build"])

    def test_user_flag_is_not_silently_removed(self):
        self.f.dependency_fixture("mpstat")
        self.rejection_compiler()
        result = self.invoke_standalone({"tools": {"cc": str(self.f.cc), "make": str(self.f.make), "readelf": str(self.f.readelf)}, "flags": {"cflags": "-pipe"}})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        receipt = json.loads((self.standalone / "result.json").read_text())
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["requested_flags"]["cflags"], "-pipe")
        self.assertFalse((self.standalone / "logs/mpstat-make.json").exists())

    def test_ignored_default_with_exit_zero_is_rejected(self):
        self.f.dependency_fixture("mpstat")
        self.rejection_compiler(warn=True)
        self.assertEqual(self.f.config({"mpstat": "on"}), 0, self.f.last_output)
        self.assertNotIn("-pipe", self.child_result()["effective_flags"]["cflags"])

    def test_capability_drift_blocks_before_upstream_make(self):
        self.f.dependency_fixture("mpstat")
        self.rejection_compiler(dynamic=True)
        self.assertEqual(self.f.config({"mpstat": "on"}), 0, self.f.last_output)
        (self.f.tools / "reject-pipe").touch()
        self.assertEqual(self.f.invoke(["build", "--plan", str(self.f.build / "plan.json")]), 2)
        self.assertIn("flag capabilities changed", self.f.last_output)
        self.assertFalse((self.f.build / "build/third_party_packages/mpstat/logs/mpstat-make.json").exists())
        self.assertFalse(self.f.prefix.exists())

    def test_recipe_change_invalidates_main_plan(self):
        self.assertEqual(self.f.config(), 0)
        recipe = self.f.source / "third_party_packages/packages/mpstat.sh"
        recipe.write_text(recipe.read_text() + "\n# modified\n")
        self.assertEqual(self.f.invoke(["build", "--plan", str(self.f.build / "plan.json")]), 2)
        self.assertIn("source changed", self.f.last_output)

    def mpip_options(self, interpreter=True):
        options = ["--mpicc", str(self.f.cc), "--mpicxx", str(self.f.cc), "--mpifc", str(self.f.cc)]
        if interpreter:
            options += ["--mpip-python", str(self.f.mpip_python)]
        return options

    def test_mpip_openmpi_python3_default_and_explicit_build_tools(self):
        self.f.dependency_fixture("mpip")
        self.f.cc.write_text(self.f.cc.read_text().replace("Intel(R) MPI Synthetic compiler 1.0", "Open MPI 5.0.5 synthetic"))
        self.assertEqual(self.f.config({"mpip": "on"}, self.mpip_options(interpreter=False)), 0, self.f.last_output)
        receipt = self.child_result("mpip")
        self.assertEqual(receipt["version"], "3.5")
        self.assertEqual(receipt["tools"]["mpip_python"]["path"], str(self.f.mpip_python))
        directory = self.f.build / "checks/third_party_packages/mpip"
        for label in ("mpip-make", "mpip-shared"):
            command = json.loads((directory / "logs" / (label + ".json")).read_text())["command"]
            self.assertTrue(any(arg.startswith("PYTHON=") and str(self.f.mpip_python) in arg for arg in command))
            self.assertIn("CXX=" + str(self.f.cc), command)
        self.f.build_install()

    def test_legacy_mpi_branch_delegates_to_same_python3_recipe(self):
        self.f.dependency_fixture("mpip")
        # Retained historical archive must not cause wildcard ambiguity.
        (self.f.source / "extra/mpiP-3.4.1.tar.gz").write_bytes(b"retained unused archive")
        legacy = (self.f.source / "installer/legacy-install.sh").read_text()
        start = legacy.rfind("\nif [[", 0, legacy.index("# Use the pinned mpiP recipe;")) + 1
        branch = legacy[start:legacy.index('if [[ "$PHI_BUILD"', start)]
        env = self.f.env.copy()
        env.update(TMPDIR=str(self.f.root), REMORA_BUILD_DIR=str(self.f.source), REMORA_DIR=str(self.f.prefix),
            CC=str(self.f.cc), F77=str(self.f.cc), MPICXX=str(self.f.cc), MPIP_PYTHON=str(self.f.mpip_python),
            haveMPICC="1", haveMPIFC="1", haveMV2="1", IMPI_stats="", haveIMPI="0",
            BUILD_LOG=str(self.f.root / "legacy-build.log"), INSTALL_LOG=str(self.f.root / "legacy-install.log"))
        (self.f.prefix / "lib").mkdir(parents=True)
        result = subprocess.run(["/bin/bash", "-c", branch], env=env, cwd=self.f.source, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.f.prefix / "lib/libmpiP.a").is_file())
        self.assertTrue((self.f.prefix / "lib/libmpiP.so").is_file())

    def test_mpip_fortran_symbol_probe_has_explicit_c_declarations(self):
        self.f.dependency_fixture("mpip")
        self.assertEqual(self.f.config({"mpip": "on"}, self.mpip_options()), 0, self.f.last_output)
        source = self.f.build / "checks/third_party_packages/mpip/source/mpip-fixture"
        probe = (source / "flink.c").read_text()
        self.assertEqual(probe.strip(), "extern void FF(void); int main(void){ FF(); return 0; }")
        self.f.build_install()

    def test_mpip_unknown_symbol_probe_fails_closed(self):
        source = self.f.root / "unknown-mpip"
        source.mkdir()
        configure = source / "configure"
        configure.write_text("#!/bin/sh\n# unexpected source revision\n")
        before = configure.read_bytes()
        recipe = self.f.source / "third_party_packages/packages/mpip.sh"
        command = 'source "$1"; SOURCE=$2; WORK=$2; error(){ printf "%s\n" "$*" >&2; }; mpi_fix_fortran_symbol_probe'
        result = subprocess.run(["/bin/bash", "-c", command, "test", str(recipe), str(source)],
            capture_output=True, text=True, env=self.f.env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unexpected mpiP", result.stderr)
        self.assertEqual(configure.read_bytes(), before)

    def test_mpip_python2_is_rejected_before_upstream_configure(self):
        self.f.dependency_fixture("mpip")
        self.f.mpip_python.write_text("#!/bin/sh\necho 'Python 2.7 synthetic'\n")
        self.assertEqual(self.f.config({"mpip": "on"}, self.mpip_options()), 2)
        self.assertIn("requires Python 3", self.f.plan()["modules"]["mpip"]["reason"])
        self.assertFalse((self.f.build / "checks/third_party_packages/mpip/logs/mpip-configure.json").exists())

    def test_mpip_python3_version_claim_is_not_enough(self):
        self.f.dependency_fixture("mpip")
        self.f.mpip_python.write_text("#!/bin/sh\nif [ \"$1\" = --version ]; then echo 'Python 3.12 synthetic'; else exit 9; fi\n")
        self.assertEqual(self.f.config({"mpip": "on"}, self.mpip_options()), 2)
        self.assertIn("mpip-python3 failed", self.f.plan()["modules"]["mpip"]["reason"])

    def test_mpip_cxx_link_failure_blocks_required_module(self):
        self.f.dependency_fixture("mpip")
        self.f.cc.write_text(self.f.cc.read_text().replace('args = sys.argv[1:]\n', 'args = sys.argv[1:]\nif "-DFAIL_CXX" in args: sys.exit(6)\n', 1))
        self.assertEqual(self.f.config({"mpip": "on"}, self.mpip_options() + ["--cxxflags=-DFAIL_CXX"]), 2)
        self.assertIn("mpi-cxx failed", self.f.plan()["modules"]["mpip"]["reason"])

    def test_mpip_archive_hash_mismatch_blocks_extraction(self):
        self.f.dependency_fixture("mpip")
        archive = self.f.source / fixtures.installer.ARCHIVES["mpip"]
        archive.write_bytes(archive.read_bytes() + b"tampered")
        self.assertEqual(self.f.config({"mpip": "on"}, self.mpip_options()), 2)
        self.assertIn("checksum differs", self.f.plan()["modules"]["mpip"]["reason"])
        self.assertFalse((self.f.build / "checks/third_party_packages/mpip/source").exists())

    def test_required_pic_rejection_blocks_mpip(self):
        self.f.dependency_fixture("mpip")
        self.f.cc.write_text(self.f.cc.read_text().replace('args = sys.argv[1:]\n', 'args = sys.argv[1:]\nif "-fPIC" in args or "-fpic" in args: sys.exit(2)\n', 1))
        extra = ["--mpicc", str(self.f.cc), "--mpicxx", str(self.f.cc), "--mpifc", str(self.f.cc), "--mpip-python", str(self.f.mpip_python)]
        self.assertEqual(self.f.config({"mpip": "on"}, extra), 2)
        self.assertIn("required PIC", self.f.plan()["modules"]["mpip"]["reason"])

    def test_pic_alternative_is_selected(self):
        self.f.dependency_fixture("mpip")
        self.f.cc.write_text(self.f.cc.read_text().replace('args = sys.argv[1:]\n', 'args = sys.argv[1:]\nif "-fPIC" in args: sys.exit(2)\n', 1))
        extra = ["--mpicc", str(self.f.cc), "--mpicxx", str(self.f.cc), "--mpifc", str(self.f.cc), "--mpip-python", str(self.f.mpip_python), "--cflags=-g0"]
        self.assertEqual(self.f.config({"mpip": "on"}, extra), 0, self.f.last_output)
        receipt = self.child_result("mpip")
        self.assertEqual(receipt["required_capabilities"]["cflags"]["PIC"], "-fpic")
        self.assertEqual(receipt["required_capabilities"]["fflags"]["PIC"], "-fpic")
        self.assertNotIn("-fPIC", receipt["effective_flags"]["cflags"])
        self.assertIn("-g0", receipt["effective_flags"]["cflags"])
        self.assertFalse(any(p["flag"]=="-g" and p["language"]=="cflags" for p in receipt["flag_probes"]))

    def test_standalone_interrupt_retains_failed_receipt(self):
        directory = self.f.root / "mpstat-fixture"
        directory.mkdir()
        marker = self.f.root / "configure-started"
        self.f.env["REMORA_TEST_CONFIGURE_STARTED"] = str(marker)
        (directory / "configure").write_text('#!/bin/sh\nprintf ready > "$REMORA_TEST_CONFIGURE_STARTED"\nsleep 30\n')
        import tarfile
        with tarfile.open(self.f.source / fixtures.installer.ARCHIVES["mpstat"], "w:gz") as tar:
            tar.add(directory, arcname="mpstat-fixture")
        cfg = self.f.root / "dependency-config.json"
        cfg.write_text(json.dumps({"tools": {"cc": str(self.f.cc), "make": str(self.f.make), "readelf": str(self.f.readelf)}}))
        proc = subprocess.Popen(["/bin/bash", str(self.f.source / "third_party_packages/install.sh"), "check", "--package", "mpstat", "--config", str(cfg), "--build-dir", str(self.standalone)], env=self.f.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        log = self.standalone / "logs/mpstat-configure.json"
        deadline = time.monotonic() + 12
        while not marker.exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.02)
        self.assertTrue(marker.exists(), "configure command did not start")
        self.assertTrue(log.exists())
        proc.terminate()
        self.assertEqual(proc.wait(timeout=10), 130)
        self.assertEqual(json.loads(log.read_text())["exit_code"], 130)
        self.assertEqual(json.loads((self.standalone / "result.json").read_text())["status"], "FAIL")

    def test_parent_interrupt_stops_dependency_and_preserves_both_logs(self):
        directory = self.f.root / "mpstat-fixture"
        directory.mkdir()
        marker = self.f.root / "configure-started"
        self.f.env["REMORA_TEST_CONFIGURE_STARTED"] = str(marker)
        (directory / "configure").write_text('#!/bin/sh\nprintf ready > "$REMORA_TEST_CONFIGURE_STARTED"\nsleep 30\n')
        import tarfile
        with tarfile.open(self.f.source / fixtures.installer.ARCHIVES["mpstat"], "w:gz") as tar:
            tar.add(directory, arcname="mpstat-fixture")
        choices = fixtures.installer.registry()
        args = ["/bin/bash", str(self.f.source / "install.sh"), "configure", "--build-dir", str(self.f.build),
                "--prefix", str(self.f.prefix), "--cc", str(self.f.cc), "--make", str(self.f.make), "--readelf", str(self.f.readelf)]
        for name in choices:
            args += ["--module", name + ("=on" if name == "mpstat" else "=off")]
        proc = subprocess.Popen(args, env=self.f.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        log = self.f.build / "checks/third_party_packages/mpstat/logs/mpstat-configure.json"
        deadline = time.monotonic() + 15
        while not marker.exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.02)
        self.assertTrue(marker.exists(), "configure command did not start")
        self.assertTrue(log.exists())
        proc.terminate()
        self.assertEqual(proc.wait(timeout=12), 130)
        self.assertEqual(json.loads(log.read_text())["exit_code"], 130)
        self.assertEqual(self.child_result()["status"], "FAIL")
        self.assertFalse(self.f.prefix.exists())

    def test_optional_dependency_failure_is_skipped_with_receipt(self):
        self.f.dependency_fixture("mpstat", configure_exit=7)
        self.assertEqual(self.f.config({"mpstat": "auto"}), 0, self.f.last_output)
        self.assertEqual(self.f.plan()["modules"]["mpstat"]["status"], "SKIPPED")
        self.assertEqual(self.f.plan()["third_party_packages"]["mpstat"]["status"], "FAIL")
        self.assertFalse((self.f.build / "checks/third_party_packages/mpstat/logs/mpstat-make.json").exists())


if __name__ == "__main__":
    unittest.main()
