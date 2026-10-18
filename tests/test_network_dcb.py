"""Portable adversarial DCB wire fixtures; test-only backend never installed."""
import errno,fcntl,json,os,shlex,shutil,signal,struct,subprocess,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def attr(t,p):
 b=struct.pack('=HH',4+len(p),t)+p
 return b+b'\0'*((-len(b))%4)
def response(name='eth0',pfc=True,ets=True,mask=129,mapping=tuple(range(8)),cap=4,extra=b''):
 fields=b''
 if ets:
  e=bytearray(59);e[1]=cap;e[27:35]=bytes(mapping);fields+=attr(1,e)
 if pfc:
  p=bytearray(136);p[0]=cap;p[1]=mask;fields+=attr(2,p)
 payload=bytes((0,21,0,0))+attr(1,name.encode()+b'\0')+attr(13,fields)+extra
 return struct.pack('=IHHII',16+len(payload),78,0,77,1234)+payload
def error_reply(e):return struct.pack('=IHHIIi',36,2,0,77,77,-e)+b'\0'*16
class DCBNetwork(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(prefix='network-dcb-build-');cls.prefix=Path(cls.tmp.name)
  binary=cls.prefix/'bin/binary_data_collectors';binary.mkdir(parents=True);(cls.prefix/'bin/modules').mkdir()
  cc=shlex.split(os.environ.get('REMORA_NETWORK_TEST_CC',os.environ.get('CC','cc')))
  r=subprocess.run(cc+['-std=c99','-O1','-Wall','-Wextra','-Werror','-DREMORA_DCB_TEST',str(ROOT/'src/C_data_collectors_src/data_collect_netdcb.c'),'-o',str(binary/'data_collect_netdcb')],capture_output=True,text=True)
  if r.returncode:raise RuntimeError(r.stderr)
  shutil.copy2(ROOT/'src/modules/netdcb',cls.prefix/'bin/modules')
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='network-dcb-case-');self.base=Path(self.tmp.name);self.sys=self.base/'sys';(self.sys/'class/net').mkdir(parents=True)
  self.raw=self.base/'raw';self.raw.mkdir();self.env=dict(os.environ,REMORA_NETWORK_SYSFS_ROOT=str(self.sys),REMORA_BIN=str(self.prefix/'bin'));self.env.pop('REMORA_ETH_INTERFACES',None)
  self.put('eth0')
 def tearDown(self):self.tmp.cleanup()
 def put(self,name,data=None,type='1',state='up'):
  p=self.sys/'class/net'/name;p.mkdir(exist_ok=True);(p/'type').write_text(type+'\n');(p/'operstate').write_text(state+'\n');(p/'ifindex').write_text('7\n');(p/'reply').write_bytes(response(name) if data is None else data);return p
 def runmod(self,code=0,shell=False,check=False):
  args=[str(self.prefix/'bin/binary_data_collectors/data_collect_netdcb'),'node',str(self.raw),str(self.raw)]+(['--check'] if check else [])
  if shell:args=['bash','-c','source "$1"; "${2}_netdcb" node "$3" "$3"','test',str(self.prefix/'bin/modules/netdcb'),'init_module' if check else 'collect_data',str(self.raw)]
  r=subprocess.run(args,env=self.env,capture_output=True,text=True,timeout=10);self.assertEqual(r.returncode,code,r.stderr)
  p=self.raw/'netdcb_node.jsonl'
  if p.exists() and p.stat().st_size and p.read_bytes().endswith(b'\n'):
   try:return json.loads(p.read_text().splitlines()[-1])
   except json.JSONDecodeError:
    if not code:raise
 def field(self,x,name,entity='eth0'):return next(r for r in x['records'] if r['metric']==name and r['entity']==entity)
 def test_enable_and_mapping(self):
  x=self.runmod();self.assertEqual(len(x['records']),19);self.assertEqual(x['readable_primary'],19)
  self.assertEqual(self.field(x,'pfc_enable_mask')['value'],'129')
  for i in range(8):
   self.assertEqual(self.field(x,f'priority_{i}_pfc_enabled')['value'],'1' if i in (0,7) else '0');self.assertEqual(self.field(x,f'priority_{i}_to_tc')['value'],str(i))
 def test_units_scopes_and_no_counters(self):
  x=self.runmod();self.assertEqual(self.field(x,'pfc_cap')['unit'],'traffic_classes');self.assertEqual(self.field(x,'pfc_enable_mask')['unit'],'bitmask');self.assertEqual(self.field(x,'priority_0_pfc_enabled')['unit'],'boolean');self.assertEqual(self.field(x,'priority_0_to_tc')['scope'],'netdev_dcb_priority_configuration');self.assertFalse(any(r['kind']=='counter' for r in x['records']))
 def test_disabled_is_readable(self):self.put('eth0',response(mask=0,mapping=(0,)*8));x=self.runmod();self.assertEqual(self.field(x,'pfc_enable_mask')['value'],'0');self.assertEqual(x['status'],'OK')
 def test_ets_zero_cap_retained(self):self.put('eth0',response(cap=0));self.assertEqual(self.field(self.runmod(),'ets_cap')['value'],'0')
 def test_pfc_only(self):self.put('eth0',response(ets=False));x=self.runmod();self.assertEqual(x['readable_primary'],10);self.assertEqual(self.field(x,'priority_0_to_tc')['availability_reason'],'response_attribute_not_exposed')
 def test_ets_only(self):self.put('eth0',response(pfc=False));x=self.runmod();self.assertEqual(x['readable_primary'],9);self.assertIsNone(self.field(x,'pfc_enable_mask')['value'])
 def test_neither_attribute(self):self.put('eth0',response(pfc=False,ets=False));x=self.runmod(code=69);self.assertTrue(all(r['status']=='ABSENT' for r in x['records']))
 def test_unknown_attribute_compatible(self):self.put('eth0',response(extra=attr(31,b'new')));self.assertEqual(self.runmod()['readable_primary'],19)
 def test_raw_peer_and_header_port_are_distinct(self):x=self.runmod();self.assertEqual(x['source_snapshots'][0]['peer_pid'],0);self.assertEqual(bytes.fromhex(x['source_snapshots'][0]['response_hex']),response())
 def test_nonidentity_many_to_one_mapping(self):self.put('eth0',response(mapping=(3,3,1,1,7,0,0,2)));x=self.runmod();self.assertEqual(self.field(x,'priority_4_to_tc')['value'],'7')
 def test_kernel_unsupported(self):self.put('eth0',error_reply(errno.EOPNOTSUPP));x=self.runmod(code=69);self.assertEqual(self.field(x,'pfc_cap')['availability_reason'],'kernel_get_not_supported')
 def test_kernel_denied(self):self.put('eth0',error_reply(errno.EPERM));x=self.runmod(code=74);self.assertTrue(all(r['status']=='DENIED' and r['value'] is None for r in x['records']))
 def test_kernel_absent(self):self.put('eth0',error_reply(errno.ENODEV));self.runmod(code=69)
 def test_kernel_timeout(self):self.put('eth0',error_reply(errno.ETIMEDOUT));self.runmod(code=74)
 def test_wrong_peer(self):self.env['REMORA_DCB_TEST_PEER']='1';self.runmod(code=74)
 def test_truncation_preserves_received_bytes(self):self.env['REMORA_DCB_TEST_TRUNC']='1';x=self.runmod(code=74);self.assertEqual(x['source_snapshots'][0]['received_bytes'],65537);self.assertTrue(x['source_snapshots'][0]['response_hex'])
 def test_identity_race(self):self.env['REMORA_DCB_TEST_RACE']='1';x=self.runmod(code=74);self.assertTrue(all(r['value'] is None for r in x['records']))
 def test_down_included(self):self.put('eth1',state='down');self.assertEqual(len(self.runmod()['entities']),2)
 def test_ipoib_inapplicable(self):self.put('eth0',type='32');x=self.runmod(code=69);self.assertEqual(self.field(x,'pfc_cap')['availability_reason'],'not_applicable_to_transport');self.assertIsNone(self.field(x,'pfc_enable_mask')['value'])
 def test_exact_sorted_selection(self):self.put('eth1');self.env['REMORA_ETH_INTERFACES']='eth1,eth0';self.assertEqual([x['name'] for x in self.runmod()['entities']],['eth0','eth1'])
 def test_invalid_selection(self):
  for s in ('eth0,eth0','../eth0','','eth0,',',eth0','eth0,,eth1','eth0 eth1','abcdefghijklmnop'):
   with self.subTest(s=s):self.env['REMORA_ETH_INTERFACES']=s;self.runmod(code=65)
 def test_explicit_absent(self):self.env['REMORA_ETH_INTERFACES']='missing';self.runmod(code=69,check=True);self.runmod(code=69)
 def test_empty_discovery(self):shutil.rmtree(self.sys/'class/net/eth0');self.runmod(code=69)
 def test_other_transports_excluded(self):self.put('lo',type='772');self.put('tun0',type='65534');self.assertEqual(len(self.runmod()['entities']),1)
 def test_interface_limit(self):
  for i in range(1,17):self.put('eth'+str(i))
  self.assertEqual(self.runmod(code=74)['limitation'],'interface_limit')
 def test_shell_parity(self):self.assertEqual(self.runmod()['records'],self.runmod(shell=True)['records'])
 def test_check_discovery_only(self):self.runmod();p=self.raw/'netdcb_node.jsonl';old=p.read_bytes();self.put('eth0',b'bad');self.runmod(check=True,shell=True);self.assertEqual(p.read_bytes(),old)
 def test_partial_with_one_supported(self):self.put('eth1',error_reply(errno.EPERM));x=self.runmod(code=74);self.assertEqual(x['status'],'PARTIAL');self.assertEqual(self.field(x,'pfc_cap')['value'],'4')
 def test_finalizer_preserves_raw(self):
  self.runmod();p=self.raw/'netdcb_node.jsonl';old=p.read_bytes();r=subprocess.run(['bash','-c','source "$1"; finalize_module_netdcb node "$2" "$2"','test',str(self.prefix/'bin/modules/netdcb'),str(self.raw)],env=self.env);self.assertEqual(r.returncode,0);self.assertEqual(p.read_bytes(),old)
 def test_output_symlink(self):
  target=self.base/'target';target.write_text('keep');(self.raw/'netdcb_node.jsonl').symlink_to(target);self.runmod(code=74);self.assertEqual(target.read_text(),'keep')
 def test_output_hardlink(self):
  target=self.base/'target';target.write_text('keep\n')
  try:os.link(target,self.raw/'netdcb_node.jsonl')
  except OSError as e:
   if e.errno in (errno.EPERM,errno.EOPNOTSUPP,errno.EXDEV,errno.ENOSYS):self.skipTest('fixture filesystem denies hardlink creation')
   raise
  self.runmod(code=74);self.assertEqual(target.read_text(),'keep\n')
 def test_output_fifo(self):os.mkfifo(self.raw/'netdcb_node.jsonl');self.runmod(code=74)
 def test_lock_contention(self):
  with (self.raw/'netdcb_node.jsonl.lock').open('w') as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);self.runmod(code=75)
 def test_pending_marker(self):(self.raw/'netdcb_node.jsonl.pending').touch();self.runmod(code=74)
 def test_incomplete_output(self):p=self.raw/'netdcb_node.jsonl';p.write_text('{partial');self.runmod(code=74);self.assertEqual(p.read_text(),'{partial')
 def test_sigterm_preserves_pending(self):
  ready=self.base/'ready';self.env['REMORA_DCB_TEST_WAIT']=str(ready);child=subprocess.Popen([str(self.prefix/'bin/binary_data_collectors/data_collect_netdcb'),'node',str(self.raw),str(self.raw)],env=self.env)
  try:
   until=time.monotonic()+3
   while not ready.exists() and time.monotonic()<until:time.sleep(.01)
   self.assertTrue(ready.exists());child.send_signal(signal.SIGTERM);self.assertEqual(child.wait(timeout=3),-signal.SIGTERM)
  finally:
   if child.poll() is None:child.kill();child.wait()
  self.assertTrue((self.raw/'netdcb_node.jsonl.pending').exists());self.env.pop('REMORA_DCB_TEST_WAIT');self.runmod(code=74)
 def test_default_off_registration(self):
  config=json.loads((ROOT/'installer/modules.json').read_text())['modules']['netdcb'];self.assertEqual(config,{'default':'off','kind':'collector','requires':[],'entry':'netdcb,NETWORK'})
 def test_production_fixture_switch_compile_guard(self):
  source=(ROOT/'src/C_data_collectors_src/remora_netdcb.h').read_text();self.assertIn('#elif defined(REMORA_DCB_TEST)',source);self.assertNotIn('RTM_SETDCB',source)
 def test_actual_runtime_worker(self):
  if not shutil.which('jq'):self.skipTest('jq unavailable')
  for mode in ('shell','collector'):
   p=self.base/mode;r=subprocess.run(['bash',str(ROOT/'runtime/worker.sh'),'netdcb',mode,str(self.prefix),str(p),'2','1',''],env=self.env,capture_output=True,text=True,timeout=15);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads((p/'result.json').read_text())['status'],'PASS')
