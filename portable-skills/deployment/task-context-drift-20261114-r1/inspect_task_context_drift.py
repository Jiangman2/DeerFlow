"""Read-only comparison of one recorded Gateway patch with its live spec."""
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess

CONTROL = Path('/var/lib/portal-task-conversation-context-20261114-r1')
PLAN_SHA = '880c16308caa9e09267a9cee041577caa0088cbf2b90100cbb5e5a91649bea0f'
OPERATION = '86bbcfd1fb7f40819284fa431ecce9f1'
CANDIDATE_SHA = 'd9a8a00422329427b737b3f67d1c13f35482a16d6614cbc3365b705ad198b616'
UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
VOLUME = 'task-conversation-context'
SOURCE = '/app/backend/packages/harness/deerflow/sandbox/tools.py'
SOURCE_SHA = 'e598a0fd0e9d9a83ecdb781be6a345bf974cdef957852d181732f3596ad58330'
CM = 'gateway-task-context-' + OPERATION[:12]
MISSING = object()

def need(ok, code):
    if not ok:
        raise ValueError(code)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def read_fixed(name):
    target = CONTROL / name
    for path in (target,) + tuple(target.parents):
        need(not path.is_symlink(), 'RECEIPT_SYMLINK')
    fd = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
             and not stat.S_IMODE(before.st_mode) & 0o077 and 0 < before.st_size <= 4194304, 'RECEIPT_BOUND')
        raw = os.read(fd, 4194305)
        after = os.fstat(fd)
        signature = lambda state: (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)
        need(len(raw) == before.st_size and signature(before) == signature(after) == signature(target.stat()), 'RECEIPT_CHANGED')
        return raw
    finally:
        os.close(fd)

def metadata(value):
    if value is MISSING:
        return {'present': False, 'type': 'missing', 'sha256': None}
    return {'present': True, 'type': type(value).__name__, 'sha256': digest(canonical(value))}

def differences(expected, actual, path='', result=None):
    if result is None:
        result = []
    if len(result) >= 64:
        return result
    if type(expected) is type(actual) and isinstance(expected, dict):
        for key in sorted(set(expected) | set(actual)):
            child = path + '/' + key.replace('~', '~0').replace('/', '~1')
            differences(expected.get(key, MISSING), actual.get(key, MISSING), child, result)
    elif type(expected) is type(actual) and isinstance(expected, list):
        for index in range(max(len(expected), len(actual))):
            differences(expected[index] if index < len(expected) else MISSING,
                        actual[index] if index < len(actual) else MISSING, path + '/' + str(index), result)
    elif type(expected) is not type(actual) or expected != actual:
        result.append({'path': path, 'expected': metadata(expected), 'actual': metadata(actual)})
    return result

def inspect_spec(expected, actual):
    rows = differences(expected, actual)
    candidate = copy.deepcopy(expected)
    volumes = candidate['template']['spec']['volumes']
    indexes = [i for i, item in enumerate(volumes) if item.get('name') == VOLUME]
    need(len(indexes) == 1, 'CANDIDATE_VOLUME_REQUIRED')
    index = indexes[0]
    live = actual['template']['spec']
    selected = [item for item in live.get('volumes', []) if item.get('name') == VOLUME]
    observed = selected[0].get('configMap', {}).get('defaultMode') if len(selected) == 1 else None
    need('defaultMode' not in volumes[index]['configMap'], 'RECORDED_DEFAULT_ALREADY_PRESENT')
    volumes[index]['configMap']['defaultMode'] = 420
    mounts = [item for container in live.get('containers', []) if container.get('name') == 'gateway'
              for item in container.get('volumeMounts', []) if item.get('name') == VOLUME]
    return {'differences': rows, 'diff_limit_reached': len(rows) >= 64,
            'only_new_volume_default_mode_420_difference': canonical(candidate) == canonical(actual) and canonical(expected) != canonical(actual),
            'new_volume_present_once': len(selected) == 1,
            'new_volume_configmap_matches_recorded': len(selected) == 1 and selected[0].get('configMap', {}).get('name') == CM,
            'new_volume_default_mode': observed if type(observed) is int else None,
            'new_readonly_source_mount_exact': mounts == [{'name': VOLUME, 'mountPath': SOURCE, 'subPath': 'tools.py', 'readOnly': True}]}

