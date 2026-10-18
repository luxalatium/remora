"""Standard ethtool GET fixtures: no test backend in production collectors."""
import errno,fcntl,json,os,shlex,shutil,signal,struct,subprocess,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def attr(t,p):
 b=struct.pack('=HH',4+len(p),t)+p;return b+b'\0'*((-len(b))%4)
def u32(v):return struct.pack('=I',v)
def message(module,fields,name='eth0',extra=b'',seq=77):
 h=attr(1,u32(7))+attr(2,name.encode()+b'\0')
 p=bytes((22 if module=='netpause' else 30,1,0,0))+attr(0x8001,h)+fields+extra
 return struct.pack('=IHHII',16+len(p),42,0,seq,1234)+p
def pause(**kw):
 fields=attr(2,bytes([kw.get('autoneg',1)]))+attr(3,bytes([kw.get('rx',0)]))+attr(4,bytes([kw.get('tx',1)]))
 fields+=attr(5,attr(2,struct.pack('=Q',kw.get('txframes',2**64-1)))+attr(3,struct.pack('=Q',kw.get('rxframes',11))))+attr(6,u32(kw.get('source',0)))
 return message('netpause',fields,name=kw.get('name','eth0'))
def bitset(values=(50,),size=75,mask=None):
 n=(size+31)//32;words=[0]*n;valid=[0]*n
 for bit in values:words[bit//32]|=1<<(bit%32)
 for bit in (49,50,51,74) if mask is None else mask:valid[bit//32]|=1<<(bit%32)
 return attr(2,u32(size))+attr(4,b''.join(u32(v) for v in words))+attr(5,b''.join(u32(v) for v in valid))
def fec(arrays=((100,3,4),(0,0,0),(2**64-1,1,2)),modes=None,active=50,auto=0,name='eth0'):
 stats=b''.join(attr(i+2,b''.join(struct.pack('=Q',v) for v in values)) for i,values in enumerate(arrays))
 return message('netfec',attr(2,bitset() if modes is None else modes)+attr(3,bytes([auto]))+attr(4,u32(active))+attr(5,stats),name=name)
def error_reply(e):return struct.pack('=IHHIIi',36,2,0,77,77,-e)+b'\0'*16
class EthtoolNetwork(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(prefix='network-ethtool-build-');cls.prefix=Path(cls.tmp.name);binary=cls.prefix/'bin/binary_data_collectors';binary.mkdir(parents=True);(cls.prefix/'bin/modules').mkdir()
  cc=shlex.split(os.environ.get('REMORA_NETWORK_TEST_CC',os.environ.get('CC','cc')))
  for m in ('netpause','netfec'):
   r=subprocess.run(cc+['-std=c99','-O1','-Wall','-Wextra','-Werror','-DREMORA_ETHTOOL_TEST',str(ROOT/f'src/C_data_collectors_src/data_collect_{m}.c'),'-o',str(binary/f'data_collect_{m}')],capture_output=True,text=True)
   if r.returncode:raise RuntimeError(r.stderr)
   shutil.copy2(ROOT/f'src/modules/{m}',cls.prefix/'bin/modules')
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='network-ethtool-case-');self.base=Path(self.tmp.name);self.sys=self.base/'sys';(self.sys/'class/net').mkdir(parents=True);self.raw=self.base/'raw';self.raw.mkdir()
  self.env=dict(os.environ,REMORA_NETWORK_SYSFS_ROOT=str(self.sys),REMORA_BIN=str(self.prefix/'bin'));self.env.pop('REMORA_ETH_INTERFACES',None);self.put('eth0')
 def tearDown(self):self.tmp.cleanup()
 def put(self,name,type='1',state='up'):
  p=self.sys/'class/net'/name;p.mkdir(exist_ok=True);(p/'type').write_text(type+'\n');(p/'operstate').write_text(state+'\n');(p/'ifindex').write_text('7\n')
  for m,data in [('netpause',pause(name=name)),('netfec',fec(name=name))]:(p/(m+'.reply')).write_bytes(data)
  return p
 def reply(self,m,data):(self.sys/'class/net/eth0'/(m+'.reply')).write_bytes(data)
 def runmod(self,m='netfec',code=0,shell=False,check=False):
  args=[str(self.prefix/f'bin/binary_data_collectors/data_collect_{m}'),'node',str(self.raw),str(self.raw)]+(['--check'] if check else [])
  if shell:args=['bash','-c','source "$1"; "${2}_$3" node "$4" "$4"','test',str(self.prefix/f'bin/modules/{m}'),'init_module' if check else 'collect_data',m,str(self.raw)]
  r=subprocess.run(args,env=self.env,capture_output=True,text=True,timeout=10);self.assertEqual(r.returncode,code,r.stderr);p=self.raw/(m+'_node.jsonl')
  if p.exists() and p.stat().st_size and p.read_bytes().endswith(b'\n'):
   try:return json.loads(p.read_text().splitlines()[-1])
   except json.JSONDecodeError:
    if not code:raise
 def field(self,x,name):return next(r for r in x['records'] if r['metric']==name and r['entity']=='eth0')
 def test_pause_settings_uint64_and_source(self):
  x=self.runmod('netpause');self.assertEqual(self.field(x,'autoneg')['value'],'1');self.assertEqual(self.field(x,'rx_pause')['value'],'0');self.assertEqual(self.field(x,'tx_pause_frames')['value'],str(2**64-1));self.assertEqual(self.field(x,'tx_pause_frames')['unit'],'frames')
 def test_pause_nonzero_boolean_normalized_raw_retained(self):
  self.reply('netpause',pause(autoneg=7,rx=128));x=self.runmod('netpause');self.assertEqual(self.field(x,'rx_pause')['value'],'1');self.assertEqual(bytes.fromhex(x['source_snapshots'][0]['exchanges'][1]['response_hex']),pause(autoneg=7,rx=128))
 def test_pause_mac_source_scope(self):
  self.reply('netpause',pause(source=2));self.assertEqual(self.field(self.runmod('netpause'),'tx_pause_frames')['scope'],'physical_port_mac_pmac')
 def test_fec_active_config_are_distinct(self):
  self.reply('netfec',fec(modes=bitset((51,)),active=50,auto=1));x=self.runmod();self.assertEqual(self.field(x,'configured_baser')['value'],'1');self.assertEqual(self.field(x,'configured_rs')['value'],'0');self.assertEqual(self.field(x,'active_mode')['value'],'RS');self.assertEqual(self.field(x,'active_mode_index')['value'],'50');self.assertEqual(self.field(x,'auto_selection')['value'],'1')
 def test_total_not_lane0(self):
  x=self.runmod();self.assertEqual(self.field(x,'corrected_blocks')['value'],'100');self.assertEqual(self.field(x,'lane_0_corrected_blocks')['value'],'3');self.assertEqual(self.field(x,'lane_1_corrected_blocks')['value'],'4');self.assertEqual(self.field(x,'corrected_blocks_lane_count')['value'],'2')
 def test_distinct_bits_and_blocks(self):
  x=self.runmod();self.assertEqual(self.field(x,'corrected_bits')['value'],str(2**64-1));self.assertEqual(self.field(x,'lane_0_corrected_bits')['unit'],'bits');self.assertEqual(self.field(x,'lane_0_uncorrectable_blocks')['unit'],'blocks')
 def test_no_fake_per_lane_link_state(self):
  r=self.field(self.runmod(),'per_lane_link_state');self.assertEqual(r['status'],'UNSUPPORTED');self.assertIsNone(r['value']);self.assertEqual(r['availability_reason'],'not_defined_by_standard_fec_get')
 def test_aggregate_only(self):
  self.reply('netfec',fec(arrays=((0,),(2,),(3,))));x=self.runmod();self.assertEqual(self.field(x,'corrected_blocks')['value'],'0');self.assertEqual(self.field(x,'corrected_blocks_lane_count')['availability_reason'],'aggregate_only_no_lane_breakdown');self.assertFalse(any(r['metric'].startswith('lane_') for r in x['records']))
 def test_empty_stats_are_unavailable(self):
  self.reply('netfec',fec(arrays=((),(),())));x=self.runmod();self.assertEqual(self.field(x,'corrected_blocks')['availability_reason'],'statistics_not_exposed');self.assertIsNone(self.field(x,'corrected_blocks')['value'])
 def test_independent_array_lengths(self):
  self.reply('netfec',fec(arrays=((10,1,2),(0,),(1,1,1,1,1))));x=self.runmod();self.assertEqual(self.field(x,'corrected_bits_lane_count')['value'],'4');self.assertEqual(self.field(x,'corrected_blocks_lane_count')['value'],'2')
 def test_missing_all_attrs_not_zero(self):
  self.reply('netfec',message('netfec',b''));x=self.runmod(code=69);self.assertTrue(all(r['value'] is None for r in x['records']))
 def test_missing_pause_stats(self):
  self.reply('netpause',message('netpause',attr(2,b'\0')));x=self.runmod('netpause');self.assertEqual(self.field(x,'autoneg')['value'],'0');self.assertEqual(self.field(x,'rx_pause_frames')['status'],'ABSENT')
 def test_unknown_active_mode_preserved(self):self.reply('netfec',fec(active=301));x=self.runmod();self.assertEqual(self.field(x,'active_mode_index')['value'],'301');self.assertEqual(self.field(x,'active_mode')['value'],'UNKNOWN')
 def test_compact_nomask(self):
  words=[0,1<<18,0];m=attr(1,b'')+attr(2,u32(75))+attr(4,b''.join(u32(x) for x in words));self.reply('netfec',fec(modes=m));self.assertEqual(self.field(self.runmod(),'configured_rs')['value'],'1')
 def test_bitset_mask_unavailable_not_false(self):self.reply('netfec',fec(modes=bitset((50,),mask=(50,))));x=self.runmod();self.assertIsNone(self.field(x,'configured_off')['value']);self.assertEqual(self.field(x,'configured_rs')['value'],'1')
 def test_ioctl_pause_fallback_raw_failure_retained(self):
  self.reply('netpause',error_reply(errno.EOPNOTSUPP));(self.sys/'class/net/eth0/netpause.ioctl').write_bytes(struct.pack('=IIII',0x12,1,0,1));x=self.runmod('netpause');self.assertEqual(self.field(x,'tx_pause')['value'],'1');self.assertEqual(x['source_snapshots'][0]['exchanges'][1]['errno'],errno.EOPNOTSUPP);self.assertTrue(x['source_snapshots'][0]['exchanges'][2]['attempted']);self.assertEqual(self.field(x,'tx_pause_frames')['availability_reason'],'netlink_unavailable_ioctl_has_no_statistics')
 def test_ioctl_fec_mask_translation(self):
  self.reply('netfec',error_reply(errno.EOPNOTSUPP));(self.sys/'class/net/eth0/netfec.ioctl').write_bytes(struct.pack('=IIII',0x50,8,2|16,0));x=self.runmod();self.assertEqual(self.field(x,'active_mode_index')['value'],'50');self.assertEqual(self.field(x,'configured_baser')['value'],'1');self.assertEqual(self.field(x,'auto_selection')['value'],'1');self.assertEqual(self.field(x,'ioctl_active_fec_mask')['value'],'8')
 def test_ioctl_none_not_off(self):
  self.reply('netfec',error_reply(errno.EOPNOTSUPP));(self.sys/'class/net/eth0/netfec.ioctl').write_bytes(struct.pack('=IIII',0x50,1,1,0));x=self.runmod(code=69);self.assertIsNone(self.field(x,'active_mode')['value']);self.assertIsNone(self.field(x,'configured_off')['value']);self.assertEqual(x['readable_primary'],0)
 def test_denial_does_not_fallback(self):
  self.reply('netfec',error_reply(errno.EPERM));x=self.runmod(code=74);self.assertFalse(x['source_snapshots'][0]['exchanges'][2]['attempted']);self.assertEqual(self.field(x,'active_mode')['status'],'DENIED')
 def test_timeout_does_not_fallback(self):self.reply('netfec',error_reply(errno.ETIMEDOUT));x=self.runmod(code=74);self.assertFalse(x['source_snapshots'][0]['exchanges'][2]['attempted'])
 def test_wrong_peer(self):self.env['REMORA_ETHTOOL_TEST_PEER']='1';self.runmod(code=74)
 def test_truncation_preserves_bytes(self):self.env['REMORA_ETHTOOL_TEST_TRUNC']='1';x=self.runmod(code=74);self.assertEqual(x['source_snapshots'][0]['exchanges'][1]['received_bytes'],65537)
 def test_identity_race(self):self.env['REMORA_ETHTOOL_TEST_RACE']='1';x=self.runmod(code=74);self.assertTrue(all(r['value'] is None for r in x['records']))
 def test_ipoib_inapplicable_no_query(self):
  self.put('eth0',type='32');x=self.runmod(code=69);self.assertEqual(self.field(x,'active_mode')['availability_reason'],'not_applicable_to_transport');self.assertFalse(any(w['attempted'] for w in x['source_snapshots'][0]['exchanges']))
 def test_down_included(self):self.put('eth1',state='down');self.assertEqual(len(self.runmod()['entities']),2)
 def test_sorted_exact_selector(self):self.put('eth1');self.env['REMORA_ETH_INTERFACES']='eth1,eth0';self.assertEqual([e['name'] for e in self.runmod()['entities']],['eth0','eth1'])
 def test_invalid_selectors(self):
  for v in ('','eth0,eth0','../eth0','eth0,,eth1','eth0,','abcdefghijklmnop','eth0 eth1'):
   with self.subTest(v=v):self.env['REMORA_ETH_INTERFACES']=v;self.runmod(code=65)
 def test_empty_discovery(self):shutil.rmtree(self.sys/'class/net/eth0');self.runmod(code=69)
 def test_explicit_absent(self):self.env['REMORA_ETH_INTERFACES']='missing';self.runmod(code=69)
 def test_interface_limit(self):
  for i in range(1,17):self.put('eth'+str(i))
  self.assertEqual(self.runmod(code=74)['limitation'],'interface_limit')
 def test_shell_native_parity(self):
  for m in ('netpause','netfec'):self.assertEqual(self.runmod(m)['records'],self.runmod(m,shell=True)['records'])
 def test_init_does_not_read_or_truncate(self):
  self.runmod();p=self.raw/'netfec_node.jsonl';old=p.read_bytes();self.reply('netfec',b'bad');self.runmod(check=True,shell=True);self.assertEqual(p.read_bytes(),old)
 def test_finalizer_preserves_raw(self):
  for m in ('netpause','netfec'):
   self.runmod(m);p=self.raw/(m+'_node.jsonl');old=p.read_bytes();r=subprocess.run(['bash','-c','source "$1"; "finalize_module_$2" node "$3" "$3"','test',str(self.prefix/f'bin/modules/{m}'),m,str(self.raw)],env=self.env);self.assertEqual(r.returncode,0);self.assertEqual(p.read_bytes(),old)
 def test_output_symlink(self):target=self.base/'target';target.write_text('keep');(self.raw/'netfec_node.jsonl').symlink_to(target);self.runmod(code=74);self.assertEqual(target.read_text(),'keep')
 def test_output_hardlink(self):
  target=self.base/'target';target.write_text('keep\n')
  try:os.link(target,self.raw/'netfec_node.jsonl')
  except OSError as e:
   if e.errno in (errno.EPERM,errno.EOPNOTSUPP,errno.EXDEV,errno.ENOSYS):self.skipTest('filesystem denies hardlink fixture')
   raise
  self.runmod(code=74);self.assertEqual(target.read_text(),'keep\n')
 def test_output_fifo(self):os.mkfifo(self.raw/'netfec_node.jsonl');self.runmod(code=74)
 def test_lock(self):
  with (self.raw/'netfec_node.jsonl.lock').open('w') as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);self.runmod(code=75)
 def test_pending(self):(self.raw/'netfec_node.jsonl.pending').touch();self.runmod(code=74)
 def test_incomplete(self):p=self.raw/'netfec_node.jsonl';p.write_text('{bad');self.runmod(code=74);self.assertEqual(p.read_text(),'{bad')
 def test_sigterm_pending(self):
  ready=self.base/'ready';self.env['REMORA_ETHTOOL_TEST_WAIT']=str(ready);p=subprocess.Popen([str(self.prefix/'bin/binary_data_collectors/data_collect_netfec'),'node',str(self.raw),str(self.raw)],env=self.env)
  try:
   until=time.monotonic()+3
   while not ready.exists() and time.monotonic()<until:time.sleep(.01)
   self.assertTrue(ready.exists());p.send_signal(signal.SIGTERM);self.assertEqual(p.wait(timeout=3),-signal.SIGTERM)
  finally:
   if p.poll() is None:p.kill();p.wait()
  self.assertTrue((self.raw/'netfec_node.jsonl.pending').exists());self.env.pop('REMORA_ETHTOOL_TEST_WAIT');self.runmod(code=74)
 def test_default_off(self):
  c=json.loads((ROOT/'installer/modules.json').read_text())['modules']
  for m in ('netpause','netfec'):self.assertEqual(c[m],{'default':'off','kind':'collector','requires':[],'entry':m+',NETWORK'})
 def test_runtime_worker(self):
  if not shutil.which('jq'):self.skipTest('jq unavailable')
  for m in ('netpause','netfec'):
   for mode in ('shell','collector'):
    out=self.base/(m+mode);r=subprocess.run(['bash',str(ROOT/'runtime/worker.sh'),m,mode,str(self.prefix),str(out),'2','1',''],env=self.env,capture_output=True,text=True,timeout=15);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads((out/'result.json').read_text())['status'],'PASS')
