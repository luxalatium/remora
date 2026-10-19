"""Local synthetic installer regressions; no MPI or HPC qualification."""
import hashlib
import json
import tarfile
from pathlib import Path
import subprocess
import unittest

import test_installer as fixtures


class ToolFailureStateTests(fixtures.InstallerTests):
    def test_mpip_build_has_initialized_failure_state_and_no_warnings(self):
        self.dependency_fixture("mpip")
        # Keep real patch inputs; only configure/make/compiler are synthetic.
        directory = self.root / "mpip-fixture"
        with tarfile.open(fixtures.SOURCE / "extra/mpiP-3.5.tar.gz") as upstream:
            for name in ("make-wrappers.py", "Makefile.in", "mpiPi.c", "mpiPi.h"):
                member, = [m for m in upstream.getmembers() if m.name.endswith("/" + name) and len(Path(m.name).parts) == 2]
                (directory / name).write_bytes(upstream.extractfile(member).read())
        archive = self.source / "extra/mpiP-3.5.tar.gz"
        with tarfile.open(archive, "w:gz") as target:
            target.add(directory, arcname="mpip-fixture")
        registry = self.source / "third_party_packages/packages.json"
        data = json.loads(registry.read_text())
        data["packages"]["mpip"]["sha256"] = hashlib.sha256(archive.read_bytes()).hexdigest()
        registry.write_text(json.dumps(data))
        # This fixture emits synthetic ELF, not a real Fortran ABI object.
        self.cc.write_text(self.cc.read_text().replace(
            "args = sys.argv[1:]\n",
            "args = sys.argv[1:]\nif pathlib.Path.cwd().name == 'f08': sys.exit(1)\n", 1))
        extra = ["--mpicc", str(self.cc), "--mpicxx", str(self.cc),
                 "--mpifc", str(self.cc), "--mpip-python", str(self.mpip_python)]
        self.assertEqual(self.config({"mpip": "on"}, extra), 0)
        configure_failures = self.build / "checks/tool-failures.json"
        original = configure_failures.read_bytes()
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 0)
        self.assertEqual(self.last_stderr, "")
        self.assertEqual(json.loads((self.build / "build/tool-failures.json").read_text()), {})
        self.assertEqual(configure_failures.read_bytes(), original)
        self.assertEqual(self.invoke(["install", "--plan", str(self.build / "plan.json")]), 0)
        self.assertEqual(self.last_stderr, "")
        self.assertTrue((self.prefix / "lib/libmpiP.a").is_file())
        self.assertTrue((self.prefix / "lib/libmpiP.so").is_file())

    def test_collector_build_has_no_failure_state_warnings(self):
        self.assertEqual(self.config(), 0)
        self.assertEqual(self.invoke(["build", "--plan", str(self.build / "plan.json")]), 0)
        self.assertEqual(self.last_stderr, "")
        self.assertEqual(json.loads((self.build / "build/tool-failures.json").read_text()), {})

    def need_with_failure_state(self, contents):
        tools = self.root / "cached-tools.json"
        tools.write_text(json.dumps({"bash": {"path": "/unused/cached/bash"}}))
        failures = self.root / "failures.json"
        if contents is not None:
            failures.write_text(contents)
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$1/build_support/common.sh"; '
             'TOOLS=$2; FAILURES=$3; need bash; status=$?; '
             'printf "%s\\n" "$LAST_ERROR"; exit "$status"',
             "test-need", str(self.source), str(tools), str(failures)],
            capture_output=True, text=True,
        )

    def test_recorded_failure_precedes_cached_tool(self):
        result = self.need_with_failure_state('{"bash":"previous probe failed"}')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("previous probe failed", result.stdout)

    def test_unreadable_failure_state_does_not_accept_cached_tool(self):
        for contents in (None, "{invalid JSON"):
            with self.subTest(contents=contents):
                result = self.need_with_failure_state(contents)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotEqual(result.stderr, "")


def load_tests(loader, tests, pattern):
    # Reuse existing synthetic fixtures without rerunning inherited test methods.
    return unittest.TestSuite(ToolFailureStateTests(name) for name in (
        "test_mpip_build_has_initialized_failure_state_and_no_warnings",
        "test_collector_build_has_no_failure_state_warnings",
        "test_recorded_failure_precedes_cached_tool",
        "test_unreadable_failure_state_does_not_accept_cached_tool",
    ))


if __name__ == "__main__":
    unittest.main()
