"""Standard resource snapshots: real C, synthetic sysfs, no remote operations."""
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
MODULES=('netinfo','neterrors','ibcounters','mlxcounters','opacounters')

class NetworkResources(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build=tempfile.TemporaryDirectory(prefix='network-resources-build-')
        cls.prefix=Path(cls.build.name)
        (cls.prefix/'bin/binary_data_collectors').mkdir(parents=True)
        (cls.prefix/'bin/modules').mkdir()
        cc=shlex.split(os.environ.get('REMORA_NETWORK_TEST_CC',os.environ.get('CC','cc')))
        for m in MODULES:
            r=subprocess.run(cc+['-std=c99','-O1','-Wall','-Wextra','-Werror',str(ROOT/f'src/C_data_collectors_src/data_collect_{m}.c'),'-o',str(cls.prefix/f'bin/binary_data_collectors/data_collect_{m}')],capture_output=True,text=True)
            if r.returncode: raise RuntimeError(r.stderr)
            shutil.copy2(ROOT/f'src/modules/{m}',cls.prefix/'bin/modules')
        # Fault injection lives exclusively in the development translation unit.
        wrapper=(ROOT/'tests/test_network.py').read_text().split("fault.write_text('''",1)[1].split("''')",1)[0]
        wrapper=wrapper.replace('remora_network.h','remora_netresources.h').replace('remora_network_main("opa"','remora_netresources_main("neterrors"')
        fault=cls.prefix/'fault.c';fault.write_text(wrapper)
        cls.fault=cls.prefix/'fault'
        r=subprocess.run(cc+['-std=c99','-O1','-Wall','-Wextra','-Werror','-I',str(ROOT/'src/C_data_collectors_src'),str(fault),'-o',str(cls.fault)],capture_output=True,text=True)
        if r.returncode: raise RuntimeError(r.stderr)

    @classmethod
    def tearDownClass(cls): cls.build.cleanup()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='network-resources-case-')
        self.base=Path(self.temp.name);self.sys=self.base/'sys';self.raw=self.base/'raw';self.raw.mkdir()
        self.env=dict(os.environ,REMORA_NETWORK_SYSFS_ROOT=str(self.sys),REMORA_BIN=str(self.prefix/'bin'))
        for k in ('REMORA_ETH_INTERFACES','REMORA_IB_PORTS','REMORA_OPA_PORTS'):self.env.pop(k,None)
        self.net()
    def tearDown(self): self.temp.cleanup()
    def put(self,path,value):
        p=self.sys/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(str(value)+'\n');return p
    def net(self,name='eth0',kind=1):
        self.put(f'class/net/{name}/type',kind);self.put(f'class/net/{name}/ifindex','10');self.put(f'class/net/{name}/flags','0x0')
        self.put(f'class/net/{name}/statistics/rx_errors','7');self.put(f'class/net/{name}/statistics/rx_dropped','9')
    def fabric(self,name='mlx5_0',port=1,layer='InfiniBand'):
        b=f'class/infiniband/{name}/ports/{port}'
        self.put(b+'/state','1: DOWN');self.put(b+'/link_layer',layer);self.put(f'class/infiniband/{name}/node_guid','0011:2233')
        return b
    def runmod(self,module='neterrors',env=None,code=0,shell=False,check=False,binary=None):
        args=['n',str(self.raw),str(self.raw)]+(['--check'] if check else [])
        if shell:
            command=['bash','-c','source "$1"; "${2}_'+module+'" "$3" "$4" "$5"','test',str(self.prefix/f'bin/modules/{module}'),'init_module' if check else 'collect_data',*args[:3]]
        else: command=[str(binary or self.prefix/f'bin/binary_data_collectors/data_collect_{module}'),*args]
        r=subprocess.run(command,env=env or self.env,capture_output=True,text=True,timeout=5)
        self.assertEqual(r.returncode,code,r.stderr)
        p=self.raw/f'{module}_n.jsonl'
        if p.exists() and p.stat().st_size:
            try: return json.loads(p.read_text().splitlines()[-1])
            except json.JSONDecodeError:
                if not code: raise
        return None
    def records(self,x,metric):return [r for r in x['records'] if r['metric']==metric]
    def test_netinfo_includes_loopback_and_down_port2(self):
        self.net('lo',772);self.fabric('hfi1_0',2)
        x=self.runmod('netinfo');self.assertEqual({e['name'] for e in x['entities']},{'eth0','lo','hfi1_0/2'})
        self.assertEqual(self.records(x,'state')[0]['value'],'1: DOWN')
    def test_neterrors_excludes_loopback_by_default(self):
        self.net('lo',772);self.assertEqual(len(self.runmod()['entities']),1)
    def test_explicit_loopback(self):
        self.net('lo',772);self.env['REMORA_ETH_INTERFACES']='lo';self.assertEqual(self.runmod()['entities'][0]['name'],'lo')
    def test_exact_multiple_selection(self):
        self.net('eth1');self.env['REMORA_ETH_INTERFACES']='eth1,eth0';self.assertEqual([e['name'] for e in self.runmod()['entities']],['eth0','eth1'])
    def test_invalid_selectors(self):
        for s in ('','eth0,eth0','../eth0','eth0,','eth0,,eth1','eth*','$(id)','eth0 eth1'):
            with self.subTest(s=s):self.env['REMORA_ETH_INTERFACES']=s;self.runmod(code=65)
    def test_missing_explicit(self):
        self.env['REMORA_ETH_INTERFACES']='missing';self.runmod(code=69)
    def test_wrong_family(self):
        self.fabric();self.env['REMORA_OPA_PORTS']='mlx5_0/1';self.runmod('opacounters',code=69)
    def test_ib_rejects_ethernet(self):
        self.fabric(layer='Ethernet');self.env['REMORA_IB_PORTS']='mlx5_0/1';self.runmod('ibcounters',code=69)
    def test_mlx_includes_ethernet_without_roce_claim(self):
        b=self.fabric(layer='Ethernet');self.put(b+'/hw_counters/np_cnp_sent',0)
        x=self.runmod('mlxcounters');self.assertEqual(self.records(x,'np_cnp_sent')[0]['value'],'0');self.assertEqual(self.records(x,'link_layer')[0]['value'],'Ethernet')
    def test_uint64_exact_and_zero(self):
        self.put('class/net/eth0/statistics/rx_errors',2**64-1);x=self.runmod();self.assertEqual(self.records(x,'rx_errors')[0]['value'],str(2**64-1))
        self.put('class/net/eth0/statistics/rx_errors',0);self.assertEqual(self.records(self.runmod(),'rx_errors')[0]['value'],'0')
    def test_counter_reset_retained_as_snapshot(self):
        self.runmod();self.put('class/net/eth0/statistics/rx_errors',1);self.assertEqual(self.records(self.runmod(),'rx_errors')[0]['value'],'1')
    def test_missing_not_zero(self):
        r=self.records(self.runmod(),'tx_errors')[0];self.assertEqual(r['status'],'ABSENT');self.assertIsNone(r['value']);self.assertEqual(r['errno'],2)
    def test_malformed_preserves_raw(self):
        self.put('class/net/eth0/statistics/rx_errors','-7');x=self.runmod(code=74);r=self.records(x,'rx_errors')[0];self.assertEqual(r['raw'],'-7\n');self.assertEqual(r['status'],'MALFORMED');self.assertIsNone(r['value'])
    def test_overflow_never_rounds(self):
        self.put('class/net/eth0/statistics/rx_errors',2**64);self.assertEqual(self.records(self.runmod(code=74),'rx_errors')[0]['status'],'MALFORMED')
    def test_embedded_nul_rejected(self):
        p=self.put('class/net/eth0/statistics/rx_errors',7);p.write_bytes(b'7\x008\n');self.runmod(code=74)
    def test_bounded_read(self):
        self.put('class/net/eth0/statistics/rx_errors','7'*3000);self.runmod(code=74)
    def test_fifo_cannot_block(self):
        p=self.sys/'class/net/eth0/statistics/rx_errors';p.unlink();os.mkfifo(p);self.runmod(code=74)
    def test_ib_extended_attempts_and_classic(self):
        b=self.fabric();self.put(b+'/counters/port_xmit_wait',19);self.put(b+'/counters_ext/symbol_error_64',13)
        x=self.runmod('ibcounters');self.assertEqual(len(self.records(x,'symbol_error')),1);r=self.records(x,'port_xmit_wait');self.assertEqual([v['status'] for v in r],['ABSENT','OK']);self.assertEqual(r[1]['unit'],'native_ticks')
    def test_opa_dword_scale4_and_ticks(self):
        b=self.fabric('hfi1_0',2);self.put(b+'/hw_counters/TxWords',37);self.put(b+'/hw_counters/TxWait',11)
        x=self.runmod('opacounters');r=self.records(x,'TxWords')[0];self.assertEqual((r['value'],r['unit'],r['byte_scale']),('37','DWORD',4));self.assertEqual(self.records(x,'TxWait')[0]['unit'],'native_ticks')
    def test_no_unknown_gauges_interpreted(self):
        b=self.fabric('hfi1_0');self.put(b+'/hw_counters/TxPkt',7);self.put(b+'/hw_counters/UnknownHighWater',999)
        self.assertFalse(self.records(self.runmod('opacounters'),'UnknownHighWater'))
    def test_driver_identity_overrides_name(self):
        b=self.fabric('renamed',2);p=self.sys/'class/infiniband/renamed/device';p.mkdir();(p/'driver').symlink_to('/drivers/hfi1');self.put(b+'/hw_counters/TxPkt',7)
        self.assertEqual(self.runmod('opacounters')['entities'][0]['family_evidence'],'hfi1')
    def test_driver_prevents_false_name_family(self):
        self.fabric('hfi1_fake');p=self.sys/'class/infiniband/hfi1_fake/device';p.mkdir();(p/'driver').symlink_to('/drivers/mlx5_core');self.runmod('opacounters',code=69)
    def test_raw_metadata_json_escaping(self):
        self.put('class/net/eth0/address','a"b\\c\t\n');r=self.records(self.runmod('netinfo'),'address')[0];self.assertEqual(r['raw'],'a"b\\c\t\n\n')
    def test_gid_netdev_mapping(self):
        b=self.fabric();self.put(b+'/gid_attrs/ndevs/0','eth0');self.assertEqual(self.records(self.runmod('netinfo'),'gid_netdev.0')[0]['value'],'eth0')
    def test_all_missing_primary_is_unsupported(self):
        b=self.fabric();x=self.runmod('mlxcounters',code=69);self.assertEqual(x['status'],'UNSUPPORTED')
    def test_no_fabric_is_unsupported(self):self.runmod('ibcounters',code=69)
    def test_shell_native_and_init_no_truncate(self):
        self.runmod(shell=True,check=True);a=self.runmod(shell=True);self.runmod(shell=True,check=True);b=self.runmod();self.assertEqual(a['records'],b['records']);self.assertEqual(len((self.raw/'neterrors_n.jsonl').read_text().splitlines()),2)
    def test_lock_contention(self):
        with (self.raw/'neterrors_n.jsonl.lock').open('w') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);self.runmod(code=75)
    def test_output_symlink(self):
        target=self.base/'target';target.write_text('keep');(self.raw/'neterrors_n.jsonl').symlink_to(target);self.runmod(code=74);self.assertEqual(target.read_text(),'keep')
    def test_output_hardlink(self):
        target=self.base/'target';target.write_text('keep\n');os.link(target,self.raw/'neterrors_n.jsonl');self.runmod(code=74);self.assertEqual(target.read_text(),'keep\n')
    def test_output_fifo(self):os.mkfifo(self.raw/'neterrors_n.jsonl');self.runmod(code=74)
    def test_incomplete_previous_output(self):
        p=self.raw/'neterrors_n.jsonl';p.write_text('{"incomplete":');self.runmod(code=74);self.assertEqual(p.read_text(),'{"incomplete":')
    def test_pending_marker_fails_closed(self):
        (self.raw/'neterrors_n.jsonl.pending').touch();self.runmod(code=74)
    def test_permission_is_not_missing(self):
        p=self.sys/'class/net/eth0/statistics/rx_errors';self.env.update(TEST_INODE=str(p.stat().st_ino),TEST_ERRNO='13')
        x=self.runmod(binary=self.fault,code=74);self.assertEqual(self.records(x,'rx_errors')[0]['status'],'DENIED')
    def test_einval_is_unsupported(self):
        p=self.sys/'class/net/eth0/statistics/rx_errors';self.env.update(TEST_INODE=str(p.stat().st_ino),TEST_ERRNO='22')
        self.assertEqual(self.records(self.runmod(binary=self.fault),'rx_errors')[0]['status'],'UNSUPPORTED')
    def test_sigterm_owned_process_preserves_pending(self):
        p=self.sys/'class/net/eth0/statistics/rx_errors';ready=self.base/'ready';self.env.update(TEST_INODE=str(p.stat().st_ino),TEST_READY=str(ready))
        child=subprocess.Popen([str(self.fault),'n',str(self.raw),str(self.raw)],env=self.env)
        try:
            deadline=time.monotonic()+3
            while not ready.exists() and time.monotonic()<deadline:time.sleep(.01)
            self.assertTrue(ready.exists());child.send_signal(signal.SIGTERM);self.assertEqual(child.wait(timeout=3),-signal.SIGTERM)
        finally:
            if child.poll() is None:child.kill();child.wait()
        self.assertTrue((self.raw/'neterrors_n.jsonl.pending').exists());self.runmod(code=74)
    def test_no_runtime_python(self):
        registry=json.loads((ROOT/'installer/modules.json').read_text())['modules']
        for m in MODULES:
            self.assertEqual(registry[m]['default'],'off');self.assertEqual(registry[m]['kind'],'collector')
            self.assertNotIn('python3',(ROOT/f'src/modules/{m}').read_text())

if __name__=='__main__':unittest.main()
