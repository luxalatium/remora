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

    def test_required_pic_rejection_blocks_mpip(self):
        self.f.dependency_fixture("mpip")
        self.f.cc.write_text(self.f.cc.read_text().replace('args = sys.argv[1:]\n', 'args = sys.argv[1:]\nif "-fPIC" in args or "-fpic" in args: sys.exit(2)\n', 1))
        extra = ["--mpicc", str(self.f.cc), "--mpifc", str(self.f.cc), "--mpip-python", str(self.f.python2)]
        self.assertEqual(self.f.config({"mpip": "on"}, extra), 2)
        self.assertIn("required PIC", self.f.plan()["modules"]["mpip"]["reason"])

    def test_pic_alternative_is_selected(self):
        self.f.dependency_fixture("mpip")
        self.f.cc.write_text(self.f.cc.read_text().replace('args = sys.argv[1:]\n', 'args = sys.argv[1:]\nif "-fPIC" in args: sys.exit(2)\n', 1))
        extra = ["--mpicc", str(self.f.cc), "--mpifc", str(self.f.cc), "--mpip-python", str(self.f.python2), "--cflags=-g0"]
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
        (directory / "configure").write_text("#!/bin/sh\nsleep 30\n")
        import tarfile
        with tarfile.open(self.f.source / fixtures.installer.ARCHIVES["mpstat"], "w:gz") as tar:
            tar.add(directory, arcname="mpstat-fixture")
        cfg = self.f.root / "dependency-config.json"
        cfg.write_text(json.dumps({"tools": {"cc": str(self.f.cc), "make": str(self.f.make), "readelf": str(self.f.readelf)}}))
        proc = subprocess.Popen(["/bin/bash", str(self.f.source / "third_party_packages/install.sh"), "check", "--package", "mpstat", "--config", str(cfg), "--build-dir", str(self.standalone)], env=self.f.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: proc.kill() if proc.poll() is None else None)
        log = self.standalone / "logs/mpstat-configure.json"
        deadline = time.monotonic() + 12
        while not log.exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.02)
        self.assertTrue(log.exists())
        proc.terminate()
        self.assertEqual(proc.wait(timeout=10), 130)
        self.assertEqual(json.loads(log.read_text())["exit_code"], 130)
        self.assertEqual(json.loads((self.standalone / "result.json").read_text())["status"], "FAIL")

    def test_parent_interrupt_stops_dependency_and_preserves_both_logs(self):
        directory = self.f.root / "mpstat-fixture"
        directory.mkdir()
        (directory / "configure").write_text("#!/bin/sh\nsleep 30\n")
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
        while not log.exists() and proc.poll() is None and time.monotonic() < deadline: time.sleep(.02)
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
