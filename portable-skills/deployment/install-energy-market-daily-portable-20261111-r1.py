"""Replace one public Skill and configure credentials for newly created sandboxes."""
import argparse
import base64
import copy
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
from urllib.parse import urlsplit

ASSETS = {}
CONTROL = Path('/var/lib/portal-energy-portable-install-20261111-r1')
QUALIFICATION = Path('/var/lib/portal-energy-portable-qualification-20261111-r1/REPORT.json')
QUALIFICATION_SHA = 'eda21ffb64720e49aedfb40897897242b1b6d73d18a1419d890d1b06f0538c83'
OLD_TREE = '3ecfce5688c0db0d0b8b703ad6fa614a24b8c8fefc22b5725657c7f507503a96'
NEW_TREE = 'f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044'
PROVISIONER_UID = '9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253'
PROVISIONER_SOURCE = 'b5c733bc176da4aa37d34b809e007b4201ff2fff0a35e321eaa322c8652171bb'
BASELINES = {
    'gateway': ('a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 413, '696ffb5c27862a8e2e4f1d43c2482de79a3df991329524b214894f0bd29711ba'),
    'frontend': ('bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 243, '8b9c78dac10bfdd4cb656e813918dd0998c7351956d03cf1203efb0214e40aaf'),
    'provisioner': (PROVISIONER_UID, 86, '009352242b1f12bb361b0874aff9ecc6d72b9d4003b4c1e54988f786c6fe7dd2'),
    'energy-market-collector': ('e5f99800-6c58-4898-b22f-a8af9078b529', 4, '3581c5c52abb257940c98b05a90d9710dd8a9f79b9d94177adabaf1663a38780'),
}
IMAGE = 'deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
NODE = 'ecs-zdz-pro-0015'
NODE_UID = 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'
PVC = 'deer-flow-skills-rwx-pvc'
PVC_UID = '5bd3d4ed-54df-45c7-8954-d32ff8e53ec9'
PVC_SPEC = '118e6b4242677ff3ec750054f965f1bd76039f22a4ec650514cd64a4f0e8c909'
PROXY = 'http://10.68.211.1:8080'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', 'deer-flow']
GATEWAY_COLUMNS = 'NAME:.metadata.name,UID:.metadata.uid,NODE:.spec.nodeName,READY:.status.conditions[?(@.type=="Ready")].status,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,RSUID:.metadata.ownerReferences[0].uid'
LIST_GATEWAYS = ['get', 'pods', '-l', 'app=gateway', '--no-headers', '-o', 'custom-columns=' + GATEWAY_COLUMNS]
VERIFY_PROVISIONER = r'''
import hashlib,json,os,pathlib
raw=pathlib.Path('/app/app.py').read_bytes()
keys=[v.strip() for v in os.environ.get('SANDBOX_SECRET_ENV_KEYS','').split(',') if v.strip()]
passed=[v.strip() for v in os.environ.get('SANDBOX_PASSTHROUGH_ENV_KEYS','').split(',') if v.strip()]
print(json.dumps({'source_sha256':hashlib.sha256(raw).hexdigest(),'provider_secret_keys_present':all(k in keys for k in ('JKX_API_URL','JKX_API_PASSWORD')),'proxy_key_passed':'ENERGY_DAILY_HTTP_PROXY' in passed,'proxy_matches':os.environ.get('ENERGY_DAILY_HTTP_PROXY')=='http://10.68.211.1:8080','secret_name_matches':os.environ.get('SANDBOX_ENV_SECRET_NAME')=='deer-flow-env','credentials_exported':False}))
'''


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def read_private(path, maximum):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and not before.st_mode & 0o077 and before.st_size <= maximum, 'PRIVATE_FILE_IDENTITY_OR_PERMISSIONS')
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        need(len(raw) == before.st_size and (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns), 'PRIVATE_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def write_private(path, value):
    raw = canonical(value)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def key_list(value):
    need(isinstance(value, str) and len(value) <= 8192, 'ENV_LIST_INVALID')
    result = [v.strip() for v in value.split(',') if v.strip()]
    need(len(result) == len(set(result)) and all(re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}', v) for v in result), 'ENV_LIST_INVALID')
    return result


def candidate_spec(deployment):
    need(deployment['metadata']['uid'] == PROVISIONER_UID and deployment['metadata']['generation'] == 86 and sha(canonical(deployment['spec'])) == BASELINES['provisioner'][2], 'PROVISIONER_BASELINE_CHANGED')
    spec = copy.deepcopy(deployment['spec'])
    containers = spec['template']['spec']['containers']
    need(len(containers) == 1 and containers[0]['name'] == 'provisioner', 'ONE_PROVISIONER_CONTAINER_REQUIRED')
    env = containers[0].get('env', [])
    need(len({row['name'] for row in env}) == len(env), 'DUPLICATE_ENVIRONMENT')
    by_name = {row['name']: row for row in env}
    need(by_name.get('SANDBOX_ENV_SECRET_NAME') == {'name': 'SANDBOX_ENV_SECRET_NAME', 'value': 'deer-flow-env'}, 'SANDBOX_SECRET_BINDING_CHANGED')
    for setting, additions in (('SANDBOX_SECRET_ENV_KEYS', ('JKX_API_URL', 'JKX_API_PASSWORD')), ('SANDBOX_PASSTHROUGH_ENV_KEYS', ('ENERGY_DAILY_HTTP_PROXY',))):
        row = by_name.get(setting)
        need(isinstance(row, dict) and set(row) == {'name', 'value'}, 'EXPLICIT_ENV_LIST_REQUIRED')
        keys = key_list(row['value'])
        keys.extend(key for key in additions if key not in keys)
        row['value'] = ','.join(keys)
    if 'ENERGY_DAILY_HTTP_PROXY' in by_name:
        need(by_name['ENERGY_DAILY_HTTP_PROXY'] == {'name': 'ENERGY_DAILY_HTTP_PROXY', 'value': PROXY}, 'DEDICATED_PROXY_CONFLICT')
    else:
        env.append({'name': 'ENERGY_DAILY_HTTP_PROXY', 'value': PROXY})
    return spec


def secret_patch(secret, credential):
    need(secret['metadata']['name'] == 'deer-flow-env' and not secret['metadata'].get('deletionTimestamp') and not secret.get('immutable') and not secret['metadata'].get('ownerReferences'), 'SECRET_NOT_INDEPENDENT_MUTABLE')
    url = credential.get('JKX_API_URL') or credential.get('JKX_API_ENDPOINT')
    password = credential.get('JKX_API_PASSWORD')
    need(isinstance(url, str) and isinstance(password, str) and password, 'CREDENTIAL_FORMAT')
    parsed = urlsplit(url)
    need(parsed.scheme == 'http' and parsed.hostname == 'ip91.jkx.com.cn' and parsed.port == 6688 and parsed.path == '/jkx_eecc.aspx' and not parsed.username and not parsed.password and not parsed.fragment, 'PROVIDER_ENDPOINT_CHANGED')
    values = {'JKX_API_URL': parsed._replace(query='', fragment='').geturl(), 'JKX_API_PASSWORD': password}
    expected = copy.deepcopy(secret.get('data', {}))
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': secret['metadata']['uid']}, {'op': 'test', 'path': '/metadata/resourceVersion', 'value': secret['metadata']['resourceVersion']}]
    need('data' in secret and isinstance(secret['data'], dict), 'SECRET_DATA_REQUIRED')
    for key, value in values.items():
        encoded = base64.b64encode(value.encode()).decode()
        need(key not in expected or expected[key] == encoded, 'EXISTING_PROVIDER_KEY_CONFLICT')
        if key not in expected:
            patch.append({'op': 'add', 'path': '/data/' + key, 'value': encoded})
            expected[key] = encoded
    return patch, expected


def pod_manifest(operation, name):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': 'deer-flow', 'labels': {'app': 'energy-portable-publisher', 'portal.energy-portable/operation': operation}},
            'spec': {'nodeName': NODE, 'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'activeDeadlineSeconds': 1200, 'terminationGracePeriodSeconds': 5,
                     'containers': [{'name': 'publisher', 'image': IMAGE, 'imagePullPolicy': 'Never', 'command': ['python3', '-I', '-B', '-c', 'import time;time.sleep(1100)'],
                                     'env': [{'name': 'HOME', 'value': '/tmp'}, {'name': 'PYTHONUTF8', 'value': '1'}],
                                     'resources': {'requests': {'cpu': '100m', 'memory': '256Mi'}, 'limits': {'cpu': '1', 'memory': '768Mi'}},
                                     'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                                     'volumeMounts': [{'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'skills', 'mountPath': '/mnt/skills'}]}],
                     'volumes': [{'name': 'tmp', 'emptyDir': {'sizeLimit': '128Mi'}}, {'name': 'skills', 'persistentVolumeClaim': {'claimName': PVC}}]}}