# Separate named malformed cases: every failed decode retains bytes and suppresses values.
def malformed(data):
 def test(self):
  self.put('eth0',data);x=self.runmod(code=74);self.assertEqual(bytes.fromhex(x['source_snapshots'][0]['response_hex']),data);self.assertTrue(all(r['value'] is None for r in x['records']))
 return test
base=response()
cases={'short_header':b'bad','wrong_interface':response('eth9'),'wrong_sequence':base[:8]+struct.pack('=I',78)+base[12:],'bad_length':struct.pack('=I',len(base)+4)+base[4:],'multi_message':base+base,'multipart':base[:6]+b'\x02\0'+base[8:],'wrong_type':base[:4]+b'\x4f\0'+base[6:],'wrong_cmd':base[:17]+b'\x16'+base[18:],'trailing_fragment':base+b'x','duplicate_ifname':response(extra=attr(1,b'eth0\0')),'duplicate_ieee':response(extra=attr(13,b'')),'invalid_cap':response(cap=9),'invalid_map':response(mapping=(8,)*8),'zero_ack':error_reply(0),'positive_kernel_error':error_reply(-1)}
for label,data in cases.items():setattr(DCBNetwork,'test_malformed_'+label,malformed(data))
# Nested payload bounds/duplicates independent of response builder.
for label,body in [('short_pfc',attr(2,b'\0'*135)),('short_ets',attr(1,b'\0'*58)),('duplicate_pfc',attr(2,b'\0'*136)*2),('duplicate_ets',attr(1,b'\0'*59)*2),('nested_tail',b'x'),('attribute_len',b'\x03\0\x02\0')]:
 payload=bytes((0,21,0,0))+attr(1,b'eth0\0')+attr(13,body);data=struct.pack('=IHHII',16+len(payload),78,0,77,77)+payload
 setattr(DCBNetwork,'test_malformed_'+label,malformed(data))
if __name__=='__main__':unittest.main()
