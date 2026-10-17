"""Native IO fixtures and failure contract; Python is a developer test dependency only."""
import errno
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import tempfile
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1]
MOUNTS = """1 0 8:1 / / rw - ext4 /dev/sda1 rw
2 1 0:42 / /home rw - nfs server:/home rw,vers=3
3 1 0:42 / /scratch rw - nfs server:/scratch rw,vers=3
4 1 0:50 / /dev/shm rw - tmpfs tmpfs rw
5 1 8:2 /private /tmp rw - xfs /dev/sda2 rw,prjquota
"""


def nfs_rows(value=100, age=100):
    return ''.join('''device server:%s mounted on %s with fstype nfs statvers=1.1
 age: %d
 bytes: %d 200 0 0 90 190 2 3
 xprt: rdma 1 2 3 4
 xprt: tcp 5 6 7 8
 READ: 4 6 1 10 20 30 40 50 2
 WRITE: 3 3 0 100 10 20 30 40
''' % (mount, mount, age, value) for mount in ('/home', '/scratch'))


def proc_stat(ticks=77):
    return '4242 (a tricky ) process name) S ' + ' '.join(['0'] * 18) + ' %d\n' % ticks


class Resources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='io-build-')
        cls.binary = Path(cls.build.name) / 'data_collect_io'
        cc = shlex.split(os.environ.get('REMORA_IO_TEST_CC', os.environ.get('CC', 'cc')))
        # Test-only capacity and UID doubles; no hooks in the installed collector.
        wrapper = Path(cls.build.name) / 'fixture.c'
        wrapper.write_text('''#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1
#define _DARWIN_C_SOURCE 1
#include <sys/statvfs.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>
static int test_statvfs(const char *path,struct statvfs *v) {
 const char *mode=getenv("IO_TEST_CAPACITY");
 if(!mode) {
  int r=statvfs(path,v);if(r) return r;
  /* Synthetic capacity must not inherit the fixture host's inode support. */
  memset(v,0,sizeof *v);v->f_blocks=1000;v->f_frsize=4096;v->f_bfree=500;v->f_bavail=400;
  v->f_files=100;v->f_ffree=90;v->f_favail=80;return 0;
 }
 memset(v,0,sizeof *v);v->f_blocks=100;v->f_frsize=4096;
 if(!strcmp(mode,"overflow")) {v->f_blocks=(fsblkcnt_t)-1;v->f_frsize=(unsigned long)-1;v->f_bfree=1;v->f_bavail=1;}
 if(!strcmp(mode,"unsupported")) v->f_blocks=0;
 return 0;
}
static uid_t test_geteuid(void) {return geteuid()+(getenv("IO_TEST_OTHER_UID")?1:0);}
#define statvfs(path,v) test_statvfs(path,v)
#define geteuid() test_geteuid()
#define REMORA_IO_TESTING 1
#include "data_collect_io.c"
''')
        result = subprocess.run(cc + ['-std=c99', '-O1', '-Wall', '-Wextra', '-Werror', '-I', str(SOURCE / 'src/C_data_collectors_src'), str(wrapper), '-o', str(cls.binary)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.production = Path(cls.build.name) / 'production-io'
        result = subprocess.run(cc + ['-std=c99', '-O1', '-Wall', '-Wextra', '-Werror', str(SOURCE / 'src/C_data_collectors_src/data_collect_io.c'), '-o', str(cls.production)], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='io fixtures ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.fixture = self.root / 'linux'
        self.state = self.root / 'state'
        self.state.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('REMORA_RESOURCE_', 'IO_TEST_'))}
        self.put('/proc/sys/kernel/random/boot_id', 'fixture-boot\n')
        self.put('/proc/self/mountinfo', MOUNTS)
        self.put('/proc/self/mountstats', nfs_rows())
        self.put('/proc/diskstats', '8 1 sda1 1 2 3 4 5 6 7 8 9 10 11\n8 2 sda2 11 12 13 14 15 16 17 18 19 20 21\n')
        self.put('/proc/4242/stat', proc_stat())
        self.put('/proc/4242/io', 'rchar: 100\nwchar: 200\nsyscr: 2\nsyscw: 3\nread_bytes: 0\nwrite_bytes: 4096\ncancelled_write_bytes: 0\n')
        self.put('/sys/dev/block/8:2/diskseq', '1000\n')
        ns = self.fixture / 'proc/self/ns/mnt'
        ns.parent.mkdir(parents=True)
        ns.symlink_to('mnt:[42]')
        (self.fixture / 'tmp').mkdir()
        (self.fixture / 'scratch').mkdir()
        (self.fixture / 'home').mkdir()
        (self.fixture / 'dev/shm').mkdir(parents=True)

    def put(self, path, data):
        p = self.fixture / path.lstrip('/')
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data)
        return p

    def command(self, module='nfs', *args, state=True):
        return [str(self.binary), '--fixture-root', str(self.fixture), '--node', 'node', '--modules', module] + (['--state-dir', str(self.state)] if state else []) + list(args)

    def call(self, module='nfs', *args, expect=0, state=True, extra=None):
        run = subprocess.run(self.command(module, *args, state=state), env=dict(self.env, **(extra or {})), capture_output=True, text=True, timeout=6)
        self.assertEqual(run.returncode, expect, run.stderr + run.stdout[:2000])
        rows = [json.loads(x) for x in run.stdout.splitlines()]
        return rows[0] if len(rows) == 1 else rows

    def metric(self, row, name='normal_read_bytes', entity=0):
        return row['entities'][entity]['metrics'][name]

    def sample_pair(self, first=100, second=110, module='nfs', *args):
        self.put('/proc/self/mountstats', nfs_rows(first))
        a = self.call(module, *args, extra={'IO_TEST_MONO_NS': '1000000000'})
        self.put('/proc/self/mountstats', nfs_rows(second))
        b = self.call(module, *args, extra={'IO_TEST_MONO_NS': '3000000000', 'IO_TEST_WALL_NS': '1'})
        return a, b

    def test_selectable_descriptions_and_discovery(self):
        run = subprocess.run([str(self.production), '--list'], env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual({json.loads(x)['id'] for x in run.stdout.splitlines()}, {'mounts', 'capacity', 'nfs', 'process', 'block', 'tmp'})
        row = self.call('nfs', '--discover')
        self.assertEqual(row['operation'], 'discover')
        self.assertTrue(all(not e['metrics'] for e in row['entities']))
        self.assertFalse(list(self.state.glob('*.state')))
        self.assertEqual(row['qualification']['correctness'], 'NOT_QUALIFIED')

    def test_production_cannot_accept_fixture_root(self):
        r = subprocess.run([str(self.production), '--fixture-root', str(self.fixture)], capture_output=True)
        self.assertEqual(r.returncode, 64)

    def test_aliases_preserved_never_aggregated(self):
        row = self.call()
        self.assertEqual(len(row['entities']), 2)
        self.assertNotIn('total', row)
        for e in row['entities']:
            self.assertTrue(e['shared_domain_candidate'])
            self.assertTrue(e['identical_snapshot_counters'])
            self.assertEqual(e['aggregation'], 'forbidden')
            self.assertEqual(e['scope'], 'node_client_mount')
        self.assertFalse(row['context']['job_attribution'])
        self.assertEqual(row['raw'][1]['text'], nfs_rows())

    def test_device_match_alone_not_proof(self):
        self.put('/proc/self/mountstats', nfs_rows().replace('bytes: 100', 'bytes: 101', 1))
        e = self.call()['entities'][0]
        self.assertTrue(e['shared_domain_candidate'])
        self.assertFalse(e['identical_snapshot_counters'])

    def test_rpc_fields_transports_optional_missing(self):
        row = self.call()
        self.assertEqual(self.metric(row, 'READ.ops')['value'], 4)
        self.assertEqual(self.metric(row, 'READ.transmissions')['value'], 6)
        self.assertIsNone(self.metric(row, 'WRITE.errors')['value'])
        self.assertEqual(self.metric(row, 'WRITE.errors')['reason'], 'OPTIONAL_FIELD_ABSENT')
        self.assertIn('rdma', row['entities'][0]['transports_raw'])
        self.assertIn('tcp', row['entities'][0]['transports_raw'])

    def test_exact_large_integers_monotonic_delta(self):
        a, b = self.sample_pair(2**63 + 2**53 + 1, 2**63 + 2**53 + 11)
        self.assertEqual(self.metric(a)['value'], 2**63 + 2**53 + 1)
        self.assertEqual(self.metric(b)['interval']['delta'], 10)
        self.assertEqual(self.metric(b)['interval']['per_second'], 5)
        self.assertEqual(b['wall_time_ns'], 1)

    def test_counter_reset_and_rebaseline(self):
        _, b = self.sample_pair(1000, 2)
        self.assertEqual(self.metric(b)['interval']['status'], 'RESET')
        self.put('/proc/self/mountstats', nfs_rows(6))
        row = self.call(extra={'IO_TEST_MONO_NS': '5000000000'})
        self.assertEqual(self.metric(row)['interval']['delta'], 4)

    def test_missing_sample_breaks_interval(self):
        self.call()
        p = self.fixture / 'proc/self/mountstats'
        p.unlink()
        row = self.call(expect=2)
        self.assertEqual(row['status'], 'PARTIAL')
        self.assertEqual(row['reason'], 'MISSING')
        self.assertEqual(row['entities'], [])
        self.put('/proc/self/mountstats', nfs_rows(120))
        self.assertEqual(self.metric(self.call())['interval']['status'], 'GAP')
        self.assertEqual(self.metric(self.call())['interval']['status'], 'PASS')

    def test_identity_boundaries(self):
        changes = [lambda: self.put('/proc/sys/kernel/random/boot_id', 'new-boot'),
                   lambda: self.put('/proc/self/mountinfo', MOUNTS.replace('2 1 0:42', '22 1 0:42')),
                   lambda: self.put('/proc/self/mountstats', nfs_rows(age=1)),
                   lambda: ((self.fixture / 'proc/self/ns/mnt').unlink(), (self.fixture / 'proc/self/ns/mnt').symlink_to('mnt:[99]'))]
        for change in changes:
            self.call(); change()
            self.assertEqual(self.metric(self.call())['interval']['status'], 'IDENTITY_CHANGED')
        self.put('/proc/sys/kernel/random/boot_id', '')
        self.assertEqual(self.metric(self.call())['interval']['status'], 'IDENTITY_UNKNOWN')

    def test_nonpositive_monotonic_interval_and_corrupt_state(self):
        self.call(extra={'IO_TEST_MONO_NS': '1000000000'})
        self.assertEqual(self.metric(self.call(extra={'IO_TEST_MONO_NS': '1000000000'}))['interval']['status'], 'INVALID_INTERVAL')
        (self.state / '.io_node_nfs.state').write_bytes(b'corrupt')
        self.assertEqual(self.metric(self.call())['interval']['status'], 'GAP')

    def test_unknown_malformed_duplicate_and_partial_formats(self):
        for data in [nfs_rows().replace('1.1', '9.9'), nfs_rows().replace('190 2 3', '190 2'), nfs_rows().replace('bytes: 100', 'bytes: -1'), nfs_rows() + ' READ: 1 2 3\n', nfs_rows().replace(' READ:', ' READX:') + ' bytes: 1 2 3 4 5 6 7 8\n']:
            self.put('/proc/self/mountstats', data)
            row = self.call(expect=2)
            self.assertEqual(row['status'], 'PARTIAL')
        self.put('/proc/self/mountinfo', MOUNTS + MOUNTS)
        self.assertEqual(self.call('mounts', expect=2)['entities'], [])

    def test_permission_and_source_limit_not_zero(self):
        row = self.call(expect=2, extra={'IO_TEST_FAULT_PATH': '/proc/self/mountstats', 'IO_TEST_ERRNO': str(errno.EACCES)})
        self.assertEqual(row['reason'], 'PERMISSION')
        self.put('/proc/self/mountstats', 'x' * (2 * 1024 * 1024))
        self.assertEqual(self.call(expect=2)['reason'], 'LIMIT')

    def test_capacity_full_zero_and_unsupported_inodes(self):
        row = self.call('capacity', expect=2, extra={'IO_TEST_CAPACITY': 'full'})
        self.assertEqual(self.metric(row, 'available_bytes')['value'], 0)
        self.assertIsNone(self.metric(row, 'total_inodes')['value'])
        self.assertFalse(row['entities'][0]['quota_claim'])
        self.assertNotIn('interval', self.metric(row, 'available_bytes'))
        row = self.call('capacity', expect=2, extra={'IO_TEST_CAPACITY': 'unsupported'})
        self.assertIsNone(self.metric(row, 'available_bytes')['value'])
        row = self.call('capacity', expect=2, extra={'IO_TEST_CAPACITY': 'overflow'})
        self.assertIsNone(self.metric(row, 'total_bytes')['value'])

    def test_tmp_scope_bind_root_memory_backed_and_symlink(self):
        row = self.call('tmp')
        self.assertEqual(row['entities'][0]['identity']['root'], '/private')
        self.assertIn('prjquota', row['entities'][0]['configuration'])
        self.assertFalse(row['entities'][0]['job_private_claim'])
        self.assertEqual(row['entities'][1]['storage_kind'], 'memory_backed')
        (self.fixture / 'alias').symlink_to(self.fixture / 'tmp')
        row = self.call('capacity', '--path', '/alias')
        self.assertEqual(row['entities'][0]['canonical_path'], '/tmp')
        row = self.call('capacity', '--path', '/not-present', expect=2)
        self.assertIsNone(self.metric(row, 'available_bytes')['value'])

    def test_process_explicit_identity_cache_zero_and_missing(self):
        row = self.call('process', '--pid', '4242:77')
        self.assertEqual(self.metric(row, 'read_bytes')['value'], 0)
        self.assertEqual(self.metric(row, 'rchar')['value'], 100)
        self.assertEqual(row['entities'][0]['scope'], 'process')
        self.assertIn('read_bytes: 0', row['entities'][0]['process_io_raw'])
        self.assertEqual(self.call('process')['status'], 'SKIP')
        self.put('/proc/4242/stat', proc_stat(78))
        row = self.call('process', '--pid', '4242:77', expect=2)
        self.assertTrue(all(m['value'] is None for m in row['entities'][0]['metrics'].values()))
        self.assertEqual(row['entities'][0]['reason'], 'IDENTITY_CHANGED')
        self.assertEqual(self.call('process', '--pid', '9999:1', expect=2)['entities'][0]['reason'], 'MISSING')

    def test_process_uid_and_read_race(self):
        row = self.call('process', '--pid', '4242:77', expect=2, state=False, extra={'IO_TEST_OTHER_UID': '1'})
        self.assertEqual(row['entities'][0]['reason'], 'PERMISSION')
        row = self.call('process', '--pid', '4242:77', expect=2, extra={'IO_TEST_FAULT_PATH': '/proc/4242/io', 'IO_TEST_RACE_STAT': '/proc/4242/stat'})
        self.assertEqual(row['entities'][0]['reason'], 'IDENTITY_CHANGED')
        self.assertTrue(all(m['value'] is None for m in row['entities'][0]['metrics'].values()))

    def test_block_explicit_scope_sector_unit_gauge_and_generation(self):
        row = self.call('block', '--path', '/tmp', '--path', '/scratch')
        self.assertEqual(self.metric(row, 'write_sectors_512')['unit'], 'sectors_512_bytes')
        self.assertEqual(self.metric(row, 'in_flight')['kind'], 'gauge')
        self.assertNotIn('interval', self.metric(row, 'in_flight'))
        self.assertEqual(row['entities'][1]['status'], 'SKIP')
        self.assertEqual(row['entities'][0]['aggregation'], 'forbidden')
        self.put('/sys/dev/block/8:2/diskseq', '1001')
        row = self.call('block', '--path', '/tmp', '--path', '/scratch')
        self.assertEqual(self.metric(row, 'read_ios')['interval']['status'], 'IDENTITY_CHANGED')
        self.assertEqual(self.call('block', '--device', '8:2')['entities'][0]['identity']['device'], '8:2')
        self.put('/proc/diskstats', '8 2 sda2 1 2 3\n')
        self.assertEqual(self.call('block', expect=2)['entities'][0]['reason'], 'UNKNOWN_DISKSTATS_LAYOUT')

    def test_lock_output_and_state_symlinks_rejected(self):
        with (self.state / '.io_node.lock').open('w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.call(expect=74)
        victim = self.root / 'victim';victim.write_text('keep')
        (self.state / '.io_node_nfs.state').symlink_to(victim)
        self.assertEqual(self.call(expect=2)['reason'], 'STATE_WRITE_ERROR')
        self.assertEqual(victim.read_text(), 'keep')
        self.assertFalse(list(self.state.glob('*.next-*')))

    def test_timeout_and_signal_kill_only_owned_child(self):
        self.call()
        ready = self.root / 'ready'
        fault = {'IO_TEST_FAULT_PATH': '/proc/self/mountstats', 'IO_TEST_READY': str(ready)}
        start = time.monotonic()
        row = self.call('nfs', '--timeout-ms', '100', expect=2, extra=fault)
        self.assertLess(time.monotonic() - start, 2)
        self.assertEqual(row['status'], 'TIMEOUT')
        child = int(ready.read_text())
        with self.assertRaises(ProcessLookupError): os.kill(child, 0)
        self.assertFalse(list(self.state.glob('*.next-*')))
        self.assertEqual(self.metric(self.call())['interval']['status'], 'GAP')
        ready.unlink()
        unrelated = subprocess.Popen(['sleep', '20'])
        try:
            proc = subprocess.Popen(self.command(), env=dict(self.env, **fault), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(ready.exists())
            child = int(ready.read_text())
            proc.send_signal(signal.SIGTERM)
            stdout, _ = proc.communicate(timeout=3)
            self.assertEqual(proc.returncode, 143)
            self.assertEqual(stdout, b'')
            with self.assertRaises(ProcessLookupError): os.kill(child, 0)
            self.assertIsNone(unrelated.poll())
            self.assertFalse(list(self.state.glob('*.next-*')))
        finally:
            unrelated.terminate();unrelated.wait()

    def test_node_qualified_handles_and_literal_paths(self):
        row = self.call('process', extra={'REMORA_RESOURCE_PROCESS_HANDLES': 'other 9999:1\nnode 4242:77'})
        self.assertEqual([e['target'] for e in row['entities']], ['4242:77'])
        path = "/literal$(never_execute) spaces"
        (self.fixture / path.lstrip('/')).mkdir()
        row = self.call('capacity', extra={'REMORA_RESOURCE_PATHS': path})
        self.assertEqual(row['entities'][0]['target'], path)

    def test_missing_process_field_and_no_baseline_bridge(self):
        self.call('process', '--pid', '4242:77')
        self.put('/proc/4242/io', 'read_bytes: 0\n')
        row = self.call('process', '--pid', '4242:77', expect=2)
        self.assertIsNone(self.metric(row, 'wchar')['value'])
        self.assertEqual(self.metric(row, 'read_bytes')['interval']['status'], 'GAP')

    def test_truncated_source_and_block_missing_generation(self):
        self.put('/proc/self/mountstats', nfs_rows().rstrip('\n'))
        self.assertEqual(self.call(expect=2)['reason'], 'INCOMPLETE_SOURCE')
        (self.fixture / 'sys/dev/block/8:2/diskseq').unlink()
        row = self.call('block')
        self.assertEqual(self.metric(row, 'read_ios')['interval']['status'], 'IDENTITY_UNKNOWN')

    def test_context_read_deadline_does_not_retry_source_in_parent(self):
        ready = self.root / 'ready-context'
        row = self.call('nfs', '--timeout-ms', '100', expect=2, extra={'IO_TEST_FAULT_PATH': '/proc/sys/kernel/random/boot_id', 'IO_TEST_READY': str(ready)})
        self.assertEqual(row['status'], 'TIMEOUT')
        self.assertIsNone(row['context']['boot_id'])
        with self.assertRaises(ProcessLookupError): os.kill(int(ready.read_text()), 0)

    def test_mount_table_race_invalidates_counters(self):
        row = self.call(expect=2, extra={'IO_TEST_FAULT_PATH': '/proc/self/mountstats', 'IO_TEST_RACE_STAT': '/proc/self/mountinfo'})
        self.assertEqual(row['reason'], 'MOUNT_TABLE_CHANGED')
        self.assertTrue(all(m['value'] is None for e in row['entities'] for m in e['metrics'].values()))

    def test_fifo_source_is_rejected_without_blocking(self):
        p = self.fixture / 'proc/self/mountstats';p.unlink();os.mkfifo(p)
        start = time.monotonic()
        self.assertEqual(self.call(expect=2)['reason'], 'PARSE')
        self.assertLess(time.monotonic() - start, 1)

    def test_output_failure_invalidates_state_and_cleans_pending(self):
        self.call()
        proc = subprocess.Popen(self.command(), env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.stdout.close()
        self.assertEqual(proc.wait(timeout=3), 74)
        proc.stderr.close()
        self.assertFalse(list(self.state.glob('*.next-*')))
        self.assertEqual(self.metric(self.call())['interval']['status'], 'GAP')

    def test_multiple_paths_same_block_device_preserve_rows(self):
        (self.fixture / 'alias').symlink_to(self.fixture / 'tmp')
        row = self.call('block', '--path', '/tmp', '--path', '/alias')
        self.assertEqual(len(row['entities']), 2)
        self.assertTrue(all(e['shared_domain_candidate'] for e in row['entities']))
        self.assertNotIn('total', row)

    def test_virtual_filesystem_without_local_block_mapping_is_skip(self):
        self.put('/proc/self/mountinfo', MOUNTS + '6 1 0:99 / /parallel rw - beegfs beegfs_nodev rw\n')
        (self.fixture / 'parallel').mkdir()
        row = self.call('block', '--path', '/parallel')
        self.assertEqual(row['entities'][0]['status'], 'SKIP')
        self.assertEqual(row['entities'][0]['reason'], 'NO_LOCAL_BLOCK_MAPPING')
        self.assertEqual(row['entities'][0]['metrics'], {})

    def test_explicit_absent_device_remains_missing(self):
        row = self.call('block', '--device', '0:99', expect=2)
        self.assertEqual(row['entities'][0]['status'], 'MISSING')
        self.assertEqual(row['entities'][0]['reason'], 'DEVICE_ABSENT')

    def test_invalid_selection_not_silently_enabled(self):
        for args in [('nfs,unknown',), ('nfs,nfs',), ('nfs,',), ('nfs', '--pid', '4242'), ('block', '--device', '-1:2'), ('capacity', '--path', 'relative')]:
            self.call(*args, expect=64)


if __name__ == '__main__': unittest.main()
