"""Publish one qualified browser fix; no Gateway/Provisioner/Secret mutation.

Python 3.7 operator. A root init stages a private backup, public code candidate
and non-secret browser dependency. UID1000 verifies the actual shared mount
before the root publisher switches only this Skill. No active-task idle gate.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ASSETS = {}
CONTROL = Path('/var/lib/portal-energy-browser-fix-install-20261112-r1')
QUALIFICATION = Path('/var/lib/portal-energy-browser-nonroot-qualification-20261112-r4/REPORT.json')
QUALIFIED_RUNTIME = QUALIFICATION.parent / 'RUNTIME.json'
PACKAGE_SHA = 'ba86a3c48dd2b6f4349f3884dfab541730759dc1670ab5f0e7bd72377c439084'
OLD_TREE = 'f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044'
EXPECTED_FIXED_PNG_SHA = 'bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b'
IMAGE = 'deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
NODE = 'ecs-zdz-pro-0015'
NODE_UID = 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'
CLAIM = 'deer-flow-skills-rwx-pvc'
CLAIM_UID = '5bd3d4ed-54df-45c7-8954-d32ff8e53ec9'
LABEL = 'portal.energy-browser-fix/operation'
KUBE = ['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=20s','-n','deer-flow']
BASELINES = {
 'gateway':('a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1',414,'542846c6041c25b7ba94073c4b1b58f883910b8042d00951e33455994df76877'),
 'frontend':('bb0b3f6e-31f7-4073-8b76-7650ea5b96ee',243,'8b9c78dac10bfdd4cb656e813918dd0998c7351956d03cf1203efb0214e40aaf'),
 'provisioner':('9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253',87,'bc12d70e8afe965ff984a8d2e961bfc5675edda73d98a5061a8e4175c29a7c12'),
}


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def safe(path):
    for part in (path,) + tuple(path.parents):
        if not part.exists() and not part.is_symlink():
            continue
        need(not part.is_symlink(), 'PRIVATE_PATH_LINK')
    return path


def read(path, maximum=4194304):
    safe(path)
    fd = os.open(str(path),os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        first = os.fstat(fd)
        need(stat.S_ISREG(first.st_mode) and first.st_nlink == 1 and 0 < first.st_size <= maximum, 'PRIVATE_FILE_BOUND')
        if os.name == 'posix':
            need(first.st_uid == 0 and not stat.S_IMODE(first.st_mode) & 0o077, 'ROOT_PRIVATE_RECEIPT_REQUIRED')
        with os.fdopen(fd,'rb',closefd=False) as stream:
            raw = stream.read(maximum+1)
        stamp = lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns if os.name == 'posix' else None)
        need(len(raw) == first.st_size and stamp(first) == stamp(os.fstat(fd)) == stamp(path.stat()), 'PRIVATE_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def private_write(path, raw):
    safe(path)
    fd = os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())


def load_assets():
    need(set(ASSETS) == {'publisher','verifier','package','test_files'}, 'ASSETS_NOT_SEALED')
    result = {}
    for name,item in ASSETS.items():
        raw = base64.b64decode(item['base64'],validate=True)
        need(len(raw) <= 4194304 and sha(raw) == item['sha256'], 'ASSET_CHANGED')
        result[name] = raw
    need(sha(result['package']) == PACKAGE_SHA, 'CODE_PACKAGE_CHANGED')
    compile(result['publisher'],'<fixed-publisher>','exec')
    compile(result['verifier'],'<fixed-nonroot-verifier>','exec')
    return result


def qualification_binding():
    raw, runtime_raw = read(QUALIFICATION), read(QUALIFIED_RUNTIME)
    report, runtime = json.loads(raw), json.loads(runtime_raw)
    observation = report.get('observation',{})
    need(report.get('status') == 'NONROOT_BROWSER_AND_REPORT_QUALIFIED_NOT_PUBLISHED' and report.get('maintenance_pod_absent') is True
         and report.get('package_sha256') == PACKAGE_SHA and report.get('source_image_id') == IMAGE_ID, 'NONROOT_QUALIFICATION_REQUIRED')
    need(observation.get('status') == 'NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED' and observation.get('uid') == 1000
         and observation.get('gid') == 1000 and observation.get('credentials_read') is False and observation.get('provider_requests') == 0
         and observation.get('runtime_loader',{}).get('automatic_binding_used') is True, 'AUTOMATIC_NONROOT_RENDER_REQUIRED')
    digest = sha(runtime_raw)
    need(observation['runtime']['inventory_sha256'] == digest and runtime.get('source_image_id') == IMAGE_ID, 'QUALIFIED_RUNTIME_CHANGED')
    need(observation['render']['sha256'] == EXPECTED_FIXED_PNG_SHA, 'QUALIFIED_PNG_VISUAL_REVIEW_REQUIRED')
    binding = {'path':str(QUALIFICATION),'sha256':sha(raw),'operation':report['operation'],'png_sha256':observation['render']['sha256']}
    return report,runtime,digest,binding


def security(uid):
    return {'runAsUser':uid,'runAsGroup':1000,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}


def pod_manifest(operation,name,configmap):
    prefix = '.energy-browser-fix-release-20261112-r1-' + operation
    source = '/tmp/energy-browser-qualification/skills'
    command = "import pathlib,sys;sys.stdin=open('/release/stage.json',encoding='utf-8');exec(compile(pathlib.Path('/release/publisher.py').read_bytes(),'/release/publisher.py','exec'))"
    root_mounts = [{'name':'skills','mountPath':'/mnt/skills'}, {'name':'release','mountPath':'/release','readOnly':True}, {'name':'tmp','mountPath':'/tmp'}]
    return {'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':'deer-flow','labels':{LABEL:operation,'app':'energy-browser-fix-publisher'}},
            'spec':{'nodeName':NODE,'restartPolicy':'Never','automountServiceAccountToken':False,'enableServiceLinks':False,'activeDeadlineSeconds':1100,
                    'terminationGracePeriodSeconds':5,'securityContext':{'fsGroup':1000},
                    'initContainers':[{'name':'stage','image':IMAGE,'imagePullPolicy':'Never','command':['python3','-I','-B','-c',command],
                                       'securityContext':security(0),'resources':{'requests':{'cpu':'100m','memory':'128Mi'},'limits':{'cpu':'1','memory':'768Mi'}},'volumeMounts':root_mounts}],
                    'containers':[{'name':'publisher','image':IMAGE,'imagePullPolicy':'Never','command':['python3','-I','-B','-c','import time;time.sleep(1000)'],
                                   'securityContext':security(0),'resources':{'requests':{'cpu':'50m','memory':'128Mi'},'limits':{'cpu':'1','memory':'768Mi'}},'volumeMounts':root_mounts},
                                  {'name':'qualification','image':IMAGE,'imagePullPolicy':'Never','command':['python3','-I','-B','-c','import time;time.sleep(1000)'],
                                   'securityContext':dict(security(1000),runAsNonRoot=True),'env':[{'name':'HOME','value':'/tmp'},{'name':'PYTHONDONTWRITEBYTECODE','value':'1'}],
                                   'resources':{'requests':{'cpu':'250m','memory':'768Mi'},'limits':{'cpu':'1','memory':'2Gi'}},
                                   'volumeMounts':[{'name':'tmp','mountPath':'/tmp'},
                                                  {'name':'skills','mountPath':source+'/public/energy-market-daily','subPath':prefix+'/candidate','readOnly':True},
                                                  {'name':'skills','mountPath':source+'/.energy-browser-runtime-20261112-r1/linux-arm64','subPath':'.energy-browser-runtime-20261112-r1/linux-arm64','readOnly':True},
                                                  {'name':'shm','mountPath':'/dev/shm'}]}],
                    'volumes':[{'name':'skills','persistentVolumeClaim':{'claimName':CLAIM}}, {'name':'release','configMap':{'name':configmap,'defaultMode':292}},
                               {'name':'tmp','emptyDir':{'sizeLimit':'1Gi'}}, {'name':'shm','emptyDir':{'medium':'Memory','sizeLimit':'128Mi'}}]}}


def guard_pod(pod,requested,uid=None,status=True):
    meta,spec = pod['metadata'],pod['spec']
    need(meta.get('name') == requested['metadata']['name'] and meta.get('labels',{}).get(LABEL) == requested['metadata']['labels'][LABEL]
         and not meta.get('ownerReferences') and not meta.get('deletionTimestamp') and (uid is None or meta.get('uid') == uid), 'OWN_POD_IDENTITY')
    need(not any(spec.get(key) for key in ('hostNetwork','hostPID','hostIPC','shareProcessNamespace','ephemeralContainers')), 'POD_UNEXPECTED_ACCESS')
    for key,value in requested['spec'].items():
        if key not in ('containers','initContainers'):
            need(spec.get(key) == value,'OWN_POD_SPEC_CHANGED')
    for key in ('containers','initContainers'):
        actual,expected = spec.get(key,[]),requested['spec'][key]
        need(len(actual) == len(expected),'CONTAINER_SHAPE')
        for left,right in zip(actual,expected):
            need(not any(left.get(field) for field in ('envFrom','lifecycle','ports','volumeDevices','livenessProbe','readinessProbe','startupProbe')),'CONTAINER_UNEXPECTED_ACCESS')
            need(all(left.get(field) == value for field,value in right.items()),'CONTAINER_SPEC_CHANGED')
    if not status:
        return False
    initial = pod.get('status',{}).get('initContainerStatuses',[])
    if initial and initial[0].get('state',{}).get('terminated'):
        need(initial[0]['state']['terminated'].get('exitCode') == 0,'STAGING_INIT_FAILED')
        need(initial[0].get('imageID') == IMAGE_ID and initial[0].get('restartCount') == 0,'INIT_IMAGE_CHANGED')
    states = pod.get('status',{}).get('containerStatuses',[])
    if pod.get('status',{}).get('phase') == 'Running' and len(states) == 2 and all(row.get('ready') for row in states):
        need(len(initial) == 1 and initial[0].get('imageID') == IMAGE_ID and all(row.get('imageID') == IMAGE_ID and row.get('restartCount') == 0 for row in states),'CACHED_RUNTIME_CHANGED')
        return True
    need(pod.get('status',{}).get('phase') not in ('Failed','Succeeded'),'OWN_POD_EXITED')
    return False


class Operation:
    def __init__(self,assets):
        self.assets,self.operation,self.started,self.commands = assets,uuid.uuid4().hex,time.monotonic(),0
        self.pod_name='energy-browser-fix-'+self.operation[:12]
        self.cm_name='energy-browser-fix-code-'+self.operation[:12]
        self.pod_uid=self.cm_uid=None
        self.requested=pod_manifest(self.operation,self.pod_name,self.cm_name)
        self.cm=None
        self.claimed=self.create_pod_requested=self.create_cm_requested=False
        self.out={'schema':'energy-browser-fix-install.v1','status':'INCOMPLETE','operation':self.operation,'once_claimed':False,
                  'automatic_retry':False,'automatic_rollback':False,'gateway_modified':False,'provisioner_modified':False,'existing_sandboxes_modified':False,
                  'secret_api_reads':0,'credentials_read':False,'credentials_exported':False,'provider_requests':0,'work_messages_sent':0,'schedule_created':False,
                  'skill_updated':False,'code_package_sha256':PACKAGE_SHA,'active_task_check_skipped_by_operator_request':True,'gateway_rollout_completion_required':False}

    def command(self,args,body=None,timeout=30,limit=4194304):
        need(self.commands < 240 and time.monotonic()-self.started < 1400,'OPERATION_BOUND')
        fixed=[['get','deployments']+list(BASELINES)+['-o','json'],['get','node',NODE,'-o','json'],['get','pvc',CLAIM,'-o','json'],
               ['get','pod',self.pod_name,'--ignore-not-found','-o','json'],['get','configmap',self.cm_name,'--ignore-not-found','-o','json'],
               ['logs',self.pod_name,'-c','stage','--tail=8','--limit-bytes=16384']]
        allowed=args in fixed and body is None
        if args==['create','-f','-','-o','json']:
            allowed=body in (canonical(self.cm) if self.cm else None,canonical(self.requested)) and body is not None
        if args[:1]==['exec']:
            container,code = args[3] if len(args)>3 else None,args[10] if len(args)>10 else None
            allowed=(args in [['exec','-i',self.pod_name,'-c',name,'--','python3','-I','-B','-c',self.assets[key].decode()] for name,key in (('publisher','publisher'),('qualification','verifier'))]
                     and self.pod_uid is not None and isinstance(body,bytes))
        if args[:1]==['delete']:
            for plural,name,uid in (('pods',self.pod_name,self.pod_uid),('configmaps',self.cm_name,self.cm_uid)):
                expected=canonical({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':uid},'gracePeriodSeconds':5})
                if args==['delete','--raw','/api/v1/namespaces/deer-flow/'+plural+'/'+name,'-f','-']:
                    allowed=uid is not None and body==expected
        need(allowed,'FIXED_OPERATION_REQUIRED')
        self.commands+=1
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            if body is not None:
                need(len(body)<=4194304,'INPUT_BOUND');incoming.write(body);incoming.seek(0)
            prefix=list(KUBE);prefix[2]='--request-timeout=%ds'%max(20,min(300,int(timeout)-3))
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=output,stderr=errors,
                                     env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'},start_new_session=True)
            until=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size<=limit and os.fstat(errors.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic()<until,'COMMAND_DEADLINE');time.sleep(.1)
                need(process.returncode in ((0,1) if args[:1]==['exec'] else (0,)),'COMMAND_FAILED_REDACTED')
                need(os.fstat(output.fileno()).st_size<=limit and os.fstat(errors.fileno()).st_size<=65536,'COMMAND_OUTPUT_BOUND')
                output.seek(0);raw=output.read(limit+1)
                if args[:1]==['logs']:
                    return raw.decode('utf-8','replace')
                return json.loads(raw) if raw.strip() else None
            finally:
                if process.poll() is None:
                    os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

    def baseline(self):
        rows=self.command(['get','deployments']+list(BASELINES)+['-o','json'])['items']
        result={}
        for row in rows:
            name=row['metadata']['name'];identity=(row['metadata']['uid'],row['metadata']['generation'],sha(canonical(row['spec'])))
            need(name in BASELINES and identity==BASELINES[name] and not row['metadata'].get('deletionTimestamp'),'DEPLOYMENT_BASELINE_CHANGED')
            result[name]={'uid':identity[0],'generation':identity[1],'spec_sha256':identity[2]}
        need(set(result)==set(BASELINES),'DEPLOYMENT_SET_CHANGED')
        return result

    def child(self,name,key,payload,timeout=90):
        result=self.command(['exec','-i',self.pod_name,'-c',name,'--','python3','-I','-B','-c',self.assets[key].decode()],canonical(payload),timeout)
        need(isinstance(result,dict),'FIXED_CHILD_RESPONSE_INVALID')
        if result.get('status')=='INCOMPLETE':
            diagnostic={field:result[field] for field in ('status','error_code','error_class','last_step','png_sha256','expected_png_sha256')
                        if isinstance(result.get(field),str) and re.fullmatch('[A-Za-z0-9_]{1,100}',result[field])}
            self.out['child_failure']=dict(diagnostic,container=name,action=payload.get('action','render'))
            raise Refused('FIXED_CHILD_REPORTED_FAILURE')
        return result

    def run(self):
        report,runtime,runtime_hash,binding=qualification_binding()
        self.out['accepted_qualification']=binding
        self.out['runtime']={'manifest_sha256':runtime_hash,'source_image_id':IMAGE_ID}
        self.out['before']=self.baseline()
        node=self.command(['get','node',NODE,'-o','json'])
        need(node['metadata']['uid']==NODE_UID and not node['spec'].get('unschedulable') and any(r.get('type')=='Ready' and r.get('status')=='True' for r in node.get('status',{}).get('conditions',[])),'FIXED_NODE_NOT_READY')
        claim=self.command(['get','pvc',CLAIM,'-o','json'])
        need(claim['metadata']['uid']==CLAIM_UID and claim.get('status',{}).get('phase')=='Bound','SKILLS_PVC_CHANGED')
        need(not safe(CONTROL).exists(),'ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
        CONTROL.mkdir(mode=0o700);self.claimed=True;self.out['once_claimed']=True
        stage={'action':'stage','operation':self.operation,'package':base64.b64encode(self.assets['package']).decode(),'package_sha256':PACKAGE_SHA,
               'runtime_manifest':runtime,'runtime_manifest_sha256':runtime_hash}
        self.cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':self.cm_name,'namespace':'deer-flow','labels':{LABEL:self.operation}},'immutable':True,
                 'data':{'publisher.py':self.assets['publisher'].decode(),'stage.json':canonical(stage).decode()}}
        self.create_cm_requested=True
        cm=self.command(['create','-f','-','-o','json'],canonical(self.cm));self.cm_uid=cm['metadata']['uid']
        need(cm.get('data')==self.cm['data'] and cm.get('immutable') is True,'CODE_CONFIGMAP_CHANGED')
        print(json.dumps({'phase':'STAGE_PRIVATE_BACKUP_AND_READABLE_BROWSER_RUNTIME'}),flush=True)
        self.out['runtime_install_may_have_applied']=True
        self.create_pod_requested=True
        pod=self.command(['create','-f','-','-o','json'],canonical(self.requested));self.pod_uid=pod['metadata']['uid']
        self.out.update(maintenance_pod=self.pod_name,maintenance_pod_uid=self.pod_uid)
        until=time.monotonic()+220
        while True:
            pod=self.command(['get','pod',self.pod_name,'--ignore-not-found','-o','json'])
            need(pod is not None,'OWN_POD_DISAPPEARED')
            if guard_pod(pod,self.requested,self.pod_uid):
                break
            need(time.monotonic()<until,'STAGING_POD_NOT_READY');time.sleep(2)
        staged=self.child('publisher','publisher',{'action':'inspect-stage','operation':self.operation})
        need(staged.get('status')=='BROWSER_FIX_STAGED' and staged.get('after_files')==107 and staged.get('before_tree_sha256')==OLD_TREE,'STAGED_CANDIDATE_NOT_ACCEPTED')
        self.out['staging']=staged
        expected_tree=staged['after_tree_sha256']
        verify={'package_sha256':PACKAGE_SHA,'after_tree_sha256':expected_tree,'runtime_manifest_sha256':runtime_hash,
                'png_sha256':binding['png_sha256'],'test_files':json.loads(self.assets['test_files'])}
        print(json.dumps({'phase':'VERIFY_ACTUAL_SHARED_MOUNTS_AS_UID_1000'}),flush=True)
        accepted=self.child('qualification','verifier',verify,timeout=210)
        self.out['shared_runtime_qualification']=accepted
        need(accepted.get('status')=='NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED' and accepted.get('uid')==1000 and accepted.get('gid')==1000
             and accepted.get('package_sha256')==PACKAGE_SHA and accepted.get('runtime_manifest_sha256')==runtime_hash
             and accepted.get('after_tree_sha256')==expected_tree and accepted.get('png_sha256')==binding['png_sha256']
             and accepted.get('provider_requests')==0 and accepted.get('credentials_read') is False
             and accepted.get('automatic_binding_used') is True and accepted.get('private_local_copy_cleaned') is True
             and accepted.get('temporary_project_cleaned') is True,'SHARED_NONROOT_PNG_NOT_ACCEPTED')
        need(self.baseline()==self.out['before'] and qualification_binding()[3]==binding,'PRODUCTION_BASELINE_CHANGED_BEFORE_SWITCH')
        print(json.dumps({'phase':'PUBLISH_ONLY_QUALIFIED_ENERGY_SKILL'}),flush=True)
        self.out['skill_switch_requested']=True
        self.out['skill_switch_may_have_applied']=True
        publication=self.child('publisher','publisher',{'action':'publish','operation':self.operation,'acceptance':accepted},timeout=120)
        self.out['publication']=publication
        need(publication.get('status')=='BROWSER_FIX_SKILL_PUBLISHED' and publication.get('after_files')==107
             and publication.get('before_tree_sha256')==OLD_TREE and publication.get('after_tree_sha256')==expected_tree,'PUBLISHED_TREE_CHANGED')
        self.out['skill_updated']=True
        final=self.child('publisher','publisher',{'action':'verify','operation':self.operation})
        need(final.get('status')=='BROWSER_FIX_SKILL_AND_RUNTIME_VERIFIED' and final['after_tree_sha256']==expected_tree,'FINAL_SKILL_TREE_CHANGED')
        need(self.baseline()==self.out['before'],'OTHER_DEPLOYMENT_CHANGED_DURING_PUBLICATION')
        self.out.update(status='ENERGY_BROWSER_FIX_INSTALLED_RUNTIME_ACCEPTED',browser_business_acceptance_pending=True)

    def cleanup(self):
        if self.create_pod_requested:
            pod=self.command(['get','pod',self.pod_name,'--ignore-not-found','-o','json'])
            if pod is not None:
                guard_pod(pod,self.requested,self.pod_uid,status=False);self.pod_uid=pod['metadata']['uid']
                self.command(['delete','--raw','/api/v1/namespaces/deer-flow/pods/'+self.pod_name,'-f','-'],canonical({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':self.pod_uid},'gracePeriodSeconds':5}))
                until=time.monotonic()+45
                while time.monotonic()<until and self.command(['get','pod',self.pod_name,'--ignore-not-found','-o','json']) is not None:
                    time.sleep(2)
            self.out['maintenance_pod_absent']=self.command(['get','pod',self.pod_name,'--ignore-not-found','-o','json']) is None
        if self.create_cm_requested:
            cm=self.command(['get','configmap',self.cm_name,'--ignore-not-found','-o','json'])
            if cm is not None:
                need(cm['metadata'].get('labels',{}).get(LABEL)==self.operation and cm.get('data')==self.cm['data'],'OWN_CONFIGMAP_CHANGED')
                self.cm_uid=cm['metadata']['uid']
                self.command(['delete','--raw','/api/v1/namespaces/deer-flow/configmaps/'+self.cm_name,'-f','-'],canonical({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':self.cm_uid},'gracePeriodSeconds':5}))
            self.out['maintenance_configmap_absent']=self.command(['get','configmap',self.cm_name,'--ignore-not-found','-o','json']) is None
        if self.create_pod_requested and not self.out.get('maintenance_pod_absent'):
            self.out.update(status='INCOMPLETE',maintenance_cleanup_pending=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install-browser-fix-once',required=True,action='store_true');parser.parse_args()
    operation=None
    out={'schema':'energy-browser-fix-install.v1','status':'INCOMPLETE','once_claimed':False,'skill_updated':False,'gateway_modified':False,'provider_requests':0,'credentials_exported':False}
    try:
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        operation=Operation(load_assets());out=operation.out;operation.run()
    except (Exception,KeyboardInterrupt) as exc:
        out['status']='INCOMPLETE';out['error_class']=type(exc).__name__
        out['error_code']=str(exc) if isinstance(exc,Refused) else 'INSTALL_FAILED_REDACTED'
        if operation and operation.pod_uid:
            try:
                diagnostic_pod=operation.command(['get','pod',operation.pod_name,'--ignore-not-found','-o','json'])
                need(diagnostic_pod is not None,'DIAGNOSTIC_POD_ABSENT')
                guard_pod(diagnostic_pod,operation.requested,operation.pod_uid,status=False)
                diagnostic=operation.command(['logs',operation.pod_name,'-c','stage','--tail=8','--limit-bytes=16384'])
                allowed={}
                for line in diagnostic.splitlines():
                    try:
                        row=json.loads(line)
                        for key in ('status','error_code','error_class','last_step'):
                            value=row.get(key)
                            if isinstance(value,str) and re.fullmatch('[A-Za-z0-9_]{1,100}',value):allowed[key]=value
                    except ValueError:pass
                out['stage_diagnostic']=allowed
            except Exception:out['stage_diagnostic_unavailable']=True
    finally:
        if operation:
            try:operation.cleanup()
            except Exception as exc:out.update(cleanup_error_class=type(exc).__name__,maintenance_cleanup_pending=True,status='INCOMPLETE')
    out.update(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),commands=operation.commands if operation else 0,
               elapsed_seconds=round(time.monotonic()-operation.started,3) if operation else 0)
    if operation and operation.claimed:
        raw=canonical(out);private_write(CONTROL/'REPORT.json',raw)
        out.update(report_path=str(CONTROL/'REPORT.json'),report_sha256=sha(raw),report_bytes=len(raw))
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':
    sys.exit(main())
