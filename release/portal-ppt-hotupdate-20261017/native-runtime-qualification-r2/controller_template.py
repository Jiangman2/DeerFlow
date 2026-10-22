"""One owned native qualification session. No production mutation or rollout."""
import base64
import copy
import datetime
import fcntl
import hashlib
import importlib.util
import ipaddress
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
import types
import uuid
import zlib

PAYLOAD_SHA = '__PAYLOAD_SHA__'
PAYLOAD_Z64 = '__PAYLOAD_Z64__'
OWNER_LABEL = 'portal-ppt-network-owner'
NODE = 'datax-02'
NODE_UID = '3148d4c1-6a97-42ed-a78c-950dcea5cbec'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s']
SAFE_ENV = {'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG':'C.UTF-8', 'HOME':'/nonexistent'}
PRELOAD = '/var/lib/portal-ppt-native-r3-report-3819d70cf18843e2ac27cbc6a1abba6c/REPORT.json'
PRELOAD_SHA = '99135299a8615bcadc90af60ab6e3332665e9714826b847d9a1f5e48c426688a'
ONCE = '/var/lib/portal-ppt-native-qualification-r2.STARTED.json'
LOCK = '/var/lib/portal-ppt-native-qualification-r2.lock'
PRIOR_OWNER = '9a948f58627a40ecadd0a5ee1fa2fc7a'
PRIOR_ROOT = '/var/lib/portal-ppt-native-qualification-' + PRIOR_OWNER
PRIOR_REPORT_SHA = 'fdd49101a4e8892b92945a30b7334f3f5b7d2436e13f590720f4a69f7f3b2a2c'
PRIOR_STDERR_SHA = 'dfdbf9ce2e5c8104d66ac46a0a92b9abe4e67a4eb381d3c7324d0d8591c5b9ad'
UID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
SCHEMA = 'portal-ppt-native-qualification.v1'


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw, limit=4194304):
    require(len(raw) <= limit, 'json_output_limit')
    def pairs(values):
        result = {}
        for key, value in values:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(Refused('nonfinite_json')))


def write_new(path, raw):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def read_fixed_bytes(path, size, sha):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_size == size, 'preload_receipt_type_size')
        raw = stream.read(size + 1)
        after = os.fstat(stream.fileno())
        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns) ==
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns), 'preload_receipt_changed')
    require(len(raw) == size and digest(raw) == sha, 'preload_receipt_digest')
    return raw


def read_pin(path,size,sha):
    return decode(read_fixed_bytes(path,size,sha))


def prior_failure():
    value=read_pin(PRIOR_ROOT+'/REPORT.json',1771,PRIOR_REPORT_SHA)
    read_fixed_bytes(PRIOR_ROOT+'/command-0015.stderr.private.log',270,PRIOR_STDERR_SHA)
    require(value['owner']==PRIOR_OWNER and value['status']=='INCOMPLETE' and value['error_code']=='kubectl_nonzero_1'
            and value['stages']=={} and value['native_runtime_passed'] is False
            and value['production_workload_mutation'] is False and value['production_database_access'] is False
            and value['model_access'] is False and value['retained_namespace_deny_all'] is True,'prior_failure_not_reviewed_boundary')
    return dict(owner=PRIOR_OWNER,report_bytes=1771,report_sha256=PRIOR_REPORT_SHA,stderr_bytes=270,stderr_sha256=PRIOR_STDERR_SHA,
                cause='EXPLICIT_NEVER_REJECTED_WITHOUT_MATCHING_PRIORITYCLASS',old_evidence_modified=False)


def payload():
    obj = zlib.decompressobj()
    raw = obj.decompress(base64.b64decode(PAYLOAD_Z64, validate=True), 2097153)
    require(len(raw) <= 2097152 and obj.eof and not obj.unconsumed_tail and not obj.unused_data
            and digest(raw) == PAYLOAD_SHA, 'embedded_payload_identity')
    return decode(raw)


def module(source, name):
    value = types.ModuleType(name)
    exec(compile(source, '<embedded-' + name + '>', 'exec'), value.__dict__)
    return value


def stop(proc):
    # The process group is ours even if its leader already exited. Transport or
    # credential descendants may otherwise outlive the observed kubectl command.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    if proc.poll() is None:
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    until=time.monotonic()+3
    while time.monotonic()<until:
        try:
            os.killpg(proc.pid,0)
        except ProcessLookupError:
            break
        time.sleep(.05)
    else:
        try:
            os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.wait(timeout=3)


