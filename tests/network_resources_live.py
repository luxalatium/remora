#!/usr/bin/env python3
"""Allocated-compute qualification only. Development harness, never installed."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time

MODULES=('netinfo','neterrors','ibcounters','mlxcounters','opacounters')
SELECTORS=('REMORA_ETH_INTERFACES','REMORA_IB_PORTS','REMORA_OPA_PORTS')
NET='ifindex iflink type flags operstate carrier mtu speed duplex address addr_assign_type'.split()
PORT='state phys_state link_layer rate lid sm_lid cap_mask active_width active_speed'.split()
DEVICE='node_guid sys_image_guid fw_ver node_type hca_type board_id'.split()
PCI='numa_node local_cpulist current_link_speed current_link_width vendor device driver'.split()

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x):p.write_text(json.dumps(x,indent=2)+'\n')
def read(path,link=False):
    try:
        if link:return {'raw':os.readlink(path),'errno':0}
        with open(path,'rb',buffering=0) as f:raw=f.read(4096)
        return {'raw':raw.decode(),'errno':0}
    except OSError as e:return {'raw':None,'errno':e.errno}

def sources():
    # Independently enumerate visible devices and source attributes, without collector output.
    items={}
    def add(p,link=False):items[str(p.relative_to('/sys'))]=link
    for dev in sorted(Path('/sys/class/net').iterdir()):
        for k in NET:add(dev/k)
        for k in PCI:add(dev/'device'/k,k=='driver')
        add(dev/'device',True)
        for p in sorted((dev/'statistics').glob('*')):add(p)
    fabric=Path('/sys/class/infiniband')
    if fabric.exists():
        for dev in sorted(fabric.iterdir()):
            for k in DEVICE:add(dev/k)
            for port in sorted((dev/'ports').iterdir()):
                for k in PORT:add(port/k)
                for k in PCI:add(dev/'device'/k,k=='driver')
                add(dev/'device',True)
                gid=port/'gid_attrs/ndevs'
                if gid.is_dir():
                    for p in sorted(gid.iterdir()):add(p)
                else:add(gid)
                for group in ('counters','counters_ext','hw_counters'):
                    for p in sorted((port/group).glob('*')):add(p)
    if len(items)>8192:raise RuntimeError('bounded oracle source limit exceeded')
    return items

def observe(items):
    return {'wall':time.time_ns(),'monotonic':time.monotonic_ns(),'readings':{p:read(Path('/sys')/p,link) for p,link in items.items()}}

def expected(module):
    names=[]
    if module in ('netinfo','neterrors'):
        for p in Path('/sys/class/net').iterdir():
            if module=='netinfo' or read(p/'type')['raw'].strip()=='1':names.append(p.name)
    if module!='neterrors' and Path('/sys/class/infiniband').exists():
        for dev in Path('/sys/class/infiniband').iterdir():
            driver=read(dev/'device/driver',True)
            family=Path(driver['raw']).name if not driver['errno'] else None
            mlx=family in ('mlx5_core','mlx4_core') if family else dev.name.startswith(('mlx5_','mlx4_'))
            hfi=family=='hfi1' if family else dev.name.startswith('hfi1_')
            if module=='mlxcounters' and not mlx or module=='opacounters' and not hfi:continue
            for port in (dev/'ports').iterdir():
                if module=='ibcounters' and read(port/'link_layer')['raw'].strip()!='InfiniBand':continue
                names.append(dev.name+'/'+port.name)
    return sorted(names)

def main():
    a=argparse.ArgumentParser();a.add_argument('--prefix',type=Path,required=True);a.add_argument('--output',type=Path,required=True);args=a.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,REMORA_BIN=str(args.prefix/'bin'))
    for k in (*SELECTORS,'REMORA_NETWORK_SYSFS_ROOT'):env.pop(k,None)
    inventory=sources();save(args.output/'oracle-source-inventory.json',inventory)
    save(args.output/'environment.json',{'platform':platform.platform(),'modules':env.get('LOADEDMODULES'),'job':env.get('SLURM_JOB_ID'),'prefix':str(args.prefix),'source_test_sha256':digest(Path(__file__))})
    matrix=[]
    for module in MODULES:
        for mode in ('collector','shell'):
            case=args.output/(module+'-'+mode);case.mkdir();out=case/'raw';out.mkdir()
            binary=args.prefix/f'bin/binary_data_collectors/data_collect_{module}'
            script=args.prefix/f'bin/modules/{module}'
            save(case/'artifacts.json',{'binary_sha256':digest(binary),'module_sha256':digest(script)})
            command=[str(binary),'node',str(out),str(out)] if mode=='collector' else ['bash','-c','source "$1"; "collect_data_$2" "$3" "$4" "$4"','live',str(script),module,'node',str(out)]
            save(case/'command.json',command)
            compared=0;checks=[];rcs=[]
            for rep in range(3):
                before=observe(inventory);save(case/f'oracle-before-{rep}.json',before)
                r=subprocess.run(command,env=env,capture_output=True,text=True,timeout=30);rcs.append(r.returncode)
                (case/f'run-{rep}.stdout').write_text(r.stdout);(case/f'run-{rep}.stderr').write_text(r.stderr)
                after=observe(inventory);save(case/f'oracle-after-{rep}.json',after)
                if r.returncode not in (0,69):checks.append({'repetition':rep,'error':'unexpected exit','rc':r.returncode});break
                file=out/f'{module}_node.jsonl'
                if not file.exists():continue
                x=json.loads(file.read_text().splitlines()[-1])
                if x['schema']!='remora-network-resources/v1':checks.append({'error':'schema'})
                if len(x['records'])!=x['record_count']:checks.append({'error':'record_count'})
                if sorted(e['name'] for e in x['entities'])!=expected(module):checks.append({'error':'entity coverage','expected':expected(module),'actual':x['entities']})
                for v in x['records']:
                    p=v['source'];b=before['readings'].get(p,{'errno':errno.ENOENT,'raw':None});z=after['readings'].get(p,{'errno':errno.ENOENT,'raw':None})
                    if v['status']=='OK':
                        if v['kind']=='counter':
                            try:
                                low=int(b['raw']);high=int(z['raw']);value=int(v['value'])
                                if not low<=value<=high:checks.append({'repetition':rep,'error':'counter bracket','record':v,'before':b,'after':z})
                                else:compared+=1
                            except (TypeError,ValueError):checks.append({'error':'counter oracle missing','record':v,'before':b,'after':z})
                        elif b['raw']==z['raw']:
                            if v['raw']!=b['raw']:checks.append({'error':'stable metadata mismatch','record':v,'before':b})
                            else:compared+=1
                    elif v['value'] is not None or not v['errno']:checks.append({'error':'missing falsely numeric','record':v})
                    elif not b['errno'] and not z['errno'] and b['raw']==z['raw']:checks.append({'error':'stable readable oracle rejected','record':v,'oracle':b})
                time.sleep(.1)
            status='FAIL' if checks else 'SKIP' if all(rc==69 for rc in rcs) else 'PASS'
            record={'module':module,'implementation':mode,'case':'default-bracketed-snapshots','status':status,'returncodes':rcs,'comparisons':compared,'failures':checks}
            if status=='SKIP':record['reason']='no applicable family or readable primary fields; raw unsupported attempts preserved'
            matrix.append(record);save(case/'result.json',record)
            # Explicit selections use independently enumerated inventory; no privileged changes.
            file=out/f'{module}_node.jsonl'
            if status=='PASS' and file.exists():
                x=json.loads(file.read_text().splitlines()[-1]);selected=[e['name'] for e in x['entities']]
                var=SELECTORS[0] if module in ('netinfo','neterrors') else SELECTORS[2] if module=='opacounters' else SELECTORS[1]
                target=next((n for n in selected if ('/' not in n)==(var==SELECTORS[0])),None)
                if target:
                    explicit=case/'explicit';explicit.mkdir();ee=dict(env,**{var:target})
                    c=[str(binary),'node',str(explicit),str(explicit)]
                    r=subprocess.run(c,env=ee,capture_output=True,text=True,timeout=30)
                    save(case/'explicit-command.json',{'argv':c,'selector':{var:target},'rc':r.returncode,'stdout':r.stdout,'stderr':r.stderr})
                    ex=json.loads((explicit/f'{module}_node.jsonl').read_text().splitlines()[-1]) if r.returncode==0 else None
                    actual=[e['name'] for e in ex['entities'] if ('/' not in e['name'])==(var==SELECTORS[0])] if ex else []
                    matrix.append({'module':module,'implementation':mode,'case':'explicit-selection','status':'PASS' if r.returncode==0 and actual==[target] else 'FAIL','selector':{var:target}})
            if file.exists():
                beforehash=digest(file)
                r=subprocess.run(['bash','-c','source "$1"; "finalize_module_$2" "$3" "$4" "$4"','finalize',str(script),module,'node',str(out)],env=env,capture_output=True,text=True,timeout=5)
                matrix.append({'module':module,'implementation':mode,'case':'finalize-preserves-raw','status':'PASS' if r.returncode==0 and digest(file)==beforehash else 'FAIL','raw_sha256':beforehash})
    save(args.output/'matrix.json',matrix)
    manifest={str(p.relative_to(args.output)):digest(p) for p in args.output.rglob('*') if p.is_file()}
    save(args.output/'checksums.json',manifest)
    print(json.dumps({'counts':{s:sum(c['status']==s for c in matrix) for s in ('PASS','FAIL','SKIP')},'cases':len(matrix)}))
    return int(any(c['status']=='FAIL' for c in matrix))

if __name__=='__main__':raise SystemExit(main())
