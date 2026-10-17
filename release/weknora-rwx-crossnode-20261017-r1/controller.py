#!/usr/bin/env python3
"""Once-only new PV/PVC and cross-node synthetic test; Python3.7+, Linux root."""
import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

PROBE_SOURCE = None  # Replaced with reviewed source by the offline packager.
STATE = Path('/var/lib/weknora-rwx-crossnode-20261017-r1')
NS = 'deer-flow'
SERVER = '192.168.111.189'
SC = 'deerflow-nfs-rwx'
IMAGE = 'deerflow.local/weknora-app:20261016-native-glm-runtimefix-fc44d25744194ece'
CONFIG_ID = 'sha256:22b73c727cfb445ecf9fabe97f693efd5ceb7cbcb449b593c219e6407aef64c5'
MANIFEST = 'sha256:3bc43f4e4643fd069f59fdcd6a824ed6edb6603c3f2b957ff5b5451b853f4709'
APP_POD = 'weknora-app-c467d64fc-2zbls'
APP_UID = '2fb12206-2d91-4b12-806b-471e1ca19c61'
NODES = {'datax-01': ('ec5698b0-8d82-4eed-bae0-3711c7b7f36c', '192.168.111.2'),
         'datax-03': ('9acb9556-0d63-46ca-8a6f-384c4b469a18', '192.168.111.18')}
OLD_VOLUMES = ('deerflow-weknora-files-pv-datax-03', 'deerflow-weknora-docreader-pv-datax-03')
OLD_CLAIMS = ('weknora-data-files', 'weknora-docreader-tmp')
PAIRS = [('files', 'deerflow-weknora-files-pv-nfs-r2', 'weknora-data-files-rwx-r2', '400Gi'),
         ('docreader', 'deerflow-weknora-docreader-pv-nfs-r2', 'weknora-docreader-tmp-rwx-r2', '32Gi')]
LABEL = 'deerflow.local/weknora-rwx-check'
NFS_SHA = '72f8c073b339dc84f4746a76d494014288f38f7bb02654a5c8697f67f31a499f'
NFS_RECEIPT = {"automatic_retry": False, "business_data_copied": False,
 "clients": ["192.168.111.2", "192.168.111.18"], "cross_node_io": "NOT_TESTED",
 "directories": ["/mnt/data/weknora-rwx-r2/files", "/mnt/data/weknora-rwx-r2/docreader"],
 "existing_export_configs_unchanged": True, "existing_exports_unchanged": True,
 "exports_created": 4, "kubernetes_writes": 0,
 "next_gate": "RECEIPT_BOUND_NEW_PV_PVC_AND_SYNTHETIC_CROSS_NODE_IO",
 "nfs_directory_export_stage": "PASS", "processes_restarted": 0,
 "production_mounts_changed": False, "provider_requests": 0, "pv_pvc_created": False,
 "ready_to_migrate": False, "ready_to_scale": False, "root_squash": True,
 "server": "192.168.111.189", "utc_finished": "2026-10-17T10:02:44.831259+00:00"}
LAST = {}


class Failure(RuntimeError):
    pass


def need(ok, check):
    if not ok:
        raise Failure(check)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def safe_dir(path):
    for p in reversed((path,) + tuple(path.parents)):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_private_directory')


def read_private(path):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size < 1048576, 'unsafe_private_file')
        raw = f.read(1048576)
        need(len(raw) < 1048576, 'oversize_private_file')
    return json.loads(raw)


