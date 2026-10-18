#!/usr/bin/env python3
"""Development-only independent ethtool GET before/after and raw wire oracle."""
import argparse,hashlib,json,os,re,struct,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--prefix',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
def save(p,v):p.write_text(json.dumps(v,indent=2)+'\n')
def run(path,args,env=None):
 save(path.with_suffix('.command.json'),{'argv':list(map(str,args)),'started_ns':time.time_ns()})
 try:r=subprocess.run(args,capture_output=True,text=True,env=env,timeout=20);rc,out,err=r.returncode,r.stdout,r.stderr
 except FileNotFoundError:rc,out,err=127,'','command unavailable'
 path.with_suffix('.stdout').write_text(out);path.with_suffix('.stderr').write_text(err);path.with_suffix('.rc').write_text(str(rc)+'\n');return rc,out,err
def attrs(data):
 pos=0;out={}
 while pos<len(data):
  size,t=struct.unpack_from('=HH',data,pos);assert size>=4 and pos+size<=len(data);kind=t&16383
  if kind:out[kind]=data[pos+4:pos+size]
  pos+=(size+3)//4*4
 assert pos==len(data);return out
def number(raw):return int.from_bytes(raw,'little' if struct.pack('=I',1)[0] else 'big')
def raw_expected(frame):
 s=frame['source_snapshots'][0];w=s['exchanges'];m=frame['module'];values={};lanes={};is_ioctl=w[2]['attempted'] and not w[2]['errno']
 for v in w:
  assert len(bytes.fromhex(v['response_hex']))==v['received_bytes']
  if v['attempted'] and v['api']!='SIOCETHTOOL_GET' and v['response_hex']:
   b=bytes.fromhex(v['response_hex']);assert len(b)==struct.unpack_from('=I',b)[0];assert v['sequence']==struct.unpack_from('=I',b,8)[0] and v['peer_pid']==0
 if s['errno']:return values,lanes
 if is_ioctl:
  cmd,active,config,reserved=struct.unpack('=IIII',bytes.fromhex(w[2]['response_hex']))
  if m=='netpause':values.update(autoneg=str(int(bool(active))),rx_pause=str(int(bool(config))),tx_pause=str(int(bool(reserved))))
  else:
   values.update(ioctl_active_fec_mask=str(active),ioctl_configured_fec_mask=str(config))
   if not config&1:
    for i,n in enumerate(('auto_selection','configured_off','configured_rs','configured_baser','configured_llrs')):values[n]=str(int(bool(config&(1<<(i+1)))))
   indexes={4:49,8:50,16:51,32:74}
   if active in indexes:values['active_mode_index']=str(indexes[active]);values['active_mode']={4:'Off',8:'RS',16:'BASER',32:'LLRS'}[active]
  return values,lanes
 b=bytes.fromhex(w[1]['response_hex']);t=attrs(b[20:]);header=attrs(t[1]);assert (2 not in header or header[2].rstrip(b'\0').decode()==s['entity']);assert 1 not in header or number(header[1])==s['ifindex_before']
 if m=='netpause':
  for i,n in ((2,'autoneg'),(3,'rx_pause'),(4,'tx_pause')):
   if i in t:values[n]=str(int(bool(number(t[i]))))
  if 6 in t:values['stats_source']=str(number(t[6]))
  st=attrs(t.get(5,b''))
  for i,n in ((2,'tx_pause_frames'),(3,'rx_pause_frames')):
   if i in st:values[n]=str(number(st[i]))
 else:
  if 2 in t:
   bs=attrs(t[2]);size=number(bs[2]);bitmap=bs[4];mask=bs.get(5)
   for bit,name in ((49,'configured_off'),(50,'configured_rs'),(51,'configured_baser'),(74,'configured_llrs')):
    if bit<size and (mask is None or number(mask[bit//32*4:bit//32*4+4])&(1<<(bit%32))):values[name]=str(int(bool(number(bitmap[bit//32*4:bit//32*4+4])&(1<<(bit%32)))))
  if 3 in t:values['auto_selection']=str(int(bool(number(t[3]))))
  if 4 in t:
   index=number(t[4]);values['active_mode_index']=str(index);values['active_mode']={49:'Off',50:'RS',51:'BASER',74:'LLRS'}.get(index,'UNKNOWN')
  st=attrs(t.get(5,b''))
  for i,name in ((2,'corrected_blocks'),(3,'uncorrectable_blocks'),(4,'corrected_bits')):
   if i not in st or not st[i]:continue
   v=list(struct.unpack('='+str(len(st[i])//8)+'Q',st[i]));values[name]=str(v[0]);lanes[name]=v
   if len(v)>1:
    values[name+'_lane_count']=str(len(v)-1);values[name+'_lane_breakdown']='available'
    values.update({f'lane_{j}_{name}':str(x) for j,x in enumerate(v[1:])})
 return values,lanes
def mode(text):
 m=re.search(r'^\s*Active FEC encoding[s]?:\s*(.+)$',text,re.M|re.I)
 if not m:return None
 token=m[1].strip().upper().replace('-','');return {'OFF':'Off','RS':'RS','BASER':'BASER','LLRS':'LLRS'}.get(token)
def pause_values(text):
 values={}
 for k,n in [('Autonegotiate','autoneg'),('RX','rx_pause'),('TX','tx_pause')]:
  m=re.search(r'^\s*'+k+r':\s*(on|off)\s*$',text,re.M|re.I)
  if m:values[n]='1' if m[1].lower()=='on' else '0'
 return values
cases=[];failures=[]
for interface in sorted(Path('/sys/class/net').iterdir()):
 if interface.name=='lo' or (interface/'type').read_text().strip() not in ('1','32'):continue
 name=interface.name;base=a.output/name;base.mkdir();env=dict(os.environ,REMORA_ETH_INTERFACES=name,REMORA_BIN=str(a.prefix/'bin'))
 cap=next(l.split(':')[1].strip() for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('CapEff:'));assert os.geteuid()!=0 and int(cap,16)==0
 save(base/'context.json',{'interface':name,'type':(interface/'type').read_text().strip(),'operstate':(interface/'operstate').read_text().strip(),'driver':str((interface/'device/driver').resolve()),'uid':os.geteuid(),'cap_eff':cap})
 for module in ('netpause','netfec'):
  for implementation in ('collector','shell'):
   for rep in range(3):
    case=base/f'{module}-{implementation}-{rep}';case.mkdir();raw=case/'raw';raw.mkdir()
    option='-a' if module=='netpause' else '--show-fec';cli=['ethtool',option,name];stats=['ethtool','-I',option,name]
    before=run(case/'cli-before',cli);before_stats=run(case/'stats-before',stats);index_before=(interface/'ifindex').read_text().strip()
    command=[str(a.prefix/f'bin/binary_data_collectors/data_collect_{module}'),'node',str(raw),str(raw)]
    if implementation=='shell':command=['bash','-c','source "$1"; init_module_$2 node "$3" "$3" && collect_data_$2 node "$3" "$3"','test',str(a.prefix/f'bin/modules/{module}'),module,str(raw)]
    rc,_,_=run(case/'collect',command,env);index_after=(interface/'ifindex').read_text().strip();after=run(case/'cli-after',cli);after_stats=run(case/'stats-after',stats)
    try:
     frame=json.loads((raw/f'{module}_node.jsonl').read_text());assert index_before==index_after
     values,lanes=raw_expected(frame);records={r['metric']:r for r in frame['records']};actual={n:r['value'] for n,r in records.items() if r['status']=='OK'};assert actual==values,(actual,values)
     assert all(r['value'] is None and r['errno'] for r in records.values() if r['status']!='OK');checks=0;skips=[]
     if frame['readable_primary']:
      assert rc==0 and frame['status']=='OK';s=frame['source_snapshots'][0];assert s['ifindex_before']==s['ifindex_after']==int(index_before) and s['driver_before']==s['driver_after']
      if not before[0] and not after[0]:
       if module=='netpause':
        bv,av=pause_values(before[1]),pause_values(after[1]);assert bv==av,'pause settings changed during bracket'
        for key,value in bv.items():
         if key in values:assert values[key]==value;checks+=1
       else:
        bmode,amode=mode(before[1]),mode(after[1]);assert bmode==amode,'FEC mode changed during bracket'
        if bmode is not None and 'active_mode' in values:assert bmode==values['active_mode'];checks+=1
        else:skips.append('CLI_active_mode_not_exposed_or_not_normalized')
      else:skips.append('independent_CLI_GET_unavailable_or_unsupported')
      # Compare any standard statistic arrays CLI exposes, retaining exact missing breakdown.
      for metric,v in lanes.items():
       label={'corrected_blocks':'corrected_blocks','uncorrectable_blocks':'uncorrectable_blocks','corrected_bits':'corrected_bits'}[metric]
       def vector(text):
        found=re.search(r'^\s*'+label+r':\s*(.*?)\s*$',text,re.M)
        return [int(x) for x in re.findall(r'\d+',found[1])] if found else None
       low,high=vector(before_stats[1]),vector(after_stats[1])
       if not before_stats[0] and not after_stats[0] and low is not None and high is not None and len(low)==len(high)==len(v):
        assert all(l<=x<=h for l,x,h in zip(low,v,high)),'counter reset/change during independent bracket';checks+=len(v)
       else:skips.append('CLI_'+metric+'_array_not_exposed')
      result={'status':'PASS','readable_primary':frame['readable_primary'],'readable_fields':len(values),'raw_checks':len(values),'cli_checks':checks,'oracle_skips':skips}
     else:
      assert rc==69 and frame['status']=='UNSUPPORTED';result={'status':'SKIP','reason':sorted({r['availability_reason'] for r in records.values() if r['status']!='OK'}),'errno':sorted({r['errno'] for r in records.values() if r['status']!='OK'}),'raw_checks':len(values),'cli_checks':0}
     digest=hashlib.sha256((raw/f'{module}_node.jsonl').read_bytes()).hexdigest();frc,_,_=run(case/'finalize',['bash','-c','source "$1"; finalize_module_$2 node "$3" "$3"','test',str(a.prefix/f'bin/modules/{module}'),module,str(raw)],env);assert frc==0 and digest==hashlib.sha256((raw/f'{module}_node.jsonl').read_bytes()).hexdigest()
    except Exception as error:result={'status':'FAIL','error':str(error)};failures.append(str(case))
    result.update(interface=name,module=module,implementation=implementation,repetition=rep,collector_rc=rc);save(case/'result.json',result);cases.append(result)
save(a.output/'summary.json',{'status':'FAIL' if failures else 'PASS','failures':failures,'cases':cases})
raise SystemExit(bool(failures))
