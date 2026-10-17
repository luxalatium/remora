"""Standard network tests for real Bash/C proc collectors; fixtures only."""
import errno
import fcntl
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
MODULES=('netprotocol','softnet')

class ProcNetwork(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='network-proc-build-')
        cls.prefix=Path(cls.tmp.name)
        binary=cls.prefix/'bin/binary_data_collectors';binary.mkdir(parents=True)
        (cls.prefix/'bin/modules').mkdir()
        cc=shlex.split(os.environ.get('REMORA_NETWORK_TEST_CC',os.environ.get('CC','cc')))
        cls.flags=cc+['-std=c99','-O1','-Wall','-Wextra','-Werror']
        for m in MODULES:
            r=subprocess.run(cls.flags+[str(ROOT/f'src/C_data_collectors_src/data_collect_{m}.c'),'-o',str(binary/f'data_collect_{m}')],capture_output=True,text=True)
            if r.returncode:raise RuntimeError(r.stderr)
            shutil.copy2(ROOT/f'src/modules/{m}',cls.prefix/'bin/modules')
        wrapper=(ROOT/'tests/test_network.py').read_text().split("fault.write_text('''",1)[1].split("''')",1)[0]
        wrapper=wrapper.replace('remora_network.h','remora_procnet.h').replace('remora_network_main("opa"','remora_procnet_main("netprotocol"')
        f=cls.prefix/'fault.c';f.write_text(wrapper);cls.fault=cls.prefix/'fault'
        r=subprocess.run(cls.flags+['-I',str(ROOT/'src/C_data_collectors_src'),str(f),'-o',str(cls.fault)],capture_output=True,text=True)
        if r.returncode:raise RuntimeError(r.stderr)
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='network-proc-case-');self.base=Path(self.tmp.name)
        self.proc=self.base/'proc';(self.proc/'net').mkdir(parents=True)
        self.raw=self.base/'raw';self.raw.mkdir()
        self.env=dict(os.environ,REMORA_NETWORK_PROC_ROOT=str(self.proc),REMORA_BIN=str(self.prefix/'bin'))
        self.put('snmp','Tcp: InSegs OutSegs RetransSegs InErrs OutRsts MaxConn\nTcp: 11 12 13 14 15 -1\nUdp: InDatagrams OutDatagrams InErrors RcvbufErrors SndbufErrors\nUdp: 21 22 23 24 25\n')
        self.put('netstat','TcpExt: ListenDrops ListenOverflows TCPBacklogDrop TCPTimeouts TCPSynRetrans\nTcpExt: 31 32 33 34 35\n')
        self.put('softnet_stat',self.row(7))
    def tearDown(self):self.tmp.cleanup()
    def put(self,name,text):p=self.proc/'net'/name;p.write_text(text);return p
    def row(self,cpu,columns=13,values=(11,12,13)):
        v=[*values]+[0]*9+[cpu]+[0]*max(0,columns-13)
        return ' '.join(f'{x:08x}' for x in v[:columns])+'\n'
    def runmod(self,m='netprotocol',code=0,shell=False,check=False,binary=None):
        a=['n',str(self.raw),str(self.raw)]+(['--check'] if check else [])
        if shell:
            a=['bash','-c','source "$1"; "${2}_$3" "$4" "$5" "$5"','test',str(self.prefix/f'bin/modules/{m}'),'init_module' if check else 'collect_data',m,'n',str(self.raw)]
        else:a=[str(binary or self.prefix/f'bin/binary_data_collectors/data_collect_{m}'),*a]
        r=subprocess.run(a,env=self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,code,r.stderr)
        p=self.raw/f'{m}_n.jsonl'
        if p.exists() and p.stat().st_size:
            try:return json.loads(p.read_text().splitlines()[-1])
            except json.JSONDecodeError:
                if not code:raise
    def field(self,x,name,entity=None):return next(r for r in x['records'] if r['metric']==name and (not entity or r['entity']==entity))
    def test_all15_protocol_fields(self):
        x=self.runmod();self.assertEqual(x['readable_primary'],15);self.assertEqual(self.field(x,'RetransSegs')['value'],'13')
        self.assertEqual(self.field(x,'InErrors')['unit'],'datagrams');self.assertTrue(all(r['scope']=='network_namespace' for r in x['records']))
    def test_header_reorder_and_unknown_fields(self):
        self.put('netstat','TcpExt: NewGauge TCPSynRetrans TCPTimeouts ListenDrops\nTcpExt: -1 35 34 31\n')
        x=self.runmod();self.assertEqual(self.field(x,'TCPSynRetrans')['value'],'35');self.assertEqual(self.field(x,'TCPTimeouts')['value'],'34')
        self.assertFalse(any(r['metric']=='NewGauge' for r in x['records']))
    def test_missing_field_is_null(self):
        self.put('netstat','TcpExt: ListenDrops\nTcpExt: 0\n');x=self.runmod()
        self.assertEqual(self.field(x,'ListenDrops')['value'],'0');r=self.field(x,'TCPTimeouts');self.assertEqual(r['status'],'ABSENT');self.assertIsNone(r['value'])
    def test_uint64_max_exact(self):
        self.put('netstat','TcpExt: ListenDrops\nTcpExt: 18446744073709551615\n');self.assertEqual(self.field(self.runmod(),'ListenDrops')['value'],str(2**64-1))
    def test_negative_overflow_nonnumeric(self):
        for bad in ('-1','18446744073709551616','hello','+1','1x'):
            with self.subTest(bad=bad):
                self.put('netstat',f'TcpExt: ListenDrops\nTcpExt: {bad}\n');r=self.field(self.runmod(code=74),'ListenDrops');self.assertEqual(r['status'],'MALFORMED');self.assertEqual(r['raw'],bad)
    def test_bad_header_value_width(self):
        self.put('snmp','Tcp: InSegs OutSegs\nTcp: 1\n');x=self.runmod(code=74);self.assertEqual(self.field(x,'InSegs')['status'],'MALFORMED');self.assertEqual(self.field(x,'ListenDrops')['status'],'OK')
    def test_group_pair_mismatch(self):self.put('snmp','Tcp: InSegs\nUdp: 1\n');self.runmod(code=74)
    def test_duplicate_header_names(self):self.put('snmp','Tcp: InSegs InSegs\nTcp: 1 2\n');self.runmod(code=74)
    def test_duplicate_protocol_groups(self):self.put('snmp','Tcp: InSegs\nTcp: 1\nTcp: OutSegs\nTcp: 2\n');self.runmod(code=74)
    def test_orphan_header(self):self.put('netstat','TcpExt: ListenDrops\n');self.runmod(code=74)
    def test_missing_source_optional(self):
        (self.proc/'net/netstat').unlink();x=self.runmod();self.assertEqual(x['readable_primary'],10);self.assertEqual(self.field(x,'ListenDrops')['errno'],2)
    def test_all_missing_is_skip_with_raw_status(self):
        (self.proc/'net/netstat').unlink();(self.proc/'net/snmp').unlink();x=self.runmod(code=69);self.assertEqual(x['status'],'UNSUPPORTED');self.assertEqual(x['readable_primary'],0)
    def test_empty_protocol_file_is_malformed(self):self.put('snmp','');self.runmod(code=74)
    def test_full_raw_source_preserved(self):
        x=self.runmod();self.assertEqual(x['source_snapshots'][0]['raw'],(self.proc/'net/snmp').read_text())
    def test_embedded_nul_preserved_rejected(self):
        p=self.proc/'net/snmp';p.write_bytes(b'Tcp: InSegs\nTcp: 1\x002\n');x=self.runmod(code=74);self.assertIn('\x00',x['source_snapshots'][0]['raw'])
    def test_incomplete_source_preserved_rejected(self):self.put('snmp','Tcp: InSegs\nTcp: 1');self.runmod(code=74)
    def test_source_cap_explicit(self):
        self.put('snmp','x'*(1024*1024+1));x=self.runmod(code=74);self.assertEqual(x['limitation'],'source_byte_limit');self.assertEqual(len(x['source_snapshots'][0]['raw']),1024*1024)
    def test_fifo_source_nonblocking(self):
        p=self.proc/'net/snmp';p.unlink();os.mkfifo(p);self.runmod(code=74)
    def test_softnet_cpu_id_not_row_number(self):
        self.put('softnet_stat',self.row(7)+self.row(23));x=self.runmod('softnet');self.assertEqual([e['name'] for e in x['entities']],['cpu.7','cpu.23'])
        self.assertEqual(self.field(x,'cpu_id','cpu.23')['value'],'23');self.assertEqual(self.field(x,'time_squeeze')['unit'],'events');self.assertEqual(self.field(x,'processed')['raw'],'0000000b')
    def test_softnet15_columns(self):
        self.put('softnet_stat',self.row(3,15));x=self.runmod('softnet');self.assertEqual(self.field(x,'layout_columns')['value'],'15')
    def test_softnet_unknown_layout(self):
        self.put('softnet_stat',self.row(3,11));x=self.runmod('softnet',code=69);self.assertEqual(x['status'],'UNSUPPORTED');self.assertEqual(self.field(x,'cpu_id')['status'],'UNSUPPORTED')
    def test_softnet_mixed_layout_partial(self):self.put('softnet_stat',self.row(7)+self.row(3,11));self.runmod('softnet',code=74)
    def test_softnet_duplicate_cpu(self):self.put('softnet_stat',self.row(7)+self.row(7));self.runmod('softnet',code=74)
    def test_softnet_invalid_hex_and_uint32_overflow(self):
        for value in ('100000000','gggggggg','-1','0x1'):
            with self.subTest(value=value):self.put('softnet_stat',self.row(7).replace('0000000b',value));self.runmod('softnet',code=74)
    def test_softnet_reset_wrap_and_hotplug_raw(self):
        self.put('softnet_stat',self.row(7,values=(2**32-1,12,13)));self.runmod('softnet')
        self.put('softnet_stat',self.row(8,values=(0,1,2)));x=self.runmod('softnet');self.assertEqual(x['entities'][0]['name'],'cpu.8');self.assertEqual(self.field(x,'processed')['value'],'0')
    def test_softnet_missing_source(self):
        (self.proc/'net/softnet_stat').unlink();x=self.runmod('softnet',code=69);self.assertEqual(self.field(x,'processed')['status'],'ABSENT')
    def test_softnet_row_cap(self):
        self.put('softnet_stat',''.join(self.row(i) for i in range(2049)));x=self.runmod('softnet',code=74);self.assertIsNotNone(x['limitation'])
    def test_invalid_proc_root(self):self.env['REMORA_NETWORK_PROC_ROOT']='relative';self.runmod(code=65)
    def test_init_discovery_does_not_parse_or_truncate(self):
        self.runmod(shell=True);p=self.raw/'netprotocol_n.jsonl';before=p.read_bytes();self.put('snmp','malformed\n')
        self.runmod(shell=True,check=True);self.assertEqual(p.read_bytes(),before)
    def test_shell_native_parity(self):
        for m in MODULES:
            a=self.runmod(m,shell=True);b=self.runmod(m);self.assertEqual(a['records'],b['records']);self.assertEqual(a['source_snapshots'],b['source_snapshots'])
    def test_finalizers_preserve_raw(self):
        for m in MODULES:
            self.runmod(m);p=self.raw/f'{m}_n.jsonl';before=p.read_bytes()
            subprocess.run(['bash','-c','source "$1"; "finalize_module_$2" n "$3" "$3"','test',str(self.prefix/f'bin/modules/{m}'),m,str(self.raw)],env=self.env,check=True)
            self.assertEqual(p.read_bytes(),before)
    def test_lock_contention(self):
        with (self.raw/'netprotocol_n.jsonl.lock').open('w') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);self.runmod(code=75)
    def test_output_symlink(self):
        target=self.base/'target';target.write_text('keep');(self.raw/'netprotocol_n.jsonl').symlink_to(target);self.runmod(code=74);self.assertEqual(target.read_text(),'keep')
    def test_output_hardlink(self):
        target=self.base/'target';target.write_text('keep\n')
        try:os.link(target,self.raw/'netprotocol_n.jsonl')
        except OSError as e:
            if e.errno in (errno.EPERM,errno.EOPNOTSUPP,errno.EXDEV,errno.ENOSYS):self.skipTest('fixture filesystem denies hardlink creation')
            raise
        self.runmod(code=74);self.assertEqual(target.read_text(),'keep\n')
    def test_output_fifo(self):os.mkfifo(self.raw/'netprotocol_n.jsonl');self.runmod(code=74)
    def test_pending_and_incomplete_output(self):
        p=self.raw/'netprotocol_n.jsonl';p.write_text('{partial');self.runmod(code=74);self.assertEqual(p.read_text(),'{partial')
    def test_denied_read_is_partial_not_skip(self):
        p=self.proc/'net/snmp';self.env.update(TEST_INODE=str(p.stat().st_ino),TEST_ERRNO='13');x=self.runmod(code=74,binary=self.fault);self.assertEqual(self.field(x,'InSegs')['status'],'DENIED')
    def test_unsupported_read_retains_other_source(self):
        p=self.proc/'net/snmp';self.env.update(TEST_INODE=str(p.stat().st_ino),TEST_ERRNO='22');x=self.runmod(binary=self.fault);self.assertEqual(self.field(x,'InSegs')['status'],'UNSUPPORTED');self.assertEqual(x['readable_primary'],5)
    def test_sigterm_preserves_pending(self):
        p=self.proc/'net/snmp';ready=self.base/'ready';self.env.update(TEST_INODE=str(p.stat().st_ino),TEST_READY=str(ready))
        child=subprocess.Popen([str(self.fault),'n',str(self.raw),str(self.raw)],env=self.env)
        try:
            deadline=time.monotonic()+3
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(ready.exists());child.send_signal(signal.SIGTERM);self.assertEqual(child.wait(timeout=3),-signal.SIGTERM)
        finally:
            if child.poll() is None:child.kill();child.wait()
        self.assertTrue((self.raw/'netprotocol_n.jsonl.pending').exists());self.runmod(code=74)
    def test_actual_runtime_worker(self):
        if not shutil.which('jq'):self.skipTest('jq unavailable')
        for m in MODULES:
            for mode in ('shell','collector'):
                r=subprocess.run(['bash',str(ROOT/'runtime/worker.sh'),m,mode,str(self.prefix),str(self.base/(m+mode)),'2','1',''],env=self.env,capture_output=True,text=True,timeout=15)
                self.assertEqual(r.returncode,0,r.stderr)
                result=json.loads((self.base/(m+mode)/'result.json').read_text());self.assertEqual(result['status'],'PASS',result)

if __name__=='__main__':unittest.main()
