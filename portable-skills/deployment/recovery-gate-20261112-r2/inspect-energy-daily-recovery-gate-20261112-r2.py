"""Read fixed-thread recovery evidence without retrying or changing a task."""
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

THREAD='0fb37c34-d996-4d8b-aeeb-ea9dcd252a28'

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

def sandbox_summary(pod,name):
    if pod is None:return {'present':False,'current_absence_does_not_prove_historical_cause':True}
    need(pod.get('metadata',{}).get('name')==name,'NAMED_SANDBOX_IDENTITY_REQUIRED')
    status=pod.get('status',{});meta=pod.get('metadata',{})
    annotation=meta.get('annotations',{}).get('deerflow.io/thread-id');need(annotation in (None,THREAD),'SANDBOX_THREAD_ANNOTATION_MISMATCH')
    allowed={'PodScheduled','Initialized','ContainersReady','Ready','PodReadyToStartContainers','DisruptionTarget','Unschedulable','ContainersNotReady','PodCompleted','KubeletNotReady','SchedulingGated'}
    reasons={'ContainerCreating','PodInitializing','CrashLoopBackOff','ImagePullBackOff','ErrImagePull','CreateContainerConfigError','CreateContainerError','RunContainerError','InvalidImageName','Completed','Error','OOMKilled'}
    containers=[]
    for row in (status.get('initContainerStatuses',[])+status.get('containerStatuses',[]))[:8]:
        state=row.get('state',{});reason=(state.get('waiting') or state.get('terminated') or {}).get('reason')
        containers.append({'container':row.get('name') if row.get('name') in ('sandbox','ppt-master-agent-host') else 'other',
            'ready':row.get('ready') is True,'restart_count':row.get('restartCount',0),'state':next((kind for kind in ('waiting','running','terminated') if kind in state),'unknown'),
            'reason':reason if reason in reasons else 'other' if reason else None})
    return {'present':True,'pod_uid_sha256':digest(str(meta.get('uid','')).encode()),'deleting':bool(meta.get('deletionTimestamp')),
        'phase':status.get('phase') if status.get('phase') in ('Pending','Running','Succeeded','Failed','Unknown') else 'other',
        'conditions':[{'type':row.get('type') if row.get('type') in allowed else 'other','status':row.get('status') if row.get('status') in ('True','False','Unknown') else 'other',
            'reason':row.get('reason') if row.get('reason') in allowed else 'other' if row.get('reason') else None} for row in status.get('conditions',[])[:12]],'containers':containers}

def sandbox_events(value,name):
    rows=value.get('items');need(isinstance(rows,list) and len(rows)<=128,'SANDBOX_EVENT_COUNT_BOUND')
    result=[]
    reasons={'FailedScheduling','FailedMount','FailedAttachVolume','FailedCreatePodSandBox','Failed','BackOff','Pulled','Pulling','Scheduled','Created','Started','Unhealthy','FailedSync','NodeNotReady','SandboxChanged','Killing'}
    flags={'insufficient_cpu':'insufficient cpu','insufficient_memory':'insufficient memory','too_many_pods':'too many pods','unbound_pvc':'unbound immediate persistentvolumeclaims',
        'failed_mount':'failedmount','container_config_error':'createcontainerconfigerror','node_affinity':'node affinity','taint':'untolerated taint','image_pull_backoff':'imagepullbackoff','crash_loop':'crashloopbackoff'}
    for row in rows:
        involved=row.get('involvedObject',{});need(involved.get('kind')=='Pod' and involved.get('name')==name,'SANDBOX_EVENT_SCOPE_REQUIRED')
        message=str(row.get('message',''));need(len(message.encode())<=131072,'EVENT_MESSAGE_BOUND');lower=message.casefold()
        detected=[key for key,term in flags.items() if term in lower]
        if 'secret' in lower and any(term in lower for term in ("couldn't find key",'could not find key','key not found')):detected.append('secret_key_missing')
        result.append({'reason':row.get('reason') if row.get('reason') in reasons else 'other','flags':detected,
            'type':row.get('type') if row.get('type') in ('Normal','Warning') else 'other','count':row.get('count') if isinstance(row.get('count'),int) else None,
            'pod_uid_sha256':digest(str(involved.get('uid','')).encode()),'message_body_exported':False})
    return {'events':result,'event_count':len(result),'historical_events_not_present':not result,'absence_does_not_identify_historical_cause':True}


