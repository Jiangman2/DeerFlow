"""Qualify one cached ARM64 browser and fixed report as UID 1000; no publication.

Python 3.7+ operator. Creates and deletes only one isolated, UID-bound Pod.
Gateway rolling state does not block this test. No credentials or model calls.
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
CONTROL = Path('/var/lib/portal-energy-browser-nonroot-qualification-20261112-r1')
NODE = 'ecs-zdz-pro-0015'
NODE_UID = 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'
IMAGE = 'deerflow.local/deer-flow-aio-sandbox:20260921-enterprise-arm64-rc15-document-runtime1'
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
LABEL = 'portal.energy-browser/operation'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', 'deer-flow']


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def private_write(path, raw):
    for part in (path.parent,) + tuple(path.parent.parents):
        need(not part.is_symlink(), 'PRIVATE_PATH_LINK')
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def sealed_assets():
    need(set(ASSETS) == {'init', 'child', 'package', 'test_files'}, 'ASSETS_NOT_SEALED')
    result = {}
    for name, item in ASSETS.items():
        need(set(item) == {'base64', 'sha256'} and len(item['base64']) <= 8388608, 'ASSET_SHAPE')
        raw = base64.b64decode(item['base64'], validate=True)
        need(sha(raw) == item['sha256'], 'ASSET_CHANGED')
        result[name] = raw
    for name in ('init', 'child'):
        compile(result[name], '<fixed-' + name + '>', 'exec')
    return result


def manifest(operation, name, assets):
    security = {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
    return {'apiVersion': 'v1', 'kind': 'Pod',
            'metadata': {'name': name, 'namespace': 'deer-flow', 'labels': {'app': 'energy-browser-nonroot-qualification', LABEL: operation}},
            'spec': {'nodeName': NODE, 'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'enableServiceLinks': False,
                     'activeDeadlineSeconds': 600, 'terminationGracePeriodSeconds': 5, 'securityContext': {'fsGroup': 1000},
                     'initContainers': [{'name': 'copy-image-browser', 'image': IMAGE, 'imagePullPolicy': 'Never',
                                         'command': ['python3', '-I', '-B', '-c', assets['init'].decode('utf-8')],
                                         'securityContext': dict(security, runAsUser=0, runAsGroup=0),
                                         'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}},
                                         'volumeMounts': [{'name': 'browser', 'mountPath': '/stage'}, {'name': 'tmp', 'mountPath': '/writable'}]}],
                     'containers': [{'name': 'qualification', 'image': IMAGE, 'imagePullPolicy': 'Never',
                                     'command': ['python3', '-I', '-B', '-c', 'import time;time.sleep(550)'],
                                     'securityContext': dict(security, runAsUser=1000, runAsGroup=1000, runAsNonRoot=True),
                                     'env': [{'name': 'HOME', 'value': '/tmp'}, {'name': 'PYTHONDONTWRITEBYTECODE', 'value': '1'}, {'name': 'PYTHONUTF8', 'value': '1'}],
                                     'resources': {'requests': {'cpu': '250m', 'memory': '768Mi'}, 'limits': {'cpu': '1', 'memory': '2Gi'}},
                                     'volumeMounts': [{'name': 'tmp', 'mountPath': '/tmp'},
                                                      {'name': 'browser', 'mountPath': '/tmp/energy-browser-qualification/skills/.energy-browser-runtime-20261112-r1/linux-arm64', 'readOnly': True},
                                                      {'name': 'shm', 'mountPath': '/dev/shm'}]}],
                     'volumes': [{'name': 'browser', 'emptyDir': {'sizeLimit': '600Mi'}}, {'name': 'tmp', 'emptyDir': {'sizeLimit': '1Gi'}},
                                 {'name': 'shm', 'emptyDir': {'medium': 'Memory', 'sizeLimit': '128Mi'}}]}}


def pod_guard(pod, requested, uid=None, inspect_status=True):
    meta, spec = pod['metadata'], pod['spec']
    need(meta.get('name') == requested['metadata']['name'] and meta.get('labels', {}).get(LABEL) == requested['metadata']['labels'][LABEL]
         and not meta.get('ownerReferences') and not meta.get('deletionTimestamp'), 'OWN_POD_IDENTITY')
    need(uid is None or meta.get('uid') == uid, 'OWN_POD_UID_CHANGED')
    need(not any(spec.get(key) for key in ('hostNetwork', 'hostPID', 'hostIPC', 'shareProcessNamespace', 'ephemeralContainers')), 'POD_UNEXPECTED_ACCESS')
    for key, value in requested['spec'].items():
        if key not in ('containers', 'initContainers'):
            need(spec.get(key) == value, 'POD_SPEC_CHANGED')
    for kind in ('containers', 'initContainers'):
        need(len(spec.get(kind, [])) == 1, 'POD_CONTAINER_SHAPE')
        actual, expected = spec[kind][0], requested['spec'][kind][0]
        need(not any(actual.get(key) for key in ('envFrom', 'lifecycle', 'ports', 'volumeDevices', 'livenessProbe', 'readinessProbe', 'startupProbe')), 'CONTAINER_UNEXPECTED_ACCESS')
        for key, value in expected.items():
            need(actual.get(key) == value, 'POD_CONTAINER_CHANGED')
    if not inspect_status:
        return False
    status = pod.get('status', {})
    initial = status.get('initContainerStatuses', [])
    states = status.get('containerStatuses', [])
    if len(initial) == 1 and initial[0].get('state', {}).get('terminated'):
        need(initial[0]['state']['terminated'].get('exitCode') == 0 and initial[0].get('imageID') == IMAGE_ID and initial[0].get('restartCount') == 0,
             'FIXED_IMAGE_BROWSER_COPY_FAILED')
    if status.get('phase') == 'Running' and len(states) == 1 and states[0].get('ready'):
        need(len(initial) == 1 and initial[0].get('imageID') == IMAGE_ID and states[0].get('imageID') == IMAGE_ID and states[0].get('restartCount') == 0,
             'CACHED_RUNTIME_IMAGE_CHANGED')
        return True
    need(status.get('phase') not in ('Failed', 'Succeeded'), 'QUALIFICATION_POD_EXITED')
    return False


class Operation:
    def __init__(self, assets):
        self.assets = assets
        self.operation = uuid.uuid4().hex
        self.name = 'energy-browser-qual-' + self.operation[:12]
        self.requested = manifest(self.operation, self.name, assets)
        self.pod_uid, self.claimed, self.created, self.commands = None, False, False, 0
        self.cleanup_mode = False
        self.started = time.monotonic()
        self.out = {'schema': 'energy-browser-nonroot-qualification.v1', 'status': 'INCOMPLETE', 'operation': self.operation,
                    'once_claimed': False, 'automatic_retry': False, 'automatic_rollback': False, 'existing_deployment_mutations': 0,
                    'secret_api_reads': 0, 'credentials_read': False, 'credentials_exported': False, 'provider_requests': 0,
                    'shared_skill_modified': False, 'work_messages_sent': 0, 'schedule_created': False,
                    'pod_create_requested': False, 'pod_delete_requested': False, 'visual_review_pending': True}

    def command(self, args, body=None, timeout=30, limit=6291456):
        need(self.commands < 140 and time.monotonic() - self.started < (700 if self.cleanup_mode else 520), 'HOST_BUDGET')
        get = args in (['get', 'node', NODE, '-o', 'json'], ['get', 'pod', self.name, '--ignore-not-found', '-o', 'json'])
        create = args == ['create', '-f', '-', '-o', 'json'] and body == canonical(self.requested)
        execute = args == ['exec', '-i', self.name, '-c', 'qualification', '--', 'python3', '-I', '-B', '-c', self.assets['child'].decode('utf-8')] and bool(self.pod_uid)
        delete = args == ['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + self.name, '-f', '-'] and bool(self.pod_uid) and body == canonical(
            {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': self.pod_uid}, 'gracePeriodSeconds': 5})
        need((get and body is None) or create or execute or delete, 'FIXED_OPERATION_REQUIRED')
        self.commands += 1
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors, tempfile.TemporaryFile() as incoming:
            if body is not None:
                need(len(body) <= 8388608, 'INPUT_BOUND')
                incoming.write(body); incoming.seek(0)
            prefix = list(KUBE)
            prefix[2] = '--request-timeout=%ds' % min(300, max(20, int(timeout) - 3))
            process = subprocess.Popen(prefix + args, stdin=incoming if body is not None else subprocess.DEVNULL, stdout=output, stderr=errors,
                                       env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}, start_new_session=True)
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 65536, 'COMMAND_OUTPUT_BOUND')
                    need(time.monotonic() < deadline, 'COMMAND_DEADLINE')
                    time.sleep(.1)
                need(process.returncode in ((0, 1) if execute else (0,)), 'COMMAND_FAILED_REDACTED')
                need(os.fstat(output.fileno()).st_size <= limit and os.fstat(errors.fileno()).st_size <= 65536, 'COMMAND_OUTPUT_BOUND')
                output.seek(0)
                raw = output.read(limit + 1)
                return json.loads(raw) if raw.strip() else None
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)

    def execute(self):
        self.out['last_step'] = 'fixed_cached_node'
        node = self.command(['get', 'node', NODE, '-o', 'json'])
        need(node['metadata']['uid'] == NODE_UID and not node['spec'].get('unschedulable')
             and any(row.get('type') == 'Ready' and row.get('status') == 'True' for row in node.get('status', {}).get('conditions', [])), 'FIXED_NODE_NOT_READY')
        need(self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json']) is None, 'OWN_POD_NAME_EXISTS')
        need(not CONTROL.exists() and not CONTROL.is_symlink(), 'ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
        for parent in CONTROL.parents:
            need(not parent.is_symlink(), 'PRIVATE_PATH_LINK')
        CONTROL.mkdir(mode=0o700)
        self.claimed = True
        self.out['once_claimed'] = True
        self.out['last_step'] = 'copy_image_browser_and_start_nonroot_qualification'
        self.out['pod_create_requested'] = True
        self.created = True
        pod = self.command(['create', '-f', '-', '-o', 'json'], canonical(self.requested))
        pod_guard(pod, self.requested)
        self.pod_uid = pod['metadata']['uid']
        self.out.update(maintenance_pod=self.name, maintenance_pod_uid=self.pod_uid)
        print(json.dumps({'phase': 'WAIT_ISOLATED_NONROOT_BROWSER_RUNTIME'}), flush=True)
        until = time.monotonic() + 160
        while True:
            pod = self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json'])
            need(pod is not None, 'OWN_POD_DISAPPEARED')
            if pod_guard(pod, self.requested, self.pod_uid):
                break
            need(time.monotonic() < until, 'CACHED_RUNTIME_NOT_READY')
            time.sleep(2)
        self.out['last_step'] = 'nonroot_offline_regressions_and_full_fixed_report'
        print(json.dumps({'phase': 'NONROOT_REGRESSIONS_AND_FIXED_REPORT_PNG', 'uid': 1000, 'provider_requests': 0}), flush=True)
        payload = {'package': base64.b64encode(self.assets['package']).decode('ascii'), 'package_sha256': sha(self.assets['package']),
                   'test_files': json.loads(self.assets['test_files'])}
        observation = self.command(['exec', '-i', self.name, '-c', 'qualification', '--', 'python3', '-I', '-B', '-c', self.assets['child'].decode('utf-8')],
                                   canonical(payload), timeout=270)
        encoded = observation.pop('sample_base64', None)
        self.out['observation'] = observation
        need(observation.get('status') == 'NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED' and observation.get('uid') == 1000
             and observation.get('gid') == 1000 and observation.get('root_private_browser_accessible') is False
             and observation.get('provider_requests') == 0 and observation.get('credentials_read') is False, 'NONROOT_RENDER_NOT_QUALIFIED')
        need(isinstance(encoded, str) and len(encoded) <= 4194304, 'SAMPLE_BOUND')
        image = base64.b64decode(encoded, validate=True)
        need(len(image) == observation['render']['bytes'] and sha(image) == observation['render']['sha256'], 'SAMPLE_CHANGED')
        private_write(CONTROL / 'SAMPLE.png', image)
        private_write(CONTROL / 'RUNTIME.json', canonical({key: value for key, value in observation['runtime'].items() if key not in ('inventory_sha256', 'total_bytes')}) + b'\n')
        self.out.update(status='NONROOT_BROWSER_AND_REPORT_QUALIFIED_NOT_PUBLISHED', package_sha256=sha(self.assets['package']),
                        source_image_id=IMAGE_ID, sample_path=str(CONTROL / 'SAMPLE.png'), runtime_inventory_path=str(CONTROL / 'RUNTIME.json'))

    def cleanup(self):
        if not self.created:
            return
        self.cleanup_mode = True
        self.out['last_step'] = 'cleanup_only_owned_qualification_pod'
        pod = self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json'])
        if pod is None:
            self.out['maintenance_pod_absent'] = True
            return
        # Even an uncertain create can be cleaned only after exact identity guard.
        pod_guard(pod, self.requested, self.pod_uid, inspect_status=False)
        self.pod_uid = pod['metadata']['uid']
        self.out['pod_delete_requested'] = True
        self.command(['delete', '--raw', '/api/v1/namespaces/deer-flow/pods/' + self.name, '-f', '-'],
                     canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': self.pod_uid}, 'gracePeriodSeconds': 5}))
        until = time.monotonic() + 35
        while time.monotonic() < until:
            if self.command(['get', 'pod', self.name, '--ignore-not-found', '-o', 'json']) is None:
                self.out['maintenance_pod_absent'] = True
                return
            time.sleep(1)
        raise Refused('OWN_POD_DELETE_PENDING')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualify-nonroot-browser-once', required=True, action='store_true')
    parser.parse_args(argv)
    op = None
    result = {'schema': 'energy-browser-nonroot-qualification.v1', 'status': 'INCOMPLETE', 'once_claimed': False,
              'existing_deployment_mutations': 0, 'credentials_read': False, 'provider_requests': 0, 'commands': 0}
    try:
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        op = Operation(sealed_assets())
        result = op.out
        op.execute()
    except (Exception, KeyboardInterrupt) as exc:
        result.update(status='INCOMPLETE', error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'OPERATOR_INTERRUPTED' if isinstance(exc, KeyboardInterrupt) else 'LOCAL_FAILURE_REDACTED')
    finally:
        if op:
            try:
                op.cleanup()
            except (Exception, KeyboardInterrupt) as exc:
                result.update(status='INCOMPLETE', cleanup_error_class=type(exc).__name__, cleanup_incomplete=True)
            result.update(commands=op.commands, elapsed_seconds=round(time.monotonic() - op.started, 3))
        result['observed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if op and op.claimed:
            try:
                raw = canonical(result)
                private_write(CONTROL / 'REPORT.json', raw)
                result.update(report_path=str(CONTROL / 'REPORT.json'), report_bytes=len(raw), report_sha256=sha(raw))
            except Exception:
                result['receipt_write_failed'] = True
    print(json.dumps(result, sort_keys=True))
    return int(result['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
