"""Readonly fresh sandbox retention policy and node-0015 workload metadata."""
import argparse

import base64

import datetime

import hashlib

import json

import os

from pathlib import PurePosixPath

import re

import signal

import subprocess

import sys

import tempfile

import time

GATEWAY_UID='a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'

ASSETS={}

KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']

LIST=['get','pods','-l','app=gateway','--no-headers','-o','custom-columns=NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.containerStatuses[?(@.name=="gateway")].ready']

class Refused(ValueError):pass

def need(ok,code):
    if not ok:raise Refused(code)

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()

def digest(raw):return hashlib.sha256(raw).hexdigest()

def assets():
    need(set(ASSETS)=={'child'},'ASSETS_NOT_SEALED')
    item=ASSETS['child'];raw=base64.b64decode(item['base64'],validate=True)
    need(len(raw)<=262144 and digest(raw)==item['sha256'],'CHILD_ASSET_CHANGED')
    compile(raw,'<fixed-persisted-child>','exec');return raw.decode()

def candidates(raw):
    need(isinstance(raw,str) and len(raw)<=131072,'GATEWAY_LIST_BOUND')
    selected=[]
    for line in raw.splitlines():
        parts=line.split();need(len(parts)==5,'GATEWAY_LIST_SHAPE')
        name,uid,phase,deleting,ready=parts
        need(re.fullmatch('gateway-[a-z0-9-]+',name) and re.fullmatch('[0-9a-f-]{36}',uid),'GATEWAY_POD_IDENTITY')
        if phase=='Running' and deleting=='<none>' and ready=='true':selected.append(parts)
    need(bool(selected) and len(selected)<=100,'READY_GATEWAY_REQUIRED')
    return sorted(selected)

def identity(pod,uid):
    meta,spec,status=pod['metadata'],pod['spec'],pod.get('status',{})
    need(meta.get('uid')==uid and not meta.get('deletionTimestamp') and meta.get('labels',{}).get('app')=='gateway','GATEWAY_IDENTITY_CHANGED')
    states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='gateway']
    need(status.get('phase')=='Running' and len(states)==1 and states[0].get('ready') is True,'GATEWAY_NOT_READY')
    return {'name':meta['name'],'uid':uid,'spec_sha256':digest(canonical(spec)),'image_id_sha256':digest(str(states[0].get('imageID','')).encode()),
            'container_id_sha256':digest(str(states[0].get('containerID','')).encode()),'restart_count':states[0].get('restartCount')}