class Transport:
    """Bounded local kubectl, no SSH. Raw API bodies never persist for GETs."""
    def __init__(self, evidence):
        self.evidence = Path(evidence)
        self.calls = 0
        self.saved_bytes = 0

    def run(self, args, body=None, timeout=30, limit=4194304, private=False):
        self.calls += 1
        ordinal = self.calls
        require(ordinal <= 3000 and self.saved_bytes <= 33554432, 'session_transport_evidence_bound')
        require(all(isinstance(arg, str) and '\x00' not in arg for arg in args), 'argv_shape')
        started = time.monotonic()
        proc = None
        interrupted = None
        timed_out = output_limit = False
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err, tempfile.TemporaryFile() as inp:
            if body is not None:
                inp.write(body)
                inp.seek(0)
            try:
                proc = subprocess.Popen(KUBE + args, stdin=inp, stdout=out, stderr=err,
                                        env=SAFE_ENV, start_new_session=True, close_fds=True)
                while proc.poll() is None:
                    output_limit = os.fstat(out.fileno()).st_size > limit or os.fstat(err.fileno()).st_size > 262144
                    timed_out = time.monotonic() - started > timeout
                    if output_limit or timed_out:
                        stop(proc)
                        break
                    time.sleep(0.1)
            except BaseException as exc:
                interrupted = exc
            finally:
                if proc is not None:
                    stop(proc)
                out.seek(0); stdout = out.read(limit + 1)
                err.seek(0); stderr = err.read(262145)
            code = None if proc is None else proc.returncode
        receipt = dict(ordinal=ordinal, returncode=code, timeout=timed_out,
                       output_limit=output_limit or len(stdout) > limit or len(stderr) > 262144,
                       elapsed_seconds=round(time.monotonic()-started,3),
                       stdout_bytes=len(stdout), stdout_sha256=digest(stdout),
                       stderr_bytes=len(stderr), stderr_sha256=digest(stderr))
        # Raw stderr is retained root-private, never included in safe report.
        write_new(self.evidence / ('command-%04d.stderr.private.log' % ordinal), stderr[:262144])
        if private:
            write_new(self.evidence / ('command-%04d.stdout.private.log' % ordinal), stdout[:limit])
            self.saved_bytes += min(len(stdout),limit)
        self.saved_bytes += min(len(stderr),262144)
        write_new(self.evidence / ('command-%04d.json' % ordinal), encoded(receipt))
        if interrupted is not None:
            raise interrupted
        require(not receipt['output_limit'], 'kubectl_output_limit')
        require(not timed_out, 'kubectl_timeout')
        require(code == 0, 'kubectl_nonzero_' + str(code))
        return stdout

    def get(self, resource, name=None, namespace=None, timeout=30):
        args = ['get', resource]
        if name is not None:
            args.append(name)
        if namespace is not None:
            args += ['-n', namespace]
        return decode(self.run(args + ['-o=json'],timeout=timeout))

    def create(self, obj):
        return decode(self.run(['create', '-f', '-', '-o=json'], encoded(obj)))

    def dry_run(self,obj,timeout):
        return decode(self.run(['create','--dry-run=server','-f','-','-o=json'],encoded(obj),timeout=timeout))

    def delete_uid(self, kind, namespace, name, uid):
        group = '/apis/scheduling.k8s.io/v1' if kind=='PriorityClass' else '/apis/networking.k8s.io/v1' if kind == 'NetworkPolicy' else '/api/v1'
        plural = {'Pod':'pods','ConfigMap':'configmaps','NetworkPolicy':'networkpolicies','PriorityClass':'priorityclasses'}[kind]
        path = group + ('/namespaces/' + namespace if namespace is not None else '') + '/' + plural + '/' + name
        body = dict(apiVersion='v1',kind='DeleteOptions',preconditions=dict(uid=uid),
                    propagationPolicy='Background',gracePeriodSeconds=5)
        return self.run(['delete', '--raw=' + path, '-f', '-'], encoded(body))

    def metadata_uid(self,resource,name,namespace=None,timeout=30):
        args=['get',resource,name,'--ignore-not-found']
        if namespace is not None:args+=['-n',namespace]
        return self.run(args+['-o=jsonpath={.metadata.uid}'],timeout=timeout,limit=1024).decode().strip()

    def priority_cache_miss(self,name):
        # Read only our just-written bounded stderr. Never generically retry a Forbidden.
        path=self.evidence/('command-%04d.stderr.private.log' % self.calls)
        raw=path.read_bytes()
        suffix=('pods "network-subject" is forbidden: no PriorityClass with name '+name+' was found\n').encode()
        return len(raw)<=4096 and raw.startswith(b'Error from server (Forbidden): error when creating "STDIN": ') and raw.endswith(suffix)

    def exec(self, namespace, pod, container, argv, body=None, timeout=20):
        return self.run(['exec', '-i', '-n', namespace, pod, '-c', container, '--'] + argv,
                        body, timeout=timeout, limit=262144, private=True)

    def logs(self, namespace, pod, container):
        return self.run(['logs', '-n', namespace, pod, '-c', container, '--limit-bytes=262144'],
                        timeout=25, limit=262144, private=True)


def image_digest(image_id):
    require(isinstance(image_id,str), 'runtime_image_id_type')
    value = image_id.rsplit('@',1)[-1].split('://')[-1]
    require(re.fullmatch(r'sha256:[0-9a-f]{64}',value), 'runtime_image_id_format')
    return value


def admitted(expected, actual, unpersisted=False):
    """Exact intent with only known API built-in default fields admitted."""
    require(actual.get('apiVersion') == expected['apiVersion'] and actual.get('kind') == expected['kind'], 'admission_kind')
    em, am = expected['metadata'], actual['metadata']
    require(am.get('name') == em['name'] and am.get('namespace') == em.get('namespace')
            and am.get('labels',{}) == em.get('labels',{}), 'admission_metadata')
    require(metadata_annotations(actual), 'admission_unrequested_annotation')
    require((unpersisted and not am.get('uid') or UID.fullmatch(am.get('uid',''))) and not am.get('deletionTimestamp'), 'admission_uid_or_deleting')
    kind = expected['kind']
    if kind == 'Namespace':
        # Namespace has the standard name label and API-created finalizer.
        require(am['labels'].get('kubernetes.io/metadata.name') == em['name'], 'namespace_name_label')
        return
    if kind=='PriorityClass':
        require('namespace' not in am and actual.get('value')==0 and type(actual.get('value')) is int
                and actual.get('globalDefault',False) is False and actual.get('preemptionPolicy')=='Never'
                and actual.get('description')==expected['description'],'priority_class_shape')
        require(not(set(actual)-{'apiVersion','kind','metadata','value','globalDefault','description','preemptionPolicy'}), 'priority_class_extra_field')
        return
    if kind == 'ConfigMap':
        for key in ('data','binaryData','immutable'):
            require(actual.get(key,{ } if key != 'immutable' else False) == expected.get(key,{ } if key != 'immutable' else False), 'admission_config_data')
        return
    if kind == 'NetworkPolicy':
        observed=copy.deepcopy(actual['spec'])
        intent=copy.deepcopy(expected['spec'])
        for key in ('ingress','egress'):
            if not intent.get(key):intent[key]=[]
            if not observed.get(key):observed[key]=[]
        require(observed == intent, 'admission_policy_spec')
        return
    require(kind == 'Pod', 'unsupported_owned_kind')
    def visit(exp, act, path=()):
        if isinstance(exp,dict):
            require(isinstance(act,dict), 'admission_shape')
            for key, value in exp.items():
                if key not in act and value is False and (path==('spec',) and key in ('hostNetwork','hostPID','hostIPC','shareProcessNamespace')
                                                          or path and path[-2:-1]==('volumeMounts',) and key=='readOnly'):
                    continue  # Kubernetes omitempty serializes these explicit false defaults away.
                require(key in act, 'admission_field_missing')
                visit(value, act[key], path+(key,))
            for key in set(act)-set(exp):
                value = act[key]
                allowed = False
                if path == ('spec',):
                    allowed = (key == 'nodeName' and value == NODE or key in ('serviceAccount','serviceAccountName') and value == 'default'
                               or key == 'priority' and value == 0 or key == 'tolerations' and default_tolerations(value)
                               or key == 'securityContext' and value == {})
                if len(path) == 3 and path[:1] == ('spec',) and path[1] in ('containers','initContainers'):
                    allowed = key == 'terminationMessagePath' and value == '/dev/termination-log' or key == 'terminationMessagePolicy' and value == 'File'
                if len(path) == 3 and path[:2] == ('spec','volumes'):
                    allowed = False
                if path and path[-1] == 'configMap':
                    allowed = key == 'defaultMode' and value == 420
                if path and path[-1] in ('startupProbe','readinessProbe','livenessProbe'):
                    allowed = (key,value) in (('successThreshold',1),('failureThreshold',3),('periodSeconds',10),('timeoutSeconds',1))
                if path and path[-1]=='fieldRef':
                    allowed = key=='apiVersion' and value=='v1'
                require(allowed, 'admission_extra_' + '_'.join(str(x) for x in path+(key,)))
        elif isinstance(exp,list):
            require(isinstance(act,list) and len(exp)==len(act), 'admission_list_shape')
            for index,(left,right) in enumerate(zip(exp,act)):
                visit(left,right,path+(index,))
        else:
            require(type(exp) is type(act) and exp==act, 'admission_value_drift')
    visit(expected['spec'],actual['spec'],('spec',))