def main():
    out = {'schema': 'task-context-spec-drift-readonly.v1', 'status': 'INCOMPLETE',
           'workload_modified': False, 'files_modified': False, 'original_receipts_modified': False,
           'provider_requests': 0, 'secret_api_reads': 0, 'credentials_exported': False,
           'raw_values_exported': False, 'queries': 0}
    try:
        need(os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
        plan_raw = read_fixed('PLAN.json')
        need(digest(plan_raw) == PLAN_SHA, 'PLAN_HASH_CHANGED')
        plan = json.loads(plan_raw)
        need(plan['operation'] == OPERATION and plan['candidate_spec_sha256'] == CANDIDATE_SHA
             and plan['new_configmap'] == CM, 'PLAN_BINDING_CHANGED')
        spec_raw = read_fixed('CANDIDATE-SPEC.json')
        expected = json.loads(spec_raw)
        need(digest(canonical(expected)) == CANDIDATE_SHA, 'CANDIDATE_SPEC_HASH_CHANGED')
        def get(kind, name):
            need((kind, name) in (('deployment', 'gateway'), ('configmap', CM)), 'READ_SCOPE')
            out['queries'] += 1
            result = subprocess.run(['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s',
                '-n', 'deer-flow', 'get', kind, name, '-o', 'json'], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=22, env={'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8', 'HOME': '/root'})
            need(result.returncode == 0 and len(result.stdout) <= 4194304 and len(result.stderr) <= 131072, 'READ_FAILED_OR_OVERSIZE')
            return json.loads(result.stdout)
        first = get('deployment', 'gateway')
        need(first['metadata']['uid'] == UID, 'GATEWAY_UID_CHANGED')
        out.update(inspect_spec(expected, first['spec']))
        status = first.get('status', {})
        out['deployment'] = {'uid': UID, 'generation': first['metadata']['generation'],
            'spec_sha256': digest(canonical(first['spec'])), 'desired': first['spec'].get('replicas'),
            **{key: status.get(key, 0) for key in ('observedGeneration', 'replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'terminatingReplicas')}}
        cm = get('configmap', CM)
        code = cm.get('data', {}).get('tools.py', '').encode('utf-8')
        out['new_configmap'] = {'present': True, 'immutable': cm.get('immutable') is True,
            'operation_label_matches': cm.get('metadata', {}).get('labels', {}).get('portal.task-conversation-context/operation') == OPERATION,
            'only_tools_source': set(cm.get('data', {})) == {'tools.py'} and not cm.get('binaryData'),
            'source_matches_candidate': len(code) == 100988 and digest(code) == SOURCE_SHA}
        last = get('deployment', 'gateway')
        out['stable_deployment_spec'] = first['metadata']['uid'] == last['metadata']['uid'] and first['metadata']['generation'] == last['metadata']['generation'] and first['spec'] == last['spec']
        need(read_fixed('PLAN.json') == plan_raw and read_fixed('CANDIDATE-SPEC.json') == spec_raw, 'RECEIPTS_CHANGED_DURING_READ')
        out.update(status='RECORDED_PATCH_SPEC_DIFF_OBSERVED_NO_CHANGE', accepted_operation=OPERATION,
                   observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    except Exception as exc:
        out.update(error_class=type(exc).__name__, error_code=str(exc) if type(exc) is ValueError else 'READONLY_INSPECTION_FAILED')
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')

if __name__ == '__main__':
    raise SystemExit(main())
