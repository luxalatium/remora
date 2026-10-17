#!/usr/bin/env python3
"""Independent ethtool CLI oracle for allocated-compute qualification only."""
import argparse,hashlib,json,os,platform,re,subprocess,time
from pathlib import Path

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):
 with p.open('x') as f:json.dump(x,f,indent=2);f.write('\n')
def names():
 selection=os.environ.get('REMORA_ETH_INTERFACES')
 if selection is not None:return sorted(selection.split(','))
 return sorted(p.name for p in Path('/sys/class/net').iterdir() if p.name!='lo' and (p/'type').read_text().strip() in ('1','32'))
def observe():
 out={}
 for name in names():
  calls={}
  for label,flag in [('driver','-i'),('stats','-S')]:
   command=['ethtool',flag,name];r=subprocess.run(command,capture_output=True,text=True,timeout=15);calls[label]=dict(command=command,rc=r.returncode,stdout=r.stdout,stderr=r.stderr)
  values={}
  if calls['stats']['rc']==0:
   for line in calls['stats']['stdout'].splitlines():
    m=re.fullmatch(r'\s*([^\s:]+):\s*(\d+)\s*',line)
    if m:
     assert m[1] not in values
     values[m[1]]=int(m[2])
  out[name]=dict(calls=calls,values=values,type=(Path('/sys/class/net')/name/'type').read_text().strip(),state=(Path('/sys/class/net')/name/'operstate').read_text().strip())
 return dict(wall_ns=time.time_ns(),interfaces=out)