def metadata_annotations(obj):
    annotations=obj['metadata'].get('annotations',{})
    if not annotations:
        return True
    if obj['kind']!='Pod' or set(annotations)-{'cni.projectcalico.org/podIP','cni.projectcalico.org/podIPs','cni.projectcalico.org/containerID'}:
        return False
    for key,value in annotations.items():
        if not isinstance(value,str) or len(value)>1024:
            return False
        if key.endswith('containerID'):
            if not re.fullmatch(r'[0-9a-f]{64}',value):return False
        else:
            try:
                for address in value.split(','):
                    ip=ipaddress.ip_interface(address)
                    if not ip.ip.is_private or ip.ip.is_loopback or ip.ip.is_unspecified:return False
            except ValueError:
                return False
    return True


def default_tolerations(value):
    return isinstance(value,list) and len(value) == 2 and sorted(value,key=lambda v:v.get('key','')) == [
        dict(key='node.kubernetes.io/not-ready',operator='Exists',effect='NoExecute',tolerationSeconds=300),
        dict(key='node.kubernetes.io/unreachable',operator='Exists',effect='NoExecute',tolerationSeconds=300)]


def pod_safety(obj,priority_name=None):
    s = obj['spec']
    require(obj['kind']=='Pod' and 'nodeName' not in s and s.get('schedulerName')=='default-scheduler'
            and s.get('preemptionPolicy')=='Never' and s.get('restartPolicy')=='Never'
            and s.get('automountServiceAccountToken') is False and s.get('enableServiceLinks') is False
            and all(s.get(key,False) is False for key in ('hostNetwork','hostPID','hostIPC')),
            'pod_scheduling_or_host_scope')
    require(priority_name and s.get('priorityClassName')==priority_name,'pod_priority_class_not_bound')
    require(s.get('nodeSelector') == {'kubernetes.io/hostname':NODE,'kubernetes.io/os':'linux','kubernetes.io/arch':'arm64'}, 'node_selector')
    require(s.get('affinity',{}).get('nodeAffinity',{}).get('requiredDuringSchedulingIgnoredDuringExecution') ==
            {'nodeSelectorTerms':[{'matchFields':[{'key':'metadata.name','operator':'In','values':[NODE]}]}]}, 'node_required_affinity')
    require(1 <= s.get('activeDeadlineSeconds',0) <= 1800, 'pod_deadline_bound')
    require(not s.get('ephemeralContainers') and not s.get('imagePullSecrets'), 'pod_unrequested_container_or_pull_credentials')
    for c in s.get('initContainers',[]) + s['containers']:
        require(c.get('imagePullPolicy')=='Never' and re.fullmatch(r'.+@sha256:[0-9a-f]{64}',c.get('image','')),
                'pod_image_pin_or_pull')
        require(c.get('resources',{}).get('requests') == c.get('resources',{}).get('limits')
                and {'cpu','memory'} <= set(c.get('resources',{}).get('requests',{})), 'pod_guaranteed_resources')
        require(not c.get('ports') and not c.get('envFrom') and all('value' in e or set(e.get('valueFrom',{}))=={'fieldRef'} for e in c.get('env',[])), 'pod_env_or_port_scope')
        security = c.get('securityContext',{})
        require(security.get('allowPrivilegeEscalation') is False and not security.get('privileged',False)
                and security.get('capabilities',{}).get('drop')==['ALL'], 'pod_security_context')
    for volume in s.get('volumes',[]):
        require(set(volume)-{'name'} in ({'emptyDir'},{'configMap'}), 'pod_volume_scope')


