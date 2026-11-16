"""Read scheduler queue capacity and the two screenshot-named tasks; no writes."""
import argparse
import base64
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from decimal import Decimal

TITLE='油气与金融市场日报'
GATEWAY_UID='a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=20s','-n','deer-flow']
LIST=['get','pods','-l','app=gateway','--no-headers','-o','custom-columns=NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,READY:.status.conditions[?(@.type=="Ready")].status,DELETING:.metadata.deletionTimestamp']
NODES=['get','nodes','--no-headers','-o','custom-columns=NAME:.metadata.name,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory,UNSCHED:.spec.unschedulable,READY:.status.conditions[?(@.type=="Ready")].status,TAINTS:.spec.taints[*].effect']
REQUESTS=['get','pods','-A','--no-headers','-o','custom-columns=NS:.metadata.namespace,NODE:.spec.nodeName,PHASE:.status.phase,CPU:.spec.containers[*].resources.requests.cpu,MEM:.spec.containers[*].resources.requests.memory']
ASSET={}

class Refused(ValueError):pass
def need(value,code):
    if not value:raise Refused(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def identity(row):return {'uid':row['metadata']['uid'],'generation':row['metadata'].get('generation'),'spec_sha256':sha(canonical(row['spec']))}
def pod_identity(row):
    return {'uid':row['metadata']['uid'],'spec_sha256':sha(canonical(row['spec'])),
            'containers':[{key:item.get(key) for key in ('name','containerID','imageID','restartCount','ready','state')} for item in row.get('status',{}).get('containerStatuses',[])]}

def quantity(raw):
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([KMGTE]i|[kMGTPE]|m)?',raw)
    need(match is not None,'RESOURCE_QUANTITY_UNSUPPORTED')
    scale={'':1,'m':Decimal('0.001'),'k':1000,'M':1000**2,'G':1000**3,'T':1000**4,'P':1000**5,'E':1000**6,
           'Ki':1024,'Mi':1024**2,'Gi':1024**3,'Ti':1024**4,'Ei':1024**6}
    return Decimal(match[1])*scale[match[2] or '']

def resource_projection(node_raw,pod_raw,pool):
    nodes={}
    for line in node_raw.decode('ascii').splitlines():
        parts=line.split();need(len(parts)==6,'NODE_PROJECTION_COLUMNS_REQUIRED')
        name,cpu,memory,unsched,ready,taints=parts
        nodes[name]={'name':name,'in_provisioner_pool':name in pool if pool else None,
                     'cpu_allocatable':cpu,'memory_allocatable':memory,'unschedulable':unsched=='true','ready':ready=='True',
                     'taint_effects':[] if taints=='<none>' else taints.split(','),
                     'regular_container_cpu_requests':Decimal(0),'regular_container_memory_requests_bytes':Decimal(0),'nonterminal_pods':0}
    lines=pod_raw.decode('ascii').splitlines();need(len(lines)<=4096,'POD_RESOURCE_PROJECTION_BOUND')
    for line in lines:
        parts=line.split();need(len(parts)==5,'POD_RESOURCE_PROJECTION_COLUMNS_REQUIRED')
        namespace,node,phase,cpu,memory=parts
        if phase in ('Succeeded','Failed') or node not in nodes:continue
        row=nodes[node];row['nonterminal_pods']+=1
        row['regular_container_cpu_requests']+=sum((quantity(value) for value in cpu.split(',') if value!='<none>'),Decimal(0))
        row['regular_container_memory_requests_bytes']+=sum((quantity(value) for value in memory.split(',') if value!='<none>'),Decimal(0))
    for row in nodes.values():
        row['regular_container_cpu_requests']=str(row['regular_container_cpu_requests'])
        row['regular_container_memory_requests_bytes']=int(row['regular_container_memory_requests_bytes'])
    return {'nodes':sorted(nodes.values(),key=lambda row:row['name']),'basis':'REGULAR_CONTAINER_REQUESTS_ONLY',
            'init_sidecar_overhead_and_pod_level_requests_not_reconciled':True,'scheduler_admission_or_real_usage_not_proven':True,
            'unscheduled_pods_excluded_from_per_node_requests':True}

def gateway_mounts(dep):
    maps={row['name']:row['configMap']['name'] for row in dep['spec']['template']['spec'].get('volumes',[]) if 'configMap' in row}
    return [{'container':container['name'],'volume':mount['name'],'configmap':maps[mount['name']],
             'mount_path':mount['mountPath'],'subpath':mount.get('subPath'),'read_only':mount.get('readOnly',False)}
            for container in dep['spec']['template']['spec'].get('containers',[]) for mount in container.get('volumeMounts',[])
            if mount['name'] in maps]

class Inspection:
    def __init__(self,child):self.child=child;self.pod=None;self.rs=None;self.queries=0
    def command(self,args,body=None):
        allowed=args==LIST or args==NODES or args==REQUESTS or args in (['get','deployment','gateway','-o','json'],['get','deployment','provisioner','-o','json'])
        if self.pod:allowed=allowed or args==['get','pod',self.pod,'-o','json'] or args==['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child] and body==canonical({'title':TITLE})
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        need(allowed and (args[0]=='exec' or body is None),'FIXED_READONLY_COMMAND_REQUIRED')
        need(self.queries<10,'TEN_QUERY_BOUND');self.queries+=1
        prefix=list(KUBE);timeout=30
        if args[0]=='exec':prefix[2]='--request-timeout=75s';timeout=80
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
            if body is not None:incoming.write(body);incoming.seek(0)
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True,
                env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            until=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    need(os.fstat(stdout.fileno()).st_size<=4194304 and os.fstat(stderr.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic()<until,'READ_DEADLINE');time.sleep(.05)
                need(process.returncode in ((0,1) if args[0]=='exec' else (0,)),'READ_COMMAND_FAILED')
                need(os.fstat(stdout.fileno()).st_size<=4194304 and os.fstat(stderr.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                stdout.seek(0);return stdout.read(4194305)
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

    def inspect(self):
        dep=json.loads(self.command(['get','deployment','gateway','-o','json']));before=identity(dep)
        need(before['uid']==GATEWAY_UID,'GATEWAY_UID_CHANGED')
        rows=self.command(LIST).decode('ascii').splitlines();need(len(rows)<=256,'POD_LIST_BOUND')
        choices=[]
        for line in rows:
            fields=line.split();need(len(fields)==5,'POD_PROJECTION_COLUMNS_REQUIRED')
            if fields[2:]==['Running','True','<none>']:
                need(re.fullmatch('gateway-[a-z0-9-]{1,180}',fields[0]),'GATEWAY_POD_NAME_REQUIRED');choices.append(fields[:2])
        need(choices,'READY_GATEWAY_REQUIRED');self.pod,uid=sorted(choices)[0]
        pod=json.loads(self.command(['get','pod',self.pod,'-o','json']));need(pod['metadata']['uid']==uid,'SAMPLE_POD_CHANGED')
        need(not pod['metadata'].get('deletionTimestamp'),'TERMINATING_SAMPLE_REFUSED');snapshot=pod_identity(pod)
        owners=[row for row in pod['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet' and re.fullmatch('gateway-[a-z0-9-]{1,180}',owners[0].get('name','')),'GATEWAY_REPLICASET_REQUIRED')
        self.rs=owners[0]['name'];rs=json.loads(self.command(['get','replicaset',self.rs,'-o','json']))
        need(rs['metadata']['uid']==owners[0]['uid'],'SAMPLE_REPLICASET_CHANGED')
        controllers=[row for row in rs['metadata'].get('ownerReferences',[]) if row.get('controller') is True]
        need(len(controllers)==1 and controllers[0].get('kind')=='Deployment' and controllers[0].get('uid')==GATEWAY_UID,'SAMPLE_DEPLOYMENT_CHANGED')
        print(json.dumps({'phase':'READ_ONLY_SCHEDULER_CAPACITY_CLAIMS_AND_TWO_NAMED_TASKS'}),flush=True)
        result=json.loads(self.command(['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child],canonical({'title':TITLE})))
        current=json.loads(self.command(['get','pod',self.pod,'-o','json']))
        need(pod_identity(current)==snapshot,'SAMPLE_CHANGED_DURING_INSPECTION')
        latest=json.loads(self.command(['get','deployment','gateway','-o','json']));need(identity(latest)==before,'GATEWAY_CHANGED_DURING_INSPECTION')
        extra={}
        try:
            provisioner=json.loads(self.command(['get','deployment','provisioner','-o','json']))
            allowed={'SANDBOX_CPU_REQUEST','SANDBOX_MEMORY_REQUEST','SANDBOX_CPU_LIMIT','SANDBOX_MEMORY_LIMIT','SANDBOX_NODE_POOL'}
            values={row['name']:row['value'] for container in provisioner['spec']['template']['spec'].get('containers',[])
                    for row in container.get('env',[]) if row['name'] in allowed and 'value' in row}
            need(all(re.fullmatch('[A-Za-z0-9_.,:/-]{1,512}',value) for value in values.values()),'PROVISIONER_RESOURCE_VALUE_BOUND')
            extra={'provisioner':identity(provisioner),'explicit_resource_environment':values,
                   'missing_resource_keys_not_defaulted':True,
                   'resources':resource_projection(self.command(NODES),self.command(REQUESTS),[part for part in values.get('SANDBOX_NODE_POOL','').split(',') if part])}
        except Exception as exc:
            extra.update(resource_projection_status='INCOMPLETE',error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'RESOURCE_READ_REDACTED')
        return {'gateway':before,'gateway_configmap_mounts':gateway_mounts(dep),
                'gateway_replicas':{key:latest.get('status',{}).get(key) for key in ('replicas','updatedReplicas','readyReplicas','availableReplicas')},
                'sample':{'name':self.pod,'uid':uid},'stable_readback':True,'observation':result,'resource_observation':extra}

def main():
    out={'schema':'scheduler-queue-inspection.v1','status':'INCOMPLETE','task_title':TITLE,'workload_modified':False,
         'database_modified':False,'persistent_files_modified':False,'credentials_exported':False,'secret_api_reads':0,
         'provider_requests':0,'work_messages_sent':0,'task_retry_requested':False,'raw_messages_exported':False}
    inspector=None
    try:
        need(sys.argv[1:]==['--inspect-readonly'],'EXPLICIT_READONLY_MODE_REQUIRED')
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        raw=base64.b64decode(ASSET['base64'],validate=True);need(len(raw)==ASSET['bytes'] and sha(raw)==ASSET['sha256'],'SEALED_CHILD_CHANGED')
        inspector=Inspection(raw.decode());out.update(inspector.inspect())
        need(out['observation'].get('status')=='SCHEDULER_QUEUE_METADATA_OBSERVED_NO_CHANGE','READONLY_CHILD_INCOMPLETE')
        out['status']='SCHEDULER_QUEUE_METADATA_OBSERVED_NO_CHANGE'
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'INSPECTION_FAILURE_REDACTED')
    out['queries']=inspector.queries if inspector else 0
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
