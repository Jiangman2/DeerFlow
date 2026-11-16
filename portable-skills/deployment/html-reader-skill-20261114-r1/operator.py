"""Install the frozen delivery Skill with its accepted public browser dependency.

Reuses the accepted existing public browser runtime. No Gateway/Provisioner,
Secret, database, provider, webhook, schedule or existing Sandbox mutations.
Template assets must be frozen and sealed before this entry can run.
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
CONTROL = Path('/var/lib/portal-energy-html-reader-skill-install-20261114-r1')
PACKAGE_SHA = 'UNSEALED_PACKAGE_SHA'
OLD_TREE = '2a09015d31cee41cd2a329fff96062848255d3d49fb7cec8c52d47c939345259'
CANDIDATE_VERSION = 'UNSEALED_CANDIDATE_VERSION'
CANDIDATE_FILES = 0
OFFLINE_TEST_COUNT = 0
EXPECTED_FIXED_PNG_SHA = 'bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b'
IMAGE = 'deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
NODE = 'ecs-zdz-pro-0015'
NODE_UID = 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'
CLAIM = 'deer-flow-skills-rwx-pvc'
CLAIM_UID = '5bd3d4ed-54df-45c7-8954-d32ff8e53ec9'
LABEL = 'portal.energy-html-reader-skill-install/operation'
KUBE = ['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=20s','-n','deer-flow']
ORDINARY_LIST = ['get','pods','-l','app.kubernetes.io/component=sandbox','-o','json']
VISIBILITY_CHILD = r'''
import hashlib,json,os,pathlib,stat,sys
out={'status':'INCOMPLETE','files_modified':False,'browser_launched':False,'credentials_read':False,'provider_requests':0}
def need(ok,code):
 if not ok:raise ValueError(code)
def read(path,limit):
 for part in (path,*path.parents):need(not part.is_symlink(),'SOURCE_LINK')
 before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size<=limit,'SOURCE_BOUND')
 raw=path.read_bytes();after=path.stat()
 need(len(raw)==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns),'SOURCE_CHANGED')
 return raw
try:
 if os.geteuid()==0:os.setgroups([]);os.setgid(1000);os.setuid(1000)
 need(os.geteuid()==1000 and os.getegid()==1000,'ORDINARY_UID_REQUIRED')
 EXPECTED_SKILL_COUNT = 0
 root=pathlib.Path('/mnt/skills/public/energy-market-daily');runtime=root.parent/'.energy-browser-runtime-20261112-r1/linux-arm64'
 rows=[]
 for directory,folders,names in os.walk(root,followlinks=False):
  for name in folders:need(not(pathlib.Path(directory)/name).is_symlink(),'SOURCE_LINK')
  for name in names:
   path=pathlib.Path(directory)/name;raw=read(path,1048576)
   rows.append({'path':path.relative_to(root).as_posix(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
   need(len(rows)<=128,'SKILL_COUNT_BOUND')
 rows.sort(key=lambda row:row['path'])
 tree=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
 need(len(rows)==EXPECTED_SKILL_COUNT and tree==sys.argv[1],'ORDINARY_SKILL_TREE_MISMATCH')
 raw=read(runtime/'RUNTIME.json',262144);digest=hashlib.sha256(raw).hexdigest()
 need(digest=='cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598','ORDINARY_RUNTIME_MANIFEST_MISMATCH')
 binding=json.loads(read(root/'assets/browser-runtime.json',32768))
 need(binding=={'schema':'energy-browser-runtime-binding.v1','directory':'../.energy-browser-runtime-20261112-r1/linux-arm64','manifest_sha256':digest},'ORDINARY_BINDING_MISMATCH')
 executable=runtime/'chrome-linux/headless_shell';state=executable.stat()
 need(stat.S_ISREG(state.st_mode) and state.st_size==298683784 and os.access(executable,os.R_OK),'ORDINARY_RUNTIME_NOT_READABLE')
 temporary=pathlib.Path('/tmp');space=os.statvfs(temporary)
 out.update(status='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE',uid=os.geteuid(),gid=os.getegid(),skill_tree_sha256=tree,runtime_manifest_sha256=digest,
  temporary={'path':'/tmp','readable':os.access(temporary,os.R_OK),'writable':os.access(temporary,os.W_OK),'searchable':os.access(temporary,os.X_OK),
  'noexec':bool(space.f_flag&getattr(os,'ST_NOEXEC',8)),'available_bytes':space.f_bavail*space.f_frsize},
  nondefault_tmpdir_configured=bool(os.environ.get('TMPDIR')))
except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,ValueError) else 'ORDINARY_VISIBILITY_FAILED_REDACTED')
print(json.dumps(out,sort_keys=True))
sys.exit(int(out['status']=='INCOMPLETE'))
'''
DEPLOYMENT_UIDS = {
 'gateway':'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1',
 'frontend':'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee',
 'provisioner':'9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253',
}
BASELINES = DEPLOYMENT_UIDS
OLD_INSTALL = Path('/var/lib/portal-energy-html-warning-skill-install-20261114-r1/REPORT.json')
OLD_INSTALL_SHA = '5dd40639193ff9a9776c235b084977d47219ea2fde4ff154822feca625066663'
OLD_INSTALL_BYTES = 9328
OLD_INSTALL_OPERATION = '91ae0c5e6aff42dbb0b73aef146732d0'
OLD_INSTALL_STATUS = 'ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING'
OLD_INSTALL_FILES = 108
RUNTIME_SHA = 'cef35e8edb510631197d5d4874e2e9d6fb2c9ef93a7be8393f38fa9446ea0598'
# The source-review owner must bind the real completed Gateway rollout receipt.
# An unfilled contract stops before command execution, once claim or staging.
GATEWAY_CONTEXT_RECEIPT = {
    'path': '/var/lib/portal-task-conversation-context-postcheck-20261114-r2/RUNTIME-ACCEPTANCE.json', 'bytes': 0,
    'sha256': 'UNBOUND_GATEWAY_CONTEXT_RECEIPT_SHA',
    'operation': 'UNBOUND_GATEWAY_CONTEXT_OPERATION',
    'schema': 'task-conversation-context-activation.v1',
    'status': 'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED',
}
GATEWAY_CONTEXT_SOURCE = {
    'parent_sha256':'a8e8b4f3ff94f79e2e0458414f4c2d55310bdd170a8859c00b99dd1d65ad929c',
    'candidate_sha256':'e598a0fd0e9d9a83ecdb781be6a345bf974cdef957852d181732f3596ad58330',
}
GATEWAY_CONTEXT_FAILURE = {
    'operation':'86bbcfd1fb7f40819284fa431ecce9f1', 'bytes':2698,
    'sha256':'5f14fb064db5c1d8ea563080f54f5955f3690d08e63665aec25959128999f464',
    'path':'/var/lib/portal-task-conversation-context-20261114-r1/REPORT.json',
    'plan_sha256':'880c16308caa9e09267a9cee041577caa0088cbf2b90100cbb5e5a91649bea0f',
}
GATEWAY_CONTEXT_REQUESTED_SPEC = 'd9a8a00422329427b737b3f67d1c13f35482a16d6614cbc3365b705ad198b616'
GATEWAY_CONTEXT_ACTUAL_SPEC = '7faa311dc54be05137ec9c756aa278927e78d47f28118f6779415729c9029aa1'



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
    need(CANDIDATE_VERSION == '2.1.5-portable' and type(CANDIDATE_FILES) is int and CANDIDATE_FILES > 107
         and OFFLINE_TEST_COUNT > 0
         and re.fullmatch('[0-9a-f]{64}', PACKAGE_SHA), 'CANDIDATE_NOT_SEALED')
    need(set(ASSETS) == {'publisher','verifier','package','test_files'}, 'CANDIDATE_NOT_SEALED')
    validate_gateway_context_contract()
    result = {}
    for name,item in ASSETS.items():
        raw = base64.b64decode(item['base64'],validate=True)
        need(len(raw) <= 4194304 and sha(raw) == item['sha256'], 'ASSET_CHANGED')
        result[name] = raw
    need(sha(result['package']) == PACKAGE_SHA, 'CODE_PACKAGE_CHANGED')
    compile(result['publisher'],'<fixed-publisher>','exec')
    compile(result['verifier'],'<fixed-nonroot-verifier>','exec')
    return result


def validate_gateway_context_contract():
    value = GATEWAY_CONTEXT_RECEIPT
    need(isinstance(value,dict) and set(value)=={'path','bytes','sha256','operation','schema','status'},
         'GATEWAY_CONTEXT_RECEIPT_NOT_BOUND')
    need(value['path']=='/var/lib/portal-task-conversation-context-postcheck-20261114-r2/RUNTIME-ACCEPTANCE.json'
         and type(value['bytes']) is int and 0<value['bytes']<=4194304
         and isinstance(value['sha256'],str) and re.fullmatch('[0-9a-f]{64}',value['sha256'])
         and isinstance(value['operation'],str) and re.fullmatch('[0-9a-f]{32}',value['operation'])
         and all(isinstance(value[key],str) and value[key] and not value[key].startswith('UNBOUND_') for key in ('schema','status')),
         'GATEWAY_CONTEXT_RECEIPT_NOT_BOUND')


def gateway_context_binding(current=None):
    validate_gateway_context_contract()
    expected = GATEWAY_CONTEXT_RECEIPT
    raw = read(Path(expected['path']))
    need(len(raw)==expected['bytes'] and sha(raw)==expected['sha256'], 'GATEWAY_CONTEXT_RECEIPT_CHANGED')
    report = json.loads(raw)
    need(report.get('schema')==expected['schema'] and report.get('status')==expected['status']
         and report.get('operation')==expected['operation']
         and report.get('all_gateway_pods_verified') is True
         and report.get('context_injection_runtime_accepted') is True
         and report.get('accepted_source')==GATEWAY_CONTEXT_SOURCE
         and report.get('verified_pods')==70 and report.get('verified_source_files_per_pod')==14
         and report.get('service_backends_verified') is True
         and report.get('future_scheduled_runs_budget')==10000 and report.get('global_max_recursion_limit')==10000
         and isinstance(report.get('plan_sha256'),str) and re.fullmatch('[0-9a-f]{64}',report['plan_sha256']),
         'GATEWAY_CONTEXT_RUNTIME_ACCEPTANCE_REQUIRED')
    gateway = report.get('after',{})
    need(isinstance(gateway,dict) and gateway.get('uid')==DEPLOYMENT_UIDS['gateway']
         and type(gateway.get('generation')) is int and gateway['generation']>0
         and isinstance(gateway.get('spec_sha256'),str) and re.fullmatch('[0-9a-f]{64}',gateway['spec_sha256']),
         'GATEWAY_CONTEXT_ACCEPTED_IDENTITY_REQUIRED')
    identity = {key:gateway[key] for key in ('uid','generation','spec_sha256')}
    defaulting = report.get('accepted_defaulting',{})
    need(report.get('tool_revision')=='postcheck-r2' and report.get('readonly') is True
         and report.get('workload_modified') is False and report.get('original_receipts_modified') is False
         and report.get('patch_request_count')==0 and report.get('configmap_create_requests')==0
         and report.get('original_patch_not_replayed') is True
         and report.get('only_observed_api_default_accepted') is True
         and report.get('accepted_original_failure')==GATEWAY_CONTEXT_FAILURE
         and report.get('operation')==GATEWAY_CONTEXT_FAILURE['operation']
         and report.get('plan_sha256')==GATEWAY_CONTEXT_FAILURE['plan_sha256']
         and report.get('requested_spec_sha256')==GATEWAY_CONTEXT_REQUESTED_SPEC
         and report.get('observed_spec_sha256')==GATEWAY_CONTEXT_ACTUAL_SPEC
         and identity['generation']==417 and identity['spec_sha256']==GATEWAY_CONTEXT_ACTUAL_SPEC
         and isinstance(defaulting,dict)
         and defaulting.get('schema')=='task-context-single-defaulting-field-proof.v1'
         and defaulting.get('gateway_uid')==identity['uid'] and defaulting.get('generation')==417
         and defaulting.get('requested_spec_sha256')==GATEWAY_CONTEXT_REQUESTED_SPEC
         and defaulting.get('actual_spec_sha256')==GATEWAY_CONTEXT_ACTUAL_SPEC
         and type(defaulting.get('actual_default_mode')) is int and defaulting['actual_default_mode']==420
         and defaulting.get('difference_count')==1
         and isinstance(defaulting.get('difference_path'),str)
         and re.fullmatch('/template/spec/volumes/[0-9]+/configMap/defaultMode',defaulting['difference_path'])
         and isinstance(defaulting.get('field_report_sha256'),str) and re.fullmatch('[0-9a-f]{64}',defaulting['field_report_sha256']),
         'GATEWAY_CONTEXT_POSTCHECK_PROOF_REQUIRED')
    if current is not None:
        need(current==identity,'GATEWAY_CONTEXT_LIVE_IDENTITY_CHANGED')
    return {'path':expected['path'],'bytes':len(raw),'sha256':sha(raw),'operation':expected['operation'],
            'gateway':identity,'context_injection_runtime_accepted':True,
            'qualification_synthetic_env_is_not_runtime_injection_evidence':True}


def qualification_binding():
    raw = read(OLD_INSTALL)
    need(len(raw) == OLD_INSTALL_BYTES and sha(raw) == OLD_INSTALL_SHA, 'ACCEPTED_DELIVERY_SKILL_INSTALL_CHANGED')
    report = json.loads(raw)
    publication, runtime = report.get('publication', {}), report.get('runtime', {})
    installed_runtime = publication.get('runtime', {})
    accepted = report.get('shared_runtime_qualification', {})
    need(report.get('status') == OLD_INSTALL_STATUS
         and report.get('operation') == OLD_INSTALL_OPERATION
         and report.get('skill_updated') is True and report.get('maintenance_pod_absent') is True
         and report.get('maintenance_configmap_absent') is True and publication.get('after_tree_sha256') == OLD_TREE
         and publication.get('after_files') == OLD_INSTALL_FILES, 'ACCEPTED_DELIVERY_SKILL_INSTALL_REQUIRED')
    need(runtime.get('manifest_sha256') == RUNTIME_SHA and runtime.get('source_image_id') == IMAGE_ID
         and installed_runtime.get('manifest_sha256') == RUNTIME_SHA and installed_runtime.get('source_image_id') == IMAGE_ID
         and installed_runtime.get('path') == '/mnt/skills/public/.energy-browser-runtime-20261112-r1/linux-arm64', 'ACCEPTED_PUBLIC_RUNTIME_CHANGED')
    need(accepted.get('uid') == 1000 and accepted.get('gid') == 1000 and accepted.get('png_sha256') == EXPECTED_FIXED_PNG_SHA,
         'ACCEPTED_PUBLIC_RENDER_REQUIRED')
    binding = {'path': str(OLD_INSTALL), 'bytes': len(raw), 'sha256': sha(raw), 'operation': report['operation'], 'png_sha256': EXPECTED_FIXED_PNG_SHA}
    return report, None, RUNTIME_SHA, binding

def security(uid):
    return {'runAsUser':uid,'runAsGroup':1000,'allowPrivilegeEscalation':False,'readOnlyRootFilesystem':True,'capabilities':{'drop':['ALL']}}


def pod_manifest(operation,name,configmap):
    prefix = '.energy-programmatic-skill-install-20261114-r1-' + operation
    command = "import pathlib,sys;sys.stdin=open('/release/stage.json',encoding='utf-8');exec(compile(pathlib.Path('/release/publisher.py').read_bytes(),'/release/publisher.py','exec'))"
    root_mounts = [{'name':'skills','mountPath':'/mnt/skills'}, {'name':'release','mountPath':'/release','readOnly':True}, {'name':'tmp','mountPath':'/tmp'}]
    return {'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':'deer-flow','labels':{LABEL:operation,'app':'energy-programmatic-skill-install'}},
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
                                                  {'name':'skills','mountPath':'/mnt/skills/public','subPath':'public','readOnly':True},
                                                  {'name':'skills','mountPath':'/mnt/skills/public/energy-market-daily','subPath':prefix+'/candidate','readOnly':True},
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
        self.pod_name='energy-programmatic-skill-'+self.operation[:12]
        self.cm_name='energy-programmatic-skill-code-'+self.operation[:12]
        self.pod_uid=self.cm_uid=None
        self.requested=pod_manifest(self.operation,self.pod_name,self.cm_name)
        self.cm=None
        self.ordinary_sample=None
        self.claimed=self.create_pod_requested=self.create_cm_requested=False
        self.out={'schema':'energy-programmatic-skill-install.v1','status':'INCOMPLETE','operation':self.operation,'once_claimed':False,
                  'automatic_retry':False,'automatic_rollback':False,'gateway_modified':False,'provisioner_modified':False,'existing_sandboxes_modified':False,
                  'secret_api_reads':0,'credentials_read':False,'credentials_exported':False,'provider_requests':0,'work_messages_sent':0,'schedule_created':False,
                  'skill_updated':False,'code_package_sha256':PACKAGE_SHA,'active_task_check_skipped_by_operator_request':True,'gateway_rollout_completion_required':True,'category_only_mount_validation':True}

    def command(self,args,body=None,timeout=30,limit=4194304):
        need(self.commands < 240 and time.monotonic()-self.started < 1400,'OPERATION_BOUND')
        fixed=[['get','deployment','nginx','-o','json'],['get','deployments']+list(BASELINES)+['-o','json'],['get','node',NODE,'-o','json'],['get','pvc',CLAIM,'-o','json'],
               ['get','pod',self.pod_name,'--ignore-not-found','-o','json'],['get','configmap',self.cm_name,'--ignore-not-found','-o','json'],
               ['logs',self.pod_name,'-c','stage','--tail=8','--limit-bytes=16384'],ORDINARY_LIST]
        if self.ordinary_sample:
            fixed.append(['get','pod',self.ordinary_sample['pod'],'-o','json'])
        allowed=args in fixed and body is None
        if args==['create','-f','-','-o','json']:
            allowed=body in (canonical(self.cm) if self.cm else None,canonical(self.requested)) and body is not None
        if args[:1]==['exec']:
            container,code = args[3] if len(args)>3 else None,args[10] if len(args)>10 else None
            allowed=(args in [['exec','-i',self.pod_name,'-c',name,'--','python3','-I','-B','-c',self.assets[key].decode()] for name,key in (('publisher','publisher'),('qualification','verifier'))]
                     and self.pod_uid is not None and isinstance(body,bytes))
            if self.ordinary_sample and len(args)==11 and re.fullmatch('[0-9a-f]{64}',args[-1]):
                allowed=args[:-1]==['exec',self.ordinary_sample['pod'],'-c','sandbox','--','python3','-I','-B','-c',VISIBILITY_CHILD] and body is None
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
            name=row['metadata']['name']
            need(name in DEPLOYMENT_UIDS and row['metadata']['uid']==DEPLOYMENT_UIDS[name]
                 and not row['metadata'].get('deletionTimestamp'),'DEPLOYMENT_IDENTITY_CHANGED')
            result[name]={'uid':row['metadata']['uid'],'generation':row['metadata']['generation'],'spec_sha256':sha(canonical(row['spec']))}
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

    def ordinary_visibility(self,expected):
        listing=self.command(ORDINARY_LIST)
        need(isinstance(listing,dict) and isinstance(listing.get('items'),list),'ORDINARY_POD_LIST_SHAPE')
        rows=[]
        for pod in listing['items']:
            meta,status=pod.get('metadata',{}),pod.get('status',{})
            states=[row for row in status.get('containerStatuses',[]) if row.get('name')=='sandbox']
            if status.get('phase')!='Running' or meta.get('deletionTimestamp') or len(states)!=1 or states[0].get('ready') is not True or states[0].get('imageID')!=IMAGE_ID:
                continue
            name,uid=meta.get('name',''),meta.get('uid','')
            need(re.fullmatch('sandbox-[a-z0-9-]+',name) and re.fullmatch('[0-9a-f-]{36}',uid),'ORDINARY_POD_IDENTITY')
            rows.append({'pod':name,'uid':uid,'node':pod.get('spec',{}).get('nodeName')})
        if not rows:
            return {'status':'ORDINARY_SANDBOX_VIEW_PENDING','reason':'NO_READY_ORDINARY_SANDBOX','files_modified':False}
        self.ordinary_sample=sorted(rows,key=lambda row:row['pod'])[0]
        pod=self.command(['get','pod',self.ordinary_sample['pod'],'-o','json'])
        need(pod['metadata']['uid']==self.ordinary_sample['uid'] and not pod['metadata'].get('deletionTimestamp'),'ORDINARY_POD_REPLACED')
        containers=[row for row in pod['spec']['containers'] if row['name']=='sandbox'];need(len(containers)==1,'ORDINARY_CONTAINER_SHAPE')
        mounts=[row for row in containers[0].get('volumeMounts',[]) if row.get('mountPath')=='/mnt/skills/public']
        need(len(mounts)==1 and mounts[0].get('subPath')=='public' and mounts[0].get('readOnly') is True,'ORDINARY_CATEGORY_MOUNT_CHANGED')
        volumes={row['name']:row for row in pod['spec'].get('volumes',[])}
        need(volumes[mounts[0]['name']].get('persistentVolumeClaim',{}).get('claimName')==CLAIM,'ORDINARY_SKILLS_CLAIM_CHANGED')
        result=self.command(['exec',self.ordinary_sample['pod'],'-c','sandbox','--','python3','-I','-B','-c',VISIBILITY_CHILD,expected],timeout=35)
        fresh=self.command(['get','pod',self.ordinary_sample['pod'],'-o','json'])
        need(fresh['metadata']['uid']==self.ordinary_sample['uid'] and not fresh['metadata'].get('deletionTimestamp'),'ORDINARY_POD_REPLACED')
        return dict(result,sample=self.ordinary_sample)

    def reader_binding(self):
        raw=read(Path('/var/lib/portal-energy-html-reader-20261114-r1/RUNTIME-ACCEPTANCE.json'))
        receipt=json.loads(raw)
        need(receipt.get('schema')=='energy-html-reader-activation.v1'
             and receipt.get('status')=='AUTHENTICATED_HTML_READER_DEPLOYED'
             and receipt.get('anonymous_login_redirect_verified') is True
             and receipt.get('reader_configmap')=='deer-flow-nginx-energy-reader-20261114-r1',
             'AUTHENTICATED_HTML_READER_ACCEPTANCE_REQUIRED')
        current=self.command(['get','deployment','nginx','-o','json'])
        actual={'uid':current['metadata']['uid'],'generation':current['metadata']['generation'],'spec_sha256':sha(canonical(current['spec']))}
        need(actual==receipt.get('nginx_after') and actual['uid']=='10fb043a-eaee-433e-bce0-80688e798611',
             'AUTHENTICATED_HTML_READER_BASELINE_CHANGED')
        self.out['accepted_html_reader']={'receipt_sha256':sha(raw),'nginx':actual,
                                         'anonymous_login_redirect_verified':True}

    def run(self):
        self.reader_binding()
        context_binding=gateway_context_binding()
        self.out['accepted_gateway_context']=context_binding
        report,runtime,runtime_hash,binding=qualification_binding()
        self.out['accepted_original_install']=binding
        self.out['runtime']={'manifest_sha256':runtime_hash,'source_image_id':IMAGE_ID}
        self.out['before']=self.baseline()
        need(gateway_context_binding(self.out['before']['gateway'])==context_binding,'GATEWAY_CONTEXT_ACCEPTANCE_CHANGED')
        self.out['deployment_guard']='CURRENT_PROTECTED_UIDS_AND_UNCHANGED_SPEC_DURING_SKILL_UPDATE'
        node=self.command(['get','node',NODE,'-o','json'])
        need(node['metadata']['uid']==NODE_UID and not node['spec'].get('unschedulable') and any(r.get('type')=='Ready' and r.get('status')=='True' for r in node.get('status',{}).get('conditions',[])),'FIXED_NODE_NOT_READY')
        claim=self.command(['get','pvc',CLAIM,'-o','json'])
        need(claim['metadata']['uid']==CLAIM_UID and claim.get('status',{}).get('phase')=='Bound','SKILLS_PVC_CHANGED')
        need(not safe(CONTROL).exists(),'ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
        CONTROL.mkdir(mode=0o700);self.claimed=True;self.out['once_claimed']=True
        stage={'action':'stage','operation':self.operation,'package':base64.b64encode(self.assets['package']).decode(),'package_sha256':PACKAGE_SHA,
               'runtime_manifest_sha256':runtime_hash}
        self.cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':self.cm_name,'namespace':'deer-flow','labels':{LABEL:self.operation}},'immutable':True,
                 'data':{'publisher.py':self.assets['publisher'].decode(),'stage.json':canonical(stage).decode()}}
        self.create_cm_requested=True
        cm=self.command(['create','-f','-','-o','json'],canonical(self.cm));self.cm_uid=cm['metadata']['uid']
        need(cm.get('data')==self.cm['data'] and cm.get('immutable') is True,'CODE_CONFIGMAP_CHANGED')
        print(json.dumps({'phase':'STAGE_PRIVATE_BACKUP_AND_REUSE_EXISTING_PUBLIC_RUNTIME'}),flush=True)
        self.out['runtime_replacement_requested']=False
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
        need(staged.get('status')=='ENERGY_DELIVERY_SKILL_STAGED' and staged.get('after_files')==CANDIDATE_FILES and staged.get('before_tree_sha256')==OLD_TREE,'STAGED_CANDIDATE_NOT_ACCEPTED')
        self.out['staging']=staged
        expected_tree=staged['after_tree_sha256']
        verify={'package_sha256':PACKAGE_SHA,'after_tree_sha256':expected_tree,'runtime_manifest_sha256':runtime_hash,
                'png_sha256':binding['png_sha256'],'test_files':json.loads(self.assets['test_files'])}
        print(json.dumps({'phase':'VERIFY_ORDINARY_PUBLIC_CATEGORY_MOUNT_AS_UID_1000'}),flush=True)
        accepted=self.child('qualification','verifier',verify,timeout=210)
        self.out['shared_runtime_qualification']=accepted
        need(accepted.get('status')=='NONROOT_ARM64_PROGRAMMATIC_SKILL_QUALIFIED' and accepted.get('uid')==1000 and accepted.get('gid')==1000
             and accepted.get('acceptance_basis')=='PROGRAMMATIC_FIXED_TEMPLATE_CHECKS'
             and accepted.get('programmatic_checks_passed') is True
             and accepted.get('visual_reviewed') is False and accepted.get('visual_review_required') is False
             and accepted.get('package_sha256')==PACKAGE_SHA and accepted.get('runtime_manifest_sha256')==runtime_hash
             and accepted.get('after_tree_sha256')==expected_tree and accepted.get('png_sha256')==binding['png_sha256']
             and accepted.get('provider_requests')==0 and accepted.get('credentials_read') is False
             and accepted.get('automatic_binding_used') is True and accepted.get('private_local_copy_cleaned') is True
             and accepted.get('temporary_project_cleaned') is True and accepted.get('final_text', {}).get('complete_artifacts_accepted') is True
              and accepted.get('final_text', {}).get('missing_artifact_rejected') is True and accepted.get('final_text', {}).get('messages_sent') == 0
               and accepted.get('final_text', {}).get('single_text_ready_before_publication') is True
               and accepted.get('final_text', {}).get('programmatic_acceptance_verified') is True
               and accepted.get('final_text', {}).get('model_visual_review_used') is False
               and accepted.get('final_text', {}).get('ocr_used') is False
               and accepted.get('final_text', {}).get('missing_host_context_blocked') is True
               and accepted.get('final_text', {}).get('synthetic_host_environment_used') is True
               and accepted.get('final_text', {}).get('real_host_context_injection_proven') is False
               and OFFLINE_TEST_COUNT > 0 and accepted.get('offline_tests', {}).get('run')==OFFLINE_TEST_COUNT
               and accepted.get('offline_tests', {}).get('errors')==0
               and accepted.get('offline_tests', {}).get('failures')==0
               and accepted.get('final_text', {}).get('freight_fallback_date_marked') is True
               and accepted.get('freight_render', {}).get('status') == 'FREIGHT_FALLBACK_RENDER_QUALIFIED'
               and accepted.get('freight_render', {}).get('actual_dates_verified') is True
               and accepted.get('freight_render', {}).get('programmatic_acceptance_verified') is True
               and accepted.get('freight_render', {}).get('model_visual_review_used') is False
               and accepted.get('freight_render', {}).get('global_warning_hidden_in_html_and_png') is True
               and accepted.get('freight_render', {}).get('non_png_quality_facts_preserved') is True
              and accepted.get('final_text', {}).get('network_requests') == 0,'SHARED_NONROOT_DELIVERY_NOT_ACCEPTED')
        self.out['prepublication_deployments']=self.baseline()
        self.out['unrelated_deployment_drift_observed']=self.out['prepublication_deployments']!=self.out['before']
        need(not self.out['unrelated_deployment_drift_observed'], 'PROTECTED_DEPLOYMENT_DRIFT')
        need(qualification_binding()[3]==binding,'ORIGINAL_ACCEPTANCE_CHANGED_BEFORE_SWITCH')
        need(gateway_context_binding(self.out['prepublication_deployments']['gateway'])==context_binding,
             'GATEWAY_CONTEXT_ACCEPTANCE_CHANGED_BEFORE_SWITCH')
        self.reader_binding()
        print(json.dumps({'phase':'PUBLISH_ONLY_QUALIFIED_ENERGY_SKILL'}),flush=True)
        self.out['skill_switch_requested']=True
        self.out['skill_switch_may_have_applied']=True
        publication=self.child('publisher','publisher',{'action':'publish','operation':self.operation,'acceptance':accepted},timeout=120)
        self.out['publication']=publication
        need(publication.get('status')=='ENERGY_DELIVERY_SKILL_PUBLISHED' and publication.get('after_files')==CANDIDATE_FILES
             and publication.get('before_tree_sha256')==OLD_TREE and publication.get('after_tree_sha256')==expected_tree,'PUBLISHED_TREE_CHANGED')
        self.out['skill_updated']=True
        final=self.child('publisher','publisher',{'action':'verify','operation':self.operation})
        need(final.get('status')=='ENERGY_DELIVERY_SKILL_VERIFIED' and final['after_tree_sha256']==expected_tree,'FINAL_SKILL_TREE_CHANGED')
        self.out['after_deployments']=self.baseline()
        self.out['unrelated_deployment_drift_observed']=self.out['after_deployments']!=self.out['before']
        need(not self.out['unrelated_deployment_drift_observed'], 'PROTECTED_DEPLOYMENT_DRIFT_AFTER_SWITCH')
        try:self.out['ordinary_sandbox_visibility']=self.ordinary_visibility(expected_tree)
        except Exception as exc:self.out['ordinary_sandbox_visibility']={'status':'ORDINARY_SANDBOX_VIEW_PENDING','error_class':type(exc).__name__}
        self.out.update(status='ENERGY_DELIVERY_SKILL_INSTALLED',browser_business_acceptance_pending=True)
        if self.out['ordinary_sandbox_visibility'].get('status')!='ORDINARY_SANDBOX_PUBLIC_RUNTIME_VISIBLE':
            self.out['status']='ENERGY_DELIVERY_SKILL_INSTALLED_VISIBILITY_PENDING'

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
                need(self.cm_uid is None or cm['metadata']['uid'] == self.cm_uid, 'OWN_CONFIGMAP_UID_CHANGED')
                self.cm_uid=cm['metadata']['uid']
                self.command(['delete','--raw','/api/v1/namespaces/deer-flow/configmaps/'+self.cm_name,'-f','-'],canonical({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':self.cm_uid},'gracePeriodSeconds':5}))
            self.out['maintenance_configmap_absent']=self.command(['get','configmap',self.cm_name,'--ignore-not-found','-o','json']) is None
        if self.create_pod_requested and not self.out.get('maintenance_pod_absent'):
            self.out.update(status='INCOMPLETE',maintenance_cleanup_pending=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--install-html-reader-skill-once',required=True,action='store_true');parser.parse_args()
    operation=None
    out={'schema':'energy-programmatic-skill-install.v1','status':'INCOMPLETE','once_claimed':False,'skill_updated':False,'gateway_modified':False,'provider_requests':0,'credentials_exported':False}
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
        raw=canonical(out)
        try:
            private_write(CONTROL/'REPORT.json',raw)
            out.update(report_path=str(CONTROL/'REPORT.json'),report_sha256=sha(raw),report_bytes=len(raw))
        except Exception as exc:
            out.update(status='INCOMPLETE',report_write_error_class=type(exc).__name__,report_write_incomplete=True)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':
    sys.exit(main())