class Session:
    def __init__(self, kube, owner, evidence, p, now=time.monotonic, sleep=time.sleep):
        self.k = kube
        self.owner = owner
        self.ns = 'portal-ppt-' + owner
        self.evidence = Path(evidence)
        self.p = p
        self.now = now
        self.sleep = sleep
        self.ledger = []
        self.namespace_uid = None
        self.receipts = {}
        self.cleaned = []
        self.network_passed = False
        self.uncertain_creates = []
        self.priority_name='portal-ppt-'+owner
        self.priority_row=None
        self.admission_ready=False
        self.readiness_deadline=None
        self.default_sa_uid=None
        self.dry_run_intent_sha=None

    def record(self,name,value):
        write_new(self.evidence / name, encoded(value))

    def progress(self,phase):
        print(json.dumps(dict(phase=phase,owner=self.owner)),flush=True)

    def node(self):
        obj = self.k.get('node',NODE)
        conditions={c['type']:c['status'] for c in obj['status']['conditions']}
        require(obj['metadata']['uid']==NODE_UID and not obj['metadata'].get('deletionTimestamp')
                and obj['metadata'].get('labels',{}).get('kubernetes.io/arch')=='arm64'
                and obj['metadata'].get('labels',{}).get('kubernetes.io/os')=='linux'
                and obj['metadata'].get('labels',{}).get('kubernetes.io/hostname')==NODE
                and not obj.get('spec',{}).get('unschedulable',False)
                and not obj.get('spec',{}).get('taints') and conditions.get('Ready')=='True'
                and all(conditions.get(key)=='False' for key in ('MemoryPressure','DiskPressure','PIDPressure','NetworkUnavailable'))
                and obj['status'].get('nodeInfo',{}).get('operatingSystem')=='linux'
                and obj['status'].get('nodeInfo',{}).get('architecture')=='arm64', 'fresh_node_identity_or_ready')
        return dict(name=NODE,uid=NODE_UID,ready=True,os='linux',architecture='arm64')

    def namespace(self):
        obj = self.k.get('namespace',self.ns,timeout=self.api_timeout())
        require(obj['metadata']['uid']==self.namespace_uid and obj['metadata'].get('labels',{}).get(OWNER_LABEL)==self.owner
                and not obj['metadata'].get('deletionTimestamp'), 'namespace_ownership_changed')
        return obj

    def api_timeout(self):
        if self.readiness_deadline is None:return 30
        remaining=self.readiness_deadline-self.now()
        require(remaining>0,'admission_readiness_30s_deadline')
        return min(20,remaining)

    def priority_intent(self):
        return dict(apiVersion='scheduling.k8s.io/v1',kind='PriorityClass',
                    metadata=dict(name=self.priority_name,labels={OWNER_LABEL:self.owner}),
                    value=0,globalDefault=False,preemptionPolicy='Never',
                    description='Isolated portal PPT native qualification owner '+self.owner)

    def create_priority(self):
        self.namespace()
        obj=self.priority_intent()
        self.record('PRIORITY-CREATE-INTENT.json',dict(name=self.priority_name,owner=self.owner,intent_sha256=digest(encoded(obj))))
        try:
            actual=self.k.create(obj)
            require(actual.get('metadata',{}).get('name')==self.priority_name and UID.fullmatch(actual.get('metadata',{}).get('uid','')),'priority_create_uid')
        except BaseException:
            self.uncertain_create(obj)
            raise
        self.priority_row=dict(kind='PriorityClass',name=self.priority_name,uid=actual['metadata']['uid'],expected=obj)
        self.record('PRIORITY-CLASS.json',dict(name=self.priority_name,uid=self.priority_row['uid'],owner=self.owner,value=0,
                                               global_default=False,preemption_policy='Never',intent_sha256=digest(encoded(obj))))
        admitted(obj,actual)
        return self.priority_row

    def priority(self):
        self.namespace()
        require(self.priority_row is not None,'priority_class_not_created')
        obj=self.k.get('priorityclass',self.priority_name,timeout=self.api_timeout())
        require(obj['metadata']['uid']==self.priority_row['uid'],'priority_class_uid_replaced')
        admitted(self.priority_row['expected'],obj)
        return obj

    def bind_priority(self,pod):
        pod=copy.deepcopy(pod)
        require(pod['spec'].get('priorityClassName') in (None,self.priority_name),'unrequested_priority_class')
        pod['spec']['priorityClassName']=self.priority_name
        return pod

    def service_account(self):
        self.namespace()
        uid=self.k.metadata_uid('serviceaccount','default',self.ns,timeout=self.api_timeout())
        if not uid:return None
        require(UID.fullmatch(uid),'default_service_account_uid')
        if self.default_sa_uid is not None:require(uid==self.default_sa_uid,'default_service_account_replaced')
        self.default_sa_uid=uid
        return uid

    def prepare_admission(self):
        self.progress('PRIORITY_ADMISSION_READINESS')
        self.readiness_deadline=self.now()+30
        polls=0
        try:
            pod=self.bind_priority(self.render_network()['pods'][0])
            pod_safety(pod,self.priority_name)
            while True:
                self.priority()
                uid=self.service_account()
                if not uid:
                    self.sleep(min(1,self.api_timeout()))
                    continue
                polls+=1
                try:
                    actual=self.k.dry_run(pod,timeout=self.api_timeout())
                except Refused as exc:
                    if str(exc)=='kubectl_nonzero_1' and self.k.priority_cache_miss(self.priority_name):
                        self.sleep(min(1,self.api_timeout()))
                        continue
                    raise
                admitted(pod,actual,unpersisted=True)
                self.dry_run_intent_sha=digest(encoded(pod))
                self.admission_ready=True
                self.record('ADMISSION-READINESS.json',dict(status='SERVER_DRY_RUN_MATCHED',priority_class_name=self.priority_name,
                    priority_class_uid=self.priority_row['uid'],default_service_account_uid=uid,dry_run_polls=polls,
                    exact_first_pod_intent_sha256=self.dry_run_intent_sha,dry_run_uid_not_adopted=True,
                    actual_pod_create_requested=False,readiness_budget_seconds=30))
                return
        finally:
            self.readiness_deadline=None

    def create(self,obj):
        obj = copy.deepcopy(obj)
        require(obj['metadata']['labels'].get(OWNER_LABEL)==self.owner and obj['metadata'].get('namespace')==self.ns, 'create_scope')
        self.namespace()
        if obj['kind']=='Pod':
            require(self.admission_ready,'priority_admission_readiness_required')
            self.priority()
            require(self.service_account(),'default_service_account_absent')
            obj=self.bind_priority(obj)
            pod_safety(obj,self.priority_name)
            if obj['metadata']['name']=='network-subject':
                require(digest(encoded(obj))==self.dry_run_intent_sha,'first_pod_dry_run_intent_changed')
            self.node()
        intent=dict(kind=obj['kind'],name=obj['metadata']['name'],namespace=self.ns,owner=self.owner,intent_sha256=digest(encoded(obj)))
        self.record('create-intent-%02d.json' % (len(self.ledger)+1),intent)
        try:
            actual = self.k.create(obj)
            require(actual.get('metadata',{}).get('name')==obj['metadata']['name']
                    and UID.fullmatch(actual.get('metadata',{}).get('uid','')), 'create_identity_missing')
        except BaseException:
            self.uncertain_create(obj)
            raise
        # Store exact UID before validation, so rejected admitted objects can be deleted only by UID.
        row = dict(kind=obj['kind'],name=obj['metadata']['name'],uid=actual['metadata']['uid'],expected=obj)
        self.ledger.append(row)
        self.record('object-%02d.json' % len(self.ledger),dict(kind=row['kind'],name=row['name'],uid=row['uid'],intent_sha256=digest(encoded(obj))))
        admitted(obj,actual)
        return row

    def uncertain_create(self,obj):
        value=dict(kind=obj['kind'],name=obj['metadata']['name'],status='UNKNOWN_CREATE_OUTCOME_REVIEW_REQUIRED',automatic_retry=False,
                   deletion_authorized=False)
        try:
            actual=self.k.get(obj['kind'],obj['metadata']['name'],None if obj['kind'] in ('Namespace','PriorityClass') else self.ns)
            md=actual.get('metadata',{})
            value['observed_identity']=dict(uid=md.get('uid'),owner=md.get('labels',{}).get(OWNER_LABEL),namespace=md.get('namespace'))
        except Exception as exc:
            value['readback_code']=safe_error(exc)
        self.uncertain_creates.append(value)
        self.record('uncertain-create-%02d.json' % len(self.uncertain_creates),value)

    def create_namespace(self):
        obj=dict(apiVersion='v1',kind='Namespace',metadata=dict(name=self.ns,labels={OWNER_LABEL:self.owner,'kubernetes.io/metadata.name':self.ns}))
        self.record('NAMESPACE-CREATE-INTENT.json',dict(name=self.ns,owner=self.owner,intent_sha256=digest(encoded(obj))))
        try:
            actual=self.k.create(obj)
            require(actual.get('metadata',{}).get('name')==self.ns and UID.fullmatch(actual.get('metadata',{}).get('uid','')),'namespace_create_uid')
        except BaseException:
            self.uncertain_create(obj)
            raise
        self.namespace_uid=actual['metadata']['uid']
        value=dict(name=self.ns,uid=self.namespace_uid,owner=self.owner)
        self.record('NAMESPACE.json',value)
        admitted(obj,actual)
        return value

    def read(self,row):
        self.namespace()
        obj = self.k.get(row['kind'],row['name'],self.ns)
        require(obj['metadata']['uid']==row['uid'], 'object_uid_replaced')
        admitted(row['expected'],obj)
        return obj

    def remove(self,row):
        # Cleanup is UID/ownership guarded independently of admitted spec. This
        # allows deleting our exact UID even when admission changed its spec.
        self.namespace()
        actual=self.k.get(row['kind'],row['name'],self.ns)
        md=actual['metadata']
        require(md.get('name')==row['name'] and md.get('namespace')==self.ns and md.get('uid')==row['uid']
                and md.get('labels',{}).get(OWNER_LABEL)==self.owner,'delete_object_ownership_changed')
        self.k.delete_uid(row['kind'],self.ns,row['name'],row['uid'])
        deadline = self.now()+25
        while self.now()<deadline:
            items = self.k.get(row['kind'],namespace=self.ns)['items']
            matches = [x for x in items if x['metadata']['name']==row['name']]
            if not matches:
                self.cleaned.append(dict(kind=row['kind'],name=row['name'],uid=row['uid']))
                return
            require(matches[0]['metadata']['uid']==row['uid'],'delete_name_replaced')
            self.sleep(1)
        raise Refused('owned_delete_timeout')

    def runtime(self,row,images,allow_terminal=False):
        self.priority()
        obj=self.read(row)
        require(obj['spec'].get('priorityClassName')==self.priority_name and obj['spec'].get('preemptionPolicy')=='Never','runtime_priority_class_changed')
        require(obj['spec'].get('nodeName')==NODE,'pod_node_not_bound')
        self.node()
        statuses=obj.get('status',{}).get('initContainerStatuses',[])+obj.get('status',{}).get('containerStatuses',[])
        require(len(statuses)==len(images) and {x['name'] for x in statuses}==set(images), 'runtime_container_set')
        result={}
        for st in statuses:
            require(st.get('restartCount')==0 and re.fullmatch(r'containerd://[0-9a-f]{64}',st.get('containerID','')),
                    'runtime_restart_or_container_id')
            pin=images[st['name']]
            value=image_digest(st.get('imageID'))
            accepted={pin['manifest_digest'],pin['config_digest']}
            if st['name'] in ('pg-profile','postgres-fixture') and pin.get('reference')==self.p['postgres_cache']['reference']:
                require(pin['manifest_digest']==self.p['postgres_cache']['manifest_digest'] and pin['config_digest']==self.p['postgres_cache']['config_digest'], 'postgres_platform_link_changed')
                accepted.add(self.p['postgres_cache']['target_digest'])
            require(value in accepted, 'runtime_image_not_bound')
            require('running' in st.get('state',{}) or allow_terminal and 'terminated' in st.get('state',{}), 'runtime_not_running_or_terminal')
            result[st['name']]=dict(pod_uid=row['uid'],restart_count=0,container_id=st['containerID'],image_id=st['imageID'],
                                    image_digest=value,node_name=NODE,node_uid=NODE_UID)
        return obj,result

    def wait_running(self,row,images,pending=120):
        deadline=self.now()+pending
        heartbeat=self.now()
        while self.now()<deadline:
            if self.now()-heartbeat>=15:
                self.progress('WAIT_'+row['name'].upper())
                heartbeat=self.now()
            obj=self.read(row)
            require(obj.get('status',{}).get('phase') not in ('Failed','Succeeded'),'pod_terminated_before_probe')
            if obj.get('status',{}).get('phase')=='Running' and obj['status'].get('podIP'):
                try:
                    return self.runtime(row,images)
                except Refused as exc:
                    if str(exc) not in ('runtime_container_set','runtime_restart_or_container_id','runtime_not_running_or_terminal'):
                        raise
                    if any(s.get('restartCount',0)>0 for s in obj['status'].get('containerStatuses',[])):
                        raise
            self.sleep(1)
        raise Refused('pending_120s_deadline')

    def deny_only(self):
        self.namespace()
        policies=self.k.get('networkpolicy',namespace=self.ns)['items']
        require(len(policies)==1 and policies[0]['metadata']['name']=='namespace-deny-all', 'unexpected_additive_policy')
        row=next(x for x in self.ledger if x['kind']=='NetworkPolicy' and x['name']=='namespace-deny-all')
        self.read(row)

    def call(self,role,target,operation,binding):
        row=next(x for x in self.ledger if x['name']=='network-'+role and x['kind']=='Pod')
        current=self.read(row)
        require(current['metadata']['uid']==binding['pods'][role]['pod_uid']
                and current.get('status',{}).get('podIP')==binding['pods'][role]['pod_ip'], 'network_current_pod_ip_changed')
        request=dict(binding=binding,source_role=role,target_role=target,operation=operation,challenge=uuid.uuid4().hex)
        own=binding['pods'][role]
        argv=['/usr/bin/env','-i','PATH=/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin',
              'PROOF_POD_UID='+own['pod_uid'],'PROOF_POD_IP='+own['pod_ip'],
              '/app/backend/.venv/bin/python','-I','-S','-B','-u','-c',self.p['network_probe.py'],'call']
        return decode(self.k.exec(self.ns,row['name'],'proof',argv,encoded(request),timeout=12),262144)

    def network_round(self,binding,since=None):
        value=dict(health_before={r:self.call(r,r,'health',binding) for r in ('subject','peer')},
                   flows=dict(egress=self.call('subject','peer','cross',binding),ingress=self.call('peer','subject','cross',binding)))
        self.sleep(.5)
        value['health_after']={r:self.call(r,r,'health',binding) for r in ('subject','peer')}
        if since is not None:
            value['elapsed_since_withdrawal_seconds']=round(self.now()-since,6)
        return value

    def render_network(self):
        template=json.loads(self.p['TEMPLATE.json'])
        text=json.dumps(template).replace('__UNBOUND_OWNER_UUID__',self.owner).replace('__UNBOUND_FRESH_OWNED_NAMESPACE__',self.ns)
        template=json.loads(text)
        for pod in template['pods']:
            args=pod['spec']['containers'][0]['args']
            args[args.index('__UNBOUND_PINNED_INLINE_NETWORK_ADAPTER__')]=self.p['network_probe.py']
        return template

    def network(self):
        self.progress('NETWORK_PROOF')
        started=self.now()
        template=self.render_network()
        policies=[self.create(x) for x in template['policies']]
        pods=[self.create(x) for x in template['pods']]
        pin=dict(manifest_digest='sha256:9b3a6d80d20b73585c6642213077236bb9948f35d0d5daa262e78d120422eb9e',
                 config_digest='sha256:5b43bc9cd5f51029b87aaf0b212c064124e29fb814c5543b7de7d864812f2902')
        observed={r['name'].split('-')[-1]:self.wait_running(r,{'proof':pin}) for r in pods}
        binding=dict(owner=self.owner,pods={role:dict(pod_uid=row[0]['metadata']['uid'],pod_ip=row[0]['status']['podIP']) for role,row in observed.items()})
        n=module(self.p['network_probe.py'],'network_probe')
        n.binding(binding)
        # Wait for both actual listener health endpoints; retries are bounded readiness only.
        health_deadline=min(started+150,self.now()+20)
        while True:
            packets=[self.call(r,r,'health',binding) for r in ('subject','peer')]
            if all(p.get('status')=='SUCCESS' for p in packets):
                break
            require(self.now()<health_deadline,'listener_readiness_deadline')
            self.sleep(1)
        baseline=self.network_round(binding)
        n.verify_round(baseline,binding,{},set(),True)
        for row in policies:
            self.read(row)
        self.record('NETWORK-BASELINE.json',baseline)
        subject=next(x for x in policies if x['name']=='subject-temporary-allow-peer')
        self.remove(subject)
        withdrawn=self.now()
        rounds=[]
        streak=0
        for _ in range(20):
            require(self.now()-withdrawn<52 and self.now()-started<225,'network_convergence_deadline')
            value=self.network_round(binding,withdrawn)
            rounds.append(value)
            # Existing adapter performs final full continuity/nonce checking.
            statuses=[value['flows'][d].get('status') for d in ('egress','ingress')]
            require(all(s in ('SUCCESS','CONNECT_BLOCKED') for s in statuses),'unproven_network_flow')
            streak=streak+1 if statuses==['CONNECT_BLOCKED','CONNECT_BLOCKED'] else 0
            if streak>=3:
                break
            self.sleep(1)
        final={r['name'].split('-')[-1]:self.runtime(r,{'proof':pin})[1]['proof'] for r in pods}
        deny=next(x for x in policies if x['name']=='namespace-deny-all')
        peer=next(x for x in policies if x['name']=='peer-allow-subject')
        self.read(deny);self.read(peer)
        require(not any(x['metadata']['name']==subject['name'] for x in self.k.get('networkpolicy',namespace=self.ns)['items']), 'temporary_policy_present')
        evidence=dict(binding=binding,total_elapsed_seconds=round(self.now()-started,6),
            policy_transition=dict(owned_namespace_uid=self.namespace_uid,deny_all_uid_before=deny['uid'],deny_all_uid_after=deny['uid'],
                                   peer_allow_uid_before=peer['uid'],peer_allow_uid_after=peer['uid'],subject_allow_uid=subject['uid'],
                                   subject_allow_deleted=True,exact_policy_shapes_verified=True),
            runtime_identity=dict(initial={r:v[1]['proof'] for r,v in observed.items()},final=final),baseline=baseline,after_withdrawal=rounds)
        checked=n.verify_evidence(evidence)
        self.record('NETWORK-EVIDENCE.json',evidence)
        self.record('NETWORK-CHECKED.json',checked)
        self.receipts['network']=checked
        self.network_passed=True
        for row in pods+[peer]:
            self.remove(row)
        self.deny_only()

    def wait_terminal(self,row,images,seconds,pending=120):
        begun=self.now()
        running=False
        heartbeat=begun
        while self.now()-begun<seconds:
            if self.now()-heartbeat>=15:
                self.progress('WAIT_'+row['name'].upper())
                heartbeat=self.now()
            obj=self.read(row)
            phase=obj.get('status',{}).get('phase')
            statusrows=obj.get('status',{}).get('initContainerStatuses',[])+obj.get('status',{}).get('containerStatuses',[])
            if obj['spec'].get('nodeName')==NODE and (phase in ('Running','Succeeded','Failed') or
                    any('running' in s.get('state',{}) or 'terminated' in s.get('state',{}) for s in statusrows)):
                running=True
            require(running or self.now()-begun<pending,'pending_120s_deadline')
            if phase in ('Succeeded','Failed'):
                statuses=obj.get('status',{}).get('initContainerStatuses',[])+obj.get('status',{}).get('containerStatuses',[])
                pg=next((s for s in statuses if s['name']=='postgres-fixture'),None)
                if phase=='Succeeded' and pg and 'running' in pg.get('state',{}):
                    # Native restartable init sidecars shut down after the main.
                    self.sleep(1)
                    continue
                logs={}
                for name in images:
                    logs[name]=self.k.logs(self.ns,row['name'],name)
                obj,ids=self.runtime(row,images,allow_terminal=True)
                self.record(row['name']+'-TERMINAL.json',dict(phase=phase,identities=ids,statuses=safe_statuses(obj)))
                terminal_success(obj)
                return obj,ids,logs
            self.sleep(1)
        raise Refused('candidate_deadline')

    def candidates(self):
        require(self.network_passed,'network_proof_required_before_candidate')
        hf=module(self.p['host_frontend_stage.py'],'host_frontend_stage')
        stages=hf.render(self.owner,self.ns,self.p['host_runtime_inputs'])['stages']
        for stage in stages:
            self.progress('NATIVE_'+stage['name'].upper())
            self.deny_only()
            owned=[self.create(x) for x in stage['objects']]
            row=next(x for x in owned if x['kind']=='Pod')
            obj,ids,logs=self.wait_terminal(row,stage['images'],stage['deadline_seconds'],stage['pending_seconds'])
            name=stage['terminal_container']; identity=ids[name]
            main=next(c for c in obj['spec']['containers'] if c['name']==name)
            observed=dict(pod_uid=row['uid'],node_name=NODE,node_uid=NODE_UID,image_id=identity['image_id'],
                          image_reference=main['image'],pod_phase=obj['status']['phase'],
                          exit_code=0,restart_count=0,container_id=identity['container_id'])
            checked=hf.validate_terminal(stage['name'],logs[name],observed,self.ns)
            self.receipts[stage['name']]=checked
            self.record(stage['name']+'-CHECKED.json',checked)
            self.deny_only()
            for item in reversed(owned):
                self.remove(item)
        self.db()

    def db(self):
        db=module(self.p['db_stage.py'],'db_stage')
        self.progress('NATIVE_PG_PROFILE')
        self.deny_only()
        pod=db.render_pg_probe(self.owner,self.ns,self.p['postgres_cache'])
        row=self.create(pod)
        names=[x['name'] for x in pod['spec'].get('initContainers',[])+pod['spec']['containers']]
        images={name:db.CONTAINER_IDENTITIES[name] for name in names}
        obj,ids,logs=self.wait_terminal(row,images,90)
        require(len(logs)==1,'pg_profile_one_container')
        profile=db.validate_pg_probe(decode(next(iter(logs.values())),262144),self.p['postgres_cache'],self.owner)
        self.receipts['postgres_profile']=dict(profile=profile,identities=ids)
        self.record('PG-PROFILE-CHECKED.json',profile)
        self.remove(row)
        self.progress('NATIVE_BACKEND_DB')
        self.deny_only()
        pod=db.render_db(self.owner,self.ns,profile)
        row=self.create(pod)
        names=[x['name'] for x in pod['spec'].get('initContainers',[])+pod['spec']['containers']]
        images={name:db.CONTAINER_IDENTITIES[name] for name in names}
        obj,ids,logs=self.wait_terminal(row,images,1500)
        receipt=decode(logs['final-receipt'],262144)
        checked=db.validate_db_summary(receipt)
        self.receipts['backend_db']=dict(checked=checked,identities=ids,postgres_profile=profile)
        self.record('BACKEND-DB-CHECKED.json',self.receipts['backend_db'])
        self.deny_only()
        self.remove(row)

    def cleanup(self):
        """Retain namespace + deny-all. Delete only ledger UIDs still owned."""
        results=[]
        for row in reversed(self.ledger):
            if row['kind']=='NetworkPolicy' and row['name']=='namespace-deny-all':
                continue
            if any(x['uid']==row['uid'] for x in self.cleaned):
                continue
            try:
                self.remove(row)
                results.append(dict(name=row['name'],uid=row['uid'],status='UID_PRECONDITION_DELETED'))
            except Exception as exc:
                results.append(dict(name=row['name'],uid=row['uid'],status='RETAINED_REVIEW_REQUIRED',code=safe_error(exc)))
        if self.priority_row is not None:
            row=self.priority_row
            try:
                known_pods=[r for r in self.ledger if r['kind']=='Pod']
                require(all(any(x['uid']==r['uid'] for x in self.cleaned) for r in known_pods),'priority_cleanup_waits_for_owned_pods')
                require(not any(r['kind']=='Pod' for r in self.uncertain_creates),'priority_cleanup_unknown_pod_outcome')
                self.namespace()
                obj=self.k.get('priorityclass',self.priority_name)
                md=obj['metadata']
                require(md.get('name')==self.priority_name and not md.get('namespace') and md.get('uid')==row['uid']
                        and md.get('labels',{}).get(OWNER_LABEL)==self.owner,'priority_delete_ownership_changed')
                self.record('PRIORITY-DELETE-REQUESTED.json',dict(name=row['name'],uid=row['uid'],owner=self.owner))
                self.k.delete_uid('PriorityClass',None,row['name'],row['uid'])
                until=self.now()+25
                while self.now()<until:
                    uid=self.k.metadata_uid('priorityclass',self.priority_name)
                    if not uid:
                        self.record('PRIORITY-DELETE-COMPLETE.json',dict(name=row['name'],uid=row['uid'],absent=True))
                        results.append(dict(kind='PriorityClass',name=row['name'],uid=row['uid'],status='UID_PRECONDITION_DELETED'))
                        break
                    require(uid==row['uid'],'priority_delete_name_replaced')
                    self.sleep(1)
                else:raise Refused('priority_delete_timeout')
            except Exception as exc:
                results.append(dict(kind='PriorityClass',name=row['name'],uid=row['uid'],status='RETAINED_REVIEW_REQUIRED',code=safe_error(exc)))
        return results

    def diagnostics(self):
        results=[]
        for row in self.ledger:
            if row['kind']!='Pod' or any(x['uid']==row['uid'] for x in self.cleaned):
                continue
            try:
                self.namespace()
                obj=self.k.get(row['kind'],row['name'],self.ns)
                require(obj['metadata']['uid']==row['uid'] and obj['metadata'].get('labels',{}).get(OWNER_LABEL)==self.owner,'diagnostic_ownership_changed')
                statuses=safe_statuses(obj)
                self.record(row['name']+'-FAILURE-STATUS.json',dict(uid=row['uid'],phase=obj.get('status',{}).get('phase'),statuses=statuses))
                for item in statuses:
                    if item['state'] in ('running','terminated'):
                        try:
                            self.k.logs(self.ns,row['name'],item['name'])
                        except Exception:
                            pass
                results.append(dict(name=row['name'],uid=row['uid'],status='OWNED_STATUS_AND_BOUNDED_LOGS_RETAINED'))
            except Exception as exc:
                results.append(dict(name=row['name'],uid=row['uid'],status='NOT_READ_OWNERSHIP_OR_TRANSPORT_GATE',code=safe_error(exc)))
        return results


