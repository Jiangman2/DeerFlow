"""Qualify the portable successor without changing any deployed workload."""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

ASSETS = {}
CONTROL = Path('/var/lib/portal-energy-portable-qualification-20261111-r1')
IMAGE = 'deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
NODE = 'ecs-zdz-pro-0015'
NODE_UID = 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'
BASELINES = {
    'gateway': ('a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 413, '696ffb5c27862a8e2e4f1d43c2482de79a3df991329524b214894f0bd29711ba'),
    'frontend': ('bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 243, '8b9c78dac10bfdd4cb656e813918dd0998c7351956d03cf1203efb0214e40aaf'),
    'provisioner': ('9d2480a2-b7d0-4f4e-8fbe-1ce0ac5d2253', 86, '009352242b1f12bb361b0874aff9ecc6d72b9d4003b4c1e54988f786c6fe7dd2'),
    'energy-market-collector': ('e5f99800-6c58-4898-b22f-a8af9078b529', 4, '3581c5c52abb257940c98b05a90d9710dd8a9f79b9d94177adabaf1663a38780'),
}
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', 'deer-flow']


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sealed_assets():
    need(set(ASSETS) == {'child', 'package', 'test_files'}, 'ASSETS_NOT_SEALED')
    result = {}
    for key, item in ASSETS.items():
        raw = base64.b64decode(item['base64'], validate=True)
        need(sha(raw) == item['sha256'], 'ASSET_CHANGED')
        result[key] = raw
    compile(result['child'], '<fixed-qualification>', 'exec')
    return result


def manifest(operation, name):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': 'deer-flow', 'labels': {'app': 'energy-portable-qualification', 'portal.energy-portable/operation': operation}},
            'spec': {'nodeName': NODE, 'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'activeDeadlineSeconds': 900, 'terminationGracePeriodSeconds': 5,
                     'containers': [{'name': 'qualification', 'image': IMAGE, 'imagePullPolicy': 'Never',
                                     'command': ['python3', '-I', '-B', '-c', 'import time;time.sleep(800)'],
                                     'env': [{'name': 'HOME', 'value': '/tmp'}, {'name': 'PYTHONUTF8', 'value': '1'}, {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}],
                                     'resources': {'requests': {'cpu': '250m', 'memory': '768Mi'}, 'limits': {'cpu': '1', 'memory': '2Gi'}},
                                     'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}},
                                     'volumeMounts': [{'name': 'tmp', 'mountPath': '/tmp'}, {'name': 'shm', 'mountPath': '/dev/shm'}]}],
                     'volumes': [{'name': 'tmp', 'emptyDir': {'sizeLimit': '1Gi'}}, {'name': 'shm', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Mi'}}]}}


class Operation:
    def __init__(self):
        self.operation = uuid.uuid4().hex
        self.name = 'energy-portable-qual-' + self.operation[:12]
        self.pod_uid = None
        self.claimed = False
        self.queries = 0
        self.step = 'preflight'
        self.out = {'schema': 'energy-portable-qualification.v1', 'status': 'INCOMPLETE', 'automatic_retry': False, 'automatic_rollback': False,
                    'once_claimed': False, 'existing_deployment_mutations': 0, 'secret_api_reads': 0, 'credentials_exported': False,
                    'skill_published': False, 'old_skill_removed': False, 'work_messages_sent': 0, 'schedule_created': False,
                    'operation': self.operation, 'visual_review_pending': True}

    def command(self, args, body=None, timeout=30, limit=2097152, binary=False):
        need(self.queries < 100, 'COMMAND_BOUND')
        # This tool has no Deployment/Secret/PVC mutation capability.
        allowed_read = args == ['get', 'deployments'] + list(BASELINES) + ['-o', 'json'] or args == ['get', 'node', NODE, '-o', 'json'] or args == ['get', 'pod', self.name, '--ignore-not-found', '-o', 'json']
        create = args == ['create', '-f', '-', '-o', 'json'] and body == canonical(manifest(self.operation, self.name))
        fixed_child = base64.b64decode(ASSETS.get('child', {}).get('base64', '')).decode()
        reader = "from pathlib import Path;import sys;sys.stdout.buffer.write(Path('/tmp/energy-portable-sample.png').read_bytes())"
        execute = args in [['exec', '-i', self.name, '-c', 'qualification', '--', 'python3', '-I', '-B', '-c', code] for code in (fixed_child, reader)] and bool(self.pod_uid)
        delete = args == ['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + self.name, '-f', '-'] and body == canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': self.pod_uid}, 'gracePeriodSeconds': 5})
        need(allowed_read or create or execute or delete, 'FIXED_OPERATION_REQUIRED')
        self.queries += 1
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as incoming:
            if body is not None:
                incoming.write(body)
                incoming.seek(0)
            command = list(KUBE)
            if execute:
                command[2] = '--request-timeout=300s'
            process = subprocess.Popen(command + args, stdin=incoming if body is not None else subprocess.DEVNULL, stdout=output, stderr=errors,
                                       env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}, start_new_session=True)
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 65536, 'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic() < deadline, 'COMMAND_DEADLINE')
                    time.sleep(.1)
                need(process.returncode in ((0, 1) if execute and not binary else (0,)), 'COMMAND_FAILED_REDACTED')
                need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 65536, 'COMMAND_OUTPUT_BOUND')
                output.seek(0)
                raw = output.read(limit + 1)
                return raw if binary else json.loads(raw) if raw.strip() else None
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def baseline(self):
        data = self.command(['get', 'deployments'] + list(BASELINES) + ['-o', 'json'])
        items = {item['metadata']['name']: item for item in data['items']}
        need(set(items) == set(BASELINES), 'DEPLOYMENT_SET_CHANGED')
        for name, (uid, generation, digest) in BASELINES.items():
            item = items[name]
            need((item['metadata']['uid'], item['metadata']['generation'], sha(canonical(item['spec']))) == (uid, generation, digest), 'BASELINE_CHANGED_' + name)
            state = item.get('status', {})
            desired = item['spec'].get('replicas', 1)
            need(not item['spec'].get('paused') and state.get('observedGeneration') == generation and all(state.get(key, 0) == desired for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')) and not state.get('terminatingReplicas', 0), 'ROLLOUT_NOT_READY_' + name)
        proxies = [row.get('value') for row in items['energy-market-collector']['spec']['template']['spec']['containers'][0].get('env', []) if row['name'] == 'ENERGY_MARKET_HTTP_PROXY']
        need(proxies == ['http://10.68.211.1:8080'], 'APPROVED_PROXY_CHANGED')
        return proxies[0]

    def credential(self):
        path = '/home/cnooc/api-credential.private.json'
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_mode & 0o077 == 0 and before.st_size <= 16384, 'PRIVATE_CREDENTIAL_FILE_PERMISSIONS')
            raw = os.read(fd, 16385)
            after = os.fstat(fd)
            need((before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns) and len(raw) == before.st_size, 'PRIVATE_FILE_CHANGED')
            data = json.loads(raw.decode('utf-8-sig'))
            need(isinstance(data.get('JKX_API_PASSWORD'), str) and bool(data['JKX_API_PASSWORD']) and isinstance(data.get('JKX_API_URL') or data.get('JKX_API_ENDPOINT'), str), 'PRIVATE_CREDENTIAL_FORMAT')
            return data
        finally:
            os.close(fd)

    def wait_ready(self):
        until = time.monotonic() + 80
        while time.monotonic() < until:
            pod = self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json'])
            need(pod is not None and pod['metadata']['uid'] == self.pod_uid and not pod['metadata'].get('deletionTimestamp'), 'POD_IDENTITY_CHANGED')
            states = pod.get('status', {}).get('containerStatuses', [])
            if len(states) == 1 and states[0].get('ready'):
                need(states[0]['imageID'] == IMAGE_ID and states[0].get('restartCount', 0) == 0, 'CACHED_IMAGE_ID_CHANGED')
                return
            need(pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded'), 'POD_EXITED')
            time.sleep(2)
        raise Refused('CACHED_RUNTIME_NOT_READY')

    def execute(self):
        assets = sealed_assets()
        proxy = self.baseline()
        node = self.command(['get', 'node', NODE, '-o', 'json'])
        need(node['metadata']['uid'] == NODE_UID and not node['spec'].get('unschedulable') and any(row['type'] == 'Ready' and row['status'] == 'True' for row in node['status']['conditions']), 'FIXED_NODE_NOT_READY')
        private = self.credential()
        need(not CONTROL.exists() and not CONTROL.is_symlink(), 'ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
        CONTROL.mkdir(mode=0o700)
        self.claimed = True
        self.out['once_claimed'] = True
        self.step = 'create_isolated_runtime'
        self.out['pod_create_requested'] = True
        pod = self.command(['create', '-f', '-', '-o', 'json'], canonical(manifest(self.operation, self.name)))
        self.pod_uid = pod['metadata']['uid']
        self.out.update(maintenance_pod=self.name, maintenance_pod_uid=self.pod_uid)
        self.wait_ready()
        print(json.dumps({'phase': 'ARM64_REGRESSIONS_RENDER_AND_ONE_FRESH_COLLECTION'}), flush=True)
        self.step = 'candidate_qualification'
        payload = {'package': base64.b64encode(assets['package']).decode(), 'package_sha256': sha(assets['package']),
                   'test_files': json.loads(assets['test_files']), 'credentials': private, 'proxy': proxy}
        observation = self.command(['exec', '-i', self.name, '-c', 'qualification', '--', 'python3', '-I', '-B', '-c', assets['child'].decode()], canonical(payload), timeout=290, limit=65536)
        payload.clear()
        private.clear()
        self.out['observation'] = observation
        need(observation.get('status') == 'PORTABLE_ARM64_RENDER_AND_FRESH_PROVIDER_ACCEPTED', 'CANDIDATE_QUALIFICATION_INCOMPLETE')
        self.step = 'save_synthetic_sample'
        reader = "from pathlib import Path;import sys;sys.stdout.buffer.write(Path('/tmp/energy-portable-sample.png').read_bytes())"
        image = self.command(['exec', '-i', self.name, '-c', 'qualification', '--', 'python3', '-I', '-B', '-c', reader], timeout=25, limit=3145728, binary=True)
        need(sha(image) == observation['render']['sha256'] and len(image) == observation['render']['bytes'], 'SAMPLE_CHANGED')
        path = CONTROL / 'SAMPLE.png'
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(image)
        self.out['sample_path'] = str(path)
        self.baseline()
        self.step = 'cleanup_own_pod'
        self.out['pod_delete_requested'] = True
        self.command(['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + self.name, '-f', '-'], canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': self.pod_uid}, 'gracePeriodSeconds': 5}))
        until = time.monotonic() + 35
        while time.monotonic() < until:
            if self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json']) is None:
                self.out['maintenance_pod_absent'] = True
                break
            time.sleep(1)
        need(self.out.get('maintenance_pod_absent'), 'OWN_POD_DELETE_PENDING')
        self.out.update(status='PORTABLE_RUNTIME_AND_FRESH_DATA_QUALIFIED_NOT_DEPLOYED', protected_deployments_unchanged=True,
                        package_sha256=sha(assets['package']), sample_visual_review_pending=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualify-portable-daily-once', required=True, action='store_true')
    parser.parse_args(argv)
    op = Operation()
    started = time.monotonic()
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        op.execute()
    except Refused as exc:
        op.out.update(error_code=str(exc), error_class='Refused')
    except Exception as exc:
        op.out.update(error_code='LOCAL_FAILURE_REDACTED', error_class=type(exc).__name__)
    op.out.update(last_step=op.step, commands=op.queries, elapsed_seconds=round(time.monotonic() - started, 3), observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    if op.claimed:
        raw = canonical(op.out)
        path = CONTROL / 'REPORT.json'
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        op.out.update(report_path=str(path), report_bytes=len(raw), report_sha256=sha(raw))
    print(json.dumps(op.out, sort_keys=True))
    return int(op.out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
