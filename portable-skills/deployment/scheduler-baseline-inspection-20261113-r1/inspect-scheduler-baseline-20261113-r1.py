"""One read-only current Gateway snapshot; accepts new generation/spec/UID."""
import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import signal
import subprocess
import sys
import tempfile
import time

CHILD_ASSET = {}
OLD_UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
NS = 'deer-flow'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', NS]
LIST = ['get', 'pods', '-l', 'app=gateway', '--no-headers', '-o',
        'custom-columns=NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp']
CONFIG = '/app/backend/config.yaml'
FILES = (
 '/app/backend/packages/harness/deerflow/config/app_config.py', '/app/backend/packages/harness/deerflow/config/scheduler_config.py',
 '/app/backend/app/gateway/services.py', '/app/backend/packages/harness/deerflow/agents/lead_agent/agent.py',
 '/app/backend/app/channels/manager.py', '/app/backend/app/integrations/work/notifications.py',
 '/app/backend/app/scheduler/work_notification_delivery.py', '/app/backend/app/scheduler/work_notifications.py',
 '/app/backend/packages/harness/deerflow/tools/builtins/present_file_tool.py',
 '/app/backend/packages/harness/deerflow/utils/conversation_url.py', '/app/backend/app/gateway/routers/uploads.py',
 '/app/backend/app/gateway/upload_document_memory_guard.py', '/etc/deerflow-openssl/openssl.cnf')


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_uid(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value)


def identity(deployment):
    meta = deployment['metadata']
    need(meta.get('name') == 'gateway' and meta.get('namespace') == NS and valid_uid(meta.get('uid'))
         and not meta.get('deletionTimestamp'), 'CURRENT_GATEWAY_IDENTITY_REQUIRED')
    return {'uid': meta['uid'], 'generation': meta['generation'], 'spec_sha256': sha(canonical(deployment['spec']))}


def deployment_summary(obj):
    result = identity(obj)
    spec, status = obj['spec'], obj.get('status', {})
    result.update(matches_old_uid=result['uid'] == OLD_UID, resource_version=obj['metadata']['resourceVersion'],
        images={row['name']: row['image'] for row in spec['template']['spec'].get('containers', [])},
        desired=spec.get('replicas', 1), paused=spec.get('paused', False), strategy=spec.get('strategy'),
        observed_generation=status.get('observedGeneration'), total=status.get('replicas', 0),
        updated=status.get('updatedReplicas', 0), ready=status.get('readyReplicas', 0),
        available=status.get('availableReplicas', 0), terminating=status.get('terminatingReplicas', 0))
    return result


def converged(obj):
    row = deployment_summary(obj)
    return (not row['paused'] and row['observed_generation'] == row['generation'] and row['desired'] > 0
            and all(row[key] == row['desired'] for key in ('total', 'updated', 'ready', 'available')) and row['terminating'] == 0)