def safe_error(exc):
    return str(exc)[:160] if isinstance(exc,Refused) else 'controller_' + type(exc).__name__


def safe_statuses(obj):
    rows=[]
    for s in obj.get('status',{}).get('initContainerStatuses',[])+obj.get('status',{}).get('containerStatuses',[]):
        state=s.get('state',{})
        rows.append(dict(name=s['name'],restart_count=s.get('restartCount'),image_id=s.get('imageID'),container_id=s.get('containerID'),
                         state=next(iter(state),'absent'),exit_code=state.get('terminated',{}).get('exitCode'),
                         reason=state.get('waiting',{}).get('reason') or state.get('terminated',{}).get('reason')))
    return rows


def terminal_success(obj):
    require(obj.get('status',{}).get('phase')=='Succeeded','candidate_terminal_failure')
    statuses=obj['status'].get('initContainerStatuses',[])+obj['status'].get('containerStatuses',[])
    for item in statuses:
        terminal=item.get('state',{}).get('terminated',{})
        if item['name']!='postgres-fixture':
            require(terminal.get('exitCode')==0,'candidate_terminal_failure')
        else:
            require(item.get('restartCount')==0 and terminal.get('exitCode') in (0,130,143)
                    and terminal.get('reason')!='OOMKilled','postgres_sidecar_abnormal_termination')
            final=next((s for s in statuses if s['name']=='final-receipt'),None)
            require(final and final.get('state',{}).get('terminated',{}).get('exitCode')==0,'postgres_sidecar_no_final_success')
            try:
                end=datetime.datetime.fromisoformat(terminal['finishedAt'].replace('Z','+00:00'))
                mainend=datetime.datetime.fromisoformat(final['state']['terminated']['finishedAt'].replace('Z','+00:00'))
                require(end>=mainend,'postgres_sidecar_ended_before_final')
            except (KeyError,ValueError,TypeError):
                raise Refused('postgres_sidecar_termination_time_missing')
    return True


