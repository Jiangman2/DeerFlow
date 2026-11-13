"""Seal one new exact-event read-only operator; preserve the original diagnostic."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
R1=HERE.parent/'recovery-gate-20261112-r1'
spec=importlib.util.spec_from_file_location('approved_recovery_transport',R1/'seal_recovery_probe.py')
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)

HOST_EXTRA=r'''
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
'''

def common_source():
    source=(R1/'inspect_recovery_child.py').read_text(encoding='utf-8');nodes=[]
    wanted={'Refused','need','sha','iso','text','signals','command_summary','json_objects','result_summary','fixed_hash'}
    constants={'TOOLS','ACTIONS','MARKERS','STATUSES','ERRORS','EXCEPTIONS'}
    for node in ast.parse(source).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,(ast.FunctionDef,ast.ClassDef)) and node.name in wanted:nodes.append(ast.get_source_segment(source,node))
        elif isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id in constants for name in node.targets):nodes.append(ast.get_source_segment(source,node))
    return ('\n\n'.join(nodes)+'\n').encode()

def replace_one(raw,old,new):
    if raw.count(old)!=1:raise ValueError('APPROVED_TRANSPORT_MAPPING_CHANGED')
    return raw.replace(old,new)

def source_host():
    raw=prior.source_host().decode()
    raw=replace_one(raw,'def skill_roots(pod):',HOST_EXTRA+'\n\ndef skill_roots(pod):')
    raw=replace_one(raw,'def __init__(self,child):self.child,self.pod,self.rs,self.payload,self.queries=child,None,None,None,0',
        'def __init__(self,child):self.child,self.pod,self.rs,self.payload,self.queries,self.sandbox=child,None,None,None,0,None')
    raw=replace_one(raw,"need(allowed and (args[0]=='exec' or body is None),'READONLY_FIXED_COMMAND_REQUIRED');need(self.queries<7,'QUERY_BOUND');self.queries+=1",
        "if self.sandbox:\n            need(re.fullmatch('sandbox-[0-9a-f]{8}',self.sandbox),'FIXED_SANDBOX_NAME_REQUIRED')\n            allowed=allowed or args==['get','pod',self.sandbox,'--ignore-not-found','-o','json'] or args==['get','events','--field-selector=involvedObject.kind=Pod,involvedObject.name='+self.sandbox,'-o','json']\n        need(allowed and (args[0]=='exec' or body is None),'READONLY_FIXED_COMMAND_REQUIRED');need(self.queries<9,'QUERY_BOUND');self.queries+=1")
    raw=replace_one(raw,"op.payload={'skill_roots':skill_roots(pod)}","op.payload={}")
    raw=replace_one(raw,"skill_source_candidate_count=len(op.payload['skill_roots'])","target_event_seq=7")
    raw=replace_one(raw,"out.update(observation=result,database_credentials_used=result.get('database_credentials_used') is True)",
        "out.update(observation=result,database_credentials_used=result.get('database_credentials_used') is True)\n        candidate=result.get('sandbox_pod_name')\n        if candidate:\n            need(re.fullmatch('sandbox-[0-9a-f]{8}',candidate),'FIXED_SANDBOX_NAME_REQUIRED');op.sandbox=candidate\n            raw_pod=op.command(['get','pod',candidate,'--ignore-not-found','-o','json'])\n            out['named_sandbox']=sandbox_summary(json.loads(raw_pod) if raw_pod.strip() else None,candidate)\n            out['named_sandbox_events']=sandbox_events(json.loads(op.command(['get','events','--field-selector=involvedObject.kind=Pod,involvedObject.name='+candidate,'-o','json'])),candidate)\n        else:out['sandbox_name_not_identified_from_fixed_error_template']=True")
    raw=raw.replace('NAMED_THREAD_RECOVERY_EVIDENCE_OBSERVED_NO_CHANGE','FIXED_DOCTOR_TRANSPORT_EVIDENCE_OBSERVED_NO_CHANGE').replace('energy-recovery-gate-inspection.v1','energy-recovery-transport-inspection.v2')
    return raw.encode()

def main():
    raw=source_host();host=HERE/'inspect-energy-daily-recovery-gate-20261112-r2.py'
    with host.open('xb') as stream:stream.write(raw)
    child=(HERE/'inspect_transport_child.py').read_bytes()
    for prefix,data in [('SAFETY',prior.source_safety()),('COMMON',common_source())]:
        for name,value in [(prefix+'_B64',base64.b64encode(data).decode()),(prefix+'_SHA',hashlib.sha256(data).hexdigest())]:
            needle=(name+"=''\n").encode()
            if child.count(needle)!=1:raise ValueError('UNSEALED_CHILD_REQUIRED')
            child=child.replace(needle,(name+'='+repr(value)+'\n').encode())
    compile(child,'<fixed-transport-child>','exec')
    assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    sealed=replace_one(raw,b'ASSETS={}',b'ASSETS='+repr(assets).encode());compile(sealed,str(host),'exec')
    directory=ROOT/'release/energy-daily-recovery-gate-20261112-r2';directory.mkdir(parents=True,exist_ok=True);target=directory/host.name
    with target.open('xb') as stream:stream.write(sealed)
    meta={'schema':'energy-recovery-transport-package.v2','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),'child_sha256':hashlib.sha256(child).hexdigest(),'production_executed':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(meta,stream,indent=2);stream.write('\n')
    print(json.dumps(meta,sort_keys=True))

if __name__=='__main__':main()
