"""Exercise the patched upstream generator, keeping the release archive intact."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MpiPCompatibilityTests(unittest.TestCase):
    def test_generated_fortran_buffers_and_collective_io(self):
        meta = json.loads((ROOT / 'third_party_packages/mpiP-source.json').read_text())
        patch = ROOT / meta['patches'][0]['path']
        self.assertEqual(hashlib.sha256(patch.read_bytes()).hexdigest(), meta['patches'][0]['sha256'])
        archive = ROOT / meta['archive']
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), meta['sha256'])
        with tempfile.TemporaryDirectory(prefix='mpiP patched generator ') as directory:
            with tarfile.open(archive) as tar:
                tar.extractall(directory, filter='data')
            work = Path(directory) / 'mpiP-3.5'
            result = subprocess.run(['patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i', str(patch)],
                                    cwd=work, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            (work/'mpi.protos.txt').write_text((work/'mpi.protos.txt.in').read_text() + '\n' + (work/'mpi.io.protos.txt').read_text())
            for arch, symbol in [('aarch64', 'symbol_'), ('x86_64', 'SYMBOL'), ('x86_64', 'symbol__')]:
                with self.subTest(arch=arch, symbol=symbol):
                    result = subprocess.run([sys.executable, '-B', 'make-wrappers.py', '--arch='+arch,
                                             '--xlate', '--f77symbol='+symbol, 'mpi.protos.txt'],
                                            cwd=work, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    wrappers = (work / 'mpiP-wrappers.c').read_text()
                    for name in ['Allreduce', 'Allgather', 'Alltoall', 'Reduce', 'Scan']:
                        start = wrappers.index('extern void F77_MPI_'+name.upper()+'(')
                        end = wrappers.find('\n\nextern ', start+1)
                        section = wrappers[start:end if end != -1 else None]
                        self.assertIn('mpiPi_fortran_buffer(sendbuf)', section)
                        self.assertIn('mpiPi_fortran_buffer(recvbuf)', section)
                    for name in ['read_all', 'write_all', 'read_at_all', 'write_at_all']:
                        start = wrappers.index('mpiPif_MPI_File_'+name+'(')
                        end = wrappers.index('extern void F77_MPI_FILE_'+name.upper()+'(', start)
                        self.assertIn('ioSize = (double)tsize * (double)*count;', wrappers[start:end])
                    # C callers already use C sentinels; their arguments stay unchanged.
                    section = wrappers[wrappers.index('extern int MPI_Allreduce ('):]
                    section = section[:section.index('\n\nextern ') ]
                    self.assertNotIn('mpiPi_fortran_buffer', section)
                    self.assertIn('F77_MPIPI_INIT_FORTRAN_SENTINELS', (work/'symbols.h').read_text())


if __name__ == '__main__':
    unittest.main()
