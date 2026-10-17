"""Deterministic network contract tests. No scheduler or network is used."""
import fcntl
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT = Path(os.environ.get('REMORA_TEST_SOURCE_ROOT', Path(__file__).resolve().parents[1]))
ETH = ('tx_packets', 'rx_packets', 'tx_bytes', 'rx_bytes', 'tx_errors', 'tx_dropped', 'rx_frame_errors', 'rx_crc_errors')
IB = ('port_xmit_packets', 'port_rcv_packets', 'port_xmit_data', 'port_rcv_data')
HW = ('TxPkt', 'RxPkt', 'TxWords', 'RxWords')


class NetworkContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='network-build-')
        cls.prefix = Path(cls.build.name)
        (cls.prefix / 'bin/binary_data_collectors').mkdir(parents=True)
        (cls.prefix / 'bin/modules').mkdir()
        cc = shlex.split(os.environ.get('REMORA_NETWORK_TEST_CC', os.environ.get('CC', 'cc')))
        for module in ('eth', 'ib', 'opa'):
            target = cls.prefix / f'bin/binary_data_collectors/data_collect_{module}'
            subprocess.run(cc + ['-std=c99', '-O1', '-Wall', '-Wextra', '-Werror', str(ROOT / f'src/C_data_collectors_src/data_collect_{module}.c'), '-o', str(target)], check=True, capture_output=True)
            shutil.copy2(ROOT / f'src/modules/{module}', cls.prefix / 'bin/modules')
        # Inject real read errno or pause inside a read; never changes production code.
        fault = cls.prefix / 'fault.c'
        fault.write_text('''#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1
#define _DARWIN_C_SOURCE 1
#include <unistd.h>
#include <sys/stat.h>
#include <stdlib.h>
#include <errno.h>
#include <fcntl.h>
static ssize_t test_read(int fd, void *buf, size_t n) {
 struct stat st; const char *inode=getenv("TEST_INODE");
 if(inode && !fstat(fd,&st) && (unsigned long long)st.st_ino==strtoull(inode,0,10)) {
  const char *err=getenv("TEST_ERRNO");
  if(err) {errno=atoi(err);return -1;}
  const char *ready=getenv("TEST_READY");
  if(ready) {int r=open(ready,O_CREAT|O_WRONLY,0600);if(r>=0) close(r); for(;;) pause();}
 }
 return read(fd,buf,n);
}
#define read test_read
#include "remora_network.h"
int main(int argc,char **argv) {return remora_network_main("opa",argc,argv);}
''')
        cls.fault = cls.prefix / 'fault-opa'
        subprocess.run(cc + ['-std=c99', '-O1', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'src/C_data_collectors_src'), str(fault), '-o', str(cls.fault)], check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='network-case-')
        self.base = Path(self.tmp.name)
        self.sys = self.base / 'sys'
        self.raw = self.base / 'raw'
        self.raw.mkdir()
        self.env = dict(os.environ, REMORA_NETWORK_SYSFS_ROOT=str(self.sys), REMORA_BIN=str(self.prefix / 'bin'), REMORA_SYMMETRIC='0', REMORA_MODE='FULL')
        for name in ('REMORA_ETH_INTERFACES', 'REMORA_IB_PORTS', 'REMORA_OPA_PORTS'):
            self.env.pop(name, None)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, path, value):
        p = self.sys / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(value) + '\n')
        return p

    def ethernet(self, name='eth0', kind=1, up=True, value=100):
        self.put(f'class/net/{name}/ifindex', 10)
        self.put(f'class/net/{name}/type', kind)
        self.put(f'class/net/{name}/flags', '0x1' if up else '0x0')
        for field in ETH:
            self.put(f'class/net/{name}/statistics/{field}', value)

    def fabric(self, name='mlx5_0', port=1, active=True, layer='InfiniBand', value=100, hw=False, extended=False):
        self.put(f'class/infiniband/{name}/node_guid', '0011:2233:4455:6677')
        base = f'class/infiniband/{name}/ports/{port}'
        self.put(base + '/state', '4: ACTIVE' if active else '1: DOWN')
        self.put(base + '/link_layer', layer)
        directory = 'hw_counters' if hw else 'counters_ext' if extended else 'counters'
        for field in HW if hw else IB:
            self.put(f'{base}/{directory}/{field}' + ('_64' if extended else ''), value)
        return base

    def bump(self, amount=3):
        for p in self.sys.rglob('*'):
            if p.is_file() and (p.parent.name in ('statistics', 'counters', 'counters_ext', 'hw_counters')):
                p.write_text(str(int(p.read_text()) + amount) + '\n')

    def call(self, module='ib', shell=False, prime=False, expect=0, extra=None, raw=None, fault=False):
        directory = raw or self.raw
        env = dict(self.env, **(extra or {}))
        if shell:
            command = ['bash', '-c', 'source "$1"; "$2" node "$3" "$4"', 'network-test', str(self.prefix / f'bin/modules/{module}'), ('init_module_' if prime else 'collect_data_') + module, str(self.base), str(directory)]
        else:
            command = [str(self.fault if fault else self.prefix / f'bin/binary_data_collectors/data_collect_{module}'), 'node', str(self.base), str(directory)] + (['--prime'] if prime else [])
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, expect, result.stderr)
        return result

    def rows(self, module='ib', raw=None):
        name = 'network_eth_traffic-node.txt' if module == 'eth' else f'{module}_packets-node.txt'
        p = (raw or self.raw) / name
        return [list(map(int, line.split()[1:])) for line in p.read_text().splitlines()[1:]] if p.exists() else []

    def raw_rows(self, module='ib', raw=None):
        return [line.split('\t') for line in ((raw or self.raw) / f'network-{module}-node.raw.tsv').read_text().splitlines() if not line.startswith('#')]

    def test_ethernet_default_aggregates_up_ethernet_only(self):
        for args in [('eth0', 1, True), ('eth1', 1, True), ('lo', 772, True), ('ib0', 32, True), ('eth2', 1, False)]:
            self.ethernet(*args)
        self.call('eth'); self.bump(); self.call('eth')
        self.assertEqual(self.rows('eth'), [[6] * 8])
        self.assertEqual({r[1] for r in self.raw_rows('eth')}, {'eth0', 'eth1'})

    def test_explicit_netdev_allows_loopback_and_ipoib(self):
        self.ethernet('lo', 772); self.ethernet('ib0', 32); self.ethernet()
        self.env['REMORA_ETH_INTERFACES'] = 'lo,ib0'
        self.call('eth'); self.bump(7); self.call('eth')
        self.assertEqual(self.rows('eth'), [[14] * 8])

    def test_fabric_multiple_devices_ports_and_link_layer(self):
        self.fabric(); self.fabric(port=2); self.fabric('mlx5_1'); self.fabric('mlx5_2', layer='Ethernet'); self.fabric('mlx5_3', active=False)
        self.call(); self.bump(); self.call()
        self.assertEqual(self.rows(), [[9, 9, 36, 36]])

    def test_explicit_fabric_subset(self):
        self.fabric(); self.fabric('mlx5_1', port=2)
        self.env['REMORA_IB_PORTS'] = 'mlx5_1/2'
        self.call(); self.bump(); self.call()
        self.assertEqual(self.rows(), [[3, 3, 12, 12]])

    def test_opa_rejects_mellanox(self):
        self.fabric(); self.call('opa', expect=69)
        self.assertEqual(self.rows('opa'), [])
        self.env['REMORA_OPA_PORTS'] = 'mlx5_0/1'
        self.call('opa', expect=69)

    def test_renamed_hfi_identified_by_driver(self):
        self.fabric('fabric0')
        driver=self.sys/'class/infiniband/fabric0/device/driver';driver.parent.mkdir()
        driver.symlink_to('/sys/bus/pci/drivers/hfi1')
        self.call('opa');self.bump();self.call('opa')
        self.assertEqual(self.rows('opa'), [[3,3,24,24]])

    def test_hfi_classic_flits_are_eight_bytes(self):
        self.fabric('hfi1_0'); self.call('opa'); self.bump(5); self.call('opa')
        self.assertEqual(self.rows('opa'), [[5, 5, 40, 40]])
        self.assertEqual([r[6] for r in self.raw_rows('opa') if r[5] == '0'][-4:], ['1','1','8','8'])

    def test_cn5000_active_port2_hw_fallback(self):
        self.fabric('hfi1_0', active=False); self.fabric('hfi1_0', port=2, hw=True)
        self.call('opa'); self.bump(5); self.call('opa')
        self.assertEqual(self.rows('opa'), [[5, 5, 20, 20]])
        self.assertEqual({r[1] for r in self.raw_rows('opa')}, {'hfi1_0/2'})
        self.assertTrue(any(r[4] == 'NA' and r[5] == '2' for r in self.raw_rows('opa')))

    def test_per_metric_fallback_preserves_available_classic_fields(self):
        base = self.fabric('hfi1_0'); self.fabric('hfi1_0', hw=True)
        (self.sys / base / 'counters/port_rcv_data').unlink()
        self.call('opa'); self.bump(); self.call('opa')
        self.assertEqual(self.rows('opa'), [[3, 3, 24, 12]])

    def test_einval_read_falls_back(self):
        import errno
        base = self.fabric('hfi1_0'); self.fabric('hfi1_0', hw=True)
        extra = {'TEST_INODE': str((self.sys / base / 'counters/port_xmit_data').stat().st_ino), 'TEST_ERRNO': str(errno.EINVAL)}
        self.call('opa', fault=True, extra=extra); self.bump(); self.call('opa', fault=True, extra=extra)
        self.assertEqual(self.rows('opa'), [[3,3,12,24]])
        self.assertTrue(any(r[5] == str(errno.EINVAL) for r in self.raw_rows('opa')))

    def test_permission_failure_not_masked_by_fallback(self):
        import errno
        base = self.fabric('hfi1_0'); self.fabric('hfi1_0', hw=True)
        extra = {'TEST_INODE': str((self.sys / base / 'counters/port_xmit_data').stat().st_ino), 'TEST_ERRNO': str(errno.EACCES)}
        self.call('opa', fault=True, extra=extra, expect=74)
        self.assertFalse(any(r[3].endswith('/TxWords') for r in self.raw_rows('opa')))
        self.assertEqual(self.rows('opa'), [])

    def test_malformed_counter_not_masked(self):
        base = self.fabric('hfi1_0'); self.fabric('hfi1_0', hw=True)
        self.put(base + '/counters/port_xmit_data', '12garbage')
        self.call('opa', expect=74)
        self.assertEqual(self.rows('opa'), [])

    def test_extended_counter_preferred(self):
        self.fabric(); self.fabric(extended=True, value=10000)
        self.call(); self.bump(); self.call()
        self.assertEqual(self.rows(), [[3, 3, 12, 12]])
        self.assertTrue(all('/counters_ext/' in r[3] for r in self.raw_rows()))

    def test_unsigned_precision_above_double_and_signed_range(self):
        self.fabric(value=2**63 + 9007199254740993)
        self.call(); self.bump(19); self.call()
        self.assertEqual(self.rows(), [[19, 19, 76, 76]])

    def test_conversion_overflow_is_gap(self):
        self.fabric(value=0); self.call(); self.bump(2**62); self.call()
        self.assertEqual(self.rows(), [])
        self.assertIn('overflow', (self.raw / 'network-ib-node.events.tsv').read_text())

    def test_aggregation_overflow_is_gap(self):
        self.fabric(value=0); self.fabric('mlx5_1', value=0); self.call(); self.bump(2**61); self.call()
        self.assertEqual(self.rows(), [])

    def test_counter_reset_rebaselines_without_zero_or_negative(self):
        self.fabric(value=1000); self.call(); self.fabric(value=2); self.call(); self.bump(4); self.call()
        self.assertEqual(self.rows(), [[4,4,16,16]])
        self.assertIn('reset_or_saturated', (self.raw / 'network-ib-node.events.tsv').read_text())

    def test_saturated_counter_is_gap(self):
        self.fabric(value=2**64-1); self.call(); self.call()
        self.assertEqual(self.rows(), [])

    def test_selection_change_rebaselines(self):
        self.fabric(); self.call(); self.fabric('mlx5_1', value=100000); self.call(); self.bump(); self.call()
        self.assertEqual(self.rows(), [[6,6,24,24]])

    def test_replaced_interface_identity_rebaselines(self):
        self.ethernet(); self.call('eth'); self.ethernet(value=10000)
        self.put('class/net/eth0/ifindex', 11); self.call('eth'); self.bump(); self.call('eth')
        self.assertEqual(self.rows('eth'), [[3]*8])

    def test_replaced_hca_identity_rebaselines(self):
        self.fabric(); self.call(); self.fabric(value=10000)
        self.put('class/infiniband/mlx5_0/node_guid', '0000:0000:0000:0001'); self.call(); self.bump(); self.call()
        self.assertEqual(self.rows(), [[3,3,12,12]])

    def test_backend_change_rebaselines(self):
        self.fabric('hfi1_0'); self.call('opa'); self.fabric('hfi1_0', extended=True, value=100000); self.call('opa'); self.bump(); self.call('opa')
        self.assertEqual(self.rows('opa'), [[3,3,24,24]])

    def test_read_failure_recovery_drops_incomplete_interval(self):
        base = self.fabric(); self.call(); self.put(base + '/counters/port_xmit_data', '-1'); self.call(expect=74)
        self.fabric(value=999); self.call(); self.bump(); self.call()
        self.assertEqual(self.rows(), [[3,3,12,12]])

    def test_explicit_missing_or_down_never_substituted(self):
        self.fabric(); self.fabric('mlx5_1', active=False)
        for value, rc in [('missing/1',74), ('mlx5_1/1',69), ('mlx5_0/1,missing/1',74)]:
            with self.subTest(value=value):
                self.env['REMORA_IB_PORTS'] = value; self.call(expect=rc)
                self.assertEqual(self.rows(), [])

    def test_invalid_selectors_rejected(self):
        self.fabric()
        for value in ('', '../mlx5_0/1', 'mlx5_0/../1', 'mlx5_0/1,', 'mlx5_0/1,,mlx5_1/1', 'mlx5_0/1,mlx5_0/1', '*', 'mlx5_0/1;touch bad', ' mlx5_0/1'):
            with self.subTest(value=value):
                self.env['REMORA_IB_PORTS'] = value; self.call(expect=65)

    def test_shell_and_native_entrypoints_match_for_all_modules(self):
        self.ethernet(); self.fabric('hfi1_0', port=2, hw=True)
        for module in ('eth','ib','opa'):
            with self.subTest(module=module):
                a=self.base/(module+'-native'); b=self.base/(module+'-shell');a.mkdir();b.mkdir()
                self.call(module, raw=a);self.call(module,shell=True,prime=True,raw=b)
                self.bump(11)
                self.call(module,raw=a);self.call(module,shell=True,raw=b)
                self.assertEqual(self.rows(module,a), self.rows(module,b))
                self.assertEqual([r[1:] for r in self.raw_rows(module,a)], [r[1:] for r in self.raw_rows(module,b)])

    def test_init_idempotent_preserves_existing_data(self):
        self.fabric();self.call(prime=True);self.bump();self.call();before=self.rows()
        self.call(prime=True);self.assertEqual(before,self.rows());self.bump();self.call()
        self.assertEqual(len(self.rows()),2)

    def test_corrupt_state_fails_closed(self):
        self.fabric();self.call();p=self.raw/'network-ib-node.state';p.write_bytes(p.read_bytes()[:-7]);self.call(expect=65)
        self.assertEqual(self.rows(), [])

    def test_state_byte_corruption_fails_closed(self):
        self.fabric();self.call();p=self.raw/'network-ib-node.state';data=bytearray(p.read_bytes());data[-1]^=1;p.write_bytes(data);self.call(expect=65)

    def test_old_data_without_versioned_state_is_preserved(self):
        p=self.raw/'ib_packets-node.txt';p.write_text('old raw evidence\n');self.fabric();self.call(expect=65)
        self.assertEqual(p.read_text(),'old raw evidence\n')

    def test_pending_transaction_is_not_replayed(self):
        self.fabric();(self.raw/'network-ib-node.pending').write_text('');self.call(expect=74)
        self.assertEqual(self.rows(), [])

    def test_output_failure_cannot_commit_new_baseline(self):
        self.fabric();self.call();p=self.raw/'ib_packets-node.txt';p.unlink();p.mkdir();self.bump();self.call(expect=74)
        self.assertTrue((self.raw/'network-ib-node.pending').exists());self.call(expect=74)

    def test_concurrent_writer_lock(self):
        self.fabric();p=self.raw/'network-ib-node.lock'
        with p.open('w') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);self.call(expect=75)
        self.call()

    def test_symlink_output_does_not_touch_target(self):
        self.fabric();target=self.base/'unrelated';target.write_text('KEEP');(self.raw/'network-ib-node.raw.tsv').symlink_to(target)
        self.call(expect=74);self.assertEqual(target.read_text(),'KEEP')

    def test_fifo_counter_cannot_hang(self):
        base=self.fabric();p=self.sys/base/'counters/port_xmit_data';p.unlink();os.mkfifo(p)
        self.call(expect=74)

    def test_run_and_node_state_isolation(self):
        self.fabric();self.call();other=self.base/'other';other.mkdir();self.bump(10);self.call(raw=other);self.bump(3);self.call();self.call(raw=other)
        self.assertEqual(self.rows(),[[13,13,52,52]]);self.assertEqual(self.rows(raw=other),[[3,3,12,12]])

    def test_sigterm_releases_only_owned_handle_and_fails_closed(self):
        base=self.fabric('hfi1_0');ready=self.base/'ready';sentinel=self.base/'unrelated';sentinel.write_text('KEEP')
        env=dict(self.env,TEST_INODE=str((self.sys/base/'counters/port_xmit_data').stat().st_ino),TEST_READY=str(ready))
        with subprocess.Popen([str(self.fault),'node',str(self.base),str(self.raw)],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE) as child:
            deadline=time.monotonic()+3
            while not ready.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(ready.exists());child.send_signal(signal.SIGTERM);child.communicate(timeout=3)
            self.assertEqual(child.returncode,-signal.SIGTERM)
        self.call('opa',expect=74);self.assertEqual(sentinel.read_text(),'KEEP')
        with (self.raw/'network-opa-node.lock').open('r') as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_missing_native_reader_is_explicit(self):
        self.ethernet();self.call('eth',shell=True,prime=True,extra={'REMORA_BIN':str(self.base/'missing')},expect=69)

    def test_nonexistent_sysfs_is_unsupported(self):
        self.call(expect=69);self.assertEqual(self.rows(),[])


if __name__ == '__main__':
    unittest.main()
