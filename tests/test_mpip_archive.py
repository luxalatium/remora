"""Verify the vendored public mpiP release and its real Python3 generator."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MpiPArchiveTests(unittest.TestCase):
    def test_pinned_public_release_and_real_python3_generator(self):
        registry = json.loads((ROOT / "third_party_packages/packages.json").read_text())["packages"]["mpip"]
        provenance = json.loads((ROOT / "third_party_packages/mpiP-source.json").read_text())
        archive = ROOT / registry["archive"]
        self.assertEqual(registry["version"], "3.5")
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), registry["sha256"])
        self.assertEqual(provenance["sha256"], registry["sha256"])
        with tarfile.open(archive) as tar:
            members = {Path(m.name).name: m for m in tar.getmembers() if m.isfile()}
            generator = tar.extractfile(members["make-wrappers.py"]).read()
            prototypes = tar.extractfile(members["mpi.protos.txt.in"]).read()
            self.assertIn(b"AC_INIT([mpiP],[3.5])", tar.extractfile(members["configure.ac"]).read())
            self.assertIn(b"Redistribution and use", tar.extractfile(members["README.md"]).read())
        for arch in ("aarch64", "x86_64"):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory(prefix="mpiP generator ") as temp:
                work = Path(temp)
                (work / "make-wrappers.py").write_bytes(generator)
                (work / "mpi.protos.txt.in").write_bytes(prototypes)
                result = subprocess.run([sys.executable, "-B", "make-wrappers.py", "--arch=" + arch,
                    "--f77symbol=symbol_", "mpi.protos.txt.in"], cwd=work, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                for name in ("mpiP-wrappers.c", "symbols.h", "lookup.c", "mpiPi_def.h"):
                    self.assertGreater((work / name).stat().st_size, 0)
                self.assertIn("MPI_Send", (work / "mpiP-wrappers.c").read_text())
