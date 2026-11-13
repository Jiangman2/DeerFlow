"""Read six fixed Gateway 413 source hashes; no application import or change.

Python 3.7-compatible operator tool. This is one Ready Pod's on-disk source
observation, not proof of loaded Python modules, fleet consistency or behavior.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import PurePosixPath

NS = 'deer-flow'
UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
GENERATION = 413
SPEC_SHA = '696ffb5c27862a8e2e4f1d43c2482de79a3df991329524b214894f0bd29711ba'
IMAGE = 'deerflow.local/deer-flow-gateway@sha256:c0fd1a6e981d551ab65459f8125b0e847ac7276433e020d1b65f88dea888ae29'
ROOT = '/app/backend'
FILES = ('app/channels/manager.py', 'app/scheduler/work_notification_delivery.py', 'packages/harness/deerflow/tools/builtins/present_file_tool.py', 'packages/harness/deerflow/utils/conversation_url.py', 'app/integrations/work/notifications.py', 'app/scheduler/work_notifications.py')
REFERENCES = {'app/channels/manager.py': {'parent': 'ec362f03f17ee491ad3a1d37e1dfa652195d168e4f1188ad3269f5db60fbd0f4', 'candidate': '1a782faf4e4cdbd491871972cca3e501d7a3011da3869fac398f994bedef56f1'}, 'app/scheduler/work_notification_delivery.py': {'parent': '1202321726b304b1c1b03e343f440f116b9734e416804cab894ea825e97bb79f', 'candidate': 'd446868a681754fa98241b62ebfe45a886c6bf21b0da0f68d0d69cd111e8a42e'}, 'packages/harness/deerflow/tools/builtins/present_file_tool.py': {'parent': '532af34c6e78a271f90b903cb328710424aec75d15dcc59d65bc01e10f0271ad', 'candidate': 'eccfe1e5975c771465abf96f1da60fdb2a11e0712716aeb2453a2d10fbe30e3b'}, 'packages/harness/deerflow/utils/conversation_url.py': {'parent': None, 'candidate': 'ef20ca84241617a489859fca4f660b07ba5923616828e0fad654b6c59d2f4abc'}, 'app/integrations/work/notifications.py': {'parent': '06a09ea242667b6769b273231827918883ac1128755086ad934951689a55079b', 'candidate': '2f25958bcb2556685d153bef55f72afebd360deb157cd13164ceb4261001f039'}, 'app/scheduler/work_notifications.py': {'parent': '83649f4a9ab99372beb81aa363e5da55bad89cbd59a50f397e4d5af1148eac32', 'candidate': 'd3f7e6c4acc4fd0cb7e4f6b31861ddf8b68ff1d2f44761a99da90536a6982645'}}
COLUMNS = 'NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,READY:.status.conditions[?(@.type=="Ready")].status,DELETING:.metadata.deletionTimestamp'
LIST_ARGS = ['get', 'pods', '-l', 'app=gateway', '--no-headers', '-o', 'custom-columns=' + COLUMNS]
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', NS]
CHILD_ERRORS = frozenset(('CODE_ROOT_LINK', 'CODE_PATH_LINK', 'CODE_PROJECTION_ESCAPE', 'FILE_BOUND', 'FILE_CHANGED', 'FILES_CHANGED', 'CHILD_TIME_BOUND', 'FIXED_SOURCE_INSPECTION_FAILED'))

CHILD = "\nimport hashlib,json,os,pathlib,signal,stat,sys\nROOT=pathlib.Path('/app/backend')\nFILES=('app/channels/manager.py', 'app/scheduler/work_notification_delivery.py', 'packages/harness/deerflow/tools/builtins/present_file_tool.py', 'packages/harness/deerflow/utils/conversation_url.py', 'app/integrations/work/notifications.py', 'app/scheduler/work_notifications.py')\ndef require(ok,code):\n if not ok:raise ValueError(code)\ndef no_links(path):return not any(p.is_symlink() for p in (path,*path.parents))\ndef source(root,name,projection):\n path=root/name\n require(no_links(root),'CODE_ROOT_LINK')\n if projection:\n  base=pathlib.Path(projection)\n  require(no_links(base) and (path==base or base in path.parents),'CODE_PROJECTION_ESCAPE')\n  if not path.exists() and not path.is_symlink():return {'present':False}\n  resolved=path.resolve(strict=True)\n  require(base in resolved.parents and no_links(resolved),'CODE_PROJECTION_ESCAPE')\n else:\n  require(no_links(path),'CODE_PATH_LINK')\n  if not path.exists():return {'present':False}\n  resolved=path\n fd=os.open(str(resolved),os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))\n try:\n  before=os.fstat(fd)\n  require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<before.st_size<=1048576,'FILE_BOUND')\n  with os.fdopen(fd,'rb',closefd=False) as stream:raw=stream.read(1048577)\n  after=os.fstat(fd)\n  stamp=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)\n  require(len(raw)==before.st_size and stamp(before)==stamp(after),'FILE_CHANGED')\n  require(path.resolve(strict=True)==resolved and stamp(os.stat(str(path)))==stamp(after),'FILE_CHANGED')\n  return {'present':True,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}\n finally:os.close(fd)\ndef inspect(root,projections):\n require(set(projections)==set(FILES),'CODE_PROJECTION_ESCAPE')\n first={name:source(root,name,projections[name]) for name in FILES}\n second={name:source(root,name,projections[name]) for name in FILES}\n require(first==second,'FILES_CHANGED')\n return {'status':'FIXED_SOURCE_HASHES_OBSERVED','files':second,'stable_readback':True,'code_root_present':root.is_dir(),\n  'isolated_python_sys_path_count':len(sys.path),'isolated_python_sys_path_sha256':hashlib.sha256(json.dumps(sys.path,separators=(',',':'),ensure_ascii=True).encode('ascii')).hexdigest(),\n  'application_imported':False,'source_text_exported':False,'environment_read':False,'files_modified':False,'provider_requests':0}\ndef main():\n out={'status':'INCOMPLETE','application_imported':False,'source_text_exported':False,'environment_read':False,'files_modified':False,'provider_requests':0}\n try:\n  def alarm(*args):raise ValueError('CHILD_TIME_BOUND')\n  signal.signal(signal.SIGALRM,alarm);signal.alarm(15)\n  out.update(inspect(ROOT,json.loads(sys.argv[1])))\n except Exception as exc:\n  allowed={'CODE_ROOT_LINK','CODE_PATH_LINK','CODE_PROJECTION_ESCAPE','FILE_BOUND','FILE_CHANGED','FILES_CHANGED','CHILD_TIME_BOUND'}\n  out['error_class']=type(exc).__name__;out['error_code']=str(exc) if type(exc) is ValueError and str(exc) in allowed else 'FIXED_SOURCE_INSPECTION_FAILED'\n finally:signal.alarm(0)\n print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')\nif __name__=='__main__':sys.exit(main())\n"


class Refused(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def valid_name(name):
    return isinstance(name, str) and len(name) <= 253 and re.fullmatch('[a-z0-9]([a-z0-9.-]*[a-z0-9])?', name) is not None


def identity(obj):
    meta = obj['metadata']
    require(valid_name(meta['name']) and re.fullmatch('[0-9a-f-]{36}', meta['uid']), 'OBJECT_IDENTITY')
    return dict(name=meta['name'], uid=meta['uid'], generation=meta.get('generation'), spec_sha256=sha(canonical(obj['spec'])))


def owner(obj, kind):
    owners = [r for r in obj['metadata'].get('ownerReferences', []) if r.get('controller') is True]
    require(len(owners) == 1 and owners[0].get('kind') == kind and valid_name(owners[0].get('name'))
            and isinstance(owners[0].get('uid'), str) and re.fullmatch('[0-9a-f-]{36}', owners[0]['uid']), 'OWNER_CHAIN')
    return owners[0]


def deployment_guard(obj):
    item = identity(obj)
    require(item == dict(name='gateway', uid=UID, generation=GENERATION, spec_sha256=SPEC_SHA), 'GATEWAY_BASELINE_CHANGED')
    spec, status = obj['spec'], obj.get('status', {})
    require(not obj['metadata'].get('deletionTimestamp') and not spec.get('paused', False)
            and spec.get('replicas') == 70 and status.get('observedGeneration') == GENERATION
            and all(status.get(k, 0) == 70 for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and status.get('terminatingReplicas', 0) == 0, 'GATEWAY_NOT_CONVERGED')
    require(spec.get('selector') == {'matchLabels': {'app': 'gateway'}}, 'GATEWAY_SELECTOR')
    containers = spec['template']['spec']['containers']
    require(len(containers) == 1 and containers[0].get('name') == 'gateway' and containers[0].get('image') == IMAGE, 'GATEWAY_IMAGE')
    return item


def parse_candidates(raw):
    require(isinstance(raw, str) and len(raw) <= 131072, 'POD_LIST_BOUND')
    rows, seen = [], set()
    lines = raw.strip().splitlines()
    require(0 < len(lines) <= 512, 'POD_LIST_COUNT')
    for line in lines:
        parts = line.split()
        require(len(parts) == 5, 'POD_LIST_SHAPE')
        name, uid, phase, ready, deleting = parts
        require(valid_name(name) and name not in seen and re.fullmatch('[0-9a-f-]{36}', uid), 'POD_LIST_IDENTITY')
        seen.add(name)
        require(phase in ('Pending', 'Running', 'Succeeded', 'Failed', 'Unknown') and ready in ('True', 'False', 'Unknown', '<none>'), 'POD_LIST_STATE')
        if phase == 'Running' and ready == 'True' and deleting == '<none>':
            rows.append((name, uid))
    require(rows, 'NO_READY_GATEWAY_POD')
    return sorted(rows)


def pod_guard(pod, rs, deployment):
    expected_owner = owner(pod, 'ReplicaSet')
    require(expected_owner['name'] == rs['metadata']['name'] and expected_owner['uid'] == rs['metadata']['uid'], 'POD_RS_MISMATCH')
    rs_owner = owner(rs, 'Deployment')
    require(rs_owner['name'] == 'gateway' and rs_owner['uid'] == UID and not rs['metadata'].get('deletionTimestamp'), 'RS_DEPLOYMENT_MISMATCH')
    template = json.loads(json.dumps(rs['spec']['template']))
    template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
    require(template == deployment['spec']['template'], 'RS_NOT_CURRENT_TEMPLATE')
    require(not pod['metadata'].get('deletionTimestamp') and pod.get('status', {}).get('phase') == 'Running'
            and any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod['status'].get('conditions', [])), 'POD_NOT_READY')
    containers = pod['spec'].get('containers', [])
    states = [c for c in pod['status'].get('containerStatuses', []) if c.get('name') == 'gateway']
    require(len(containers) == 1 and containers[0].get('name') == 'gateway' and containers[0].get('image') == IMAGE
            and len(states) == 1 and states[0].get('ready') is True, 'POD_CONTAINER')
    state = states[0]
    image_id = state.get('imageID', '')
    started = state.get('state', {}).get('running', {}).get('startedAt', '')
    require(isinstance(image_id, str) and re.search(r'sha256:[0-9a-f]{64}$', image_id)
            and isinstance(state.get('containerID'), str) and 0 < len(state['containerID']) < 512
            and type(state.get('restartCount')) is int and state['restartCount'] >= 0
            and isinstance(started, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z', started)
            and valid_name(pod['spec'].get('nodeName')), 'POD_RUNTIME_IDENTITY')
    return dict(identity(pod), node=pod['spec']['nodeName'], image_id_sha256=sha(image_id.encode()),
                container_id_sha256=sha(state['containerID'].encode()), restart_count=state['restartCount'], running_since=started)


def path_value(value, absolute=False):
    require(isinstance(value, str) and 0 < len(value) <= 512 and '\\' not in value and '\x00' not in value, 'MOUNT_PATH')
    path = PurePosixPath(value)
    require(path.is_absolute() == absolute and '..' not in path.parts and str(path) == value, 'MOUNT_PATH')
    return path


def mount_projection(pod):
    """Inspect only metadata of volumes covering the four source paths."""
    spec = pod['spec']
    volumes = {v['name']: v for v in spec.get('volumes', [])}
    require(len(volumes) == len(spec.get('volumes', [])), 'DUPLICATE_VOLUME')
    mounts = spec['containers'][0].get('volumeMounts', [])
    result, projections = {}, {}
    for name in FILES:
        target = PurePosixPath(ROOT) / name
        covering = []
        for mount in mounts:
            base = path_value(mount['mountPath'], absolute=True)
            if base == target or base in target.parents:
                covering.append((base, mount))
        # Overlapping mounts can obscure the physical source. Refuse ambiguity.
        require(len(covering) <= 1, 'OVERLAPPING_CODE_MOUNTS')
        projections[name] = None
        if not covering:
            result[name] = {'source': 'image_filesystem', 'path': str(target)}
            continue
        base, mount = covering[0]
        require(not mount.get('subPathExpr') and mount.get('name') in volumes, 'CODE_MOUNT_SHAPE')
        volume = volumes[mount['name']]
        require(set(volume).issubset({'name', 'configMap'}) and isinstance(volume.get('configMap'), dict), 'CODE_MOUNT_NOT_CONFIGMAP')
        cm = volume['configMap']
        require(valid_name(cm.get('name')), 'CODE_CONFIGMAP_NAME')
        relative = target.relative_to(base)
        subpath = mount.get('subPath')
        if subpath:
            relative = path_value(subpath) / relative
        relative_name = str(relative)
        keys = []
        if cm.get('items') is not None:
            require(isinstance(cm['items'], list) and len(cm['items']) <= 256, 'CODE_CONFIGMAP_ITEMS')
            for item in cm['items']:
                mapped = path_value(item['path'])
                if str(mapped) == relative_name:
                    key = item.get('key')
                    require(isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9._-]{1,253}', key), 'CODE_CONFIGMAP_KEY')
                    keys.append(key)
            require(len(keys) <= 1, 'CODE_CONFIGMAP_DUPLICATE_PATH')
        elif '/' not in relative_name and relative_name not in ('.', '..'):
            keys = [relative_name]
        if not subpath:
            require(base != target and base != PurePosixPath('/'), 'CODE_PROJECTION_ROOT')
            projections[name] = str(base)
        result[name] = dict(source='configmap_mount', path=str(target), volume=mount['name'], mount_path=str(base),
                            subpath=subpath, read_only=mount.get('readOnly', False), configmap_name=cm['name'],
                            mapped_keys=keys, configmap_api_values_read=False)
    return result, projections


def runtime_projection(raw):
    require(isinstance(raw, dict), 'CHILD_RESULT_SHAPE')
    result = {'status': 'INCOMPLETE'}
    if raw.get('status') == 'INCOMPLETE':
        if raw.get('error_code') in CHILD_ERRORS:
            result['error_code'] = raw['error_code']
        return result
    require(raw.get('status') == 'FIXED_SOURCE_HASHES_OBSERVED' and raw.get('stable_readback') is True
            and all(raw.get(k) is False for k in ('application_imported', 'source_text_exported', 'environment_read', 'files_modified'))
            and raw.get('provider_requests') == 0, 'CHILD_RESULT_FLAGS')
    require(isinstance(raw.get('files'), dict) and set(raw['files']) == set(FILES), 'CHILD_FILE_SET')
    rows = {}
    for name in FILES:
        row = raw['files'][name]
        require(isinstance(row, dict) and type(row.get('present')) is bool, 'CHILD_FILE_SHAPE')
        if row['present']:
            require(type(row.get('bytes')) is int and 0 < row['bytes'] <= 1048576 and isinstance(row.get('sha256'), str)
                    and re.fullmatch('[0-9a-f]{64}', row['sha256']), 'CHILD_FILE_HASH')
            rows[name] = {key: row[key] for key in ('present', 'bytes', 'sha256')}
        else:
            rows[name] = {'present': False}
        digest = row.get('sha256') if row['present'] else None
        rows[name].update(matches_git_parent=digest == REFERENCES[name]['parent'], matches_candidate=digest == REFERENCES[name]['candidate'])
    require(type(raw.get('code_root_present')) is bool and type(raw.get('isolated_python_sys_path_count')) is int
            and 0 <= raw['isolated_python_sys_path_count'] <= 32 and isinstance(raw.get('isolated_python_sys_path_sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', raw['isolated_python_sys_path_sha256']), 'CHILD_PATH_RESULT')
    result.update(status='FIXED_SOURCE_HASHES_OBSERVED', files=rows, stable_readback=True,
                  code_root_present=raw['code_root_present'], isolated_python_sys_path_count=raw['isolated_python_sys_path_count'],
                  isolated_python_sys_path_sha256=raw['isolated_python_sys_path_sha256'])
    return result


class Inspector:
    def __init__(self):
        self.started = time.monotonic()
        self.commands = 0
        self.pod_name = None
        self.rs_name = None
        self.projections = None
        self.observation = {}

    def command(self, args, child=False):
        allowed = [LIST_ARGS, ['get', 'deployment', 'gateway', '-o', 'json']]
        if self.pod_name:
            allowed.append(['get', 'pod', self.pod_name, '-o', 'json'])
        if self.rs_name:
            allowed.append(['get', 'replicaset', self.rs_name, '-o', 'json'])
        if child:
            require(args == ['exec', self.pod_name, '-c', 'gateway', '--', 'python3', '-I', '-S', '-B', '-c', CHILD, canonical(self.projections).decode('ascii')], 'CHILD_SCOPE')
        else:
            require(args in allowed, 'READ_SCOPE')
        self.commands += 1
        require(self.commands <= 12 and time.monotonic() - self.started < 180, 'QUERY_BUDGET')
        timeout = min(30, 180 - (time.monotonic() - self.started))
        bound = 65536 if child else (131072 if args == LIST_ARGS else 4194304)
        prefix = list(KUBE)
        if child:
            prefix[2] = '--request-timeout=25s'
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(prefix + args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, start_new_session=os.name == 'posix')
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    require(os.fstat(stdout.fileno()).st_size <= bound and os.fstat(stderr.fileno()).st_size <= 65536, 'OUTPUT_BOUND')
                    require(time.monotonic() < deadline, 'COMMAND_TIMEOUT')
                    time.sleep(0.05)
                require(os.fstat(stdout.fileno()).st_size <= bound and os.fstat(stderr.fileno()).st_size <= 65536, 'OUTPUT_BOUND')
                require(process.returncode in ((0, 1) if child else (0,)), 'READ_COMMAND_FAILED')
                stdout.seek(0)
                raw = stdout.read(bound + 1)
                return raw.decode('utf-8') if args == LIST_ARGS else json.loads(raw)
            finally:
                if process.poll() is None:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)

    def snapshot(self, select=False):
        deployment = self.command(['get', 'deployment', 'gateway', '-o', 'json'])
        dep_identity = deployment_guard(deployment)
        if select:
            selected, selected_uid = parse_candidates(self.command(LIST_ARGS))[0]
            self.pod_name = selected
        pod = self.command(['get', 'pod', self.pod_name, '-o', 'json'])
        if select:
            require(pod['metadata']['uid'] == selected_uid, 'POD_REPLACED_AFTER_LIST')
            self.rs_name = owner(pod, 'ReplicaSet')['name']
        rs = self.command(['get', 'replicaset', self.rs_name, '-o', 'json'])
        pod_identity = pod_guard(pod, rs, deployment)
        mounts, projections = mount_projection(pod)
        if select:
            self.projections = projections
        else:
            require(projections == self.projections, 'MOUNT_PROJECTION_CHANGED')
        return dict(deployment=dep_identity, replica_set=identity(rs), pod=pod_identity, source_mounts=mounts)

    def run(self):
        before = self.snapshot(select=True)
        self.observation['before'] = before
        raw = self.command(['exec', self.pod_name, '-c', 'gateway', '--', 'python3', '-I', '-S', '-B', '-c', CHILD, canonical(self.projections).decode('ascii')], child=True)
        runtime = runtime_projection(raw)
        self.observation['runtime'] = runtime
        after = self.snapshot()
        self.observation['after'] = after
        require(before == after, 'GATEWAY_CHANGED_DURING_READ')
        require(runtime['status'] == 'FIXED_SOURCE_HASHES_OBSERVED', 'SOURCE_INSPECTION_INCOMPLETE')
        return dict(status='GATEWAY_LINK_SOURCE_OBSERVED_NO_CHANGE', baseline=after, runtime=runtime, stable_readback=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-readonly', action='store_true', required=True)
    parser.parse_args(argv)
    out = dict(schema='energy-daily-gateway-link-source-inspection.v2', tool_revision='r2', status='INCOMPLETE',
               workload_modified=False, persistent_files_modified=False, secret_api_reads=0, configmap_api_reads=0,
               environment_values_exported=False, source_text_exported=False, credentials_exported=False,
               database_access=False, provider_requests=0, business_requests=0, work_messages_sent=0,
               application_imported=False, automatic_retry=False, raw_errors_exported=False,
               ready_for_deployment=False, inspection_scope='ONE_READY_OWNED_GATEWAY_POD_FIXED_SIX_FILES',
               loaded_application_modules_verified=False, all_gateway_pods_verified=False,
               business_acceptance=False, isolated_sys_path_is_not_application_import_resolution=True)
    inspector = None
    try:
        require(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'DATAX01_ROOT_REQUIRED')
        inspector = Inspector()
        out.update(inspector.run())
    except Exception as exc:
        out['error_class'] = type(exc).__name__
        out['error_code'] = str(exc) if isinstance(exc, Refused) and re.fullmatch('[A-Z0-9_]{1,100}', str(exc)) else 'LOCAL_FAILURE_REDACTED'
        if inspector and inspector.observation:
            out['observation'] = inspector.observation
    out.update(queries=inspector.commands if inspector else 0, observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(), temporary_command_spools_cleaned=True)
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