def record(name, value):
    need(re.fullmatch('[A-Z0-9-]+', name), 'bad_record_name')
    safe_dir(STATE)
    fd = os.open(str(STATE / (name + '.json')),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(canonical(value) + b'\n')
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(str(STATE), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(args, data=None, timeout=25, limit=4194304):
    """Bound both streams and own CLI lifetime; never log args or raw errors."""
    global LAST
    LAST = dict(program='kubectl', exit_code=None)
    p = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True)
    out = [bytearray(), bytearray()]
    bad = threading.Event()

    def drain(pipe, index):
        try:
            while True:
                block = pipe.read(4096)
                if not block:
                    break
                if len(out[index]) + len(block) > limit:
                    bad.set()
                    break
                out[index].extend(block)
        except Exception:
            bad.set()
        finally:
            pipe.close()

    def feed():
        try:
            p.stdin.write(data)
            p.stdin.close()
        except Exception:
            bad.set()

    threads = [threading.Thread(target=drain, args=(p.stdout, 0), daemon=True),
               threading.Thread(target=drain, args=(p.stderr, 1), daemon=True)]
    if data is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for t in threads:
        t.start()
    end = time.monotonic() + timeout
    expired = False
    try:
        while p.poll() is None and not bad.is_set():
            if time.monotonic() > end:
                expired = True
                break
            time.sleep(.05)
    finally:
        if p.poll() is None:
            p.kill()  # Only this CLI. Server/remote operation may still have happened.
        p.wait(timeout=5)
        for t in threads:
            t.join(timeout=2)
    LAST.update(exit_code=p.returncode, stdout_bytes=len(out[0]), stderr_bytes=len(out[1]),
                timed_out=expired)
    if p.returncode:
        text = bytes(out[1]).decode('utf-8', 'replace')
        LAST['stderr_classes'] = [key for key, pattern in (
            ('FORBIDDEN', r'(?i)forbidden|unauthorized'), ('NOT_FOUND', r'(?i)not found|NotFound'),
            ('TIMEOUT', r'(?i)timed out|deadline exceeded'), ('PERMISSION', r'(?i)permission denied'),
            ('CONNECTION', r'(?i)connection refused|no route to host')) if re.search(pattern, text)]
        try:
            failure = json.loads(bytes(out[0]))
            code = failure.get('check', '')
            if failure.get('probe') == 'UNCONFIRMED' and re.fullmatch('[A-Za-z0-9_]{1,80}', code):
                LAST['remote_probe_check'] = code
        except (ValueError, AttributeError):
            pass
    need(not expired and not bad.is_set() and not any(t.is_alive() for t in threads),
         'command_incomplete_no_retry')
    need(p.returncode == 0, 'command_failed_no_retry')
    return bytes(out[0])


def kubectl_path():
    path = shutil.which('kubectl', path='/usr/local/bin:/usr/bin:/bin')
    need(path, 'kubectl_missing')
    p = Path(path).resolve(strict=True)
    safe_dir(p.parent)
    s = p.stat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_kubectl')
    return str(p)


class Kube:
    def __init__(self):
        self.base = [kubectl_path(), '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s']

    def call(self, args, value=None, timeout=25):
        return command(self.base + args, None if value is None else canonical(value), timeout)

    def get(self, kind, name=None, optional=False):
        need(kind in ('pv', 'pvc', 'pods', 'deployment', 'service', 'endpoints',
                      'nodes', 'storageclass', 'networkpolicy'), 'get_kind_forbidden')
        args = [] if kind in ('pv', 'nodes', 'storageclass') else ['-n', NS]
        args += ['get', kind] + ([name] if name else []) + ['-o', 'json']
        if optional:
            need(name is not None, 'optional_list_forbidden')
            args += ['--ignore-not-found=true']
        raw = self.call(args)
        if not raw.strip() and optional:
            return None
        obj = json.loads(raw)
        need(isinstance(obj, dict), 'bad_api_json')
        return obj


def object_plan(token):
    label = {LABEL: token}
    result = []
    for role, pv, pvc, size in PAIRS:
        result.append(dict(apiVersion='v1', kind='PersistentVolume',
            metadata=dict(name=pv, labels=label), spec=dict(
                capacity=dict(storage=size), accessModes=['ReadWriteMany'], volumeMode='Filesystem',
                persistentVolumeReclaimPolicy='Retain', storageClassName=SC,
                mountOptions=['nfsvers=4.1', 'proto=tcp', 'hard', 'timeo=600', 'retrans=2'],
                nfs=dict(server=SERVER, path='/mnt/data/weknora-rwx-r2/' + role, readOnly=False),
                claimRef=dict(namespace=NS, name=pvc),
                nodeAffinity=dict(required=dict(nodeSelectorTerms=[dict(matchExpressions=[
                    dict(key='kubernetes.io/hostname', operator='In', values=sorted(NODES))])])))))
        result.append(dict(apiVersion='v1', kind='PersistentVolumeClaim',
            metadata=dict(name=pvc, namespace=NS, labels=label), spec=dict(
                accessModes=['ReadWriteMany'], volumeMode='Filesystem', storageClassName=SC,
                volumeName=pv, resources=dict(requests=dict(storage=size)))))
    result.append(dict(apiVersion='networking.k8s.io/v1', kind='NetworkPolicy',
        metadata=dict(name='weknora-rwx-check-' + token[:12], namespace=NS, labels=label),
        spec=dict(podSelector=dict(matchLabels=label), policyTypes=['Ingress', 'Egress'], ingress=[], egress=[])))
    for node in sorted(NODES):
        result.append(dict(apiVersion='v1', kind='Pod',
            metadata=dict(name='weknora-rwx-' + node + '-' + token[:12], namespace=NS, labels=label),
            spec=dict(nodeSelector={'kubernetes.io/hostname': node}, restartPolicy='Never',
                activeDeadlineSeconds=600, terminationGracePeriodSeconds=5,
                automountServiceAccountToken=False, enableServiceLinks=False,
                securityContext=dict(runAsUser=1000, runAsGroup=1000, runAsNonRoot=True,
                                     seccompProfile=dict(type='RuntimeDefault')),
                containers=[dict(name='probe', image=IMAGE, imagePullPolicy='Never',
                    command=['python3', '-I', '-B', '-u', '-c', 'import time; time.sleep(600)'],
                    resources=dict(requests=dict(cpu='25m', memory='32Mi'),
                                   limits=dict(cpu='100m', memory='128Mi')),
                    securityContext=dict(allowPrivilegeEscalation=False, readOnlyRootFilesystem=True,
                                         capabilities=dict(drop=['ALL'])),
                    volumeMounts=[dict(name=r, mountPath='/probe/' + r) for r, _, _, _ in PAIRS])],
                volumes=[dict(name=r, persistentVolumeClaim=dict(claimName=pvc)) for r, _, pvc, _ in PAIRS])))
    return result


KINDS = {'PersistentVolume': 'pv', 'PersistentVolumeClaim': 'pvc', 'Pod': 'pods', 'NetworkPolicy': 'networkpolicy'}


def normalize_spec(obj):
    s = copy.deepcopy(obj['spec'])
    kind = obj['kind']
    if kind == 'NetworkPolicy':
        for key in ('ingress', 'egress'):
            s.setdefault(key, [])
        s['policyTypes'] = sorted(s.get('policyTypes', []))
    elif kind == 'PersistentVolume':
        s.setdefault('volumeMode', 'Filesystem')
        s.get('nfs', {}).setdefault('readOnly', False)
        for key in ('apiVersion', 'kind', 'resourceVersion', 'uid'):
            s.get('claimRef', {}).pop(key, None)  # UID is checked separately after binding.
    elif kind == 'PersistentVolumeClaim':
        s.setdefault('volumeMode', 'Filesystem')
    elif kind == 'Pod':
        defaults = {'dnsPolicy': 'ClusterFirst', 'schedulerName': 'default-scheduler',
                    'serviceAccountName': 'default', 'serviceAccount': 'default',
                    'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
                    'shareProcessNamespace': False, 'priority': 0, 'preemptionPolicy': 'PreemptLowerPriority'}
        for key, value in defaults.items():
            if s.get(key) == value:
                s.pop(key)
        # Scheduler may fill nodeName; never accept a different node.
        if 'nodeName' in s:
            need(s['nodeName'] == s.get('nodeSelector', {}).get('kubernetes.io/hostname'), 'wrong_probe_node')
            s.pop('nodeName')
        tolerations = s.pop('tolerations', [])
        for t in tolerations:
            need(t in [dict(key='node.kubernetes.io/' + key, operator='Exists', effect='NoExecute', tolerationSeconds=300)
                       for key in ('not-ready', 'unreachable')], 'unexpected_toleration')
        for c in s.get('containers', []):
            for key, value in (('terminationMessagePath', '/dev/termination-log'), ('terminationMessagePolicy', 'File')):
                if c.get(key) == value:
                    c.pop(key)
            for m in c.get('volumeMounts', []):
                if m.get('readOnly') is False:
                    m.pop('readOnly')
    return s


def validate(expected, actual):
    need(actual and actual.get('kind') == expected['kind'], 'object_kind_changed')
    a, e = actual['metadata'], expected['metadata']
    need(a['name'] == e['name'] and a.get('namespace') == e.get('namespace')
         and a.get('labels', {}).get(LABEL) == e['labels'][LABEL]
         and not a.get('ownerReferences') and not a.get('deletionTimestamp'), 'object_metadata_changed')
    need(normalize_spec(actual) == normalize_spec(expected), 'admission_changed_spec_' + expected['kind'])


def service_safety(k, pods):
    services = k.get('service')['items']
    for pod in pods:
        labels = pod['metadata'].get('labels', {})
        for svc in services:
            selector = svc.get('spec', {}).get('selector', {})
            need(not selector or not all(labels.get(key) == value for key, value in selector.items()),
                 'probe_selected_by_existing_service')


def fresh_nodes(k):
    for name, (uid, ip) in NODES.items():
        n = k.get('nodes', name)
        conditions = {x['type']: x['status'] for x in n.get('status', {}).get('conditions', [])}
        need(n['metadata']['uid'] == uid and n['metadata'].get('labels', {}).get('kubernetes.io/hostname') == name,
             'node_identity_changed')
        need(ip in [a['address'] for a in n['status']['addresses'] if a['type'] == 'InternalIP'], 'node_ip_changed')
        need(conditions.get('Ready') == 'True' and all(conditions.get(x) == 'False'
             for x in ('MemoryPressure', 'DiskPressure', 'PIDPressure'))
             and not n.get('spec', {}).get('unschedulable') and not n.get('spec', {}).get('taints'), 'node_not_eligible')


def baseline(k):
    d = k.get('deployment', 'weknora-app')
    need(d['metadata']['uid'] == '44aca5b9-8fc1-44d6-af61-37c6d62723d5'
         and d['spec'].get('replicas') == 1 and all(d['status'].get(key) == 1
             for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
         and d['status'].get('observedGeneration') == d['metadata']['generation'], 'app_baseline_not_stable')
    pod = k.get('pods', APP_POD)
    need(pod['metadata']['uid'] == APP_UID and pod['spec'].get('nodeName') == 'datax-03'
         and not pod['metadata'].get('deletionTimestamp')
         and any(c['type'] == 'Ready' and c['status'] == 'True' for c in pod['status'].get('conditions', [])),
         'app_process_changed')
    need(any(c['name'] == 'app' and c['image'] == IMAGE for c in pod['spec']['containers']), 'app_image_changed')
    objects = [d, pod, k.get('deployment', 'weknora-docreader'), k.get('service', 'weknora-app')]
    for kind, names in (('pv', OLD_VOLUMES), ('pvc', OLD_CLAIMS)):
        for name in names:
            obj = k.get(kind, name)
            need(obj['status'].get('phase') == 'Bound', 'old_volume_not_bound')
            objects.append(obj)
    # Hash only. Never save or print existing config/environment values.
    projection = [(x['kind'], x['metadata']['name'], x['metadata']['uid'], x.get('spec')) for x in objects]
    containers = [{key: s.get(key) for key in ('name', 'containerID', 'imageID', 'restartCount', 'ready')}
                  for s in pod['status'].get('containerStatuses', [])]
    endpoints = k.get('endpoints', 'weknora-app')
    return digest(dict(objects=projection, containers=containers, endpoints=endpoints.get('subsets', [])))


def live_owned(k, expected):
    obj = k.get(KINDS[expected['kind']], expected['metadata']['name'])
    validate(expected, obj)
    ack = read_private(STATE / (key_for(expected) + '-CREATED.json'))
    need(obj['metadata']['uid'] == ack['uid'], 'owned_object_uid_changed')
    return obj


def key_for(obj):
    return (obj['kind'] + '-' + obj['metadata']['name']).upper()


def create_one(k, obj):
    name = key_for(obj)
    record(name + '-REQUESTED', dict(manifest=obj))
    emit(dict(cross_node_test='CREATE', kind=obj['kind'], name=obj['metadata']['name']))
    value = json.loads(k.call(['create', '-f', '-', '-o', 'json'], obj))
    # Keep acknowledged UID even when admission review fails.
    record(name + '-CREATED', dict(uid=value['metadata']['uid']))
    validate(obj, value)


def check_bindings(k, plan):
    bound = True
    for i in (0, 2):
        pv, pvc = live_owned(k, plan[i]), live_owned(k, plan[i + 1])
        ref = pv['spec']['claimRef']
        if ref.get('uid') is not None:
            need(ref['uid'] == pvc['metadata']['uid'], 'pv_bound_to_other_claim_uid')
        bound &= (pv.get('status', {}).get('phase') == 'Bound'
                  and pvc.get('status', {}).get('phase') == 'Bound' and ref.get('uid') == pvc['metadata']['uid'])
    return bound


def check_probe(k, expected, ready=True):
    pod = live_owned(k, expected)
    service_safety(k, [pod])
    need(pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded'), 'test_pod_terminal')
    cs = pod.get('status', {}).get('containerStatuses', [])
    waiting = [s.get('state', {}).get('waiting', {}).get('reason') for s in cs]
    need(not set(waiting) & {'ErrImageNeverPull', 'ImagePullBackOff', 'CreateContainerConfigError', 'CrashLoopBackOff'},
         'test_pod_start_failed')
    running = (pod.get('status', {}).get('phase') == 'Running' and len(cs) == 1
               and cs[0].get('ready') is True and bool(cs[0].get('state', {}).get('running')))
    if running:
        image_id = cs[0].get('imageID', '')
        need(any(image_id == v or image_id.endswith('@' + v) or image_id == 'containerd://' + v
                 for v in (CONFIG_ID, MANIFEST)), 'probe_image_identity_mismatch')
        need(cs[0].get('restartCount') == 0 and pod['spec'].get('nodeName')
             == expected['spec']['nodeSelector']['kubernetes.io/hostname'], 'probe_restarted_or_wrong_node')
    if ready:
        need(running, 'probe_not_ready')
    return running


def wait_for(check, label, deadline=100):
    start = time.monotonic()
    while True:
        if check():
            return
        elapsed = int(time.monotonic() - start)
        emit(dict(cross_node_test='WAIT', phase=label, elapsed_seconds=elapsed))
        need(elapsed < deadline, 'wait_timeout_' + label)
        time.sleep(5)


def exec_phase(k, plan, phase, token, index):
    need(check_bindings(k, plan), 'binding_lost')
    live_owned(k, plan[4])
    for p in plan[5:]:
        check_probe(k, p)
    pod = plan[index]['metadata']['name']
    key = 'IO-' + phase.upper()
    record(key + '-REQUESTED', dict(pod=pod, phase=phase, token=token))
    emit(dict(cross_node_test='SYNTHETIC_IO', phase=phase,
              node=plan[index]['spec']['nodeSelector']['kubernetes.io/hostname']))
    # No remote shell, no stdin credential, no business-file or network client.
    raw = k.call(['-n', NS, 'exec', pod, '-c', 'probe', '--', 'python3', '-I', '-B', '-u',
                  '-c', PROBE_SOURCE, phase, token], timeout=45)
    result = json.loads(raw)
    need(result.get('probe') == 'PASS' and result.get('phase') == phase and result.get('token') == token
         and result.get('uid') == 1000 and result.get('gid') == 1000
         and [x.get('role') for x in result.get('volumes', [])] == ['files', 'docreader']
         and all(x.get('result') == 'PASS' for x in result['volumes']), 'probe_receipt_incomplete')
    record(key + '-ACK', result)
    emit(result)


def delete_test_object(k, obj):
    need(obj['kind'] in ('Pod', 'NetworkPolicy'), 'delete_kind_forbidden')
    live = live_owned(k, obj)
    prefix = '/api/v1' if obj['kind'] == 'Pod' else '/apis/networking.k8s.io/v1'
    plural = 'pods' if obj['kind'] == 'Pod' else 'networkpolicies'
    path = prefix + '/namespaces/' + NS + '/' + plural + '/' + obj['metadata']['name']
    options = dict(apiVersion='v1', kind='DeleteOptions', preconditions=dict(uid=live['metadata']['uid']),
                   propagationPolicy='Foreground', gracePeriodSeconds=5)
    record(key_for(obj) + '-DELETE-REQUESTED', dict(uid=live['metadata']['uid']))
    k.call(['delete', '--raw', path, '-f', '-'], options)
    record(key_for(obj) + '-DELETE-ACK', dict(uid=live['metadata']['uid']))
    wait_for(lambda: k.get(KINDS[obj['kind']], obj['metadata']['name'], optional=True) is None,
             'TEST_OBJECT_REMOVAL', deadline=40)


def environment():
    need(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
         'run_as_root_on_datax01_only')
    need(isinstance(PROBE_SOURCE, str) and len(PROBE_SOURCE) < 24000, 'packaged_probe_required')
    need(hashlib.sha256(canonical(NFS_RECEIPT) + b'\n').hexdigest() == NFS_SHA, 'nfs_receipt_binding')
    safe_dir(STATE.parent)
    return Kube()


def once():
    k = environment()
    # A second invocation must stop before any API mutation and never overwrite evidence.
    STATE.mkdir(mode=0o700)
    token = uuid.uuid4().hex
    plan = object_plan(token)
    record('STARTED', dict(token=token, plan=plan, nfs_receipt_sha256=NFS_SHA,
                          evidence_source='OPERATOR_RETURNED_PINNED_NFS_RECEIPT', automatic_retry=False))
    phase = 'FRESH_BASELINE_AND_DRY_RUN'
    result = dict(cross_node_io='UNCONFIRMED', ready_to_migrate=False, ready_to_scale=False,
                  production_mounts_changed=False, business_data_copied=False,
                  existing_workloads_modified=False, provider_requests=0, automatic_retry=False,
                  network_policy_enforcement='NOT_INDEPENDENTLY_VERIFIED', state=str(STATE))
    rc = 1
    try:
        emit(dict(cross_node_test='RUNNING', phase=phase, state=str(STATE)))
        fresh_nodes(k)
        original = baseline(k)
        sc = k.get('storageclass', SC)
        need(sc.get('provisioner') == 'kubernetes.io/no-provisioner'
             and sc.get('volumeBindingMode', 'Immediate') == 'Immediate', 'storageclass_changed')
        service_safety(k, plan[5:])
        for obj in plan:
            need(k.get(KINDS[obj['kind']], obj['metadata']['name'], optional=True) is None, 'new_name_already_exists')
            dry = json.loads(k.call(['create', '--dry-run=server', '-f', '-', '-o', 'json'], obj))
            validate(obj, dry)
            if obj['kind'] == 'Pod':
                service_safety(k, [dry])
        record('BASELINE', dict(sha256=original, nfs_stage_receipt=NFS_RECEIPT))
        need(baseline(k) == original, 'baseline_changed_before_create')
        phase = 'CREATE_NEW_VOLUMES_AND_TEST_OBJECTS'
        for obj in plan[:5]:
            create_one(k, obj)
        wait_for(lambda: check_bindings(k, plan), 'NEW_PVC_BINDING')
        for obj in plan[5:]:
            create_one(k, obj)
        wait_for(lambda: all([check_probe(k, obj, ready=False) for obj in plan[5:]]), 'TWO_TEST_PODS')
        need(baseline(k) == original, 'production_baseline_drift_before_io')
        for phase, index in (('write-a', 5), ('read-a-write-b', 6), ('read-b', 5)):
            exec_phase(k, plan, phase, token, index)
        phase = 'CLEANUP_VERIFIED_SYNTHETIC_FILES'
        exec_phase(k, plan, 'cleanup', token, 5)
        phase = 'REMOVE_ONLY_UID_BOUND_TEST_PODS_AND_POLICY'
        for obj in plan[5:] + [plan[4]]:
            delete_test_object(k, obj)
        fresh_nodes(k)
        need(check_bindings(k, plan), 'final_binding_missing')
        need(baseline(k) == original, 'production_baseline_drift_at_finish')
        result.update(cross_node_io='PASS', cleanup='SYNTHETIC_FILES_AND_TWO_PODS_AND_POLICY_REMOVED',
                      new_pv_pvc_retained=True, new_pv_pvc_pairs=2, synthetic_bytes_written=262144,
                      production_baseline='UNCHANGED', next_gate='MIGRATION_PLAN_AND_BOUNDED_COPY_PILOT_REVIEW',
                      performance_acceptance='NOT_TESTED', server_failure_durability='NOT_TESTED')
        rc = 0
    except BaseException as exc:
        result.update(error_type=type(exc).__name__, failed_stage=phase,
                      failed_check=str(exc) if isinstance(exc, Failure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                      last_operation=LAST, raw_error_printed=False,
                      cleanup='NOT_AUTOMATIC_ON_FAILURE; TEST_POD_DEADLINE_600S',
                      instruction='USE_STATUS_NO_REPEAT_NO_DELETE_STATE_NO_PRODUCTION_SWITCH')
    result['utc_finished'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    record('RESULT', result)
    emit(result)
    emit(dict(report=str(STATE / 'RESULT.json'), report_sha256=hashlib.sha256(canonical(result) + b'\n').hexdigest(),
              status_command='sudo python3 -I -B ' + str(Path(__file__).resolve()) + ' --status'))
    return rc


def status():
    k = environment()
    if not os.path.lexists(str(STATE)):
        emit(dict(cross_node_status='NOT_STARTED', files_modified=False))
        return
    safe_dir(STATE)
    started = read_private(STATE / 'STARTED.json')
    need(started['nfs_receipt_sha256'] == NFS_SHA and started['plan'] == object_plan(started['token']), 'state_binding_changed')
    rows = []
    for obj in started['plan']:
        actual = k.get(KINDS[obj['kind']], obj['metadata']['name'], optional=True)
        row = dict(kind=obj['kind'], name=obj['metadata']['name'], present=actual is not None)
        if actual:
            row.update(uid=actual['metadata']['uid'], phase=actual.get('status', {}).get('phase'),
                       deleting=bool(actual['metadata'].get('deletionTimestamp')))
        rows.append(row)
    path = STATE / 'RESULT.json'
    result = read_private(path) if path.exists() else None
    emit(dict(cross_node_status='READ_ONLY', objects=rows, result=result, pod_exec=False,
              files_modified=False, kubernetes_writes=0, provider_requests=0,
              instruction='SEND_STATUS_NO_REPEAT_VERIFY_NO_DELETE_STATE'))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--verify-once', action='store_true')
    group.add_argument('--status', action='store_true')
    args = parser.parse_args()
    try:
        if args.status:
            status()
            return 0
        return once()
    except BaseException as exc:
        emit(dict(cross_node_test='NOT_STARTED_OR_STATUS_INCOMPLETE', error_type=type(exc).__name__,
                  failed_check=str(exc) if isinstance(exc, Failure) else 'EXISTING_STATE_OR_SAFE_DIAGNOSTIC_REQUIRED',
                  automatic_retry=False, raw_error_printed=False,
                  instruction='USE_STATUS_NO_REPEAT_NO_DELETE_STATE'))
        return 1


if __name__ == '__main__':
    sys.exit(main())
