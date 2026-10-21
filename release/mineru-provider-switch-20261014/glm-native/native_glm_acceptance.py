"""One isolated native GLM request from the preloaded candidate. Linux Py3.7+."""
import argparse
import base64
import getpass
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import socket
import stat
import subprocess
import sys
import time
import traceback
import uuid
import warnings

STATE = Path('/var/lib/weknora-glm-native-acceptance-20261017-r1')
PRELOAD = Path('/var/lib/weknora-glm-preload-tool-0ZkMSXwO/preload.py')
PRELOAD_SHA = 'f1a11904740573ec86ddfa7f5f387ecc07a1c79730a8ff013ee22a134853317f'
HOST = 'aiapi.cnooc.com.cn'
CA_SHA = '84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s']
PHASE = 'READ_ONLY_PREFLIGHT'
LAST = {}


class AcceptanceFailure(RuntimeError):
    pass


def need(ok, check):
    if not ok: raise AcceptanceFailure(check)


def emit(row):
    print(json.dumps(row, sort_keys=True), flush=True)


def phase(name):
    global PHASE
    PHASE = name
    emit({'native_acceptance': 'RUNNING', 'phase': name, 'production_writes': 0})


def safe_read(path, expected):
    for p in path.parents:
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_helper_parent')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and 0 < s.st_size < 1048576, 'unsafe_helper')
        raw = f.read(1048576)
    need(hashlib.sha256(raw).hexdigest() == expected, 'helper_digest_changed')
    return raw


def initialize():
    raw = safe_read(PRELOAD, PRELOAD_SHA)
    spec = importlib.util.spec_from_loader('native_preload_bound', loader=None)
    p = importlib.util.module_from_spec(spec); p.__file__ = str(PRELOAD)
    exec(compile(raw, str(PRELOAD), 'exec'), p.__dict__)
    return p, p.load_helper()