def ownership(pod,replicaset,dep_uid):
    owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
    need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet','GATEWAY_REPLICASET_REQUIRED')
    need(replicaset['metadata'].get('uid')==owners[0].get('uid') and replicaset['metadata'].get('name')==owners[0].get('name'),'GATEWAY_REPLICASET_CHANGED')
    parents=[row for row in replicaset['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
    need(len(parents)==1 and parents[0].get('kind')=='Deployment' and parents[0].get('name')=='gateway' and parents[0].get('uid')==dep_uid,'GATEWAY_DEPLOYMENT_OWNERSHIP_REQUIRED')
    return {'name':replicaset['metadata']['name'],'uid':replicaset['metadata']['uid']}
NODE_PODS=['get','pods','-A','--field-selector=spec.nodeName=ecs-zdz-pro-0015,status.phase!=Succeeded,status.phase!=Failed','--no-headers','-o',
    'custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,OWNER_KIND:.metadata.ownerReferences[?(@.controller==true)].kind,OWNER:.metadata.ownerReferences[?(@.controller==true)].name,CPU:.spec.containers[*].resources.requests.cpu,MEMORY:.spec.containers[*].resources.requests.memory,COMPONENT:.metadata.labels.app\\.kubernetes\\.io/component']

def identifier(value):
    need(isinstance(value,str) and (value=='<none>' or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,252}',value)),'METADATA_IDENTIFIER_SHAPE')
    return re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}','[id]',value)

def quantities(value):
    if value=='<none>':return []
    need(len(value)<=1024,'RESOURCE_COLUMN_BOUND');parts=value.split(',')
    need(len(parts)<=16 and all(re.fullmatch(r'[0-9][0-9.eE+-]*(?:[A-Za-z]{1,3})?',part) for part in parts),'RESOURCE_QUANTITY_SHAPE')
    return parts

def workload_groups(raw):
    need(isinstance(raw,str) and len(raw.encode())<=262144,'NODE_POD_PROJECTION_BOUND')
    groups={};total=0
    for line in raw.splitlines():
        fields=line.split();need(len(fields)==7,'NODE_POD_ROW_SHAPE');total+=1;need(total<=256,'NODE_POD_COUNT_BOUND')
        namespace,name,kind,owner,cpu,memory,component=fields
        cpu,memory=quantities(cpu),quantities(memory)
        if name.startswith('sandbox-') or component=='sandbox':
            row={'workload_type':'sandbox','container_cpu_requests':cpu,'container_memory_requests':memory}
        else:
            namespace,name,owner,component=map(identifier,(namespace,name,owner,component))
            need(kind in ('<none>','ReplicaSet','DaemonSet','StatefulSet','Job','CronJob','ReplicationController','Pod'),'OWNER_KIND_SHAPE')
            # Non-sandbox standalone maintenance names may contain operation IDs.
            if owner=='<none>':owner=re.sub(r'(?<=-)[0-9a-f]{8,64}$','[id]',name)
            row={'workload_type':'non_sandbox','namespace':namespace,'owner_kind':kind if kind!='<none>' else 'StandalonePod',
                'owner':owner,'component':component if component!='<none>' else None,'container_cpu_requests':cpu,'container_memory_requests':memory}
        key=canonical(row);groups.setdefault(key,dict(row,pod_count=0))['pod_count']+=1
    return {'node':'ecs-zdz-pro-0015','nonterminated_pod_count':total,'groups':sorted(groups.values(),key=lambda row:(row['workload_type'],row.get('namespace',''),row.get('owner',''),str(row['container_cpu_requests']))),
        'resource_basis':'REGULAR_CONTAINER_REQUEST_COLUMNS_ONLY_NOT_SCHEDULER_EFFECTIVE_TOTAL','init_overhead_and_pod_level_not_in_projection':True,
        'sandbox_names_threads_or_owner_exported':False,'idle_or_cleanup_eligibility_not_inferred':True}

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.queries=child,None,None,0
    def command(self,args):
        allowed=args==LIST or args==['get','deployment','gateway','-o','json'] or args==NODE_PODS
        if self.pod:allowed=allowed or args==['get','pod',self.pod,'-o','json'] or args==['exec',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child]
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        need(allowed,'FIXED_READONLY_COMMAND_REQUIRED');need(self.queries<8,'QUERY_BOUND');self.queries+=1
        prefix=KUBE[:-2] if args==NODE_PODS else KUBE
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(prefix+args,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+25
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=1048576 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                    need(time.monotonic()<until,'READ_DEADLINE');time.sleep(.1)
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READ_COMMAND_FAILED')
                need(os.fstat(output.fileno()).st_size<=1048576 and os.fstat(errors.fileno()).st_size<=65536,'OUTPUT_BOUND')
                output.seek(0);return output.read(1048577).decode()
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-readonly',required=True,action='store_true');parser.parse_args()
    op=None
    out={'schema':'sandbox-retention-and-node-occupants.v1','status':'INCOMPLETE','workload_modified':False,'files_modified':False,'database_access':False,
        'secret_api_reads':0,'credentials_exported':False,'provider_requests':0,'lease_files_read_or_acquired':False,'cleanup_requested':False,
        'sandboxes_created':0,'sandboxes_deleted':0,'policy_is_fresh_config_not_live_provider_state':True}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(assets());dep=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(dep['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_DEPLOYMENT_CHANGED')
        dep_identity={'uid':GATEWAY_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':digest(canonical(dep['spec']))}
        chosen=candidates(op.command(LIST))[0];op.pod=chosen[0]
        pod=json.loads(op.command(['get','pod',op.pod,'-o','json']));before=identity(pod,chosen[1])
        owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet' and re.fullmatch('gateway-[a-z0-9-]+',owners[0].get('name','')),'GATEWAY_REPLICASET_REQUIRED')
        op.rs=owners[0]['name'];rs=json.loads(op.command(['get','replicaset',op.rs,'-o','json']))
        out.update(gateway=dep_identity,sample=before,replica_set=ownership(pod,rs,GATEWAY_UID))
        policy=json.loads(op.command(['exec',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',op.child]));out['retention_policy']=policy
        need(policy.get('status')=='SANDBOX_RETENTION_CONFIGURATION_OBSERVED_NO_CHANGE','FRESH_CONFIG_READ_INCOMPLETE')
        out['node_occupants']=workload_groups(op.command(NODE_PODS))
        final=json.loads(op.command(['get','pod',op.pod,'-o','json']));dep_after=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(before==identity(final,chosen[1]) and pod['metadata'].get('ownerReferences')==final['metadata'].get('ownerReferences') and digest(canonical(dep_after['spec']))==dep_identity['spec_sha256'] and dep_after['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_CHANGED_DURING_READ')
        out.update(status='SANDBOX_RETENTION_AND_NODE_OCCUPANTS_OBSERVED_NO_CHANGE',stable_gateway_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_INSPECTION_FAILURE_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat());print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