def production(k):
    rows=[]
    # The API projects only fixed identity/image/resource fields. Neither Env nor
    # raw Deployment objects are read into controller stdout or private logs.
    template=('D|{.metadata.name}|{.metadata.uid}|{.metadata.generation}|{.spec.replicas}|'
              '{.status.observedGeneration}|{.status.readyReplicas}|{.status.unavailableReplicas}{"\\n"}'
              '{range .spec.template.spec.containers[*]}C|{.name}|{.image}|'
              '{.resources.requests.cpu}|{.resources.requests.memory}|{.resources.limits.cpu}|{.resources.limits.memory}{"\\n"}{end}')
    for name in ('gateway','frontend','ppt-master-runner','provisioner'):
        raw=k.run(['get','deployment',name,'-n','deer-flow','-o=jsonpath='+template],limit=16384)
        lines=[line.split('|') for line in raw.decode().splitlines()]
        require(lines and len(lines[0])==8 and lines[0][0:2]==['D',name] and UID.fullmatch(lines[0][2]),'production_projection_shape')
        require(all(len(x)==7 and x[0]=='C' and re.fullmatch(r'[a-z0-9-]+',x[1]) for x in lines[1:]),'production_container_projection_shape')
        require(all(len(value)<=512 and '\x00' not in value for line in lines for value in line),'production_projection_bound')
        rows.append(lines)
    return rows