def expected(driver,name):
 # Independent interpretation checks; driver-specific fields do not use universal suffix guesses.
 if driver in ('mlx5_core','mlx5_core[ib_ipoib]'):
  if '_prio' in name:scope='physical_port_priority'
  elif re.fullmatch(r'rx\d+_buff_alloc_err',name):return 'events','netdev_receive_queue'
  elif re.fullmatch(r'tx\d+_(stopped|wake|dropped)',name):return ('packets' if name.endswith('dropped') else 'events'),'netdev_transmit_queue'
  elif name=='rx_out_of_buffer':return 'events','driver_receive_queue_aggregate'
  elif name in ('rx_buff_alloc_err','tx_queue_stopped','tx_queue_wake'):return 'events','netdev_aggregate'
  elif name in ('tx_queue_dropped','rx_oversize_pkts_buffer','rx_oversize_pkts_sw_drop'):return 'packets','netdev_aggregate'
  else:scope='physical_port'
  if name.endswith('pause_duration'):unit='microseconds'
  elif name in ('rx_bits_phy','rx_corrected_bits_phy'):unit='bits'
  elif name=='rx_pcs_symbol_err_phy':unit='symbols'
  elif name.endswith('pause') or name in ('rx_pause_ctrl_phy','tx_pause_ctrl_phy'):unit='frames'
  elif 'discard' in name:unit='packets'
  else:unit='events'
  return unit,scope
 if driver in ('ixgbe','igb'):
  if name=='rx_no_buffer_count':return 'native_count','driver_hardware'
  if re.fullmatch(r'rx_queue_\d+_drops',name):return 'packets','netdev_receive_queue'
  if name in ('rx_dropped','tx_dropped'):return 'packets','netdev_aggregate'
  return 'frames','physical_port_packet_buffer' if '_pb_' in name else 'physical_port'
 if driver=='tg3':
  if name in ('rx_discards','tx_discards'):return 'packets','driver_hardware'
  return ('events' if name=='rx_xoff_entered' else 'frames'),'physical_port'
 if driver=='bnxt_en':
  if '_total_' in name:return 'native_count','netdev_aggregate'
  if '_cos' in name:return ('bytes' if '_bytes_' in name else 'packets'),'physical_port_cos'
  unit='bits' if name=='rx_corrected_bits' else 'blocks' if '_fec_' in name else 'frames' if name.endswith('pause_frames') else 'packets' if name in ('rx_stat_discard','tx_stat_discard') else 'events'
  return unit,'physical_port'
 raise ValueError(driver)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--prefix',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args();a.output.mkdir(parents=True,exist_ok=False)
 env=dict(os.environ,REMORA_BIN=str(a.prefix/'bin'));env.pop('REMORA_NETWORK_SYSFS_ROOT',None);env.pop('REMORA_NIC_TEST_RACE',None)
 save(a.output/'environment.json',dict(platform=platform.platform(),job=env.get('SLURM_JOB_ID'),modules=env.get('LOADEDMODULES'),test_sha256=digest(Path(__file__))))
 matrix=[]
 for module in ('nicstats','linkhealth'):
  binary=a.prefix/f'bin/binary_data_collectors/data_collect_{module}';script=a.prefix/f'bin/modules/{module}'
  for mode in ('collector','shell'):
   case=a.output/(module+'-'+mode);case.mkdir();raw=case/'raw';raw.mkdir();failures=[];gaps=[];compared=0;rcs=[];skips={}
   save(case/'artifacts.json',dict(binary_sha256=digest(binary),module_sha256=digest(script)))
   command=[str(binary),'node',str(raw),str(raw)] if mode=='collector' else ['bash','-c','source "$1"; "collect_data_$2" node "$3" "$3"','test',str(script),module,str(raw)]
   save(case/'command.json',command)
   for rep in range(3):
    before=observe();save(case/f'before-{rep}.json',before)
    r=subprocess.run(command,capture_output=True,text=True,env=env,timeout=30);rcs.append(r.returncode);save(case/f'command-{rep}.json',dict(rc=r.returncode,stdout=r.stdout,stderr=r.stderr))
    after=observe();save(case/f'after-{rep}.json',after)
    if r.returncode not in (0,69):failures.append(dict(error='exit',rep=rep,rc=r.returncode));continue
    p=raw/f'{module}_node.jsonl'
    if not p.exists():failures.append(dict(error='no raw',rep=rep));continue
    sample=json.loads(p.read_text().splitlines()[-1]);assert sample['record_count']==len(sample['records']) and sample['schema']=='remora-network-resources/v1'
    sources={s['entity']:s for s in sample['source_snapshots']};entities={e['name']:e for e in sample['entities']}
    if sorted(entities)!=sorted(before['interfaces']):failures.append(dict(error='interface coverage',rep=rep))
    for iface,s in sources.items():
     if s['status']!='OK':skips[iface]=dict(reason='GET API unavailable',status=s['status'],errno=s['errno'],stage=s['stage']);continue
     if s['names_hex']!=s['names_after_hex'] or s['driver_info_hex']!=s['driver_info_after_hex'] or s['ifindex_before']!=s['ifindex_after']:failures.append(dict(error='unstable raw interpreted',rep=rep,interface=iface))
     b=bytes.fromhex(s['names_hex']);assert len(b)==s['count']*32
     parsed={b[i*32:(i+1)*32].split(b'\0',1)[0].decode('ascii'):int(v) for i,v in enumerate(s['values'])};assert len(parsed)==s['count']
     for v in (v for v in sample['records'] if v['entity']==iface):
      if v['kind']!='counter':skips[iface]=dict(reason='driver not allowlisted',driver=entities[iface]['driver']);continue
      name=v['metric']
      if v['status']!='OK':
       if v.get('availability_reason')=='not_applicable_to_ipoib_transport':
        assert entities[iface]['type']=='32' and name in parsed and v['raw']==str(parsed[name]) and v['value'] is None
        skips[iface+'/'+name]=dict(reason=v['availability_reason'],raw=v['raw']);continue
       if name in parsed:failures.append(dict(error='present raw reported missing',rep=rep,record=v))
       continue
      if name not in parsed or v['raw']!=str(parsed[name]) or v['value']!=str(parsed[name]):failures.append(dict(error='raw index/name/value mapping',rep=rep,record=v));continue
      if (v['unit'],v['scope'])!=expected(entities[iface]['driver'],name):failures.append(dict(error='units/scope',rep=rep,record=v))
      lo=before['interfaces'][iface]['values'].get(name);hi=after['interfaces'][iface]['values'].get(name)
      if lo is None or hi is None or hi<lo:gaps.append(dict(rep=rep,interface=iface,metric=name,reason='oracle missing/reset/wrap'));continue
      if not lo<=int(v['value'])<=hi:failures.append(dict(error='independent bracket',rep=rep,record=v,before=lo,after=hi))
      else:compared+=1
    time.sleep(.05)
   result=dict(module=module,implementation=mode,case='three-independent-source-brackets',status='FAIL' if failures else 'PARTIAL' if gaps else 'PASS' if compared else 'SKIP',comparisons=compared,failures=failures,gaps=gaps,returncodes=rcs,interface_skips=skips)
   matrix.append(result);save(case/'result.json',result)
   p=raw/f'{module}_node.jsonl';old=digest(p)
   r=subprocess.run(['bash','-c','source "$1"; "finalize_module_$2" node "$3" "$3"','test',str(script),module,str(raw)],env=env,capture_output=True,text=True,timeout=5)
   matrix.append(dict(module=module,implementation=mode,case='finalize-preserves-raw',status='PASS' if r.returncode==0 and digest(p)==old else 'FAIL',raw_sha256=old))
 save(a.output/'matrix.json',matrix);save(a.output/'checksums.json',{str(p.relative_to(a.output)):digest(p) for p in a.output.rglob('*') if p.is_file()})
 print(json.dumps(dict(counts={s:sum(c['status']==s for c in matrix) for s in ('PASS','PARTIAL','SKIP','FAIL')})))
 return int(any(c['status'] not in ('PASS','SKIP') for c in matrix))
if __name__=='__main__':raise SystemExit(main())