def skill_roots(pod):
    volumes={row['name']:row for row in pod['spec'].get('volumes',[])}
    mains=[row for row in pod['spec'].get('containers',[]) if row.get('name')=='gateway'];need(len(mains)==1,'ONE_GATEWAY_CONTAINER_REQUIRED')
    roots=[]
    for mount in mains[0].get('volumeMounts',[]):
        claim=volumes.get(mount.get('name'),{}).get('persistentVolumeClaim',{}).get('claimName','')
        if 'skill' not in claim:continue
        path=mount.get('mountPath');sub=mount.get('subPath','')
        need(isinstance(path,str) and path.startswith('/') and str(PurePosixPath(path))==path and '..' not in PurePosixPath(path).parts and not mount.get('subPathExpr'),'SKILLS_PVC_MOUNT_SHAPE')
        if sub=='public':candidate=PurePosixPath(path)/'energy-market-daily'
        elif sub=='public/energy-market-daily':candidate=PurePosixPath(path)
        elif not sub:candidate=PurePosixPath(path)/'public/energy-market-daily'
        else:continue
        # Never accept a deeper overlay for the selected official Skill subtree.
        for other in mains[0].get('volumeMounts',[]):
            target=PurePosixPath(other['mountPath'])
            need(not (target!=PurePosixPath(path) and (target==candidate or candidate in target.parents or (PurePosixPath(path) in target.parents and target in candidate.parents))),'SKILL_SOURCE_SHADOW_MOUNT')
        roots.append(str(candidate))
    need(len(roots)<=4,'SKILL_ROOT_BOUND');return sorted(set(roots))

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.payload,self.queries,self.sandbox=child,None,None,None,0,None
    def command(self,args,body=None):
        allowed=args==LIST or args==['get','deployment','gateway','-o','json']
        if self.pod:
            allowed=allowed or args==['get','pod',self.pod,'-o','json']
            if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
            if args==['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child]:
                allowed=self.payload is not None and body==canonical(self.payload)
        if self.sandbox:
            need(re.fullmatch('sandbox-[0-9a-f]{8}',self.sandbox),'FIXED_SANDBOX_NAME_REQUIRED')
            allowed=allowed or args==['get','pod',self.sandbox,'--ignore-not-found','-o','json'] or args==['get','events','--field-selector=involvedObject.kind=Pod,involvedObject.name='+self.sandbox,'-o','json']
        need(allowed and (args[0]=='exec' or body is None),'READONLY_FIXED_COMMAND_REQUIRED');need(self.queries<9,'QUERY_BOUND');self.queries+=1
        timeout=55 if args[0]=='exec' else 25;prefix=list(KUBE)
        if args[0]=='exec':prefix[2]='--request-timeout=50s'
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            if body is not None:incoming.write(body);incoming.seek(0)
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+timeout
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
    out={'schema':'energy-recovery-transport-inspection.v2','status':'INCOMPLETE','thread_id':THREAD,'workload_modified':False,'database_readonly':True,
        'database_modified':False,'database_credentials_used':False,'credentials_exported':False,'provider_requests':0,'files_modified':False,'secret_api_reads':0,
        'new_sandboxes_created':0,'task_retry_requested':False,'gate_modified':False,'raw_messages_or_news_exported':False}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(assets());dep=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(dep['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_DEPLOYMENT_CHANGED')
        dep_identity={'uid':GATEWAY_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':digest(canonical(dep['spec']))}
        chosen=candidates(op.command(LIST))[0];op.pod=chosen[0]
        pod=json.loads(op.command(['get','pod',op.pod,'-o','json']));before=identity(pod,chosen[1])
        controllers=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(controllers)==1 and controllers[0].get('kind')=='ReplicaSet' and re.fullmatch('gateway-[a-z0-9-]+',controllers[0].get('name','')),'GATEWAY_REPLICASET_REQUIRED')
        op.rs=controllers[0]['name'];rs=json.loads(op.command(['get','replicaset',op.rs,'-o','json']))
        op.payload={}
        out.update(gateway=dep_identity,sample=before,replica_set=ownership(pod,rs,GATEWAY_UID),target_event_seq=7)
        result=json.loads(op.command(['exec','-i',op.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',op.child],canonical(op.payload)))
        out.update(observation=result,database_credentials_used=result.get('database_credentials_used') is True)
        candidate=result.get('sandbox_pod_name')
        if candidate:
            need(re.fullmatch('sandbox-[0-9a-f]{8}',candidate),'FIXED_SANDBOX_NAME_REQUIRED');op.sandbox=candidate
            raw_pod=op.command(['get','pod',candidate,'--ignore-not-found','-o','json'])
            out['named_sandbox']=sandbox_summary(json.loads(raw_pod) if raw_pod.strip() else None,candidate)
            out['named_sandbox_events']=sandbox_events(json.loads(op.command(['get','events','--field-selector=involvedObject.kind=Pod,involvedObject.name='+candidate,'-o','json'])),candidate)
        else:out['sandbox_name_not_identified_from_fixed_error_template']=True
        final=json.loads(op.command(['get','pod',op.pod,'-o','json']));dep_after=json.loads(op.command(['get','deployment','gateway','-o','json']))
        need(before==identity(final,chosen[1]) and pod['metadata'].get('ownerReferences')==final['metadata'].get('ownerReferences') and digest(canonical(dep_after['spec']))==dep_identity['spec_sha256'] and dep_after['metadata'].get('uid')==GATEWAY_UID,'GATEWAY_CHANGED_DURING_READ')
        need(result.get('status')=='FIXED_DOCTOR_TRANSPORT_EVIDENCE_OBSERVED_NO_CHANGE','RECOVERY_CHILD_INCOMPLETE')
        out.update(status='FIXED_DOCTOR_TRANSPORT_EVIDENCE_OBSERVED_NO_CHANGE',stable_gateway_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_INSPECTION_FAILED_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