def main():
    require(sys.flags.isolated and sys.flags.dont_write_bytecode and os.geteuid()==0,'root_I_B_required')
    require(sys.argv[1:]==['--qualify-native-once'],'exact_action_required')
    require(os.uname().machine in ('aarch64','arm64') and os.uname().nodename.split('.')[0]=='datax-01','source_host_identity')
    os.umask(0o077)
    data=payload()
    require(data['preload_report']==dict(bytes=24125,sha256=PRELOAD_SHA),'accepted_preload_binding')
    reviewed_failure=prior_failure()
    lockfd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    lockstat=os.fstat(lockfd)
    require(stat.S_ISREG(lockstat.st_mode) and lockstat.st_uid==0 and lockstat.st_nlink==1
            and stat.S_IMODE(lockstat.st_mode)&0o077==0,'lock_identity')
    fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    owner=uuid.uuid4().hex
    evidence=Path('/var/lib/portal-ppt-native-qualification-r2-'+owner)
    evidence.mkdir(mode=0o700)
    write_new(ONCE,encoded(dict(owner=owner,evidence=str(evidence),preload_sha256=PRELOAD_SHA)))
    k=Transport(evidence)
    session=Session(k,owner,evidence,data)
    report=dict(schema=SCHEMA,status='INCOMPLETE',owner=owner,evidence=str(evidence),automatic_retry=False,
                production_workload_mutation=False,production_database_access=False,model_access=False,
                ready_for_rollout=False,native_runtime_passed=False,preload_report_sha256=PRELOAD_SHA,
                revision=2,reviewed_prior_failure=reviewed_failure,old_r1_evidence_modified=False)
    before=None
    def interrupted(signum,frame):
        raise Refused('operator_interrupted_' + str(signum))
    signal.signal(signal.SIGTERM,interrupted)
    signal.signal(signal.SIGINT,interrupted)
    signal.signal(signal.SIGHUP,interrupted)
    try:
        session.progress('FRESH_IDENTITIES')
        report['node_before']=session.node()
        before=production(k)
        session.record('PRODUCTION-BEFORE.json',before)
        report['namespace']=session.create_namespace()
        session.create_priority()
        report['priority_class']=dict(name=session.priority_name,uid=session.priority_row['uid'],value=0,global_default=False,preemption_policy='Never')
        session.prepare_admission()
        session.network()
        session.candidates()
        report['status']='NATIVE_QUALIFICATION_COMPLETE_REVIEW_REQUIRED'
        report['native_runtime_passed']=True
    except BaseException as exc:
        report['error_code']=safe_error(exc)
    finally:
        report['stages']=session.receipts
        if session.priority_row is not None:
            report['priority_class']=dict(name=session.priority_name,uid=session.priority_row['uid'],value=0,global_default=False,preemption_policy='Never')
        session.record('STAGES-BEFORE-CLEANUP.json',session.receipts)
        report['failure_diagnostics']=session.diagnostics() if report['status']=='INCOMPLETE' else []
        report['uncertain_creates']=session.uncertain_creates
        report['cleanup']=session.cleanup() if session.namespace_uid else []
        try:
            session.deny_only()
            report['retained_namespace_deny_all']=True
        except Exception:
            report['retained_namespace_deny_all']=False
        try:
            after=production(k)
            report['production_identity_and_shape_stable']=before is not None and before==after
            session.record('PRODUCTION-AFTER.json',after)
            report['node_after']=session.node()
            require(report['retained_namespace_deny_all'],'retained_namespace_deny_all_not_verified')
            require(report['production_identity_and_shape_stable'],'production_identity_or_shape_changed')
            require(not any(x['status']=='RETAINED_REVIEW_REQUIRED' for x in report['cleanup']),'cleanup_review_required')
        except BaseException as exc:
            report['status']='INCOMPLETE'
            report['native_runtime_passed']=False
            report['final_gate_error']=safe_error(exc)
        raw=encoded(report)
        write_new(evidence/'REPORT.json',raw)
        # Safe report copied to a fresh private cnooc-readable directory only.
        import pwd
        account=pwd.getpwnam('cnooc')
        handoff=Path(tempfile.mkdtemp(prefix='portal-ppt-native-qualification-report-',dir='/var/lib'))
        write_new(handoff/'REPORT.json',raw)
        os.chown(handoff/'REPORT.json',account.pw_uid,account.pw_gid)
        os.chown(handoff,account.pw_uid,account.pw_gid)
        print(json.dumps(dict(schema=SCHEMA,status=report['status'],automatic_retry=False,owner=owner,
                              download_report=str(handoff/'REPORT.json'),report_bytes=len(raw),report_sha256=digest(raw),
                              native_runtime_passed=report['native_runtime_passed'],ready_for_rollout=False,
                              error_code=report.get('error_code') or report.get('final_gate_error'),
                              exitcode=0 if report['native_runtime_passed'] else 1)),flush=True)
        os.close(lockfd)
    return 0 if report['native_runtime_passed'] else 1


if __name__=='__main__':
    try:
        sys.exit(main())
    except BaseException as exc:
        if isinstance(exc,SystemExit):
            raise
        print(json.dumps(dict(schema=SCHEMA,status='INCOMPLETE',error_code=safe_error(exc),automatic_retry=False,
                              native_runtime_passed=False,ready_for_rollout=False,exitcode=1)),flush=True)
        sys.exit(1)