def parse_list(raw):
    need(isinstance(raw, str) and len(raw.encode()) <= 262144 and len(raw.splitlines()) <= 512, 'SPARSE_LIST_BOUND')
    rows, names, uids = [], set(), set()
    for line in raw.splitlines():
        fields = line.split()
        need(len(fields) == 4, 'SPARSE_LIST_ROW')
        name, uid, phase, deleting = fields
        need(re.fullmatch('[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?', name) and valid_uid(uid)
             and name not in names and uid not in uids, 'SPARSE_LIST_IDENTITY')
        need(phase in ('Pending', 'Running', 'Failed', 'Succeeded', 'Unknown') and (deleting == '<none>'
             or re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z', deleting)), 'SPARSE_LIST_STATE')
        names.add(name); uids.add(uid)
        if phase == 'Running' and deleting == '<none>':
            rows.append((name, uid))
    return sorted(rows)


def owner(obj, kind):
    rows = [row for row in obj['metadata'].get('ownerReferences', []) if row.get('kind') == kind and row.get('controller') is True]
    need(len(rows) == 1 and valid_uid(rows[0].get('uid')) and isinstance(rows[0].get('name'), str), 'CONTROLLER_OWNER_REQUIRED')
    return rows[0]


def pod_stamp(pod):
    states = [row for row in pod.get('status', {}).get('containerStatuses', []) if row.get('name') == 'gateway']
    need(not pod['metadata'].get('deletionTimestamp') and pod.get('status', {}).get('phase') == 'Running'
         and len(states) == 1 and states[0].get('ready') is True and states[0].get('state', {}).get('running'), 'OWNED_READY_GATEWAY_REQUIRED')
    row = states[0]
    return {'name': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'spec_sha256': sha(canonical(pod['spec'])),
        'container_id_sha256': sha(row['containerID'].encode()), 'image_id_sha256': sha(row['imageID'].encode()),
        'restart_count': row.get('restartCount'), 'running_since': row['state']['running'].get('startedAt')}


def mount_summary(spec):
    targets = [PurePosixPath(value) for value in (CONFIG,) + FILES + ('/etc/deerflow-ca', '/etc/deerflow-openssl')]
    volumes = {row['name']: row for row in spec.get('volumes', [])}
    rows, skip = [], set()
    for container in spec.get('containers', []):
        for mount in container.get('volumeMounts', []):
            base = PurePosixPath(mount['mountPath'])
            need(base.is_absolute() and '..' not in base.parts, 'MOUNT_PATH_INVALID')
            if not any(base == path or base in path.parents or path in base.parents for path in targets):
                continue
            volume = volumes.get(mount['name'], {})
            secret = 'secret' in volume or any('secret' in item for item in volume.get('projected', {}).get('sources', []))
            kind = next((public for key, public in (('configMap', 'ConfigMap'), ('secret', 'Secret'),
                ('persistentVolumeClaim', 'PersistentVolumeClaim'), ('emptyDir', 'EmptyDir'), ('projected', 'Projected'),
                ('hostPath', 'HostPath'), ('csi', 'CSI')) if key in volume), 'Unknown')
            row = {'container': container['name'], 'mount_path': str(base), 'source_type': 'Secret' if secret else kind,
                   'read_only': mount.get('readOnly', False)}
            if not secret:
                row.update(sub_path=mount.get('subPath'), sub_path_expr_present=bool(mount.get('subPathExpr')))
                if 'configMap' in volume:
                    row['configmap_name'] = volume['configMap'].get('name')
                    row['items'] = [{'key': item.get('key'), 'path': item.get('path')} for item in volume['configMap'].get('items', [])]
            else:
                if container['name'] == 'gateway':
                    skip.update(str(path) for path in targets if str(path) in (CONFIG,) + FILES and (path == base or base in path.parents))
            rows.append(row)
    return rows, sorted(skip)


class Inspector:
    def __init__(self, child):
        self.child, self.calls, self.pod, self.rs = child, 0, None, None
        self.out = {'schema': 'scheduler-current-baseline-inspection.v1', 'status': 'INCOMPLETE', 'readonly': True,
            'workload_modified': False, 'persistent_files_modified': False, 'secret_api_reads': 0, 'credentials_exported': False,
            'raw_configuration_exported': False, 'provider_requests': 0, 'explicit_database_queries': 0, 'work_messages_sent': 0,
            'inspection_scope': 'GATEWAY_DEPLOYMENT_METADATA_ONLY', 'all_gateway_pods_verified': False, 'business_acceptance': False}

    def call(self, args, body=None):
        fixed = [['get', 'deployment', 'gateway', '-o', 'json'], LIST]
        if self.pod:
            fixed.append(['get', 'pod', self.pod, '-o', 'json'])
        if self.rs:
            fixed.append(['get', 'replicaset', self.rs, '-o', 'json'])
        execute = ['exec', '-i', self.pod, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-c', self.child]
        need((args in fixed and body is None) or (self.pod and args == execute and isinstance(body, bytes) and len(body) <= 8192), 'READONLY_COMMAND_SCOPE')
        self.calls += 1
        need(self.calls <= 12, 'READONLY_QUERY_BOUND')
        limit = 262144 if args == LIST else (65536 if args[:1] == ['exec'] else 4194304)
        kind = 'SPARSE_POD_LIST' if args == LIST else ('FIXED_RUNTIME_EXEC' if args[:1] == ['exec'] else 'GET_' + args[1].upper())
        self.out['last_command_kind'] = kind
        with tempfile.TemporaryFile() as incoming, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            if body is not None:
                incoming.write(body); incoming.seek(0)
            process = subprocess.Popen(KUBE + args, stdin=incoming if body is not None else subprocess.DEVNULL,
                stdout=output, stderr=errors, env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/root', 'LANG': 'C.UTF-8'}, start_new_session=True)
            until = time.monotonic() + 35
            try:
                while True:
                    for stream, observed, cap in (('stdout', os.fstat(output.fileno()).st_size, limit), ('stderr', os.fstat(errors.fileno()).st_size, 131072)):
                        if observed > cap:
                            self.out['output_bound'] = {'command_kind': kind, 'stream': stream, 'limit': cap, 'observed_bytes': observed}
                            raise Refused('COMMAND_OUTPUT_BOUND')
                    if process.poll() is not None:
                        break
                    need(time.monotonic() < until, 'READONLY_COMMAND_TIMEOUT'); time.sleep(.05)
                need(process.returncode in ((0, 1) if args[:1] == ['exec'] else (0,)), 'READONLY_COMMAND_FAILED')
                output.seek(0); raw = output.read(limit + 1)
                return raw.decode('utf-8') if args == LIST else json.loads(raw)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)

    def run(self):
        before = self.call(['get', 'deployment', 'gateway', '-o', 'json'])
        self.out['deployment'] = deployment_summary(before)
        self.out['deployment_mounts'], _ = mount_summary(before['spec']['template']['spec'])
        if not converged(before):
            self.out.update(status='GATEWAY_ROLLOUT_METADATA_OBSERVED_RUNTIME_PENDING', rollout_in_progress=True, runtime_pending=True)
        else:
            candidates = parse_list(self.call(LIST))
            need(candidates, 'NO_RUNNING_GATEWAY_SAMPLE')
            self.pod, selected_uid = candidates[0]
            pod = self.call(['get', 'pod', self.pod, '-o', 'json'])
            need(pod['metadata'].get('namespace') == NS and pod['metadata']['uid'] == selected_uid, 'LISTED_POD_CHANGED')
            stamp = pod_stamp(pod)
            ref = owner(pod, 'ReplicaSet'); self.rs = ref['name']
            rs = self.call(['get', 'replicaset', self.rs, '-o', 'json'])
            ref2 = owner(rs, 'Deployment')
            need(rs['metadata']['uid'] == ref['uid'] and ref2['name'] == 'gateway' and ref2['uid'] == before['metadata']['uid'], 'CURRENT_DEPLOYMENT_OWNER_CLOSURE')
            template = json.loads(json.dumps(rs['spec']['template']))
            template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
            current = template == before['spec']['template']
            self.out.update(sample=stamp, sample_template_matches_current=current)
            self.out['sample_mounts'], skip = mount_summary(pod['spec'])
            if current:
                self.out['inspection_scope'] = 'ONE_READY_OWNED_GATEWAY_POD'
                self.out['runtime'] = self.call(['exec', '-i', self.pod, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-c', self.child], canonical({'skip_secret_paths': skip}))
                self.out['status'] = 'CURRENT_GATEWAY_BASELINE_OBSERVED_NO_CHANGE'
                if self.out['runtime'].get('status') == 'INCOMPLETE':
                    self.out.update(status='CURRENT_GATEWAY_METADATA_OBSERVED_RUNTIME_INCOMPLETE', runtime_pending=True)
            else:
                self.out.update(status='GATEWAY_SAMPLE_OLD_TEMPLATE_RUNTIME_PENDING', runtime_pending=True)
            fresh_pod = self.call(['get', 'pod', self.pod, '-o', 'json'])
            fresh_rs = self.call(['get', 'replicaset', self.rs, '-o', 'json'])
            need(pod_stamp(fresh_pod) == stamp and owner(fresh_pod, 'ReplicaSet') == ref
                 and fresh_rs['metadata']['uid'] == rs['metadata']['uid'] and fresh_rs['spec'] == rs['spec']
                 and owner(fresh_rs, 'Deployment') == ref2, 'SAMPLE_CHANGED_DURING_INSPECTION')
        after = self.call(['get', 'deployment', 'gateway', '-o', 'json'])
        need(identity(after) == identity(before), 'GATEWAY_CHANGED_DURING_INSPECTION')
        self.out['deployment_after'] = deployment_summary(after)
        self.out['stable_readback'] = True


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--inspect-readonly', required=True, action='store_true'); parser.parse_args()
    inspection = Inspector('')
    try:
        need(sys.version_info >= (3, 7), 'PYTHON_37_REQUIRED')
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        need(set(CHILD_ASSET) == {'base64', 'sha256'}, 'CHILD_NOT_SEALED')
        raw = base64.b64decode(CHILD_ASSET['base64'], validate=True)
        need(0 < len(raw) <= 32768 and sha(raw) == CHILD_ASSET['sha256'], 'CHILD_ASSET_CHANGED')
        inspection.child = raw.decode('utf-8'); inspection.run()
    except Exception as exc:
        inspection.out.update(status='INCOMPLETE', error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'LOCAL_FAILURE_REDACTED')
    inspection.out.update(queries=inspection.calls, observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    print(json.dumps(inspection.out, sort_keys=True)); return int(inspection.out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
