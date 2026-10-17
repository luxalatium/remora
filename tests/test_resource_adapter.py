"""Bash/native ABI and literal environment tests, without legacy daemon launch."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='io-adapter-build-')
        cls.native = Path(cls.build.name) / 'data_collect_io'
        cc = shlex.split(os.environ.get('REMORA_IO_TEST_CC', os.environ.get('CC', 'cc')))
        r = subprocess.run(cc + ['-std=c99', '-Wall', '-Wextra', '-Werror', str(SOURCE / 'src/C_data_collectors_src/data_collect_io.c'), '-o', str(cls.native)], capture_output=True, text=True)
        if r.returncode: raise RuntimeError(r.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='io adapter ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        (self.bin / 'binary_data_collectors').mkdir(parents=True)
        shutil.copy2(self.native, self.bin / 'binary_data_collectors/data_collect_io')
        self.out = self.root / 'output'
        self.out.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('REMORA_RESOURCE_')}
        self.env['REMORA_BIN'] = str(self.bin)

    def shell(self, script, *args):
        return subprocess.run(['/bin/bash', '-c', 'source "$1"; shift; ' + script, 'bash', str(SOURCE / 'src/modules/io'), *map(str, args)], env=self.env, capture_output=True, text=True)

    def test_selective_json_output_and_no_clobber(self):
        self.env['REMORA_RESOURCE_MODULES'] = 'mounts,capacity'
        r = self.shell('init_module_io node "$1" "$1" && collect_data_io node "$1" "$1"', self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        path = self.out / 'io_node.jsonl'
        rows = [json.loads(x) for x in path.read_text().splitlines()]
        self.assertEqual([r['module'] for r in rows], ['mounts', 'capacity'])
        self.assertTrue(all(r['schema'] == 'remora-resource-sample/v1' for r in rows))
        original = path.read_bytes()
        self.assertNotEqual(self.shell('init_module_io node "$1" "$1"', self.out).returncode, 0)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_bash_and_direct_native_abi_both_append_same_output(self):
        self.env['REMORA_RESOURCE_MODULES'] = 'process'
        r = self.shell('init_module_io node "$1" "$1" && collect_data_io node "$1" "$1"', self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        r = subprocess.run([str(self.native), 'node', str(self.out), str(self.out)], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len((self.out / 'io_node.jsonl').read_text().splitlines()), 2)

    def test_unsafe_node_missing_output_and_symlink(self):
        self.assertNotEqual(self.shell('init_module_io ../escape "$1" "$1"', self.out).returncode, 0)
        self.assertNotEqual(self.shell('collect_data_io node "$1" "$1"', self.out).returncode, 0)
        victim = self.root / 'victim';victim.write_text('keep')
        (self.out / 'io_node.jsonl').symlink_to(victim)
        self.assertNotEqual(self.shell('collect_data_io node "$1" "$1"', self.out).returncode, 0)
        self.assertEqual(victim.read_text(), 'keep')

    def test_remote_environment_roundtrip_uses_literal_quoting(self):
        file = self.root / 'env.sh'
        marker = self.root / 'must-not-execute'
        value = "/with spaces\n/literal$(touch '%s')" % marker
        self.env.update(REMORA_RESOURCE_PATHS=value, REMORA_RESOURCE_DEVICES='8:1,8:2', REMORA_RESOURCE_PROCESS_HANDLES='other 1:2\nnode 4242:77')
        script = 'source "$1"; write_resource_environment "$2"; unset REMORA_RESOURCE_PATHS; source "$2"; "$3" -c \'import os,json;print(json.dumps(os.environ["REMORA_RESOURCE_PATHS"]))\''
        r = subprocess.run(['/bin/bash', '-c', script, 'bash', str(SOURCE / 'src/aux/extra'), str(file), sys.executable], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), value)
        self.assertFalse(marker.exists())
        self.assertIn('REMORA_RESOURCE_DEVICES', file.read_text())
        self.assertNotIn('PYTHON', file.read_text())

    def test_invalid_selection_and_absent_collector_fail_initialization(self):
        self.env['REMORA_RESOURCE_MODULES'] = 'unknown'
        self.assertNotEqual(self.shell('init_module_io node "$1" "$1"', self.out).returncode, 0)
        self.assertFalse((self.out / 'io_node.jsonl').exists())
        self.env.pop('REMORA_RESOURCE_MODULES')
        (self.bin / 'binary_data_collectors/data_collect_io').unlink()
        self.assertNotEqual(self.shell('init_module_io node "$1" "$1"', self.out).returncode, 0)


if __name__ == '__main__': unittest.main()
