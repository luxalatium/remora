"""Stage-two runtime contracts using bounded synthetic commands, not HPC evidence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import test_installer as fixtures

SOURCE = Path(__file__).resolve().parents[1]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.InstallerTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.prefix = self.root / "prefix"
        (self.prefix / "bin/modules").mkdir(parents=True)
        (self.prefix / "bin/binary_data_collectors").mkdir()
        self.output = self.root / "runtime-output"
        # Legacy module functions cannot safely consume whitespace in output paths.
        self.out_parent = tempfile.TemporaryDirectory(prefix="remora-runtime-")
        self.addCleanup(self.out_parent.cleanup)
        self.output = Path(self.out_parent.name) / "results"
        self.env = self.fixture.env
        readlink = self.fixture.tools / "readlink"
        readlink.write_text("#!" + __import__("sys").executable + "\nfrom pathlib import Path\nimport sys\nprint(Path(sys.argv[-1]).resolve())\n")
        readlink.chmod(0o755)
        self.bin = self.prefix / "bin"
        (self.bin / "mpstat").write_text('#!/bin/sh\necho "Average: all 1.0 0.0 1.0 0.0 0.0 0.0 0.0 0.0 0.0 98.0"\n')
        (self.bin / "mpstat").chmod(0o755)

    def run_check(self, module="mpstat", implementation="collector", extra=()):
        return subprocess.run(
            ["/bin/bash", str(SOURCE / "runtime/check.sh"), "--prefix", str(self.prefix),
             "--output", str(self.output), "--module", module, "--implementation", implementation,
             "--samples", "2", "--timeout", "10", *extra],
            env=self.env, capture_output=True, text=True,
        )

    def case(self, module="mpstat", implementation="collector"):
        return json.loads((self.output / module / implementation / "result.json").read_text())

    def validate(self, content, width=3, cpu=0):
        path = self.root / "raw.txt"
        path.write_text(content)
        return subprocess.run(
            ["/bin/bash", "-c", 'source "$1"; validate_raw "$2" "$3" 2 "$4"',
             "bash", str(SOURCE / "runtime/common.sh"), str(path), str(width), str(cpu)],
            env=self.env, capture_output=True,
        ).returncode

    def test_numeric_schema_and_monotonic_timestamps(self):
        self.assertEqual(self.validate("TIME A B\n100 1 2\n101 3 4\n"), 0)
        for text in ["TIME A B\n", "100 1 2\n100 3 4\n", "100 1 NaN\n101 3 4\n",
                     "100 1 2\n101 3\n", "100 1 2\n101 -1 4\n"]:
            self.assertNotEqual(self.validate(text), 0, text)

    def test_cpu_labels_are_not_parsed_as_numbers(self):
        block = "%time {time}\n%usr 1 2\n%sys 3 4\n%idle 96 94\n"
        self.assertEqual(self.validate(block.format(time=100) + block.format(time=101), cpu=1), 0)
        self.assertNotEqual(self.validate(block.format(time=100) + "%time 101\n%usr 1 2\n", cpu=1), 0)
        self.assertNotEqual(self.validate((block.format(time=100) + block.format(time=101)).replace("96", "196"), cpu=1), 0)

    def test_mpstat_positive_and_nonqualification_scope(self):
        run = self.run_check()
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertEqual(self.case()["status"], "PASS")
        self.assertEqual(self.case()["correctness"], "OPEN")
        self.assertEqual(self.case()["qualification_scope"], "availability_smoke")
        self.assertTrue((self.output / "mpstat/collector/command.json").is_file())

    def test_zero_exit_without_data_is_failure(self):
        (self.bin / "mpstat").write_text("#!/bin/sh\nexit 0\n")
        run = self.run_check()
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertEqual(self.case()["status"], "FAIL")

    def test_missing_artifact_is_blocked(self):
        (self.bin / "mpstat").unlink()
        run = self.run_check()
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertEqual(self.case()["status"], "BLOCKED")

    def test_global_state_binary_is_never_executed(self):
        binary = self.bin / "binary_data_collectors/data_collect_cpu"
        marker = self.root / "unsafe-executed"
        binary.write_text('#!/bin/sh\n# /dev/shm/remora_cpu.tmp\ntouch "' + str(marker) + '"\n')
        binary.chmod(0o755)
        run = self.run_check("cpu")
        self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
        self.assertIn("global /dev/shm", self.case("cpu")["reason"])
        self.assertFalse(marker.exists())

    def test_timeout_is_not_a_skip(self):
        (self.bin / "mpstat").write_text("#!/bin/sh\nsleep 30\n")
        run = self.run_check()
        self.assertEqual(run.returncode, 1, run.stdout + run.stderr)
        self.assertEqual(self.case()["status"], "FAIL")
        self.assertEqual(self.case()["exit_code"], 124)

    def test_output_cannot_overwrite_existing_evidence(self):
        self.output.mkdir()
        marker = self.output / "keep"
        marker.write_text("original")
        self.assertEqual(self.run_check().returncode, 2)
        self.assertEqual(marker.read_text(), "original")

    def test_all_modules_have_independent_entrypoints(self):
        names = json.loads((SOURCE / "installer/modules.json").read_text())["modules"]
        self.assertEqual(set(names), {p.stem for p in (SOURCE / "runtime/modules").glob("*.sh")})

    def test_state_files_use_supplied_tmpdir(self):
        for name in ["cpu", "eth", "ib", "memory", "opa", "power"]:
            source = (SOURCE / "src/C_data_collectors_src" / f"data_collect_{name}.c").read_text()
            self.assertNotIn(f'fopen("/dev/shm/remora_{name}.tmp"', source)
            self.assertEqual(source.count(f'remora_open_state(argv[3], "remora_{name}.tmp"'), 2)
        self.assertNotIn("du /dev/shm", (SOURCE / "src/modules/memory").read_text())
