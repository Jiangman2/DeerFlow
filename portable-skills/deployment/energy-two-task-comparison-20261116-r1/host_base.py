"""Read one report notification's durable state; no mutation or send support."""
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

THREAD='aa98356f-e332-4019-9d68-0d1e79c30ed7'
GATEWAY_UID='a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=20s','-n','deer-flow']
LIST=['get','pods','-l','app=gateway','--no-headers','-o','custom-columns=NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,READY:.status.conditions[?(@.type=="Ready")].status,DELETING:.metadata.deletionTimestamp']
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

class Inspection:
    def __init__(self,child):self.child=child;self.pod=None;self.rs=None;self.queries=0
    def command(self,args,body=None):
        allowed=args==LIST or args==['get','deployment','gateway','-o','json']
        if self.pod:allowed=allowed or args==['get','pod',self.pod,'-o','json'] or args==['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child] and body==canonical({'thread_id':THREAD})
        if self.rs:allowed=allowed or args==['get','replicaset',self.rs,'-o','json']
        need(allowed and (args[0]=='exec' or body is None),'FIXED_READONLY_COMMAND_REQUIRED')
        need(self.queries<7,'SEVEN_QUERY_BOUND');self.queries+=1
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
        print(json.dumps({'phase':'READ_ONLY_NAMED_TASK_NOTIFICATION_AND_FORMATTER_STATE'}),flush=True)
        result=json.loads(self.command(['exec','-i',self.pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.child],canonical({'thread_id':THREAD})))
        current=json.loads(self.command(['get','pod',self.pod,'-o','json']))
        need(pod_identity(current)==snapshot,'SAMPLE_CHANGED_DURING_INSPECTION')
        latest=json.loads(self.command(['get','deployment','gateway','-o','json']));need(identity(latest)==before,'GATEWAY_CHANGED_DURING_INSPECTION')
        return {'gateway':before,'sample':{'name':self.pod,'uid':uid},'stable_readback':True,'observation':result}

def main():
    out={'schema':'energy-work-return-inspection.v1','status':'INCOMPLETE','thread_id':THREAD,'workload_modified':False,
         'database_modified':False,'persistent_files_modified':False,'credentials_exported':False,'secret_api_reads':0,
         'provider_requests':0,'work_messages_sent':0,'task_retry_requested':False,'raw_messages_exported':False}
    inspector=None
    try:
        need(sys.argv[1:]==['--inspect-readonly'],'EXPLICIT_READONLY_MODE_REQUIRED')
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        raw=base64.b64decode(ASSET['base64'],validate=True);need(len(raw)==ASSET['bytes'] and sha(raw)==ASSET['sha256'],'SEALED_CHILD_CHANGED')
        inspector=Inspection(raw.decode());out.update(inspector.inspect())
        need(out['observation'].get('status')=='WORK_RETURN_DIAGNOSTIC_COLLECTED_NO_CHANGE','READONLY_CHILD_INCOMPLETE')
        out['status']='WORK_RETURN_DIAGNOSTIC_COLLECTED_NO_CHANGE'
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'INSPECTION_FAILURE_REDACTED')
    out['queries']=inspector.queries if inspector else 0
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())
