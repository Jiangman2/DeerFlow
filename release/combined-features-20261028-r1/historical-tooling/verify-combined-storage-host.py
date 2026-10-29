"""Two live nodes, isolated canary directory, no membership or production display changes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import uuid

HELPERS = None
VALIDATE = None
BASELINES = None
CHILD = None
STORE = None
CONTROL='/var/lib/portal-combined-storage-check-20261028-r1'


def select_samples(reader,deps):
    sets=reader.get('replicaset')['items'];pods=reader.get('pods')['items']
    samples=[]
    for p in sorted(owned_pods(deps['gateway'],sets,pods),key=lambda p:p['metadata']['name']):
        if p['metadata'].get('deletionTimestamp') or p.get('status',{}).get('phase')!='Running':continue
        if not any(c['name']=='gateway' and c.get('ready') for c in p['status'].get('containerStatuses',[])):continue
        if p['spec'].get('nodeName') in [q['spec']['nodeName'] for q in samples]:continue
        container=next(c for c in p['spec']['containers'] if c['name']=='gateway')
        mounts=[v for v in container.get('volumeMounts',[]) if v.get('mountPath')=='/app/backend/.deer-flow']
        require(len(mounts)==1 and not mounts[0].get('readOnly') and mounts[0].get('subPath')=='deer-flow','USERDATA_MOUNT_CHANGED')
        volumes=[v for v in p['spec']['volumes'] if v['name']==mounts[0]['name']]
        require(len(volumes)==1 and volumes[0].get('persistentVolumeClaim',{}).get('claimName')=='deer-flow-userdata-rwx-pvc','USERDATA_CLAIM_CHANGED')
        samples.append(p)
        if len(samples)==2:break
    require(len(samples)==2,'TWO_READY_NODES_REQUIRED')
    return samples


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-shared-storage-once',required=True,action='store_true');parser.parse_args()
    result=dict(schema='portal-combined-storage-host.v1',status='INCOMPLETE',once_claimed=False,checks=[],
        database_access=False,provider_requests=0,workload_modified=False,production_display_access=False,
        candidate_only_in_children=True,canary_files_may_exist=False,automatic_retry=False,credentials_exported=False,ready_for_rollout=False)
    def save():
        if result['once_claimed']:
            temp=Path(CONTROL)/'REPORT.tmp'
            with temp.open('w',encoding='utf-8') as f:json.dump(result,f,sort_keys=True);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(str(temp),str(Path(CONTROL)/'REPORT.json'))
    reader=Reader()
    try:
        require(socket.gethostname().split('.')[0]=='datax-01' and os.geteuid()==0,'WRONG_HOST_OR_UID')
        require(CHILD and STORE and BASELINES,'PACKAGED_TOOL_REQUIRED');require(not os.path.lexists(CONTROL),'ONCE_ALREADY_CLAIMED')
        deps=validate(reader);samples=select_samples(reader,deps)
        result['samples']=[identity(p) for p in samples]
        for p in samples:require(identity(reader.get('pod',p['metadata']['name']))==identity(p),'SAMPLE_CHANGED')
        os.mkdir(CONTROL,0o700);result['once_claimed']=True
        operation=uuid.uuid4().hex;result['operation']=operation
        result['canary_directory']='/app/backend/.deer-flow/.combined-storage-check-'+operation
        result['canary_files_may_exist']=True;save()
        pin={}
        def call(index,mode):
            args=dict(STORE,mode=mode,operation=operation,directory_identity=pin)
            raw=reader.read(BASE+['exec','-i',samples[index]['metadata']['name'],'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',CHILD],json.dumps(args).encode(),timeout=45,cap=65536)
            row=json.loads(raw);require(row.get('status')=='PASS' and row.get('mode')==mode,'STORAGE_'+mode.upper()+'_NOT_ACCEPTED')
            return row
        row=call(0,'initialize');pin=row['directory_identity'];result['checks'].append(row);save()
        for index,mode in ((1,'update_b'),(0,'stale_a')):
            result['checks'].append(call(index,mode));save()
        print(json.dumps({'phase':'VERIFY_CROSS_NODE_EXCLUSIVE_LOCK'}),flush=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            held=executor.submit(call,0,'hold_a')
            contender=call(1,'contend_b')
            result['checks'].extend([held.result(timeout=50),contender]);save()
        for index,mode in ((0,'final_a'),(1,'read_b'),(0,'cleanup')):
            result['checks'].append(call(index,mode));save()
        result['canary_files_may_exist']=False;result['canary_removed']=True;save()
        for p in samples:require(identity(reader.get('pod',p['metadata']['name']))==identity(p),'SAMPLE_CHANGED')
        validate(reader);result['status']='CROSS_NODE_STORE_ACCEPTED_NOT_DEPLOYED'
    except Exception as exc:
        result.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'STORAGE_CHECK_FAILED',raw_errors_exported=False)
    save()
    if result['once_claimed']:
        raw=(Path(CONTROL)/'REPORT.json').read_bytes();result.update(report_path=CONTROL+'/REPORT.json',report_bytes=len(raw),report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='CROSS_NODE_STORE_ACCEPTED_NOT_DEPLOYED' else 1


if __name__=='__main__':raise SystemExit(main())