class Release:
    def __init__(self):
        self.operation = uuid.uuid4().hex
        self.name = 'energy-portable-pub-' + self.operation[:12]
        self.pod_uid = None
        self.gateway_pods = set()
        self.provisioner_pods = set()
        self.provisioner_rs = set()
        self.gateway_pod = None
        self.expected = None
        self.queries = 0
        self.claimed = False
        self.step = 'preflight'
        self.assets = {}
        self.permit = None
        self.out = {'schema': 'energy-portable-install.v1', 'status': 'INCOMPLETE', 'operation': self.operation, 'once_claimed': False,
                    'automatic_retry': False, 'automatic_rollback': False, 'credentials_exported': False, 'provider_requests': 0,
                    'work_messages_sent': 0, 'schedule_created': False, 'gateway_modified': False, 'collector_modified': False,
                    'skill_switch_requested': False, 'secret_patch_requests': 0, 'provisioner_patch_requests': 0,
                    'new_sandboxes_only_for_credential_injection': True, 'existing_sandboxes_modified': False,
                    'browser_business_acceptance_pending': True, 'gateway_link_activation_pending': True}

    def command(self, args, body=None, timeout=30, limit=4194304):
        reads = [['get', 'deployments'] + list(BASELINES) + ['-o', 'json'], ['get', 'node', NODE, '-o', 'json'],
                 ['get', 'pvc', PVC, '-o', 'json'], ['get', 'secret', 'deer-flow-env', '-o', 'json'],
                 ['get', 'pods', '-l', 'app=provisioner', '-o', 'json'], LIST_GATEWAYS,
                 ['get', 'pod', self.name, '--ignore-not-found', '-o', 'json']]
        pods = self.gateway_pods | self.provisioner_pods
        read_pod = len(args) == 5 and args[:2] == ['get', 'pod'] and args[2] in pods and args[3:] == ['-o', 'json']
        read_rs = len(args) == 5 and args[:2] == ['get', 'replicaset'] and args[2] in self.provisioner_rs and args[3:] == ['-o', 'json']
        execute = False
        if len(args) == 10 and args[0] == 'exec' and args[2:5] in (['-c', 'gateway', '--'], ['-c', 'provisioner', '--']) and args[5:9] == ['python3', '-I', '-B', '-c']:
            execute = args[1] in self.gateway_pods and args[2:5] == ['-c', 'gateway', '--'] and args[9] == self.assets.get('idle_code') or args[1] in self.provisioner_pods and args[2:5] == ['-c', 'provisioner', '--'] and args[9] == VERIFY_PROVISIONER
        if args == ['exec', '-i', self.name, '-c', 'publisher', '--', 'python3', '-I', '-B', '-c', self.assets.get('publisher', b'').decode()]:
            execute = bool(self.pod_uid) and isinstance(body, bytes) and len(body) < 262144
        permitted = self.permit == (tuple(args), sha(body or b''))
        need(args in reads or read_pod or read_rs or execute or permitted, 'FIXED_COMMAND_REQUIRED')
        self.permit = None
        need(self.queries < 240, 'COMMAND_BOUND')
        self.queries += 1
        with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            if body is not None:
                incoming.write(body)
                incoming.seek(0)
            prefix = list(KUBE)
            if execute:
                prefix[2] = '--request-timeout=' + str(timeout + 5) + 's'
            process = subprocess.Popen(prefix + args, stdin=incoming if body is not None else subprocess.DEVNULL, stdout=output, stderr=errors,
                                       env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}, start_new_session=True)
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 65536, 'OUTPUT_BOUND')
                    need(time.monotonic() < deadline, 'COMMAND_DEADLINE')
                    time.sleep(.05)
                need(process.returncode in ((0, 1) if execute else (0,)) and os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 65536, 'COMMAND_FAILED_REDACTED')
                output.seek(0)
                raw = output.read(limit + 1)
                return raw.decode().strip() if args == LIST_GATEWAYS or args[-2:] == ['-o', 'name'] else json.loads(raw) if raw.strip() else None
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def mutation(self, args, value, purpose):
        permitted = purpose == 'create_publisher' and args == ['create', '-f', '-', '-o', 'json'] and value == pod_manifest(self.operation, self.name)
        if purpose == 'secret':
            permitted = args == ['patch', 'secret', 'deer-flow-env', '--type=json', '--patch-file=/dev/stdin', '-o', 'name'] and value == self.secret_request
        if purpose == 'provisioner':
            permitted = args == ['patch', 'deployment', 'provisioner', '--type=json', '--patch-file=/dev/stdin', '-o', 'name'] and value == self.provisioner_request
        if purpose == 'delete_publisher':
            permitted = args == ['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + self.name, '-f', '-'] and value == {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': self.pod_uid}, 'gracePeriodSeconds': 5}
        need(permitted, 'MUTATION_SCOPE_REFUSED')
        body = canonical(value)
        self.permit = (tuple(args), sha(body))
        # Patch responses are names only, never Secret values.
        result = self.command(args, body)
        if purpose in ('secret', 'provisioner'):
            need(result == {'secret': 'secret/deer-flow-env', 'provisioner': 'deployment.apps/provisioner'}[purpose], 'PATCH_ACKNOWLEDGEMENT_REQUIRED')
        return result

    def load(self):
        need(set(ASSETS) == {'publisher', 'package', 'source_inspector', 'idle_guard', 'visual_receipt'}, 'ASSETS_NOT_SEALED')
        for key, value in ASSETS.items():
            raw = base64.b64decode(value['base64'], validate=True)
            need(sha(raw) == value['sha256'], 'ASSET_CHANGED')
            self.assets[key] = raw
        visual = json.loads(self.assets['visual_receipt'])
        need(visual.get('actual_image_viewed') is True and visual.get('png_sha256') == 'bedd5af036641c695510cca936b1e530ed7600238a8892fe88b5d7d5953bdd9b', 'ACTUAL_ARM64_VISUAL_REVIEW_REQUIRED')
        raw = read_private(QUALIFICATION, 65536)
        need(sha(raw) == QUALIFICATION_SHA, 'QUALIFICATION_RECEIPT_CHANGED')
        qualification = json.loads(raw)
        need(qualification['status'] == 'PORTABLE_RUNTIME_AND_FRESH_DATA_QUALIFIED_NOT_DEPLOYED' and qualification['maintenance_pod_absent'] and qualification['package_sha256'] == sha(self.assets['package']), 'QUALIFICATION_REQUIRED')
        self.out['accepted_qualification'] = {'path': str(QUALIFICATION), 'sha256': QUALIFICATION_SHA, 'operation': qualification['operation']}
        self.assets['idle_code'] = "import sys;sys.path[:0]=['/app/backend','/app/backend/packages/harness'];sys.argv=['idle','--inspect-idle-and-origin'];\n" + self.assets['idle_guard'].decode()

    def baseline(self, changed=False, ready=True):
        items = {d['metadata']['name']: d for d in self.command(['get', 'deployments'] + list(BASELINES) + ['-o', 'json'])['items']}
        need(set(items) == set(BASELINES), 'DEPLOYMENTS_REQUIRED')
        for name, (uid, generation, digest) in BASELINES.items():
            d = items[name]
            candidate = changed and name == 'provisioner'
            need(d['metadata']['uid'] == uid and d['metadata']['generation'] == generation + int(candidate) and (d['spec'] == self.expected if candidate else sha(canonical(d['spec'])) == digest), 'BASELINE_CHANGED_' + name)
            if ready:
                desired = d['spec'].get('replicas', 1)
                s = d.get('status', {})
                need(not d['spec'].get('paused') and s.get('observedGeneration') == d['metadata']['generation'] and all(s.get(k, 0) == desired for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')) and not s.get('terminatingReplicas', 0), 'ROLLOUT_NOT_CONVERGED_' + name)
        return items

    def idle(self):
        value = self.command(['exec', self.gateway_pod, '-c', 'gateway', '--', 'python3', '-I', '-B', '-c', self.assets['idle_code']], timeout=55, limit=65536)
        need(value.get('status') == 'GATEWAY_IDLE_ORIGIN_OBSERVED' and value.get('read_only') and value.get('transaction_readonly_verified') and value.get('origin') == 'https://cnoocclaw.cnooc.com.cn', 'IDLE_QUERY_INCOMPLETE')
        need(value.get('idle') is True and all(type(value.get(k)) is int and value[k] == 0 for k in ('active_runs', 'active_oa', 'active_callbacks', 'scheduled_running', 'active_work_notifications')), 'ACTIVE_TASKS_PRESENT_NO_SWITCH')
        self.out['latest_idle_observation'] = value

    def worker(self, action):
        payload = {'action': action, 'operation': self.operation}
        if action == 'stage':
            payload['package'] = base64.b64encode(self.assets['package']).decode()
        value = self.command(['exec', '-i', self.name, '-c', 'publisher', '--', 'python3', '-I', '-B', '-c', self.assets['publisher'].decode()], canonical(payload), timeout=60, limit=65536)
        need(value.get('status') == {'stage': 'PORTABLE_SKILL_STAGED', 'publish': 'PORTABLE_SKILL_INSTALLED', 'verify': 'PORTABLE_SKILL_TREE_VERIFIED'}[action], 'SKILL_' + action.upper() + '_INCOMPLETE')
        return value

    def wait_publisher(self):
        until = time.monotonic() + 90
        while time.monotonic() < until:
            pod = self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json'])
            need(pod and pod['metadata']['uid'] == self.pod_uid and not pod['metadata'].get('deletionTimestamp'), 'PUBLISHER_REPLACED')
            states = pod.get('status', {}).get('containerStatuses', [])
            if len(states) == 1 and states[0].get('ready'):
                need(states[0]['imageID'] == IMAGE_ID and not states[0].get('restartCount'), 'CACHED_RUNTIME_CHANGED')
                return
            need(pod.get('status', {}).get('phase') not in ('Succeeded', 'Failed'), 'PUBLISHER_EXITED')
            time.sleep(2)
        raise Refused('PUBLISHER_NOT_READY')

    def execute(self):
        self.load()
        before = self.baseline()
        namespace = {'__name__': 'sealed_gateway_source_inspector'}
        exec(compile(self.assets['source_inspector'], '<sealed-source-inspector>', 'exec'), namespace)
        inspector = namespace['Inspector']()
        source = inspector.run()
        need(source['stable_readback'] and all(row.get('matches_git_parent') for row in source['runtime']['files'].values()), 'GATEWAY_PARENT_SOURCE_REQUIRED')
        self.gateway_pod = inspector.pod_name
        self.gateway_pods.add(self.gateway_pod)
        self.out['gateway_parent_source_verified'] = True
        self.idle()
        node = self.command(['get', 'node', NODE, '-o', 'json'])
        need(node['metadata']['uid'] == NODE_UID and not node['spec'].get('unschedulable') and any(r['type'] == 'Ready' and r['status'] == 'True' for r in node['status']['conditions']), 'FIXED_NODE_NOT_READY')
        pvc = self.command(['get', 'pvc', PVC, '-o', 'json'])
        need(pvc['metadata']['uid'] == PVC_UID and sha(canonical(pvc['spec'])) == PVC_SPEC and pvc['status']['phase'] == 'Bound', 'SKILLS_PVC_CHANGED')
        secret = self.command(['get', 'secret', 'deer-flow-env', '-o', 'json'])
        credential = json.loads(read_private('/home/cnooc/api-credential.private.json', 16384).decode('utf-8-sig'))
        self.secret_request, expected_secret = secret_patch(secret, credential)
        credential.clear()
        self.expected = candidate_spec(before['provisioner'])
        metadata = before['provisioner']['metadata']
        self.provisioner_request = [{'op': 'test', 'path': '/metadata/uid', 'value': PROVISIONER_UID}, {'op': 'test', 'path': '/metadata/resourceVersion', 'value': metadata['resourceVersion']}, {'op': 'test', 'path': '/spec', 'value': before['provisioner']['spec']}, {'op': 'replace', 'path': '/spec', 'value': self.expected}]
        need(not CONTROL.exists() and not CONTROL.is_symlink(), 'ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
        CONTROL.mkdir(mode=0o700)
        self.claimed = True
        self.out['once_claimed'] = True
        write_private(CONTROL / 'PROVISIONER-BEFORE.private.json', before['provisioner'])
        write_private(CONTROL / 'SECRET-ADDED-KEYS.private.json', {'uid': secret['metadata']['uid'], 'originally_present': [key for key in ('JKX_API_URL', 'JKX_API_PASSWORD') if key in secret['data']]})
        self.step = 'stage_candidate_and_backup'
        print(json.dumps({'phase': 'STAGE_PORTABLE_SKILL_AND_PRIVATE_BACKUP'}), flush=True)
        pod = self.mutation(['create', '-f', '-', '-o', 'json'], pod_manifest(self.operation, self.name), 'create_publisher')
        self.pod_uid = pod['metadata']['uid']
        self.out.update(maintenance_pod=self.name, maintenance_pod_uid=self.pod_uid)
        self.wait_publisher()
        self.out['staging'] = self.worker('stage')
        self.baseline()
        self.idle()
        self.step = 'inject_two_provider_secret_keys'
        if len(self.secret_request) > 2:
            self.out.update(secret_patch_requests=1, secret_patch_may_have_applied=True)
            write_private(CONTROL / 'BEFORE-SECRET-PATCH.json', self.out)
            self.mutation(['patch', 'secret', 'deer-flow-env', '--type=json', '--patch-file=/dev/stdin', '-o', 'name'], self.secret_request, 'secret')
        checked = self.command(['get', 'secret', 'deer-flow-env', '-o', 'json'])
        need(checked['metadata']['uid'] == secret['metadata']['uid'] and checked['data'] == expected_secret, 'SECRET_PATCH_READBACK_MISMATCH')
        self.out['unrelated_secret_data_preserved'] = True
        secret.clear()
        checked.clear()
        expected_secret.clear()
        self.secret_request = None
        self.step = 'roll_only_provisioner'
        print(json.dumps({'phase': 'CONFIGURE_NEW_SANDBOX_CREDENTIAL_REFERENCES'}), flush=True)
        self.out.update(provisioner_patch_requests=1, provisioner_patch_may_have_applied=True)
        write_private(CONTROL / 'BEFORE-PROVISIONER-PATCH.json', self.out)
        self.mutation(['patch', 'deployment', 'provisioner', '--type=json', '--patch-file=/dev/stdin', '-o', 'name'], self.provisioner_request, 'provisioner')
        until = time.monotonic() + 300
        while time.monotonic() < until:
            try:
                self.baseline(changed=True)
                break
            except Refused as exc:
                need(str(exc).startswith('ROLLOUT_NOT_CONVERGED_'), 'PROVISIONER_ROLLOUT_DRIFT')
                time.sleep(3)
        else:
            raise Refused('PROVISIONER_ROLLOUT_TIMEOUT')
        pods = self.command(['get', 'pods', '-l', 'app=provisioner', '-o', 'json'])['items']
        ready = [p for p in pods if p['status'].get('phase') == 'Running' and not p['metadata'].get('deletionTimestamp') and any(c.get('ready') for c in p['status'].get('containerStatuses', []))]
        need(len(ready) == 1, 'ONE_READY_PROVISIONER_REQUIRED')
        selected = ready[0]
        owners = [o for o in selected['metadata'].get('ownerReferences', []) if o.get('controller') and o.get('kind') == 'ReplicaSet']
        need(len(owners) == 1 and re.fullmatch('provisioner-[a-z0-9-]+', owners[0]['name']), 'PROVISIONER_OWNER_REQUIRED')
        self.provisioner_rs.add(owners[0]['name'])
        rs = self.command(['get', 'replicaset', owners[0]['name'], '-o', 'json'])
        need(rs['metadata']['uid'] == owners[0]['uid'] and any(o.get('controller') and o.get('kind') == 'Deployment' and o.get('uid') == PROVISIONER_UID for o in rs['metadata'].get('ownerReferences', [])), 'PROVISIONER_OWNER_CHAIN_CHANGED')
        template = copy.deepcopy(rs['spec']['template'])
        template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
        need(template == self.expected['template'], 'PROVISIONER_CURRENT_TEMPLATE_REQUIRED')
        self.provisioner_pods.add(selected['metadata']['name'])
        result = self.command(['exec', selected['metadata']['name'], '-c', 'provisioner', '--', 'python3', '-I', '-B', '-c', VERIFY_PROVISIONER], limit=65536)
        need(result['source_sha256'] == PROVISIONER_SOURCE and all(result[key] is True for key in ('provider_secret_keys_present', 'proxy_key_passed', 'proxy_matches', 'secret_name_matches')), 'PROVISIONER_CONFIGURATION_NOT_ACCEPTED')
        self.out['provisioner_after'] = {'generation': 87, 'spec_sha256': sha(canonical(self.expected)), 'source_sha256': PROVISIONER_SOURCE}
        self.baseline(changed=True)
        self.idle()
        self.step = 'replace_public_skill'
        self.out['skill_switch_requested'] = True
        write_private(CONTROL / 'BEFORE-SKILL-SWITCH.json', self.out)
        self.out['publication'] = self.worker('publish')
        self.worker('verify')
        self.out['skill_updated'] = True
        self.out['old_skill_removed_from_discovery'] = True
        self.baseline(changed=True)
        self.step = 'cleanup_own_publisher'
        self.out['pod_delete_requested'] = True
        self.mutation(['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + self.name, '-f', '-'], {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': self.pod_uid}, 'gracePeriodSeconds': 5}, 'delete_publisher')
        until = time.monotonic() + 35
        while time.monotonic() < until:
            if self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json']) is None:
                self.out['maintenance_pod_absent'] = True
                break
            time.sleep(1)
        need(self.out.get('maintenance_pod_absent'), 'OWN_POD_DELETE_PENDING')
        self.out.update(status='PORTABLE_SKILL_INSTALLED_GATEWAY_LINK_PENDING', package_sha256=sha(self.assets['package']), after_tree_sha256=NEW_TREE,
                        new_sandbox_business_acceptance_pending=True, consumer_views_pending_gateway_rollout=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-portable-daily-once', required=True, action='store_true')
    parser.parse_args(argv)
    release = Release()
    started = time.monotonic()
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        release.execute()
    except Refused as exc:
        release.out.update(error_class='Refused', error_code=str(exc))
    except Exception as exc:
        release.out.update(error_class=type(exc).__name__, error_code='LOCAL_FAILURE_REDACTED')
    release.out.update(last_step=release.step, commands=release.queries, elapsed_seconds=round(time.monotonic() - started, 3), observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if release.claimed:
        path = CONTROL / 'REPORT.json'
        write_private(path, release.out)
        raw = read_private(path, 262144)
        release.out.update(report_path=str(path), report_bytes=len(raw), report_sha256=sha(raw))
    print(json.dumps(release.out, sort_keys=True))
    return int(release.out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