def call(args, data=None, timeout=40, limit=4*1024**2, checked=True):
    """No shell/raw errors. Input (including key) never appears in argv or logs."""
    LAST.clear(); LAST.update(operation=args[0] if args else 'unknown')
    # A long exec has its own bounded client window, not the 20s GET timeout.
    prefix = [('--request-timeout=%ds' % int(timeout)) if
              x.startswith('--request-timeout=') and args[0] == 'exec' and timeout > 40 else x for x in KUBE]
    command = prefix + args
    child = subprocess.Popen(command, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    out = {'stdout': bytearray(), 'stderr': bytearray()}
    start = time.monotonic(); beat = start; sent = 0
    try:
        with selectors.DefaultSelector() as sel:
            for name in ('stdout', 'stderr'):
                sel.register(getattr(child, name), selectors.EVENT_READ, name)
            if data is not None:
                os.set_blocking(child.stdin.fileno(), False)
                sel.register(child.stdin, selectors.EVENT_WRITE, 'stdin')
            while sel.get_map() or child.poll() is None:
                now = time.monotonic()
                need(now-start < timeout, 'command_deadline_USE_STATUS')
                if now-beat >= 15:
                    emit({'native_acceptance': 'RUNNING', 'phase': PHASE, 'elapsed_seconds': int(now-start)})
                    beat = now
                for key, _ in sel.select(.2):
                    if key.data == 'stdin':
                        try: sent += os.write(child.stdin.fileno(), data[sent:sent+65536])
                        except BrokenPipeError: sent = len(data)
                        if sent == len(data): sel.unregister(child.stdin); child.stdin.close()
                    else:
                        block = os.read(key.fileobj.fileno(), 65536)
                        if not block: sel.unregister(key.fileobj); continue
                        out[key.data].extend(block)
                        need(len(out[key.data]) <= (limit if key.data == 'stdout' else 65536), 'command_output_bound')
            code = child.wait(timeout=2)
        LAST.update(exit_code=code, stdout_bytes=len(out['stdout']), stderr_bytes=len(out['stderr']))
        if checked: need(code == 0, 'kubernetes_command_failed_USE_STATUS')
        return code, bytes(out['stdout'])
    finally:
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try: child.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(child.pid, signal.SIGKILL); child.wait(timeout=5)
        for pipe in (child.stdin, child.stdout, child.stderr):
            if pipe: pipe.close()


def get(kind, name, namespace=None, missing=False):
    args = ['get', kind, name, '--show-managed-fields=false', '-o', 'json']
    if namespace: args += ['-n', namespace]
    if missing: args += ['--ignore-not-found']
    raw = call(args)[1]
    return json.loads(raw) if raw.strip() else None


# Only the public trust bundle is copied; no config/environment dump or Secret GET.
CA_READ = r'''
import os,sys,hashlib
from pathlib import Path
p=Path(os.environ['SSL_CERT_FILE']); b=p.read_bytes()
assert 0<len(b)<=1048576
assert hashlib.sha256(b).hexdigest()=='84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af'
sys.stdout.buffer.write(b)
'''

# PID1 idles without credentials or server startup. A separate one-shot exec
# owns the remote exclusive attempt marker. Raw native logs are never persisted.
RUNNER = r'''
import hashlib,json,os,resource,selectors,signal,stat,subprocess,sys,time
from pathlib import Path
ROOT=Path('/tmp/native-glm')
def save(name,value):
 b=(json.dumps(value,sort_keys=True)+'\n').encode()
 fd=os.open(str(ROOT/name),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f: f.write(b); f.flush(); os.fsync(f.fileno())
def env():
 return {'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8',
 'SSL_CERT_FILE':'/probe/ca.bin','SSL_CERT_DIR':'/probe/empty-certs',
 'JIEBA_DICT_DIR':'/usr/local/share/weknora/jieba','SSRF_WHITELIST_EXTRA':'aiapi.cnooc.com.cn',
 'NO_PROXY':'aiapi.cnooc.com.cn','no_proxy':'aiapi.cnooc.com.cn',
 'VLM_HTTP_TIMEOUT_SECONDS':'180','LLM_DEBUG_LOG':'false','LOG_PATH':'','LOG_LEVEL':'error'}
def validate():
 assert os.geteuid()==1000 and os.getegid()==1000
 cfg=json.loads(Path('/probe/expected.json').read_bytes())
 assert hashlib.sha256(Path('/probe/ca.bin').read_bytes()).hexdigest()==cfg['ca_sha256']
 for n,x in cfg['files'].items():
  p=Path('/'+n); s=p.lstat()
  assert stat.S_ISREG(s.st_mode) and s.st_uid==s.st_gid==0 and stat.S_IMODE(s.st_mode)==x['mode']
  h=hashlib.sha256()
  with p.open('rb') as f:
   for b in iter(lambda:f.read(1048576),b''): h.update(b)
  assert h.hexdigest()==x['sha256']
def native(data):
 p=subprocess.Popen(['/usr/local/bin/weknora-glm-probe','-probe-once'],stdin=subprocess.PIPE,
 stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env(),cwd='/tmp',start_new_session=True)
 out={'stdout':bytearray(),'stderr':bytearray()}; start=time.monotonic()
 try:
  p.stdin.write(data); p.stdin.close()
  with selectors.DefaultSelector() as sel:
   sel.register(p.stdout,selectors.EVENT_READ,'stdout'); sel.register(p.stderr,selectors.EVENT_READ,'stderr')
   while sel.get_map() or p.poll() is None:
    if time.monotonic()-start>200: raise RuntimeError('native_deadline')
    for key,_ in sel.select(.2):
     b=os.read(key.fileobj.fileno(),8192)
     if not b: sel.unregister(key.fileobj); continue
     out[key.data].extend(b)
     if len(out[key.data])>1048576: raise RuntimeError('output_bound')
  rc=p.wait(timeout=2)
  rows=[]
  for line in out['stdout'].splitlines():
   try: row=json.loads(line)
   except (ValueError,UnicodeError): continue
   if isinstance(row,dict) and 'native_glm_probe' in row: rows.append(row)
  assert len(rows)==1
  row=rows[0]
  allowed=('native_glm_probe','scope','maximum_submissions','elapsed_seconds','synthetic_red_check',
  'visible_characters','raw_response_saved','credentials_saved','production_model_changed',
  'native_enrichment_acceptance','ready_for_rollout','provider_submission_may_have_occurred')
  result={k:row[k] for k in allowed if k in row}
  label=row.get('failed_check')
  if label in ('explicit_probe_once_required','probe_requires_debug_logging_disabled_at_startup',
  'credential_input_invalid','native_client_initialization_failed','synthetic_image_failed','native_predict_failed'):
   result['failed_check']=label
  result.update(native_exit_code=rc,raw_logs_saved=False)
  return result
 finally:
  if p.poll() is None:
   os.killpg(p.pid,signal.SIGTERM)
   try: p.wait(timeout=5)
   except subprocess.TimeoutExpired: os.killpg(p.pid,signal.SIGKILL); p.wait(timeout=5)
  p.stdout.close(); p.stderr.close()
resource.setrlimit(resource.RLIMIT_CORE,(0,0)); os.umask(0o077)
mode=sys.argv[1]
if mode=='idle':
 try:
  ROOT.mkdir(mode=0o700); validate()
  r=subprocess.run(['/usr/local/bin/weknora-glm-probe','-h'],env=env(),cwd='/tmp',
  stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
  assert r.returncode==0 and b'probe-once' in r.stdout+r.stderr
  save('READY.json',{'runtime_files':'PASS','nonroot_init':'PASS','provider_requests':0})
 except BaseException as e:
  print(json.dumps({'native_fixture_init':'FAIL','error_type':type(e).__name__,'raw_error_printed':False}),flush=True)
  sys.exit(1)
 printed=False
 for _ in range(720):
  if not printed and (ROOT/'RESULT.json').is_file():
   print(json.dumps({'native_terminal_receipt':json.loads((ROOT/'RESULT.json').read_bytes())}),flush=True)
   printed=True
  time.sleep(1)
elif mode=='status':
 print(json.dumps({n:json.loads((ROOT/n).read_bytes()) for n in ('READY.json','ATTEMPTED.json','RESULT.json') if (ROOT/n).is_file()}))
elif mode=='run':
 assert (ROOT/'READY.json').is_file()
 validate()
 raw=sys.stdin.buffer.read(8193); assert len(raw)<=8192
 x=json.loads(raw); assert set(x)=={'api_key'} and isinstance(x['api_key'],str)
 assert 8<=len(x['api_key'])<=4096 and all(33<=ord(c)<=126 for c in x['api_key'])
 save('ATTEMPTED.json',{'maximum_submissions':1,'automatic_retry':False})
 try: result=native(raw)
 except BaseException as e:
  result={'native_glm_probe':'UNCONFIRMED','error_type':type(e).__name__,
  'provider_submission_may_have_occurred':True,'raw_error_printed':False}
 save('RESULT.json',result)
 print(json.dumps({'remote_result_saved':True}))
else: raise ValueError('unsupported_mode')
'''


def resolve_ips(source_pod=None):
    # Bounded resolver in child; names stay exact. Temporary fixture hostAliases
    # avoid DNS-wide egress permissions and preserve TLS hostname validation.
    script = 'import socket,json;print(json.dumps(sorted({r[4][0] for r in socket.getaddrinfo(%r,443,socket.AF_INET,socket.SOCK_STREAM)})))' % HOST
    if source_pod:
        raw = call(['exec', '-n', 'deer-flow', source_pod, '-c', 'app', '--',
                    'python3', '-I', '-B', '-c', script], timeout=25, limit=4096)[1]
    else:
        r = subprocess.run([sys.executable, '-I', '-B', '-c', script], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        need(r.returncode == 0 and len(r.stdout) < 4096, 'provider_dns_unavailable')
        raw = r.stdout
    ips = json.loads(raw)
    need(isinstance(ips, list) and 0 < len(ips) <= 8, 'provider_dns_shape')
    for value in ips:
        ip = ipaddress.ip_address(value)
        need(ip.version == 4 and not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified), 'provider_dns_unsafe')
    return ips


def manifests(namespace, image, ca, files, ips, taints):
    labels = {'app': 'weknora-native-qualification'}
    meta = lambda name: {'name': name, 'namespace': namespace, 'labels': labels}
    policy = {'apiVersion': 'networking.k8s.io/v1', 'kind': 'NetworkPolicy', 'metadata': meta('isolation'),
        'spec': {'podSelector': {}, 'policyTypes': ['Ingress', 'Egress'], 'ingress': [],
                 'egress': [{'to': [{'ipBlock': {'cidr': ip+'/32'}} for ip in ips],
                             'ports': [{'protocol': 'TCP', 'port': 443}]}]}}
    cm = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta('probe'), 'immutable': True,
          'binaryData': {'ca.bin': base64.b64encode(ca).decode('ascii')},
          'data': {'runner.py': RUNNER, 'expected.json': json.dumps({'ca_sha256': CA_SHA, 'files': files})}}
    tolerations = []
    for t in taints:
        if t.get('effect') not in ('NoSchedule', 'NoExecute'): continue
        need(t['key'] in ('node-role.kubernetes.io/control-plane', 'node-role.kubernetes.io/master')
             and t['effect'] == 'NoSchedule', 'node_taint_requires_review')
        tolerations.append({'key': t['key'], 'effect': 'NoSchedule', 'operator': 'Equal', 'value': t.get('value', '')})
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta('native-glm'), 'spec': {
        'nodeSelector': {'kubernetes.io/hostname': 'datax-01'}, 'restartPolicy': 'Never',
        'activeDeadlineSeconds': 900, 'terminationGracePeriodSeconds': 10,
        'automountServiceAccountToken': False, 'enableServiceLinks': False,
        'dnsPolicy': 'None', 'dnsConfig': {'nameservers': ['127.0.0.1']},
        'hostAliases': [{'ip': ip, 'hostnames': [HOST]} for ip in ips], 'tolerations': tolerations,
        'securityContext': {'runAsNonRoot': True, 'runAsUser': 1000, 'runAsGroup': 1000,
                            'fsGroup': 1000, 'seccompProfile': {'type': 'RuntimeDefault'}},
        'containers': [{'name': 'probe', 'image': image, 'imagePullPolicy': 'Never',
            'command': ['python3', '-I', '-B', '/probe/runner.py', 'idle'],
            'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                'capabilities': {'drop': ['ALL']}},
            'resources': {'requests': {'cpu': '250m', 'memory': '512Mi', 'ephemeral-storage': '64Mi'},
                          'limits': {'cpu': '1', 'memory': '1Gi', 'ephemeral-storage': '256Mi'}},
            'volumeMounts': [{'name': 'probe', 'mountPath': '/probe', 'readOnly': True},
                             {'name': 'scratch', 'mountPath': '/tmp'}]}],
        'volumes': [{'name': 'probe', 'configMap': {'name': 'probe', 'defaultMode': 292}},
                    {'name': 'scratch', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '32Mi'}}]}}
    return policy, cm, pod


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(subset(a, b) for a, b in zip(expected, actual))
    return expected == actual


