"""Readonly native Kubernetes requested-resource and sandbox-template snapshot."""
import argparse
import base64
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time

ASSETS={}
PROVISIONER_UID='9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253'
EXPECTED_IMAGE='sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
NAMED_THREADS=('201fb3d5-49c0-4276-9e3f-3db67f8edfe7','0fb37c34-d996-4d8b-aeeb-ea9dcd252a28')
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
SANDBOX_LIST=['get','pods','-l','app.kubernetes.io/component=sandbox','--no-headers','-o',
 'custom-columns=NAME:.metadata.name,UID:.metadata.uid,NODE:.spec.nodeName,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.containerStatuses[?(@.name=="sandbox")].ready,CREATED:.metadata.creationTimestamp,SUBPATHS:.spec.containers[?(@.name=="sandbox")].volumeMounts[*].subPath']

class Refused(ValueError):pass
def need(ok,code):
    if not ok:raise Refused(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()

def child_source():
    need(set(ASSETS)=={'child'},'ASSETS_NOT_SEALED');row=ASSETS['child'];raw=base64.b64decode(row['base64'],validate=True)
    need(len(raw)<=65536 and sha(raw)==row['sha256'],'CHILD_RESOURCE_CHANGED');compile(raw,'<fixed-resource-reader>','exec');return raw.decode()

def pod_identity(pod):
    meta=pod['metadata'];status=pod.get('status',{});states=status.get('containerStatuses',[])
    need(meta.get('name','').startswith('provisioner-') and not meta.get('deletionTimestamp') and status.get('phase')=='Running' and states and all(row.get('ready') is True for row in states),'READY_PROVISIONER_REQUIRED')
    return {'name':meta['name'],'uid':meta['uid'],'spec_sha256':sha(canonical(pod['spec'])),'container_state_sha256':sha(canonical([{key:row.get(key) for key in ('name','containerID','imageID','restartCount')} for row in states]))}

def owned_pod(pods,dep):
    items=pods.get('items');need(isinstance(items,list) and len(items)<=32,'PROVISIONER_POD_LIST_BOUND')
    found=[]
    for pod in items:
        try:identity=pod_identity(pod)
        except Refused:continue
        owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet' and re.fullmatch('provisioner-[a-z0-9-]+',owners[0].get('name','')),'PROVISIONER_RS_REQUIRED')
        found.append((pod,identity,owners[0]))
    need(len(found)==1,'ONE_READY_PROVISIONER_REQUIRED');return found[0]

def verify_owner(owner,rs):
    need(rs['metadata'].get('name')==owner['name'] and rs['metadata'].get('uid')==owner['uid'],'PROVISIONER_RS_CHANGED')
    controllers=[row for row in rs['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
    need(len(controllers)==1 and controllers[0].get('kind')=='Deployment' and controllers[0].get('uid')==PROVISIONER_UID,'PROVISIONER_OWNER_CHANGED')

def summarize_nodes(value):
    items=value.get('items');need(isinstance(items,list) and 0<len(items)<=64,'NODE_COUNT_BOUND');result=[]
    for node in items:
        meta,spec,status=node['metadata'],node.get('spec',{}),node.get('status',{})
        ready=next((row.get('status') for row in status.get('conditions',[]) if row.get('type')=='Ready'),'Unknown')
        result.append({'name':meta['name'],'hostname':meta.get('labels',{}).get('kubernetes.io/hostname'),'architecture':meta.get('labels',{}).get('kubernetes.io/arch'),
            'role_or_workload_labels':{key:value for key,value in meta.get('labels',{}).items() if key.startswith('node-role.kubernetes.io/') or key in ('workload','workload-type','deerflow.io/workload','deerflow.io/node-role','deerflow.ai/workload','deerflow.ai/node-role')},
            'ready':ready,'unschedulable':spec.get('unschedulable',False),'taints':[{key:row.get(key) for key in ('key','value','effect')} for row in spec.get('taints',[])],
            'allocatable':{key:status.get('allocatable',{}).get(key) for key in ('cpu','memory','pods','ephemeral-storage')}})
    return sorted(result,key=lambda row:row['name'])

def native_allocations(raw,names):
    need(isinstance(raw,str) and len(raw.encode())<=4194304,'DESCRIBE_NODE_BOUND');result=[];current=None;section=None
    for line in raw.splitlines():
        match=re.match(r'^Name:\s+(\S+)\s*$',line)
        if match:
            need(match.group(1) in names,'DESCRIBE_NODE_SCOPE');current={'name':match.group(1),'allocated_resources':{},'native_nonterminated_pod_count':None};result.append(current);section=None;continue
        if current is None:continue
        match=re.match(r'^Non-terminated Pods:\s+\((\d+) in total\)',line)
        if match:current['native_nonterminated_pod_count']=int(match.group(1));section=None;continue
        if line.startswith('Allocated resources:'):section='allocated';continue
        if line and not line[0].isspace():section=None
        if section=='allocated':
            columns=re.split(r'\s{2,}',line.strip())
            if len(columns)==3 and columns[0] in ('cpu','memory','ephemeral-storage'):
                need(all(re.fullmatch(r'[0-9A-Za-z.+-]+(?: \([0-9.]+%\))?',value) for value in columns[1:]),'NATIVE_RESOURCE_CELL')
                current['allocated_resources'][columns[0]]={'requests_native':columns[1],'limits_native':columns[2]}
    need({row['name'] for row in result}==set(names) and len(result)==len(names) and all({'cpu','memory'}<=set(row['allocated_resources']) and row['native_nonterminated_pod_count'] is not None for row in result),'NATIVE_ALLOCATION_SECTIONS_REQUIRED')
    return result

def sandbox_rows(raw):
    need(isinstance(raw,str) and len(raw.encode())<=262144,'SANDBOX_LIST_BOUND');rows=[];counts={}
    for line in raw.splitlines():
        values=line.split();need(len(values)==8,'SANDBOX_ROW_SHAPE')
        name,uid,node,phase,deleting,ready,created,subpaths=values
        need(re.fullmatch('sandbox-[a-z0-9-]+',name) and re.fullmatch('[0-9a-f-]{36}',uid),'SANDBOX_ID_SHAPE')
        matches=[thread for thread in NAMED_THREADS if any(path.endswith('/threads/'+thread+'/user-data') for path in subpaths.split(','))]
        row={'name':name,'uid':uid,'node':node,'phase':phase,'deleting':deleting!='<none>','ready':ready=='true','created_at':created,'named_thread_matches':matches};rows.append(row)
        key=node if node!='<none>' else 'unassigned';summary=counts.setdefault(key,{'total':0,'ready':0,'pending':0,'deleting':0});summary['total']+=1;summary['ready']+=int(row['ready']);summary['pending']+=int(phase=='Pending');summary['deleting']+=int(row['deleting'])
    need(len(rows)<=2048,'SANDBOX_COUNT_BOUND');return rows,counts

def sandbox_template(pod,chosen):
    meta,spec,status=pod['metadata'],pod['spec'],pod.get('status',{})
    need(meta.get('uid')==chosen['uid'] and meta.get('name')==chosen['name'] and not meta.get('deletionTimestamp'),'SANDBOX_SAMPLE_CHANGED')
    states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='sandbox']
    need(len(states)==1 and states[0].get('ready') is True and states[0].get('imageID','').endswith(EXPECTED_IMAGE),'EXPECTED_ORDINARY_SANDBOX_REQUIRED')
    containers=lambda rows:[{'name':row.get('name'),'resources':row.get('resources',{}),'restart_policy':row.get('restartPolicy')} for row in rows]
    return {'sample_name':meta['name'],'uid':meta['uid'],'node':spec.get('nodeName'),'containers':containers(spec.get('containers',[])),'init_containers':containers(spec.get('initContainers',[])),
        'sandbox_container_ready':True,'pod_ready':any(row.get('type')=='Ready' and row.get('status')=='True' for row in status.get('conditions',[])),
        'affinity':spec.get('affinity'),'node_selector':spec.get('nodeSelector'),'tolerations':spec.get('tolerations',[]),'overhead':spec.get('overhead',{}),'pod_level_resources':spec.get('resources'),
        'pvc_claims':[row['persistentVolumeClaim']['claimName'] for row in spec.get('volumes',[]) if isinstance(row.get('persistentVolumeClaim'),dict)],
        'environment_or_commands_exported':False,'sample_is_not_proof_every_sandbox_has_identical_template':True}

class Operation:
    def __init__(self,child):self.child,self.pod,self.rs,self.sandbox,self.queries=child,None,None,None,0
    def command(self,args):
        allowed=args in (['get','deployment','provisioner','-o','json'],['get','pods','-l','app=provisioner','-o','json'],['get','nodes','-o','json'],['describe','nodes'],SANDBOX_LIST)
        if self.pod:allowed=allowed or args==['get','pod',self.pod,'-o','json'] or args==['exec',self.pod,'-c','provisioner','--','python3','-I','-B','-c',self.child]
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        if self.sandbox:allowed=allowed or args==['get','pod',self.sandbox,'-o','json']
        need(allowed,'FIXED_READONLY_COMMAND_REQUIRED');need(self.queries<9,'QUERY_BOUND');self.queries+=1
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(KUBE+args,stdin=subprocess.DEVNULL,stdout=output,stderr=errors,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+25
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=4194304 and os.fstat(errors.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic()<until,'COMMAND_DEADLINE');time.sleep(.1)
                need(process.returncode==0,'READ_COMMAND_FAILED');need(os.fstat(output.fileno()).st_size<=4194304 and os.fstat(errors.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                output.seek(0);return output.read(4194305).decode()
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inspect-readonly',required=True,action='store_true');parser.parse_args()
    op=None;out={'schema':'sandbox-scheduling-capacity.v1','status':'INCOMPLETE','workload_modified':False,'files_modified':False,'database_access':False,'secret_api_reads':0,
        'credentials_exported':False,'provider_requests':0,'sandboxes_created':0,'sandboxes_deleted':0,'execution_leases_read_or_acquired':False,
        'idle_state_not_inferred_from_pod_age':True,'headroom_formula_not_reimplemented':True,'existing_business_tasks_preserved':True}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        op=Operation(child_source());dep=json.loads(op.command(['get','deployment','provisioner','-o','json']))
        need(dep['metadata'].get('uid')==PROVISIONER_UID,'PROVISIONER_DEPLOYMENT_CHANGED')
        out['provisioner']={'uid':PROVISIONER_UID,'generation':dep['metadata'].get('generation'),'spec_sha256':sha(canonical(dep['spec']))}
        pod,before,owner=owned_pod(json.loads(op.command(['get','pods','-l','app=provisioner','-o','json'])),dep);op.pod=before['name'];op.rs=owner['name']
        verify_owner(owner,json.loads(op.command(['get','replicaset',op.rs,'-o','json'])))
        out['provisioner_sample']=before;observed=json.loads(op.command(['exec',op.pod,'-c','provisioner','--','python3','-I','-B','-c',op.child]));out['provisioner_inputs']=observed
        need(observed.get('status')=='PROVISIONER_RESOURCE_INPUTS_OBSERVED_NO_CHANGE','PROVISIONER_SOURCE_INCOMPLETE')
        nodes=summarize_nodes(json.loads(op.command(['get','nodes','-o','json'])));out['nodes']=nodes
        out['native_node_allocations']=native_allocations(op.command(['describe','nodes']),[row['name'] for row in nodes])
        rows,counts=sandbox_rows(op.command(SANDBOX_LIST));out.update(sandbox_count=len(rows),sandboxes_by_node=counts)
        out['sandbox_projection_readiness_basis']='SANDBOX_CONTAINER_ONLY_NOT_POD_READY'
        out['named_thread_existing_sandboxes']={thread:[{**{key:row[key] for key in ('name','node','phase','deleting')},'sandbox_container_ready':row['ready']} for row in rows if thread in row['named_thread_matches']] for thread in NAMED_THREADS}
        out['userdata_subpath_or_owner_exported']=False
        ready=sorted((row for row in rows if row['ready'] and not row['deleting'] and row['phase']=='Running'),key=lambda row:(NAMED_THREADS[0] not in row['named_thread_matches'],row['name']))
        if ready:
            chosen=ready[0];op.sandbox=chosen['name'];out['ordinary_sandbox_template']=sandbox_template(json.loads(op.command(['get','pod',op.sandbox,'-o','json'])),chosen)
        else:out['ordinary_sandbox_template_unavailable']=True
        need(before==pod_identity(json.loads(op.command(['get','pod',op.pod,'-o','json']))),'PROVISIONER_CHANGED_DURING_READ')
        out.update(status='SANDBOX_NATIVE_CAPACITY_AND_TEMPLATE_OBSERVED_NO_CHANGE',stable_provisioner_readback=True)
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'CAPACITY_INSPECTION_FAILED_REDACTED')
    out.update(queries=op.queries if op else 0,observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat());print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
