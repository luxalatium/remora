#!/usr/bin/env python3
"""Development-only read-only DCB oracle. Never part of installed runtime."""
import argparse,hashlib,json,os,shutil,struct,subprocess,time
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--prefix',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=False)
def save(path,data):path.write_text(json.dumps(data,indent=2)+'\n')
def command(path,args,env=None):
 path.parent.mkdir(parents=True,exist_ok=True);save(path.with_suffix('.command.json'),{'argv':list(map(str,args)),'started_ns':time.time_ns()})
 try:r=subprocess.run(args,env=env,capture_output=True,text=True,timeout=25);rc,out,err=r.returncode,r.stdout,r.stderr
 except FileNotFoundError:rc,out,err=127,'','command unavailable'
 path.with_suffix('.stdout').write_text(out);path.with_suffix('.stderr').write_text(err);path.with_suffix('.rc').write_text(str(rc)+'\n');return rc,out,err
def attrs(data):
 pos=0
 while pos<len(data):
  size,kind=struct.unpack_from('=HH',data,pos);assert size>=4 and pos+size<=len(data)
  yield kind&16383,data[pos+4:pos+size];pos+=(size+3)//4*4
 assert pos==len(data)
def raw_fields(s):
 raw=bytes.fromhex(s['response_hex']);size,typ,flags,seq,port=struct.unpack_from('=IHHII',raw);assert size==len(raw) and seq==s['sequence'] and s['peer_pid']==0
 if typ==2:return {},-struct.unpack_from('=i',raw,16)[0]
 assert typ==78 and raw[17]==21
 top=dict(attrs(raw[20:]));assert top[1].rstrip(b'\0').decode()==s['entity'];nested=dict(attrs(top.get(13,b'')));fields={}
 if 2 in nested:
  x=nested[2];fields.update(pfc_cap=str(x[0]),pfc_enable_mask=str(x[1]));fields.update({f'priority_{i}_pfc_enabled':str((x[1]>>i)&1) for i in range(8)})
 if 1 in nested:
  x=nested[1];fields['ets_cap']=str(x[1]);fields.update({f'priority_{i}_to_tc':str(x[27+i]) for i in range(8)})
 return fields,0
summary=[];failures=[]
for iface in sorted(Path('/sys/class/net').iterdir()):
 if iface.name=='lo' or (iface/'type').read_text().strip() not in ('1','32'):continue
 name=iface.name;base=a.output/name;base.mkdir();env=dict(os.environ,REMORA_ETH_INTERFACES=name,REMORA_BIN=str(a.prefix/'bin'))
 save(base/'context.json',{'interface':name,'type':(iface/'type').read_text().strip(),'operstate':(iface/'operstate').read_text().strip(),'driver':str((iface/'device/driver').resolve()),'uid':os.geteuid(),'cap_eff':next(l.strip() for l in Path('/proc/self/status').read_text().splitlines() if l.startswith('CapEff:'))})
 for mode in ('collector','shell'):
  for rep in range(3):
   case=base/f'{mode}-{rep}';case.mkdir();raw=case/'raw';raw.mkdir()
   before={};after={}
   for group in ('pfc','ets'):before[group]=command(case/f'{group}-before',['dcb','-j',group,'show','dev',name])
   index_before=(iface/'ifindex').read_text().strip()
   args=[str(a.prefix/'bin/binary_data_collectors/data_collect_netdcb'),'node',str(raw),str(raw)]
   if mode=='shell':args=['bash','-c','source "$1"; init_module_netdcb node "$2" "$2" && collect_data_netdcb node "$2" "$2"','test',str(a.prefix/'bin/modules/netdcb'),str(raw)]
   rc,_,_=command(case/'collect',args,env)
   index_after=(iface/'ifindex').read_text().strip()
   for group in ('pfc','ets'):after[group]=command(case/f'{group}-after',['dcb','-j',group,'show','dev',name])
   command(case/'prototype',[str(a.output.parent/'dcb-probe'),name])
   try:
    frame=json.loads((raw/'netdcb_node.jsonl').read_text());assert index_before==index_after
    records={r['metric']:r for r in frame['records']};s=frame['source_snapshots'][0];assert len(records)==19
    if frame['readable_primary']:
     assert rc==0 and frame['status']=='OK';values,error=raw_fields(s);assert not error
     assert {k:r['value'] for k,r in records.items() if r['status']=='OK'}==values
     assert s['ifindex_before']==s['ifindex_after']==int(index_before)
     cli_checks=0;cli_skips=[]
     for group in ('pfc','ets'):
      b,e=before[group],after[group]
      if b[0] or e[0]:cli_skips.append({'group':group,'reason':'independent_cli_unavailable_or_GET_unsupported','before_rc':b[0],'after_rc':e[0]});continue
      bj,ej=json.loads(b[1]),json.loads(e[1]);assert bj==ej,'configuration changed during bracket'
      if group=='pfc' and 'pfc_cap' in values:
       assert values['pfc_cap']==str(bj['pfc_cap']);assert [values[f'priority_{i}_pfc_enabled']=='1' for i in range(8)]==bj['prio_pfc'];cli_checks+=9
      if group=='ets' and 'ets_cap' in values:
       assert values['ets_cap']==str(bj['ets_cap']);assert [int(values[f'priority_{i}_to_tc']) for i in range(8)]==bj['prio_tc'];cli_checks+=9
     result={'status':'PASS','readable':len(values),'raw_checks':len(values),'cli_checks':cli_checks,'cli_skips':cli_skips}
    else:
     assert rc==69 and frame['status']=='UNSUPPORTED';assert all(r['value'] is None and r['errno'] for r in records.values())
     if (iface/'type').read_text().strip()=='32':assert all(r['availability_reason']=='not_applicable_to_transport' for r in records.values())
     elif s['response_hex']:
      values,error=raw_fields(s)
      assert not values and (error or all(r['errno']==2 for r in records.values()))
     result={'status':'SKIP','reason':sorted(set(r['availability_reason'] for r in records.values())),'errno':sorted(set(r['errno'] for r in records.values())),'raw_checks':0,'cli_checks':0}
    # Finalizer is a no-op preserving raw evidence regardless of availability.
    digest=hashlib.sha256((raw/'netdcb_node.jsonl').read_bytes()).hexdigest()
    frc,_,_=command(case/'finalize',['bash','-c','source "$1"; finalize_module_netdcb node "$2" "$2"','test',str(a.prefix/'bin/modules/netdcb'),str(raw)],env)
    assert frc==0 and hashlib.sha256((raw/'netdcb_node.jsonl').read_bytes()).hexdigest()==digest
   except Exception as error:result={'status':'FAIL','error':str(error)};failures.append(str(case))
   result.update(interface=name,mode=mode,repetition=rep,collector_rc=rc);save(case/'result.json',result);summary.append(result)
save(a.output/'summary.json',{'cases':summary,'failures':failures,'status':'FAIL' if failures else 'PASS'})
raise SystemExit(bool(failures))