def malformed(data,module='netfec'):
 def test(self):
  self.reply(module,data);x=self.runmod(module,code=74);self.assertTrue(all(r['value'] is None for r in x['records']));self.assertEqual(bytes.fromhex(x['source_snapshots'][0]['exchanges'][1]['response_hex']),data);self.assertFalse(x['source_snapshots'][0]['exchanges'][2]['attempted'])
 return test
base=fec();cases={'short_header':b'bad','wrong_interface':message('netfec',b'',name='eth9'),'wrong_sequence':fec()[:8]+u32(78)+fec()[12:],'wrong_family':base[:4]+b'\x2b\0'+base[6:],'wrong_command':base[:16]+b'\x1f'+base[17:],'multipart':base[:6]+b'\x02\0'+base[8:],'multi_message':base+base,'short_array':message('netfec',attr(5,attr(2,b'123'))),'too_many_lanes':fec(arrays=(tuple(range(34)),(),())),'short_scalar':message('netfec',attr(4,b'abc')),'bad_active_range':fec(active=4096),'duplicate_active':message('netfec',attr(4,u32(50))*2),'duplicate_header':message('netfec',attr(1,b'')),'short_attrs':message('netfec',b'x'),'nested_short':message('netfec',attr(5,b'x')),'zero_ACK':error_reply(0),'bad_bitset':fec(modes=attr(2,u32(75))),'mask_wrong_length':fec(modes=attr(2,u32(75))+attr(4,b'0'*12)+attr(5,b'0'*8)),'mask_and_nomask':fec(modes=attr(1,b'')+attr(2,u32(75))+attr(4,b'0'*12)+attr(5,b'0'*12)),'duplicate_stat':message('netfec',attr(5,attr(2,b'0'*8)*2))}
for name,data in cases.items():setattr(EthtoolNetwork,'test_malformed_'+name,malformed(data))
setattr(EthtoolNetwork,'test_malformed_pause_size',malformed(message('netpause',attr(2,u32(1))),'netpause'))
setattr(EthtoolNetwork,'test_malformed_pause_duplicate',malformed(message('netpause',attr(2,b'1')*2),'netpause'))
if __name__=='__main__':unittest.main()
