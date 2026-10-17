#!/usr/bin/env python3
"""Independent proc oracle on allocated Linux compute; never installed."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time

FIELDS={'Tcp':dict.fromkeys('InSegs OutSegs RetransSegs InErrs OutRsts'.split(),'segments'),
        'Udp':dict.fromkeys('InDatagrams OutDatagrams InErrors RcvbufErrors SndbufErrors'.split(),'datagrams'),
        'TcpExt':dict(dict.fromkeys('ListenDrops ListenOverflows TCPBacklogDrop TCPTimeouts'.split(),'events'),TCPSynRetrans='segments')}
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def observe(module):
    readings={};values={};units={};widths={}
    for source in ('net/softnet_stat',) if module=='softnet' else ('net/snmp','net/netstat'):
        raw=(Path('/proc')/source).read_text();readings[source]=raw
        lines=raw.splitlines()
        if module=='softnet':
            for line in lines:
                fields=line.split()
                if len(fields) not in (13,15):raise RuntimeError('unsupported oracle layout: '+str(len(fields)))
                numbers=[int(x,16) for x in fields];cpu='cpu.'+str(numbers[12]);widths[cpu]=len(fields)
                for i,key in enumerate(('processed','dropped','time_squeeze')):
                    identity=(cpu,key);assert identity not in values;values[identity]=numbers[i];units[identity]='events' if i==2 else 'packets'
        else:
            assert len(lines)%2==0
            for i in range(0,len(lines),2):
                names=lines[i].split();data=lines[i+1].split();assert names[0]==data[0] and len(names)==len(data)
                group=names[0].rstrip(':')
                if group not in FIELDS:continue
                parsed=dict(zip(names[1:],data[1:]))
                for key,unit in FIELDS[group].items():
                    if key in parsed:values[(group,key)]=int(parsed[key]);units[(group,key)]=unit
    return {'wall_ns':time.time_ns(),'readings':readings,'values':{'/'.join(k):v for k,v in values.items()},'units':{'/'.join(k):v for k,v in units.items()},'widths':widths}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prefix',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    a.output.mkdir(parents=True,exist_ok=False);env=dict(os.environ,REMORA_BIN=str(a.prefix/'bin'));env.pop('REMORA_NETWORK_PROC_ROOT',None)
    save(a.output/'environment.json',dict(platform=platform.platform(),job=env.get('SLURM_JOB_ID'),modules=env.get('LOADEDMODULES'),test_sha256=digest(Path(__file__))))
    matrix=[]
    for module in ('netprotocol','softnet'):
        binary=a.prefix/f'bin/binary_data_collectors/data_collect_{module}';script=a.prefix/f'bin/modules/{module}'
        for mode in ('collector','shell'):
            case=a.output/(module+'-'+mode);case.mkdir();raw=case/'raw';raw.mkdir();failures=[];gaps=[];compared=0;rcs=[]
            save(case/'artifacts.json',dict(binary_sha256=digest(binary),module_sha256=digest(script)))
            command=[str(binary),'node',str(raw),str(raw)] if mode=='collector' else ['bash','-c','source "$1"; "collect_data_$2" node "$3" "$3"','test',str(script),module,str(raw)]
            save(case/'command.json',command)
            for rep in range(3):
                before=observe(module);save(case/f'before-{rep}.json',before)
                r=subprocess.run(command,capture_output=True,text=True,env=env,timeout=30);rcs.append(r.returncode)
                save(case/f'command-{rep}.json',dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr))
                after=observe(module);save(case/f'after-{rep}.json',after)
                if r.returncode not in (0,69):failures.append(dict(error='exit',rep=rep,rc=r.returncode));continue
                sample=json.loads((raw/f'{module}_node.jsonl').read_text().splitlines()[-1]);source={s['source']:s for s in sample['source_snapshots']}
                assert sample['record_count']==len(sample['records']) and sample['schema']=='remora-network-resources/v1'
                values=[x for x in sample['records'] if x['kind']=='counter'];actual={x['entity']+'/'+x['metric'] for x in values}
                common=set(before['values']) & set(after['values']);expected=set(before['values'])|set(after['values'])
                if not common<=actual<=expected:failures.append(dict(error='coverage',missing=sorted(common-actual),extra=sorted(actual-expected)))
                for v in values:
                    key=v['entity']+'/'+v['metric'];lo=before['values'].get(key);hi=after['values'].get(key)
                    if lo is None or hi is None or hi<lo:gaps.append(dict(rep=rep,field=key,reason='CPU hotplug/reset/wrap'));continue
                    if v['status']!='OK' or not lo<=int(v['value'])<=hi:failures.append(dict(error='bracket/status',record=v,before=lo,after=hi));continue
                    if v['unit']!=before['units'][key] or v['scope']!=('host_cpu' if module=='softnet' else 'network_namespace'):failures.append(dict(error='unit/scope',record=v))
                    # Parse collector's preserved source independently; never use its value as sole oracle.
                    body=source[v['source']]['raw'].splitlines();parsed={}
                    if module=='softnet':
                        for line in body:
                            row=line.split();cpu='cpu.'+str(int(row[12],16))
                            for i,name in enumerate(('processed','dropped','time_squeeze')):parsed[cpu+'/'+name]=(int(row[i],16),row[i])
                    else:
                        for i in range(0,len(body),2):
                            names=body[i].split();nums=body[i+1].split();group=names[0].rstrip(':')
                            for name,num in zip(names[1:],nums[1:]):parsed[group+'/'+name]=(num,num)
                    value,token=parsed[key]
                    if int(v['value'])!=int(value) or v['raw']!=token:failures.append(dict(error='preserved source mapping',record=v,token=token))
                    else:compared+=1
                time.sleep(.05)
            result=dict(module=module,implementation=mode,case='three-independent-source-brackets',status='FAIL' if failures else 'PASS' if compared and not gaps else 'PARTIAL',comparisons=compared,failures=failures,gaps=gaps,returncodes=rcs)
            matrix.append(result);save(case/'result.json',result)
            file=raw/f'{module}_node.jsonl';old=digest(file)
            r=subprocess.run(['bash','-c','source "$1"; "finalize_module_$2" node "$3" "$3"','test',str(script),module,str(raw)],env=env,capture_output=True,text=True,timeout=5)
            matrix.append(dict(module=module,implementation=mode,case='finalize-preserves-raw',status='PASS' if r.returncode==0 and digest(file)==old else 'FAIL',raw_sha256=old))
    save(a.output/'matrix.json',matrix)
    save(a.output/'checksums.json',{str(p.relative_to(a.output)):digest(p) for p in a.output.rglob('*') if p.is_file()})
    print(json.dumps(dict(counts={s:sum(c['status']==s for c in matrix) for s in ('PASS','PARTIAL','SKIP','FAIL')})))
    return int(any(c['status']!='PASS' for c in matrix))
if __name__=='__main__':raise SystemExit(main())