def validate_pod(pod, expected, uid=None):
    need(pod['metadata']['name'] == 'native-glm' and pod['metadata']['namespace'] == expected['metadata']['namespace']
         and not pod['metadata'].get('deletionTimestamp'), 'fixture_identity_changed')
    if uid: need(pod['metadata']['uid'] == uid, 'fixture_uid_changed')
    s = pod['spec']; e = expected['spec']
    # K8s may default false fields to absence, add harmless ServiceAccountName,
    # default image messages and transient node-not-ready tolerations.
    core = {k: v for k, v in e.items() if k != 'tolerations'}
    need(subset(core, s), 'fixture_spec_changed')
    need(not any(s.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC', 'shareProcessNamespace', 'initContainers', 'ephemeralContainers')),
         'fixture_privilege_or_extra_container')
    c = s['containers'][0]
    need(not any(c.get(k) for k in ('env', 'envFrom', 'args', 'ports', 'lifecycle'))
         and not c['securityContext'].get('privileged')
         and not c['securityContext'].get('capabilities', {}).get('add'), 'fixture_injected_settings')
    if s.get('nodeName'): need(s['nodeName'] == 'datax-01', 'fixture_wrong_node')


def remote_status(namespace):
    raw = call(['exec', '-n', namespace, 'native-glm', '-c', 'probe', '--',
                'python3', '-I', '-B', '/probe/runner.py', 'status'], limit=16384)[1]
    result = json.loads(raw)
    need(isinstance(result, dict) and set(result) <= {'READY.json', 'ATTEMPTED.json', 'RESULT.json'}, 'unexpected_remote_status')
    return result


def terminal_diagnostics(namespace):
    # PID1 writes only safe summaries, never native stdout/stderr or the key.
    raw = call(['logs', '-n', namespace, 'native-glm', '-c', 'probe', '--limit-bytes=65536'], limit=65536)[1]
    rows = []
    for line in raw.splitlines():
        try: row = json.loads(line)
        except (ValueError, UnicodeError): continue
        if isinstance(row, dict) and (row.get('native_fixture_init') == 'FAIL' or 'native_terminal_receipt' in row):
            rows.append(row)
    return rows[-2:]


def accepted(row):
    return (row.get('native_glm_probe') == 'PASS' and row.get('native_exit_code') == 0
            and row.get('scope') == 'NATIVE_GO_VLM_CLIENT_SYNTHETIC_IMAGE'
            and row.get('maximum_submissions') == 1 and row.get('synthetic_red_check') is True
            and row.get('credentials_saved') is False and row.get('raw_response_saved') is False
            and row.get('production_model_changed') is False and row.get('ready_for_rollout') is False
            and row.get('native_enrichment_acceptance') == 'NOT_TESTED')


def hidden_key():
    import termios
    fd = os.open('/dev/tty', os.O_RDWR | getattr(os, 'O_NOCTTY', 0))
    try:
        need(os.isatty(fd), 'hidden_terminal_required'); termios.tcgetattr(fd)
    finally: os.close(fd)
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        token = getpass.getpass('GLM API key (hidden; never saved): ')
    need(re.fullmatch(r'[!-~]{8,4096}', token) is not None, 'credential_shape')
    return token


def create(q, obj, receipt):
    q.record(STATE/(receipt+'-REQUESTED.json'), {'kind': obj['kind'], 'name': obj['metadata']['name']})
    raw = call(['create', '-f', '-', '-o', 'json'], json.dumps(obj).encode())[1]
    result = json.loads(raw)
    need(result['kind'] == obj['kind'] and result['metadata']['name'] == obj['metadata']['name'], 'created_identity_changed')
    q.record(STATE/(receipt+'-ACK.json'), {'name': result['metadata']['name'], 'uid': result['metadata']['uid']})
    return result


def preflight(p, q):
    phase('VERIFY_PRELOAD_AND_LIVE_BASELINE')
    r = q.document(p.STATE/'RESULT.json')
    need(r.get('weknora_candidate_preload') == 'PASS' and r.get('node') == 'datax-01'
         and r.get('production_workloads_mutated') is False, 'preload_not_passed')
    p.image_check(require_pin=True)
    raw = q.read(p.SOURCE/'BUILD-RESULT.json')
    need(hashlib.sha256(raw).hexdigest() == p.REPORT_SHA, 'candidate_build_receipt_changed')
    build = json.loads(raw); files = build['component']['runtime_files']
    need(len(files) == 8 and 'usr/local/bin/weknora-glm-probe' in files, 'runtime_files_changed')
    _, guard, plan = q.package(); guard.live(plan, binary=True)
    need(guard.available_memory() >= 4*1024**3, 'host_memory_reserve')
    p.disk_budget(q, p.image_fs_paths(p.cri('imagefsinfo', '--output', 'json')), initial=False)
    node = get('node', 'datax-01')
    need(not node['spec'].get('unschedulable') and node['metadata']['labels'].get('kubernetes.io/hostname') == 'datax-01',
         'fixture_node_unavailable')
    ca = call(['exec', '-n', 'deer-flow', guard.audit.POD, '-c', 'app', '--',
               'python3', '-I', '-B', '-c', CA_READ], limit=1048576)[1]
    need(hashlib.sha256(ca).hexdigest() == CA_SHA, 'trusted_ca_changed')
    return guard, plan, ca, files, resolve_ips(guard.audit.POD), node['spec'].get('taints', [])


def common():
    return {'production_writes': 0, 'production_workloads_mutated': False, 'model_changed': False,
        'consumer_slots_changed': False, 'native_enrichment_acceptance': 'NOT_TESTED',
        'credentials_saved': False, 'automatic_retry': False, 'ready_for_rollout': False,
        'maximum_submissions': 1, 'state': str(STATE),
        'instruction': 'USE_STATUS_ON_UNCERTAINTY_DO_NOT_REPEAT_PROBE'}


def run(p, q):
    need(not os.path.lexists(str(STATE)), 'previous_operation_exists_USE_STATUS')
    q.directory(STATE.parent)
    guard, plan, ca, files, ips, taints = preflight(p, q)
    ns = 'weknora-glm-check-'+uuid.uuid4().hex[:16]
    policy, cm, pod = manifests(ns, p.IMAGE, ca, files, ips, taints)
    STATE.mkdir(mode=0o700)
    q.record(STATE/'STARTED.json', {'namespace': ns, 'candidate': p.IMAGE, 'manifest': p.MANIFEST,
                                  'maximum_submissions': 1, 'production_writes': 0})
    q.record(STATE/'POD-EXPECTED.json', pod)
    report = dict(common(), native_glm_acceptance='UNCONFIRMED', provider_submission_attempted=False,
                  namespace=ns, cleanup='NOT_REQUESTED', network_policy_enforcement='NOT_INDEPENDENTLY_VERIFIED')
    try:
        phase('CREATE_ISOLATED_FIXTURE_NO_PRODUCTION_STORES')
        create(q, {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns,
                   'labels': {'app': 'weknora-native-qualification'}}}, 'NAMESPACE')
        for obj, label in ((policy, 'POLICY'), (cm, 'CONFIG'), (pod, 'POD')):
            dry = json.loads(call(['create', '--dry-run=server', '-f', '-', '-o', 'json'], json.dumps(obj).encode())[1])
            if label == 'POD': validate_pod(dry, pod)
            else: need(subset(obj, dry), 'admission_changed_isolation_or_probe')
            live = create(q, obj, label)
            if label == 'POD': validate_pod(live, pod)
            else: need(subset(obj, live), 'created_isolation_or_probe_changed')
        uid = live['metadata']['uid']; start = time.monotonic()
        phase('WAIT_NONROOT_NATIVE_INIT')
        while True:
            current = get('pod', 'native-glm', ns); validate_pod(current, pod, uid)
            status = current.get('status', {}); cs = status.get('containerStatuses', [])
            if status.get('phase') in ('Failed', 'Succeeded'):
                report['fixture_diagnostics'] = terminal_diagnostics(ns)
                raise AcceptanceFailure('fixture_terminated_before_request')
            need(time.monotonic()-start < 180, 'fixture_start_deadline')
            if cs and cs[0].get('state', {}).get('running') is not None:
                need(len(cs) == 1 and cs[0].get('restartCount') == 0 and cs[0].get('imageID') == p.CONFIG,
                     'fixture_runtime_identity_changed')
                state = remote_status(ns)
                if state.get('READY.json', {}).get('runtime_files') == 'PASS': break
            emit({'native_fixture': 'WAITING', 'elapsed_seconds': int(time.monotonic()-start), 'phase': status.get('phase')})
            time.sleep(5)
        need(not state.get('ATTEMPTED.json') and not state.get('RESULT.json'), 'unexpected_prior_remote_request')
        q.record(STATE/'RUNTIME-READY.json', {'pod_uid': uid, 'ca_sha256': CA_SHA, 'runtime_files': 'PASS',
                 'image_id': p.CONFIG, 'database_or_redis_configuration_supplied': False})
        phase('HIDDEN_INPUT_AND_ONCE_ONLY_NATIVE_REQUEST')
        token = hidden_key()
        guard.live(plan, binary=True)
        current = get('pod', 'native-glm', ns); validate_pod(current, pod, uid)
        statuses = current.get('status', {}).get('containerStatuses', [])
        need(len(statuses) == 1 and statuses[0].get('restartCount') == 0
             and statuses[0].get('imageID') == p.CONFIG and statuses[0].get('state', {}).get('running') is not None,
             'fixture_runtime_changed_before_submission')
        need(subset(policy, get('networkpolicy', 'isolation', ns)), 'network_policy_changed')
        current_cm = get('configmap', 'probe', ns)
        need(subset(cm, current_cm) and current_cm['metadata']['uid'] == q.document(STATE/'CONFIG-ACK.json')['uid'],
             'probe_configmap_changed_before_submission')
        q.record(STATE/'PROVIDER-REQUESTED.json', {'maximum_submissions': 1, 'pod_uid': uid, 'automatic_retry': False})
        report['provider_submission_attempted'] = True
        # No retry even if the exec transport fails before its acknowledgement.
        call(['exec', '-i', '-n', ns, 'native-glm', '-c', 'probe', '--',
              'python3', '-I', '-B', '/probe/runner.py', 'run'],
             json.dumps({'api_key': token}).encode(), timeout=230, limit=16384)
        del token
        state = remote_status(ns)
        q.record(STATE/'NATIVE-RECEIPT.json', state)
        result = state.get('RESULT.json', {})
        report['native'] = result; emit({'native_glm_receipt': result})
        need(accepted(result), 'native_glm_probe_not_passed')
        phase('POST_REQUEST_PRODUCTION_BASELINE')
        guard.live(plan, binary=True)
        report.update(native_glm_acceptance='PASS', production_baseline='UNCHANGED')
        # Delete only our disposable namespace, with the server-enforced UID.
        nsuid = q.document(STATE/'NAMESPACE-ACK.json')['uid']
        q.record(STATE/'CLEANUP-REQUESTED.json', {'namespace': ns, 'uid': nsuid})
        delete = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': nsuid}, 'propagationPolicy': 'Foreground'}
        call(['delete', '--raw', '/api/v1/namespaces/'+ns, '-f', '-'], json.dumps(delete).encode())
        report['cleanup'] = 'REQUESTED_WITH_UID_PRECONDITION'
    except BaseException as exc:
        report.update(native_glm_acceptance='UNCONFIRMED', failed_stage=PHASE, error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, AcceptanceFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            last_operation=dict(LAST), raw_error_printed=False,
            code_frames=[{'file': Path(x.filename).name, 'function': x.name, 'line': x.lineno}
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
        # Retain failed Pod evidence; its 900s active deadline bounds execution.
    q.record(STATE/'RESULT.json', report)
    emit(dict(report, report=str(STATE/'RESULT.json'), report_sha256=q.digest(STATE/'RESULT.json')))
    return 0 if report['native_glm_acceptance'] == 'PASS' else 1


def status(q):
    value = dict(common(), status='READ_ONLY', files_modified=False, provider_requests=0)
    if not STATE.exists(): emit(dict(value, operation_exists=False)); return
    start = q.document(STATE/'STARTED.json'); ns = start['namespace']
    need(re.fullmatch(r'weknora-glm-check-[a-f0-9]{16}', ns) is not None, 'invalid_saved_namespace')
    value['submission_requested'] = (STATE/'PROVIDER-REQUESTED.json').exists()
    if (STATE/'RESULT.json').exists(): value['result'] = q.document(STATE/'RESULT.json')
    current = get('pod', 'native-glm', ns, missing=True)
    if current:
        need((STATE/'POD-ACK.json').exists(), 'unacknowledged_pod_requires_review')
        validate_pod(current, q.document(STATE/'POD-EXPECTED.json'), q.document(STATE/'POD-ACK.json')['uid'])
        value['pod_phase'] = current.get('status', {}).get('phase')
        if value['pod_phase'] == 'Running': value['remote'] = remote_status(ns)
        elif value['pod_phase'] in ('Failed', 'Succeeded'): value['terminal_diagnostics'] = terminal_diagnostics(ns)
    else: value['pod_present'] = False
    emit(value)


def interrupt(signum, frame):
    raise AcceptanceFailure('interrupted_USE_STATUS_NO_REPEAT')


def main():
    parser = argparse.ArgumentParser(); modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--probe-once', action='store_true'); modes.add_argument('--status', action='store_true')
    args = parser.parse_args()
    need(os.name == 'posix' and os.geteuid() == 0 and platform.machine() == 'aarch64'
         and socket.gethostname().split('.')[0] == 'datax-01', 'root_arm64_datax01_required')
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP): signal.signal(sig, interrupt)
    p, q = initialize()
    if args.status: status(q); return 0
    return run(p, q)


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        emit(dict(common(), native_glm_acceptance='UNCONFIRMED', failed_stage=PHASE,
                  failed_check=str(exc) if isinstance(exc, AcceptanceFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                  error_type=type(exc).__name__, raw_error_printed=False))
        sys.exit(1)
