#!/usr/bin/env python3
"""Bounded compute-node network qualification, stdlib only; never submits jobs."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import threading
import time

METRICS = ('tx_packets','rx_packets','tx_bytes','rx_bytes','tx_err','tx_dropped','rx_frame_err','rx_crc_err')
ETH = ('tx_packets','rx_packets','tx_bytes','rx_bytes','tx_errors','tx_dropped','rx_frame_errors','rx_crc_errors')
IB = ('port_xmit_packets','port_rcv_packets','port_xmit_data','port_rcv_data')
HW = ('TxPkt','RxPkt','TxWords','RxWords')


def save(path, data):
    path.write_text(json.dumps(data, indent=2) + '\n')


def read(path):
    try:
        return {'value': path.read_text().strip(), 'errno': 0}
    except OSError as e:
        return {'value': None, 'errno': e.errno}


def inventory():
    net, ports = {}, {}
    for p in sorted(Path('/sys/class/net').iterdir()):
        net[p.name] = {name: read(p/name) for name in ('flags','type','ifindex','operstate')}
    fabric = Path('/sys/class/infiniband')
    for p in sorted(fabric.glob('*/ports/*')):
        name = p.parent.parent.name + '/' + p.name
        ports[name] = {field: read(p/field) for field in ('state','link_layer','rate')}
        ports[name]['node_guid'] = read(p.parent.parent/'node_guid')
        ports[name]['driver'] = str((p.parent.parent/'device/driver').resolve())
        ports[name]['hfi'] = p.parent.parent.name.startswith('hfi1_') or ports[name]['driver'].endswith('/hfi1')
    return {'net': net, 'ports': ports}


def selected(module, inv):
    if module == 'eth':
        return sorted(n for n,v in inv['net'].items() if v['flags']['errno']==0 and int(v['flags']['value'],0)&1 and v['type']['value']=='1')
    return sorted(n for n,v in inv['ports'].items() if v['state']['value']=='4: ACTIVE' and v['link_layer']['value']=='InfiniBand' and (module!='opa' or v['hfi']))


def candidates(module, names):
    paths = []
    for entity in names:
        if module == 'eth':
            paths.extend(f'class/net/{entity}/statistics/{m}' for m in ETH)
        else:
            dev, port = entity.split('/')
            base = f'class/infiniband/{dev}/ports/{port}'
            paths.extend(f'{base}/counters/{m}' for m in IB)
            paths.extend(f'{base}/counters_ext/{m}_64' for m in IB)
            paths.extend(f'{base}/hw_counters/{m}' for m in HW)
    return paths


def snapshot(paths):
    return {p: read(Path('/sys')/p) for p in paths}


def proc_net():
    text=Path('/proc/net/dev').read_text()
    data={}
    for line in text.splitlines()[2:]:
        n,v=line.split(':',1); numbers=list(map(int,v.split()))
        data[n.strip()]={'rx_bytes':numbers[0],'rx_packets':numbers[1],'tx_bytes':numbers[8],'tx_packets':numbers[9]}
    return {'text':text,'data':data}


def loopback():
    listener=socket.socket();listener.settimeout(5);listener.bind(('127.0.0.1',0));listener.listen(1)
    errors=[]; received=[]
    def receive():
        try:
            conn,_=listener.accept();conn.settimeout(5);total=0
            with conn:
                while True:
                    data=conn.recv(65536)
                    if not data:break
                    total+=len(data)
            received.append(total)
        except Exception as e:errors.append(str(e))
    thread=threading.Thread(target=receive);thread.start()
    with socket.create_connection(listener.getsockname(),timeout=5) as client:
        for _ in range(16):client.sendall(b'x'*65536)
    thread.join(6);listener.close()
    if thread.is_alive() or errors or received!=[1048576]:raise RuntimeError(('loopback',errors,received))
    return {'payload_bytes':1048576,'received_bytes':received[0]}


class Suite:
    def __init__(self, prefix, output, legacy=None):
        self.prefix,self.output,self.legacy=prefix,output,legacy
        self.inv=inventory();save(output/'inventory.json',self.inv)
        self.results=[]
        self.env=dict(os.environ,REMORA_BIN=str(prefix/'bin'),REMORA_MODE='FULL',REMORA_SYMMETRIC='0',REMORA_PLOT_RESULTS='0')
        for key in ('REMORA_NETWORK_SYSFS_ROOT','REMORA_ETH_INTERFACES','REMORA_IB_PORTS','REMORA_OPA_PORTS'):self.env.pop(key,None)

    def call(self, module, implementation, case, sample, env, prime=False):
        paths=candidates(module,case['entities'])
        before=snapshot(paths);proc_before=proc_net()
        if implementation=='shell':
            args=['bash','-c','source "$1"; "$2" node "$3" "$4"','network-live',str(self.prefix/f'bin/modules/{module}'),('init_module_' if prime else 'collect_data_')+module,str(case['path']),str(case['path']/'raw')]
        else:
            args=[str(self.prefix/f'bin/binary_data_collectors/data_collect_{module}'),'node',str(case['path']),str(case['path']/'raw')]+(['--prime'] if prime else [])
        r=subprocess.run(args,env=env,capture_output=True,text=True,timeout=10)
        after=snapshot(paths);proc_after=proc_net()
        save(case['path']/f'call-{sample}.json',{'argv':args,'exit':r.returncode,'before':before,'after':after,'proc_before':proc_before,'proc_after':proc_after})
        (case['path']/f'call-{sample}.stdout').write_text(r.stdout);(case['path']/f'call-{sample}.stderr').write_text(r.stderr)
        case['calls'].append({'before':before,'after':after})
        return r.returncode

    def verify(self,module,case):
        raw=case['path']/'raw'
        records=[r.split('\t') for r in (raw/f'network-{module}-node.raw.tsv').read_text().splitlines() if not r.startswith('#')]
        samples=defaultdict(list)
        for r in records:samples[r[0]].append(r)
        assert len(samples)==4, ('raw repetitions',len(samples))
        previous=None;totals=[]
        for index,rows in enumerate(samples.values()):
            call=case['calls'][index];values={};entities=set()
            for r in rows:
                _,entity,metric,path,value,err,scale,identity=r
                if int(err):
                    assert value=='NA';continue
                assert entity in case['entities'] and identity
                entities.add(entity)
                value=int(value);scale=int(scale)
                low=call['before'][path];high=call['after'][path]
                assert low['errno']==high['errno']==0
                assert int(low['value'])<=value<=int(high['value']), ('independent bracket',r,low,high)
                if module=='eth':expected=1
                elif metric.endswith('bytes'):
                    expected=4 if '/hw_counters/' in path else 8 if self.inv['ports'][entity]['hfi'] else 4
                else:expected=1
                assert scale==expected, ('unit',r,expected)
                key=(entity,metric)
                assert key not in values, ('duplicate successful metric',key)
                values[key]=(value,scale,path)
            width=8 if module=='eth' else 4
            assert entities==set(case['entities']) and len(values)==width*len(entities)
            if previous is not None:
                sums=[0]*width
                for key,(value,scale,path) in values.items():
                    old,old_scale,old_path=previous[key]
                    assert (scale,path)==(old_scale,old_path) and value>=old
                    sums[METRICS.index(key[1])]+=(value-old)*scale
                totals.append(sums)
            previous=values
        filename='network_eth_traffic-node.txt' if module=='eth' else f'{module}_packets-node.txt'
        lines=(raw/filename).read_text().splitlines()
        actual=[list(map(int,line.split()[1:])) for line in lines[1:]]
        assert actual==totals, ('legacy delta oracle',actual,totals)
        times=[float(line.split()[0]) for line in lines[1:]]
        assert len(times)==3 and all(a<b for a,b in zip(times,times[1:]))
        events=(raw/f'network-{module}-node.events.tsv').read_text().splitlines()[1:]
        assert [e.split('\t')[1] for e in events]==['BASELINE','OK','OK','OK']
        assert not list(raw.glob('*.pending')) and not list(raw.glob('*.next'))
        return filename

    def compatibility(self,module,case,filename):
        if not shutil.which('bc'):
            return {'status':'SKIP','reason':'bc unavailable; legacy plot requires bc'}
        result=[]
        for producer in ('new','golden-old'):
            for consumer in ('new','old'):
                source=self.prefix/f'bin/modules/{module}' if consumer=='new' else self.legacy/module if self.legacy else None
                if source is None:continue
                directory=case['path']/f'compat-{producer}-{consumer}';directory.mkdir()
                data=(case['path']/'raw'/filename).read_bytes() if producer=='new' else ('time '+ ' '.join(METRICS[:8 if module=='eth' else 4])+'\n1700000000.100 '+ ' '.join(str(x) for x in range(1,9 if module=='eth' else 5))+'\n1700000001.100 '+ ' '.join(str(x*2) for x in range(1,9 if module=='eth' else 5))+'\n').encode()
                (directory/filename).write_bytes(data);before=hashlib.sha256(data).hexdigest()
                r=subprocess.run(['bash','-c','source "$1"; plot_data_' +module+' node "$2" "$2"','compat',str(source),str(directory)],env=dict(self.env,REMORA_PLOT_RESULTS='1'),capture_output=True,text=True,timeout=15)
                (directory/'plot.stderr').write_text(r.stderr)
                assert r.returncode==0 and not r.stderr, (producer,consumer,r.stderr)
                html=(directory/f'{module}_node.html').read_text()
                assert 'data.addRows' in html and 'Transmitted Bytes' in html
                assert hashlib.sha256((directory/filename).read_bytes()).hexdigest()==before
                rows=re.findall(r'^\[([^\]]+)\]',html,re.M)
                expected=[list(map(int,line.split()[1:])) for line in data.decode().splitlines()[1:]]
                parsed=[[int(v.strip()) for v in row.split(',')[1:] if v.strip()] for row in rows]
                assert parsed==expected, ('plot column preservation',parsed,expected)
                result.append({'producer':producer,'consumer':consumer,'status':'PASS','raw_sha256':before})
        return {'status':'PASS','cases':result}

    def case(self,module,implementation,label,entities,selection=None,traffic=False):
        path=self.output/label;path.mkdir();(path/'raw').mkdir()
        case={'path':path,'entities':entities,'calls':[]}
        env=dict(self.env)
        if selection is not None:env[{'eth':'REMORA_ETH_INTERFACES','ib':'REMORA_IB_PORTS','opa':'REMORA_OPA_PORTS'}[module]]=selection
        result={'case':label,'module':module,'implementation':implementation,'entities':entities}
        self.results.append(result)
        try:
            rc=self.call(module,implementation,case,0,env,prime=True)
            if not entities:
                assert rc==69, ('unsupported exit',rc)
                result.update(status='SKIP',reason='no eligible active interface/port; exit69 observed')
                return
            assert rc==0, ('baseline exit',rc)
            stimuli=[]
            for repetition in range(1,4):
                if traffic:stimuli.append(loopback())
                time.sleep(.2)
                assert self.call(module,implementation,case,repetition,env)==0
            filename=self.verify(module,case)
            if traffic:
                for i in range(1,4):
                    # A separate kernel interface brackets the whole transfer interval.
                    prev=json.loads((path/f'call-{i-1}.json').read_text())['proc_after']['data']['lo']
                    now=json.loads((path/f'call-{i}.json').read_text())['proc_before']['data']['lo']
                    assert now['tx_bytes']-prev['tx_bytes']>=1048576 and now['rx_bytes']-prev['rx_bytes']>=1048576
                save(path/'stimuli.json',stimuli)
            result.update(status='PASS',repetitions=3,oracle='independent sysfs brackets, explicit source units, integer delta/legacy projection')
            if label.endswith('default-native'):
                result['compatibility']=self.compatibility(module,case,filename)
        except Exception as e:
            result.update(status='FAIL',reason=str(e))
        finally:
            save(path/'result.json',result)

    def run(self):
        for module in ('eth','ib','opa'):
            names=selected(module,self.inv)
            for implementation in ('native','shell'):
                self.case(module,implementation,module+'-default-'+implementation,names)
            for i,name in enumerate(names):
                self.case(module,'native',f'{module}-explicit-{i}',[name],selection=name)
        self.case('eth','native','eth-loopback-native',['lo'],selection='lo',traffic=True)
        self.case('eth','shell','eth-loopback-shell',['lo'],selection='lo',traffic=True)
        # Explicit wrong-family/down selection cannot silently choose another port.
        bad=next((n for n,v in self.inv['ports'].items() if not v['hfi']),None)
        if bad:self.case('opa','native','opa-reject-mlx',[],selection=bad)
        down=next((n for n,v in self.inv['ports'].items() if v['state']['value']!='4: ACTIVE'),None)
        if down:self.case('ib','native','ib-reject-down',[],selection=down)
        binaries={m:hashlib.sha256((self.prefix/f'bin/binary_data_collectors/data_collect_{m}').read_bytes()).hexdigest() for m in ('eth','ib','opa')}
        save(self.output/'summary.json',{'schema':'remora-network-qualification/v1','hostname':socket.gethostname(),'binary_sha256':binaries,'cases':self.results,'status':'FAIL' if any(r['status']=='FAIL' for r in self.results) else 'PASS','limits':['single node, sampled node classes only','no end-to-end fabric payload/overhead claim','historical unit correction requires producer revision; golden-old tests prove layout only']})
        return int(any(r['status']=='FAIL' for r in self.results))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--legacy-modules',type=Path)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    return Suite(args.prefix.resolve(),args.output.resolve(),args.legacy_modules).run()


if __name__=='__main__':
    raise SystemExit(main())
