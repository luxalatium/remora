"""Standard tests for Bash/C NIC collectors. Fixture backend never installed."""
import errno,fcntl,json,os,shlex,shutil,signal,subprocess,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MODS=('nicstats','linkhealth')
class NICNetwork(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmp=tempfile.TemporaryDirectory(prefix='network-nic-build-');cls.prefix=Path(cls.tmp.name)
  binary=cls.prefix/'bin/binary_data_collectors';binary.mkdir(parents=True);(cls.prefix/'bin/modules').mkdir()
  cc=shlex.split(os.environ.get('REMORA_NETWORK_TEST_CC',os.environ.get('CC','cc')))
  for m in MODS:
   r=subprocess.run(cc+['-std=c99','-O1','-Wall','-Wextra','-Werror','-DREMORA_NIC_TEST',str(ROOT/f'src/C_data_collectors_src/data_collect_{m}.c'),'-o',str(binary/f'data_collect_{m}')],capture_output=True,text=True)
   if r.returncode:raise RuntimeError(r.stderr)
   shutil.copy2(ROOT/f'src/modules/{m}',cls.prefix/'bin/modules')
 @classmethod
 def tearDownClass(cls):cls.tmp.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='network-nic-case-');self.base=Path(self.tmp.name);self.sys=self.base/'sys';(self.sys/'class/net').mkdir(parents=True)
  self.raw=self.base/'raw';self.raw.mkdir();self.env=dict(os.environ,REMORA_NETWORK_SYSFS_ROOT=str(self.sys),REMORA_BIN=str(self.prefix/'bin'));self.env.pop('REMORA_ETH_INTERFACES',None)
  self.put('eth0','mlx5_core','rx_out_of_buffer 12\nrx_corrected_bits_phy 24\nrx_global_pause_duration 35\nrx_prio3_pause 46\nrx0_buff_alloc_err 57\n')
 def tearDown(self):self.tmp.cleanup()
 def put(self,name,driver,data,type='1',state='up'):
  p=self.sys/'class/net'/name;p.mkdir(exist_ok=True);(p/'type').write_text(type+'\n');(p/'operstate').write_text(state+'\n');(p/'fixture').write_text('driver '+driver+'\n'+data);return p
 def runmod(self,m='nicstats',code=0,shell=False,check=False):
  args=[str(self.prefix/f'bin/binary_data_collectors/data_collect_{m}'),'node',str(self.raw),str(self.raw)]+(['--check'] if check else [])
  if shell:args=['bash','-c','source "$1"; "${2}_$3" node "$4" "$4"','test',str(self.prefix/f'bin/modules/{m}'),'init_module' if check else 'collect_data',m,str(self.raw)]
  r=subprocess.run(args,env=self.env,capture_output=True,text=True,timeout=10);self.assertEqual(r.returncode,code,r.stderr)
  p=self.raw/f'{m}_node.jsonl'
  if p.exists() and p.stat().st_size and p.read_bytes().endswith(b'\n'):
   try:return json.loads(p.read_text().splitlines()[-1])
   except json.JSONDecodeError:
    if not code:raise
 def field(self,x,name,entity='eth0'):return next(r for r in x['records'] if r['metric']==name and r['entity']==entity)
 def test_units_and_scopes(self):
  x=self.runmod();self.assertEqual(self.field(x,'rx_out_of_buffer')['unit'],'events');self.assertEqual(self.field(x,'rx0_buff_alloc_err')['scope'],'netdev_receive_queue')
  x=self.runmod('linkhealth');self.assertEqual(self.field(x,'rx_corrected_bits_phy')['unit'],'bits');self.assertEqual(self.field(x,'rx_global_pause_duration')['unit'],'microseconds');self.assertEqual(self.field(x,'rx_prio3_pause')['scope'],'physical_port_priority')
 def test_source_mapping_and_padding(self):
  x=self.runmod();s=x['source_snapshots'][0];names=bytes.fromhex(s['names_hex']);self.assertEqual(len(names),32*s['count']);self.assertEqual(names[:32].split(b'\0')[0],b'rx_out_of_buffer');self.assertEqual(s['values'][0],'12');self.assertEqual(s['names_hex'],s['names_after_hex'])
 def test_uint64_exact(self):
  self.put('eth0','mlx5_core','rx_out_of_buffer 18446744073709551615\n');x=self.runmod();self.assertEqual(self.field(x,'rx_out_of_buffer')['value'],str(2**64-1))
 def test_zero_not_absent(self):
  self.put('eth0','mlx5_core','rx_out_of_buffer 0\n');x=self.runmod();self.assertEqual(self.field(x,'rx_out_of_buffer')['value'],'0');self.assertEqual(self.field(x,'tx_queue_dropped')['status'],'ABSENT');self.assertIsNone(self.field(x,'tx_queue_dropped')['value'])
 def test_reorder_names_mapping(self):
  self.put('eth0','mlx5_core','rx0_buff_alloc_err 57\nrx_out_of_buffer 12\n');x=self.runmod();self.assertEqual(self.field(x,'rx_out_of_buffer')['raw'],'12')
 def test_unknown_driver_not_name_inference(self):
  self.put('eth0','other','rx_out_of_buffer 12\n');x=self.runmod(code=69);self.assertEqual(x['source_snapshots'][0]['values'],['12']);self.assertEqual(x['records'][0]['status'],'UNSUPPORTED')
 def test_mlx_ipoib_family(self):
  self.put('ib0','mlx5_core[ib_ipoib]','rx_out_of_buffer 99\n',type='32');x=self.runmod();self.assertEqual(self.field(x,'rx_out_of_buffer','ib0')['value'],'99')
 def test_plain_ipoib_unsupported_with_other_readable(self):
  self.put('ib0','ib_ipoib','rx_packets 99\n',type='32');x=self.runmod();self.assertEqual(self.field(x,'driver_allowlist','ib0')['status'],'UNSUPPORTED')
 def test_down_interfaces_included(self):
  self.put('eth1','tg3','rx_discards 9\n',state='down');x=self.runmod();self.assertEqual(self.field(x,'rx_discards','eth1')['value'],'9')
 def test_loopback_and_other_type_excluded(self):
  self.put('lo','mlx5_core','rx_out_of_buffer 3\n',type='772');self.put('tun0','mlx5_core','rx_out_of_buffer 3\n',type='65534');self.assertEqual(len(self.runmod()['entities']),1)
 def test_exact_selection_sorted(self):
  self.put('eth1','tg3','rx_discards 9\n');self.env['REMORA_ETH_INTERFACES']='eth1';self.assertEqual([x['name'] for x in self.runmod()['entities']],['eth1'])
 def test_duplicate_selection(self):self.env['REMORA_ETH_INTERFACES']='eth0,eth0';self.runmod(code=65)
 def test_invalid_selection(self):
  for s in ('../eth0','eth0\neth1','','thisnameistoolong','eth0 eth1','eth0,',',eth0','eth0,,eth1'):
   with self.subTest(s=s):self.env['REMORA_ETH_INTERFACES']=s;self.runmod(code=65)
 def test_explicit_absent(self):self.env['REMORA_ETH_INTERFACES']='missing';self.runmod(code=69,check=True);x=self.runmod(code=69);self.assertEqual(x['records'][0]['status'],'ABSENT')
 def test_denial_partial(self):
  self.put('eth0','mlx5_core','error 13\n');x=self.runmod(code=74);self.assertTrue(all(r['value'] is None and r['status']=='DENIED' for r in x['records']))
 def test_optional_unsupported(self):self.put('eth0','mlx5_core','error '+str(errno.EOPNOTSUPP)+'\n');x=self.runmod(code=69);self.assertEqual(x['status'],'UNSUPPORTED')
 def test_schema_race_keeps_raw_no_values_interpreted(self):
  self.env['REMORA_NIC_TEST_RACE']='1';x=self.runmod(code=74);self.assertNotEqual(x['source_snapshots'][0]['names_hex'],x['source_snapshots'][0]['names_after_hex']);self.assertTrue(all(r['value'] is None for r in x['records']))
 def test_duplicate_stats_malformed(self):self.put('eth0','mlx5_core','rx_out_of_buffer 1\nrx_out_of_buffer 2\n');self.runmod(code=74)
 def test_uint64_overflow_and_signed(self):
  for s in ('18446744073709551616','-1','1.5'):
   with self.subTest(s=s):self.put('eth0','mlx5_core','rx_out_of_buffer '+s+'\n');self.runmod(code=74)
 def test_counter_reset_preserved(self):
  self.put('eth0','mlx5_core','rx_out_of_buffer 100\n');self.runmod();self.put('eth0','mlx5_core','rx_out_of_buffer 0\n');self.assertEqual(self.field(self.runmod(),'rx_out_of_buffer')['value'],'0')
 def test_all_drivers(self):
  for driver,name,module,unit in [('igb','rx_queue_7_drops','nicstats','packets'),('ixgbe','tx_pb_7_pxoff','linkhealth','frames'),('tg3','rx_xoff_entered','linkhealth','events'),('bnxt_en','rx_fec_uncorrectable_blocks','linkhealth','blocks')]:
   with self.subTest(driver=driver):self.put('eth0',driver,name+' 23\n');x=self.runmod(module);self.assertEqual(self.field(x,name)['unit'],unit)
 def test_bnxt_cos_bytes(self):
  self.put('eth0','bnxt_en','rx_discard_bytes_cos7 90\n');self.assertEqual(self.field(self.runmod(),'rx_discard_bytes_cos7')['unit'],'bytes')
 def test_pattern_index_bounds(self):
  self.put('eth0','ixgbe','rx_pb_9_pxon 9\nrx_pb_7_pxon 7\n');x=self.runmod('linkhealth');self.assertFalse(any(r['metric']=='rx_pb_9_pxon' for r in x['records']))
 def test_pattern_canonical_digits(self):
  self.put('eth0','igb','rx_queue_007_drops 7\nrx_queue_7_drops 8\n');x=self.runmod();self.assertFalse(any(r['metric']=='rx_queue_007_drops' for r in x['records']))
 def test_interface_limit(self):
  for i in range(1,17):self.put('eth'+str(i),'tg3','rx_discards 1\n')
  x=self.runmod(code=74);self.assertEqual(x['limitation'],'interface_limit')
 def test_stat_limit(self):
  self.put('eth0','mlx5_core',''.join(f'unknown{i} 1\n' for i in range(16385)));x=self.runmod(code=74);self.assertEqual(x['limitation'],'driver_stat_limit')
 def test_shell_native_parity(self):
  for m in MODS:
   a=self.runmod(m,shell=True);b=self.runmod(m);self.assertEqual(a['records'],b['records']);self.assertEqual(a['source_snapshots'],b['source_snapshots'])
 def test_init_discovery_only_no_truncation(self):
  self.runmod();p=self.raw/'nicstats_node.jsonl';old=p.read_bytes();self.put('eth0','mlx5_core','error 13\n');self.runmod(shell=True,check=True);self.assertEqual(old,p.read_bytes())
 def test_empty_discovery(self):shutil.rmtree(self.sys/'class/net/eth0');self.runmod(code=69)
 def test_finalizers_preserve_raw(self):
  for m in MODS:
   self.runmod(m);p=self.raw/f'{m}_node.jsonl';old=p.read_bytes();r=subprocess.run(['bash','-c','source "$1"; "finalize_module_$2" node "$3" "$3"','test',str(self.prefix/f'bin/modules/{m}'),m,str(self.raw)],env=self.env);self.assertEqual(r.returncode,0);self.assertEqual(old,p.read_bytes())
 def test_output_symlink(self):
  target=self.base/'target';target.write_text('keep');(self.raw/'nicstats_node.jsonl').symlink_to(target);self.runmod(code=74);self.assertEqual(target.read_text(),'keep')
 def test_output_hardlink(self):
  target=self.base/'target';target.write_text('keep\n')
  try:os.link(target,self.raw/'nicstats_node.jsonl')
  except OSError as e:
   if e.errno in (errno.EPERM,errno.EOPNOTSUPP,errno.EXDEV,errno.ENOSYS):self.skipTest('fixture filesystem denies hardlink creation')
   raise
  self.runmod(code=74);self.assertEqual(target.read_text(),'keep\n')
 def test_output_fifo(self):os.mkfifo(self.raw/'nicstats_node.jsonl');self.runmod(code=74)
 def test_lock_contention(self):
  with (self.raw/'nicstats_node.jsonl.lock').open('w') as f:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);self.runmod(code=75)
 def test_pending_marker(self):(self.raw/'nicstats_node.jsonl.pending').touch();self.runmod(code=74)
 def test_incomplete_output(self):
  p=self.raw/'nicstats_node.jsonl';p.write_text('{partial');self.runmod(code=74);self.assertEqual(p.read_text(),'{partial')
 def test_sigterm_preserves_pending(self):
  ready=self.base/'ready';self.put('eth0','mlx5_core','signal_wait '+str(ready)+'\n');child=subprocess.Popen([str(self.prefix/'bin/binary_data_collectors/data_collect_nicstats'),'node',str(self.raw),str(self.raw)],env=self.env)
  try:
   until=time.monotonic()+3
   while not ready.exists() and time.monotonic()<until:time.sleep(.01)
   self.assertTrue(ready.exists());child.send_signal(signal.SIGTERM);self.assertEqual(child.wait(timeout=3),-signal.SIGTERM)
  finally:
   if child.poll() is None:child.kill();child.wait()
  self.assertTrue((self.raw/'nicstats_node.jsonl.pending').exists());self.runmod(code=74)
 def test_actual_runtime_worker(self):
  if not shutil.which('jq'):self.skipTest('jq unavailable')
  for m in MODS:
   for mode in ('shell','collector'):
    path=self.base/(m+mode);r=subprocess.run(['bash',str(ROOT/'runtime/worker.sh'),m,mode,str(self.prefix),str(path),'2','1',''],env=self.env,capture_output=True,text=True,timeout=15);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads((path/'result.json').read_text())['status'],'PASS')
if __name__=='__main__':unittest.main()
