"""Gateway 415 -> 416: only scheduled-run budget 300 -> 10000.

Creates one new immutable ConfigMap, then one full-spec CAS reference patch.
No automatic retry/rollback. --verify-readonly resumes verification only.
Existing runs keep their captured budgets. Normal application startup and
already-enabled background schedules may access DB/Redis/providers/Work.
Python 3.7-compatible operator; YAML qualification uses existing Gateway Python.
"""
import argparse
import base64
import copy
import datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

CHILD_ASSET = {}  # Sealer fills only after independent source review.
NS = 'deer-flow'
DEPLOYMENT_UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
BEFORE_GENERATION = 415
BEFORE_SPEC_SHA = '162716342c8bbc067e5d6441dd00e0ab235139f626a1df691037a56bd1c6fb52'
OLD_CM = 'deer-flow-budget-10000-bf39d16f673d2259'
OLD_CONFIG_SHA = 'bf39d16f673d2259a0430357e7a9a435482add27194aa947a0e34cdd594f1fb7'
OLD_CONFIG_BYTES = 15990
OPENSSL_CM = 'deer-flow-gateway-openssl-compat'
OPENSSL_SHA = '20130c1b277e0a5fb79a27af7dc35be38162a6e086aa91e45b4f249d73c61604'
UPLOAD_CM = 'gateway-conversation-stream-12dc02bb473d5ad9'
LINK_CM = 'gateway-energy-links-b6fb1ab44d10'
CONTROL = Path('/var/lib/portal-scheduler-budget-20261113-r5')
LABEL = 'portal.scheduler-budget/operation'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', NS]
POD_LIST_COLUMNS = 'NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp'
POD_LIST_ARGS = ['get', 'pods', '-l', 'app=gateway', '--no-headers', '-o', 'custom-columns=' + POD_LIST_COLUMNS]
POD_LIST_LIMIT = 262144
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
TOTAL_SECONDS, ROLLOUT_SECONDS = 7200, 6000
CORE_SOURCES = {
    '/app/backend/packages/harness/deerflow/config/app_config.py': (31735, 'ab3056db7657d4ee9de7fbd754f59f4d8e14be78f2535b049047d6d03cc863ad'),
    '/app/backend/packages/harness/deerflow/config/scheduler_config.py': (972, '4495e7c800fa602dd7a0bf85ef15768030abf6bf4f2095e654330a56db1070cb'),
    '/app/backend/app/gateway/services.py': (54226, '4bf26f6ac2166de7851015af36fed73a8b7d1b0e98b7918806c454e623dbd435'),
    '/app/backend/packages/harness/deerflow/agents/lead_agent/agent.py': (41101, 'ec108a4b8cc9c608ba790e71bc5a1c34bbd33cd5241503c7a4ddba7a7e5e0d30'),
}
EXPECTED_SOURCES = {'/app/backend/app/channels/manager.py': {'bytes': 94358, 'sha256': '1a782faf4e4cdbd491871972cca3e501d7a3011da3869fac398f994bedef56f1'}, '/app/backend/app/gateway/routers/uploads.py': {'bytes': 37879, 'sha256': '13b842ee8fd7035555768b1ecb787d944c53088121a6e240f32b69b669b93b1f'}, '/app/backend/app/gateway/services.py': {'bytes': 54226, 'sha256': '4bf26f6ac2166de7851015af36fed73a8b7d1b0e98b7918806c454e623dbd435'}, '/app/backend/app/gateway/upload_document_memory_guard.py': {'bytes': 8956, 'sha256': '37e67be268214537ae8f149a3d45751111cfd34c460a0125e1b03cf65bce4fcf'}, '/app/backend/app/integrations/work/notifications.py': {'bytes': 10025, 'sha256': '2f25958bcb2556685d153bef55f72afebd360deb157cd13164ceb4261001f039'}, '/app/backend/app/scheduler/work_notification_delivery.py': {'bytes': 4297, 'sha256': 'd446868a681754fa98241b62ebfe45a886c6bf21b0da0f68d0d69cd111e8a42e'}, '/app/backend/app/scheduler/work_notifications.py': {'bytes': 14201, 'sha256': 'd3f7e6c4acc4fd0cb7e4f6b31861ddf8b68ff1d2f44761a99da90536a6982645'}, '/app/backend/packages/harness/deerflow/agents/lead_agent/agent.py': {'bytes': 41101, 'sha256': 'ec108a4b8cc9c608ba790e71bc5a1c34bbd33cd5241503c7a4ddba7a7e5e0d30'}, '/app/backend/packages/harness/deerflow/config/app_config.py': {'bytes': 31735, 'sha256': 'ab3056db7657d4ee9de7fbd754f59f4d8e14be78f2535b049047d6d03cc863ad'}, '/app/backend/packages/harness/deerflow/config/scheduler_config.py': {'bytes': 972, 'sha256': '4495e7c800fa602dd7a0bf85ef15768030abf6bf4f2095e654330a56db1070cb'}, '/app/backend/packages/harness/deerflow/tools/builtins/present_file_tool.py': {'bytes': 19640, 'sha256': 'eccfe1e5975c771465abf96f1da60fdb2a11e0712716aeb2453a2d10fbe30e3b'}, '/app/backend/packages/harness/deerflow/utils/conversation_url.py': {'bytes': 5042, 'sha256': 'f5ac36f16038a3532cd3110d134b72e58c42693c1b9ab9a1b6390d9b4209c0b8'}, '/etc/deerflow-openssl/openssl.cnf': {'bytes': 189, 'sha256': '20130c1b277e0a5fb79a27af7dc35be38162a6e086aa91e45b4f249d73c61604'}}
IMAGE = 'deerflow.local/deer-flow-gateway@sha256:0255237caedba428d7ceafd584e171a6d23739f07fc7a566bce016f28d543c5e'
OVERLAYS = {
    LINK_CM: {
        'manager.py': '/app/backend/app/channels/manager.py',
        'notifications.py': '/app/backend/app/integrations/work/notifications.py',
        'work_notification_delivery.py': '/app/backend/app/scheduler/work_notification_delivery.py',
        'work_notifications.py': '/app/backend/app/scheduler/work_notifications.py',
        'present_file_tool.py': '/app/backend/packages/harness/deerflow/tools/builtins/present_file_tool.py',
        'conversation_url.py': '/app/backend/packages/harness/deerflow/utils/conversation_url.py',
    },
    UPLOAD_CM: {
        'uploads.py': '/app/backend/app/gateway/routers/uploads.py',
        'upload_document_memory_guard.py': '/app/backend/app/gateway/upload_document_memory_guard.py',
    },
    OPENSSL_CM: {'openssl.cnf': '/etc/deerflow-openssl/openssl.cnf'},
}


class Refused(ValueError):
    pass


def need(ok, code, field=None, path=None):
    if not ok:
        error = Refused(code)
        error.field, error.path = field, path
        raise error


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe(path):
    path = Path(path)
    need(path.is_absolute() and '..' not in path.parts, 'PRIVATE_PATH')
    for part in (path,) + tuple(path.parents):
        try:
            state = part.lstat()
        except FileNotFoundError:
            continue
        need(not stat.S_ISLNK(state.st_mode) and not getattr(state, 'st_file_attributes', 0) & 0x400,
             'PRIVATE_PATH_LINK')
    return path


def private_write(path, raw):
    path = safe(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def private_read(path, maximum=4194304):
    path = safe(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 < before.st_size <= maximum,
             'PRIVATE_FILE_BOUND')
        if os.name == 'posix':
            need(before.st_uid == 0 and stat.S_IMODE(before.st_mode) == 0o600, 'PRIVATE_FILE_OWNER_MODE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        stamp = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        need(len(raw) == before.st_size and stamp(before) == stamp(after) == stamp(path.stat()), 'PRIVATE_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def load_child():
    need(set(CHILD_ASSET) == {'base64', 'sha256'}, 'CHILD_ASSET_NOT_SEALED')
    need(isinstance(CHILD_ASSET['base64'], str) and len(CHILD_ASSET['base64']) <= 131072,
         'CHILD_ASSET_BOUND')
    raw = base64.b64decode(CHILD_ASSET['base64'], validate=True)
    need(0 < len(raw) <= 65536 and sha(raw) == CHILD_ASSET['sha256'], 'CHILD_ASSET_HASH')
    return raw.decode('utf-8')


def identity(obj):
    meta = obj['metadata']
    return {'uid': meta['uid'], 'generation': meta.get('generation'), 'spec_sha256': sha(canonical(obj['spec']))}


def cm_identity(cm):
    need(cm.get('kind') == 'ConfigMap' and cm['metadata'].get('namespace') == NS
         and not cm['metadata'].get('deletionTimestamp'), 'CONFIGMAP_SHAPE')
    data, binary = cm.get('data', {}), cm.get('binaryData', {})
    need(isinstance(data, dict) and isinstance(binary, dict) and not set(data).intersection(binary), 'CONFIGMAP_DATA_SHAPE')
    need(all(isinstance(k, str) and isinstance(v, str) for k, v in list(data.items()) + list(binary.items())),
         'CONFIGMAP_DATA_TYPES')
    need(len(canonical({'data': data, 'binaryData': binary})) <= 1048576, 'CONFIGMAP_DATA_BOUND')
    return {'uid': cm['metadata']['uid'], 'resource_version': cm['metadata']['resourceVersion'],
            'data_sha256': sha(canonical({'data': data, 'binaryData': binary})),
            'immutable': cm.get('immutable', False)}


def replace_budget(raw):
    """Preserve exact source bytes: one unique scalar token, not YAML reformatting."""
    need(len(raw) == OLD_CONFIG_BYTES and sha(raw) == OLD_CONFIG_SHA, 'ORIGINAL_CONFIG_HASH_REQUIRED')
    lines = raw.splitlines(keepends=True)
    blocks = [i for i, line in enumerate(lines) if re.fullmatch(br'scheduler:[ \t]*(?:#[^\r\n]*)?\r?\n', line)]
    need(len(blocks) == 1, 'SCHEDULER_BLOCK_NOT_UNIQUE')
    start = blocks[0] + 1
    end = next((i for i in range(start, len(lines))
                if lines[i].strip() and not lines[i].startswith((b' ', b'\t', b'#'))), len(lines))
    matches = [(i, re.fullmatch(br'(  recursion_limit:[ \t]*)300([ \t]*(?:#[^\r\n]*)?)(\r?\n|$)', lines[i]))
               for i in range(start, end)]
    matches = [(i, match) for i, match in matches if match]
    need(len(matches) == 1, 'SCHEDULER_RECURSION_SCALAR_NOT_UNIQUE')
    i, match = matches[0]
    offset = sum(len(line) for line in lines[:i]) + len(match.group(1))
    candidate = raw[:offset] + b'10000' + raw[offset + 3:]
    need(len(candidate) == len(raw) + 2 and candidate[:offset] == raw[:offset]
         and candidate[offset + 5:] == raw[offset + 3:], 'CONFIG_BYTE_SCOPE_EXCEEDED')
    return candidate


def config_volume(spec, expected_cm):
    pod = spec['template']['spec']
    containers = pod.get('containers', [])
    need(len(containers) == 1 and containers[0].get('name') == 'gateway', 'GATEWAY_CONTAINER_SHAPE')
    target = PurePosixPath('/app/backend/config.yaml')
    mounts = containers[0].get('volumeMounts', [])
    matches = []
    for mount in mounts:
        base = PurePosixPath(mount['mountPath'])
        need(base.is_absolute() and '..' not in base.parts, 'MOUNT_SHAPE')
        if base == target:
            matches.append(mount)
        else:
            need(base not in target.parents and target not in base.parents, 'CONFIG_SHADOW_MOUNT')
    need(len(matches) == 1 and matches[0].get('subPath') == 'config.yaml'
         and matches[0].get('readOnly') is True and not matches[0].get('subPathExpr'), 'CONFIG_MOUNT_REQUIRED')
    found = [(i, value) for i, value in enumerate(pod.get('volumes', [])) if value['name'] == matches[0]['name']]
    need(len(found) == 1 and found[0][1].get('configMap', {}).get('name') == expected_cm, 'CONFIG_VOLUME_CHANGED')
    return found[0][0]


def build_spec(deployment, cm_name):
    need(identity(deployment) == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION,
                                  'spec_sha256': BEFORE_SPEC_SHA}, 'GATEWAY_BASELINE_CHANGED')
    need(deployment['spec'].get('replicas') == 70 and not deployment['spec'].get('paused', False), 'GATEWAY_REPLICAS_OR_PAUSE')
    index = config_volume(deployment['spec'], OLD_CM)
    need(deployment['spec']['template']['spec']['containers'][0].get('image') == IMAGE, 'FRESH_BASELINE_IMAGE_CHANGED')
    spec = copy.deepcopy(deployment['spec'])
    spec['template']['spec']['volumes'][index]['configMap']['name'] = cm_name
    restored = copy.deepcopy(spec)
    restored['template']['spec']['volumes'][index]['configMap']['name'] = OLD_CM
    need(restored == deployment['spec'], 'DEPLOYMENT_PATCH_SCOPE_EXCEEDED')
    return spec


def cas_patch(deployment, spec):
    index = config_volume(deployment['spec'], OLD_CM)
    meta = deployment['metadata']
    return [{'op': 'test', 'path': '/metadata/uid', 'value': DEPLOYMENT_UID},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': meta['resourceVersion']},
            {'op': 'test', 'path': '/metadata/generation', 'value': BEFORE_GENERATION},
            {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
            {'op': 'replace', 'path': '/spec/template/spec/volumes/%d/configMap/name' % index,
             'value': spec['template']['spec']['volumes'][index]['configMap']['name']}]


def new_configmap(original, name, operation, candidate):
    obj = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': name, 'namespace': NS,
            'labels': {LABEL: operation}}, 'immutable': True}
    for key in ('data', 'binaryData'):
        if key in original:
            obj[key] = copy.deepcopy(original[key])
    need(obj.get('data', {}).get('config.yaml') == original.get('data', {}).get('config.yaml'), 'ORIGINAL_CONFIG_KEY')
    obj['data']['config.yaml'] = candidate.decode('utf-8')
    restored = copy.deepcopy(obj)
    restored['data']['config.yaml'] = original['data']['config.yaml']
    need(all(restored.get(k, {}) == original.get(k, {}) for k in ('data', 'binaryData')), 'CONFIGMAP_COPY_SCOPE')
    return obj


def source_binding(spec, maps):
    result = {path: {'bytes': length, 'sha256': value} for path, (length, value) in CORE_SOURCES.items()}
    pod = spec['template']['spec']
    mounts = pod['containers'][0].get('volumeMounts', [])
    volumes = {row['name']: row for row in pod.get('volumes', [])}
    for name, paths in OVERLAYS.items():
        cm = maps[name]
        need(cm['metadata']['name'] == name, 'SOURCE_CONFIGMAP_NAME')
        cm_identity(cm)
        for key, path in paths.items():
            need(key in cm.get('data', {}) and key not in cm.get('binaryData', {}), 'SOURCE_CONFIGMAP_KEY')
            raw = cm['data'][key].encode('utf-8')
            need(0 < len(raw) <= 1048576, 'SOURCE_CONFIGMAP_FILE_BOUND')
            target = PurePosixPath(path)
            containing = [row for row in mounts if PurePosixPath(row['mountPath']) == target
                          or PurePosixPath(row['mountPath']) in target.parents]
            need(len(containing) == 1, 'SOURCE_MOUNT_CLOSURE')
            mount = containing[0]
            volume = volumes.get(mount['name'], {})
            need(mount.get('readOnly') is True and not mount.get('subPathExpr')
                 and volume.get('configMap', {}).get('name') == name, 'SOURCE_CONFIGMAP_MOUNT')
            if name == OPENSSL_CM:
                need(mount['mountPath'] == '/etc/deerflow-openssl' and not mount.get('subPath')
                     and (len(raw), sha(raw)) == (189, OPENSSL_SHA), 'OPENSSL_COMPATIBILITY_CHANGED')
            else:
                need(mount['mountPath'] == path and mount.get('subPath') == key, 'SOURCE_SUBPATH_CHANGED')
            items = volume['configMap'].get('items')
            if items is not None:
                need(any(item.get('key') == key and item.get('path') == key for item in items), 'SOURCE_ITEM_MAPPING')
            result[path] = {'bytes': len(raw), 'sha256': sha(raw)}
    need(len(result) == 13, 'SOURCE_BINDING_COUNT')
    need(result == EXPECTED_SOURCES, 'FRESH_BASELINE_SOURCE_HASH_CHANGED')
    return result


def guard_deployment(obj, expected_spec, generation, converged=False):
    need(obj['metadata'].get('uid') == DEPLOYMENT_UID and obj['metadata'].get('generation') == generation
         and not obj['metadata'].get('deletionTimestamp') and obj['spec'] == expected_spec, 'GATEWAY_SPEC_DRIFT')
    if converged:
        need(is_converged(obj, generation), 'GATEWAY_NOT_CONVERGED')


def is_converged(obj, generation):
    status = obj.get('status', {})
    return (status.get('observedGeneration') == generation
            and all(status.get(key, 0) == 70 for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and status.get('terminatingReplicas', 0) == 0)


def owner(obj, kind):
    found = [row for row in obj['metadata'].get('ownerReferences', [])
             if row.get('kind') == kind and row.get('controller') is True]
    need(len(found) == 1 and isinstance(found[0].get('uid'), str), 'CONTROLLER_OWNER_REQUIRED')
    return found[0]


def pod_rows(items):
    need(isinstance(items, list) and len(items) <= 512, 'POD_LIST_BOUND')
    rows = [pod for pod in items if pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded')]
    need(len({row['metadata']['name'] for row in rows}) == len(rows), 'DUPLICATE_POD_NAMES')
    return sorted(rows, key=lambda row: row['metadata']['name'])


def parse_pod_list(raw):
    """Bounded CLI projection; selected identities still require full Pod GETs."""
    need(isinstance(raw, str) and len(raw.encode('utf-8')) <= POD_LIST_LIMIT, 'POD_LIST_OUTPUT_BOUND')
    lines = raw.splitlines()
    need(len(lines) <= 512, 'POD_LIST_ROW_BOUND')
    rows, names, uids = [], set(), set()
    for line in lines:
        fields = line.split()
        need(len(fields) == 4, 'POD_LIST_ROW_SHAPE')
        name, uid, phase, deleting = fields
        need(len(name) <= 253 and re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', name)
             and all(0 < len(part) <= 63 and not part.startswith('-') and not part.endswith('-') for part in name.split('.')),
             'POD_LIST_NAME_INVALID')
        need(re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', uid), 'POD_LIST_UID_INVALID')
        need(name not in names and uid not in uids, 'POD_LIST_DUPLICATE_IDENTITY')
        need(phase in ('Pending', 'Running', 'Succeeded', 'Failed', 'Unknown'), 'POD_LIST_PHASE_INVALID')
        need(deleting == '<none>' or re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z', deleting),
             'POD_LIST_DELETION_INVALID')
        names.add(name)
        uids.add(uid)
        rows.append({'metadata': {'name': name, 'uid': uid, 'deletionTimestamp': None if deleting == '<none>' else deleting},
                     'status': {'phase': phase}})
    return pod_rows(rows)


def pod_ips(pod):
    status = pod.get('status', {})
    rows = status.get('podIPs')
    if rows is None:
        rows = [{'ip': status.get('podIP')}]
    need(isinstance(rows, list) and 0 < len(rows) <= 2, 'POD_IP_SET_REQUIRED')
    addresses = []
    for row in rows:
        need(isinstance(row, dict) and set(row) == {'ip'} and isinstance(row['ip'], str), 'POD_IP_SHAPE')
        try:
            address = ipaddress.ip_address(row['ip'])
        except ValueError:
            raise Refused('POD_IP_INVALID')
        need(not address.is_unspecified and not address.is_multicast and not address.is_loopback, 'POD_IP_INVALID')
        addresses.append((address.version, str(address)))
    need(len(set(addresses)) == len(addresses) and len({row[0] for row in addresses}) == len(addresses)
         and isinstance(status.get('podIP'), str)
         and str(ipaddress.ip_address(status['podIP'])) in {row[1] for row in addresses}, 'POD_IP_SET_CHANGED')
    return tuple(sorted(addresses))


DEFAULT_TOLERATIONS = (
    {'key': 'node.kubernetes.io/not-ready', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300},
    {'key': 'node.kubernetes.io/unreachable', 'operator': 'Exists', 'effect': 'NoExecute', 'tolerationSeconds': 300},
)


def pod_default_fields(actual, requested):
    """Allow only explicit, inert API/admission defaults outside the template."""
    defaults = {'priority': 0, 'preemptionPolicy': 'PreemptLowerPriority', 'schedulerName': 'default-scheduler',
        'restartPolicy': 'Always', 'dnsPolicy': 'ClusterFirst', 'terminationGracePeriodSeconds': 30,
        'enableServiceLinks': True, 'serviceAccountName': 'default', 'serviceAccount': 'default',
        'securityContext': {}, 'hostNetwork': False, 'hostPID': False, 'hostIPC': False,
        'shareProcessNamespace': False, 'hostUsers': True}
    defaults['serviceAccount'] = requested.get('serviceAccountName', 'default')
    defaults['serviceAccountName'] = requested.get('serviceAccount', 'default')
    extras = set(actual) - set(requested) - {'containers', 'volumes', 'nodeName', 'tolerations'}
    invalid = sorted(key for key in extras if key not in defaults or type(actual[key]) is not type(defaults[key]) or actual[key] != defaults[key])
    need(not invalid, 'POD_UNEXPECTED_SPEC_FIELD', invalid[0] if invalid else None, '/spec/' + invalid[0] if invalid else None)
    need(isinstance(actual.get('nodeName'), str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}', actual['nodeName']),
         'POD_NODE_REQUIRED')
    expected_tolerations, actual_tolerations = requested.get('tolerations', []), actual.get('tolerations', [])
    need(isinstance(expected_tolerations, list) and isinstance(actual_tolerations, list)
         and all(row in actual_tolerations for row in expected_tolerations), 'POD_TOLERATIONS_CHANGED')
    added = [row for row in actual_tolerations if row not in expected_tolerations]
    need(len(added) <= 2 and all(row in DEFAULT_TOLERATIONS for row in added)
         and len({canonical(row) for row in actual_tolerations}) == len(actual_tolerations), 'POD_EXTRA_TOLERATIONS')


SA_PATH = '/var/run/secrets/kubernetes.io/serviceaccount'


def mismatch_path(left, right, path, depth=0):
    """Only schema field/index paths; no compared values or command content."""
    if depth < 20 and isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            next_path = path + '/' + key.replace('~', '~0').replace('/', '~1')
            if key not in left or key not in right:
                return next_path
            if left[key] != right[key]:
                return mismatch_path(left[key], right[key], next_path, depth + 1)
    elif depth < 20 and isinstance(left, list) and isinstance(right, list):
        for index, (first, second) in enumerate(zip(left, right)):
            if first != second:
                return mismatch_path(first, second, path + '/' + str(index), depth + 1)
        if len(left) != len(right):
            return path + '/' + str(min(len(left), len(right)))
    return path


def admission_expected(pod, replica_set, requested):
    """Construct only two evidenced API-admission projections, never mutate inputs."""
    actual = pod['spec']
    expected = copy.deepcopy(requested)
    declared_names = {row['name'] for row in requested.get('volumes', [])}
    extra = [row for row in actual.get('volumes', []) if row['name'] not in declared_names]
    mount = None
    if extra:
        need(len(extra) == 1 and re.fullmatch('kube-api-access-[a-z0-9]+', extra[0].get('name', ''))
             and isinstance(extra[0].get('projected'), dict), 'POD_UNEXPECTED_VOLUME', 'volumes', '/spec/volumes')
        sources = extra[0]['projected'].get('sources')
        need(isinstance(sources, list), 'SA_PROJECTED_SOURCES_REQUIRED', 'volumes', '/spec/volumes')
        tokens = [row['serviceAccountToken'] for row in sources if isinstance(row, dict) and 'serviceAccountToken' in row]
        need(len(tokens) == 1 and isinstance(tokens[0], dict) and tokens[0].get('path') == 'token'
             and actual.get('automountServiceAccountToken') is not False and requested.get('automountServiceAccountToken') is not False,
             'SA_PROJECTED_TOKEN_REQUIRED', 'volumes', '/spec/volumes')
        mount = {'name': extra[0]['name'], 'mountPath': SA_PATH, 'readOnly': True}
    source_init, actual_init = requested.get('initContainers', []), actual.get('initContainers', [])
    need(isinstance(source_init, list) and isinstance(actual_init, list) and len(source_init) == len(actual_init),
         'INIT_CONTAINER_COUNT_CHANGED', 'initContainers', '/spec/initContainers')
    main = actual.get('containers', [])
    for index, (source, active) in enumerate(zip(source_init, actual_init)):
        if active == source:
            continue
        path = '/spec/initContainers/' + str(index)
        need(mount is not None and len(main) == 1 and mount in main[0].get('volumeMounts', []),
             'INIT_SA_MOUNT_NOT_SHARED', 'initContainers', path + '/volumeMounts')
        projected = copy.deepcopy(source)
        mounts = projected.setdefault('volumeMounts', [])
        need(isinstance(mounts, list) and mount not in mounts and not any(row.get('mountPath') == SA_PATH for row in mounts),
             'INIT_MOUNT_DECLARATION_CHANGED', 'initContainers', path + '/volumeMounts')
        mounts.append(copy.deepcopy(mount))
        need(active == projected, 'INIT_CONTAINER_ADMISSION_CHANGED', 'initContainers', mismatch_path(projected, active, path))
        expected['initContainers'][index] = projected
    source_spread, actual_spread = requested.get('topologySpreadConstraints', []), actual.get('topologySpreadConstraints', [])
    need(isinstance(source_spread, list) and isinstance(actual_spread, list) and len(source_spread) == len(actual_spread),
         'TOPOLOGY_SPREAD_COUNT_CHANGED', 'topologySpreadConstraints', '/spec/topologySpreadConstraints')
    for index, (source, active) in enumerate(zip(source_spread, actual_spread)):
        if active == source:
            continue
        path = '/spec/topologySpreadConstraints/' + str(index)
        keys = source.get('matchLabelKeys')
        need(isinstance(keys, list) and keys.count('pod-template-hash') == 1,
             'TOPOLOGY_HASH_MATCH_KEY_NOT_DECLARED', 'topologySpreadConstraints', path + '/matchLabelKeys')
        value = pod['metadata'].get('labels', {}).get('pod-template-hash')
        need(isinstance(value, str) and re.fullmatch('[a-z0-9]{1,63}', value)
             and replica_set['metadata'].get('labels', {}).get('pod-template-hash') == value
             and replica_set['spec']['template'].get('metadata', {}).get('labels', {}).get('pod-template-hash') == value
             and replica_set.get('spec', {}).get('selector', {}).get('matchLabels', {}).get('pod-template-hash') == value,
             'TOPOLOGY_POD_RS_HASH_MISMATCH', 'topologySpreadConstraints', path + '/labelSelector')
        projected = copy.deepcopy(source)
        selector = projected.get('labelSelector')
        need(isinstance(selector, dict), 'TOPOLOGY_SELECTOR_REQUIRED', 'topologySpreadConstraints', path + '/labelSelector')
        need('pod-template-hash' not in selector.get('matchLabels', {}), 'TOPOLOGY_HASH_SELECTOR_ALREADY_DECLARED',
             'topologySpreadConstraints', path + '/labelSelector/matchLabels')
        expressions = selector.setdefault('matchExpressions', [])
        need(isinstance(expressions, list) and not any(row.get('key') == 'pod-template-hash' for row in expressions),
             'TOPOLOGY_HASH_SELECTOR_ALREADY_DECLARED', 'topologySpreadConstraints', path + '/labelSelector/matchExpressions')
        expressions.append({'key': 'pod-template-hash', 'operator': 'In', 'values': [value]})
        need(active == projected, 'TOPOLOGY_SELECTOR_ADMISSION_CHANGED', 'topologySpreadConstraints', mismatch_path(projected, active, path))
        expected['topologySpreadConstraints'][index] = projected
    return expected


def pod_guard(pod, replica_set, deployment, image_id=None):
    meta = pod['metadata']
    ref, rs_ref = owner(pod, 'ReplicaSet'), owner(replica_set, 'Deployment')
    need(ref['name'] == replica_set['metadata']['name'] and ref['uid'] == replica_set['metadata']['uid']
         and rs_ref['uid'] == DEPLOYMENT_UID and rs_ref['name'] == 'gateway', 'POD_OWNER_MISMATCH')
    template = copy.deepcopy(replica_set['spec']['template'])
    template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
    expected = copy.deepcopy(deployment['spec']['template'])
    expected.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
    need(template == expected, 'REPLICASET_TEMPLATE_CHANGED')
    need(not meta.get('deletionTimestamp') and pod.get('status', {}).get('phase') == 'Running'
         and not pod['spec'].get('ephemeralContainers'), 'POD_NOT_STABLE_RUNNING')
    requested = deployment['spec']['template']['spec']
    pod_default_fields(pod['spec'], requested)
    projected = admission_expected(pod, replica_set, requested)
    for key, value in requested.items():
        if key not in ('containers', 'volumes', 'tolerations'):
            need(pod['spec'].get(key) == projected[key], 'POD_TEMPLATE_FIELD_CHANGED', key,
                 mismatch_path(projected[key], pod['spec'].get(key), '/spec/' + key))
    need(len(pod['spec'].get('containers', [])) == len(requested['containers']) == 1, 'POD_CONTAINER_COUNT')
    actual, source = pod['spec']['containers'][0], requested['containers'][0]
    for key, value in source.items():
        if key != 'volumeMounts':
            need(actual.get(key) == value, 'POD_CONTAINER_FIELD_CHANGED')
    need(set(actual) - set(source) <= {'volumeMounts'}, 'POD_CONTAINER_EXTRA_FIELD')
    volumes = {row['name']: row for row in pod['spec'].get('volumes', [])}
    requested_volumes = {row['name']: row for row in requested.get('volumes', [])}
    need(len(volumes) == len(pod['spec'].get('volumes', []))
         and len(requested_volumes) == len(requested.get('volumes', [])), 'POD_DUPLICATE_VOLUME')
    need(all(volumes.get(name) == row for name, row in requested_volumes.items()), 'POD_VOLUME_CHANGED')
    extras = set(volumes) - set(requested_volumes)
    need(len(extras) <= 1 and all(name.startswith('kube-api-access-') and 'projected' in volumes[name] for name in extras),
         'POD_UNEXPECTED_VOLUME')
    actual_mounts = actual.get('volumeMounts', [])
    expected_mounts = source.get('volumeMounts', [])
    need(all(row in actual_mounts for row in expected_mounts), 'POD_MOUNT_CHANGED')
    added = [row for row in actual_mounts if row not in expected_mounts]
    need(len(actual_mounts) == len(expected_mounts) + len(added)
         and len(added) <= 1 and all(row.get('name') in extras and row.get('readOnly') is True
         and row.get('mountPath') == '/var/run/secrets/kubernetes.io/serviceaccount' for row in added), 'POD_EXTRA_MOUNT')
    states = pod.get('status', {}).get('containerStatuses', [])
    need(len(states) == 1 and states[0].get('name') == 'gateway' and states[0].get('ready') is True
         and states[0].get('restartCount') == 0 and states[0].get('state', {}).get('running')
         and any(row.get('type') == 'Ready' and row.get('status') == 'True' for row in pod['status'].get('conditions', [])),
         'POD_NOT_READY_OR_RESTARTED')
    state = states[0]
    need(isinstance(state.get('containerID'), str) and isinstance(state.get('imageID'), str), 'POD_RUNTIME_ID_REQUIRED')
    need(image_id is None or state['imageID'] == image_id, 'POD_IMAGE_ID_CHANGED')
    return {'name': meta['name'], 'uid': meta['uid'], 'spec_sha256': sha(canonical(pod['spec'])),
            'container_id_sha256': sha(state['containerID'].encode()), 'image_id_sha256': sha(state['imageID'].encode()),
            'node': pod['spec']['nodeName'], 'restart_count': state['restartCount'],
            'running_since': state['state']['running'].get('startedAt'),
            'pod_ips_sha256': sha(canonical(pod_ips(pod)))}


def port_name(value):
    need(value is None or isinstance(value, str), 'SERVICE_PORT_NAME_TYPE')
    return value or None


def resolved_service_ports(service, pods):
    ports = service.get('spec', {}).get('ports')
    need(isinstance(ports, list) and 0 < len(ports) <= 16, 'SERVICE_PORT_SET_REQUIRED')
    result = set()
    for row in ports:
        need(type(row.get('port')) is int and 1 <= row['port'] <= 65535, 'SERVICE_PORT_INVALID')
        protocol, name = row.get('protocol', 'TCP'), port_name(row.get('name'))
        need(protocol in ('TCP', 'UDP', 'SCTP'), 'SERVICE_PORT_IDENTITY')
        target = row.get('targetPort', row['port'])
        resolved = set()
        for pod in pods:
            declared = [value for container in pod['spec'].get('containers', []) for value in container.get('ports', [])]
            if type(target) is int:
                need(1 <= target <= 65535, 'TARGET_PORT_INVALID')
                # A numeric targetPort is legal without containerPort metadata;
                # when declared, its protocol must agree. The numeric value
                # remains the exact route target even when it is undeclared.
                matching = [item for item in declared if item.get('containerPort') == target]
                need(not matching or any(item.get('protocol', 'TCP') == protocol for item in matching), 'CONTAINER_PORT_PROTOCOL_MISMATCH')
                port = target
            else:
                need(isinstance(target, str) and target, 'TARGET_PORT_INVALID')
                matching = [item for item in declared if item.get('name') == target and item.get('protocol', 'TCP') == protocol]
                need(len(matching) == 1 and type(matching[0].get('containerPort')) is int
                     and 1 <= matching[0]['containerPort'] <= 65535, 'NAMED_CONTAINER_PORT_NOT_UNIQUE')
                port = matching[0]['containerPort']
            resolved.add(port)
        need(len(resolved) == 1, 'SERVICE_TARGET_PORT_VARIANCE')
        identity_row = (name, protocol, next(iter(resolved)), row.get('appProtocol'))
        need(identity_row not in result, 'DUPLICATE_SERVICE_PORT')
        result.add(identity_row)
    return result


def endpoints(slices, service, pods):
    need(isinstance(slices.get('items'), list) and 0 < len(slices['items']) <= 128, 'ENDPOINT_SLICE_BOUND')
    pod_set = {(pod['metadata']['name'], pod['metadata']['uid']): dict(pod_ips(pod)) for pod in pods}
    need(len(pod_set) == len(pods) == 70, 'ENDPOINT_VERIFIED_POD_COUNT')
    families = service.get('spec', {}).get('ipFamilies')
    if families is None:
        cluster_ip = service.get('spec', {}).get('clusterIP')
        need(isinstance(cluster_ip, str) and cluster_ip != 'None', 'SERVICE_ADDRESS_FAMILY_REQUIRED')
        families = ['IPv%d' % ipaddress.ip_address(cluster_ip).version]
    need(isinstance(families, list) and 0 < len(families) <= 2 and len(set(families)) == len(families)
         and all(value in ('IPv4', 'IPv6') for value in families), 'SERVICE_ADDRESS_FAMILY_INVALID')
    versions = {int(value[-1]) for value in families}
    expected = set()
    for target, addresses in pod_set.items():
        need(versions.issubset(addresses), 'POD_SERVICE_FAMILY_MISSING')
        expected.update((target[0], target[1], version, addresses[version]) for version in versions)
    expected_ports = resolved_service_ports(service, pods)
    found, addresses_seen, slice_names = set(), set(), set()
    for item in slices['items']:
        slice_name = item['metadata'].get('name')
        need(isinstance(slice_name, str) and slice_name not in slice_names, 'ENDPOINT_SLICE_DUPLICATE')
        slice_names.add(slice_name)
        need(item['metadata'].get('namespace', NS) == NS
             and item['metadata'].get('labels', {}).get('kubernetes.io/service-name') == 'gateway'
             and any(row.get('kind') == 'Service' and row.get('name') == 'gateway' and row.get('uid') == service['metadata']['uid']
                 for row in item['metadata'].get('ownerReferences', [])), 'ENDPOINT_SERVICE_OWNER')
        family = item.get('addressType')
        need(family in families, 'ENDPOINT_ADDRESS_TYPE_CHANGED')
        port_rows = item.get('ports')
        need(isinstance(port_rows, list) and len(port_rows) == len(expected_ports), 'ENDPOINT_PORT_SET_CHANGED')
        need(all(isinstance(row, dict) and type(row.get('port')) is int
             and 1 <= row['port'] <= 65535 and row.get('protocol', 'TCP') in ('TCP', 'UDP', 'SCTP')
             and (row.get('name') is None or isinstance(row['name'], str))
             and (row.get('appProtocol') is None or isinstance(row['appProtocol'], str)) for row in port_rows),
             'ENDPOINT_PORT_SHAPE')
        actual_ports = {(port_name(row.get('name')), row.get('protocol', 'TCP'), row.get('port'), row.get('appProtocol')) for row in port_rows}
        need(len(actual_ports) == len(port_rows) and actual_ports == expected_ports, 'ENDPOINT_PORT_SET_CHANGED')
        for row in item.get('endpoints', []):
            condition, target = row.get('conditions', {}), row.get('targetRef', {})
            need(condition.get('ready') is True and condition.get('terminating', False) is False
                 and target.get('kind') == 'Pod' and target.get('namespace', NS) == NS
                 and isinstance(target.get('name'), str) and isinstance(target.get('uid'), str), 'ENDPOINT_NOT_READY')
            key = target['name'], target['uid']
            need(key in pod_set and isinstance(row.get('addresses'), list) and len(row['addresses']) == 1,
                 'ENDPOINT_ADDRESS_SET_CHANGED')
            try:
                address = ipaddress.ip_address(row['addresses'][0])
            except (TypeError, ValueError):
                raise Refused('ENDPOINT_ADDRESS_INVALID')
            need('IPv%d' % address.version == family, 'ENDPOINT_ADDRESS_FAMILY_MISMATCH')
            value = (key[0], key[1], address.version, str(address))
            address_key = address.version, str(address)
            need(value in expected and value not in found and address_key not in addresses_seen, 'ENDPOINT_ADDRESS_MISMATCH_OR_DUPLICATE')
            found.add(value)
            addresses_seen.add(address_key)
    need(found == expected, 'ENDPOINT_ADDRESS_CLOSURE_MISMATCH')
    return set(pod_set)


class Operator:
    def __init__(self, child, readonly=False):
        self.child, self.readonly = child, readonly
        self.started, self.commands, self.sequence = time.monotonic(), 0, 0
        self.operation = uuid.uuid4().hex
        self.plan = self.before = self.expected = self.manifest = None
        self.known_pods, self.known_rs = set(), set()
        self.report = {'schema': 'scheduler-budget-activation.v1', 'tool_revision': 'r5', 'operation': self.operation,
            'status': 'INCOMPLETE', 'once_claimed': False, 'automatic_retry': False, 'automatic_rollback': False,
            'patch_request_count': 0, 'patch_may_have_applied': False, 'configmap_create_requests': 0,
            'secret_api_reads': 0, 'credentials_exported': False, 'raw_configuration_exported': False,
            'raw_errors_exported': False, 'explicit_database_queries': 0, 'provider_requests': 0, 'work_messages_sent': 0,
            'other_deployments_modified': False, 'existing_runs_replayed': False, 'existing_run_budgets_modified': False,
            'future_scheduled_runs_budget': 10000, 'global_max_recursion_limit': 10000,
            'all_existing_gateway_spec_fields_except_configmap_name_preserved': True,
            'active_tasks_may_be_interrupted': not readonly, 'active_task_safe_shutdown_proven': False,
            'normal_application_startup_database_and_redis_access_may_occur': not readonly,
            'existing_background_schedules_may_execute_normally': True, 'business_acceptance_pending': True,
            'ca_secret_contents_read': False, 'ca_secret_references_preserved': True,
            'readonly': readonly, 'persistent_files_modified': False}

    def journal(self, phase):
        self.report['last_step'] = phase
        if self.report['once_claimed'] and not self.readonly:
            private_write(CONTROL / ('JOURNAL-%04d.json' % self.sequence), canonical(self.report))
            self.sequence += 1

    def command(self, args, body=None, timeout=30, limit=4194304):
        need(isinstance(args, list) and all(isinstance(item, str) for item in args), 'COMMAND_ARGUMENT_TYPES')
        need(args[:1] != ['get'] or body is None, 'READ_COMMAND_BODY_REFUSED')
        allowed = args in [
            ['get', 'deployment', 'gateway', '-o', 'json'], ['get', 'service', 'gateway', '-o', 'json'],
            POD_LIST_ARGS,
            ['get', 'endpointslices', '-l', 'kubernetes.io/service-name=gateway', '-o', 'json']]
        if args[:2] == ['get', 'configmap']:
            allowed = len(args) == 5 and args[3:] == ['-o', 'json'] and args[2] in set(OVERLAYS) | {OLD_CM} | ({self.plan['new_configmap']} if self.plan else set())
        if args[:2] == ['get', 'pod']:
            allowed = len(args) == 5 and args[2] in self.known_pods and args[3:] == ['-o', 'json']
        if args[:2] == ['get', 'replicaset']:
            allowed = len(args) == 5 and args[2] in self.known_rs and args[3:] == ['-o', 'json']
        if args[:1] == ['exec']:
            allowed = (len(args) == 12 and args[1] == '-i' and args[2] in self.known_pods
                       and args[3:] == ['-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', self.child]
                       and body is not None and len(body) <= 262144)
        if args[:1] == ['create']:
            need(not self.readonly and self.report['once_claimed'] and self.report['configmap_create_requests'] == 1,
                 'CREATE_NOT_AUTHORIZED')
            allowed = args == ['create', '-f', '-', '-o', 'json'] and body == canonical(self.manifest)
        if args[:1] == ['patch']:
            need(not self.readonly and self.report['once_claimed'] and self.report['patch_request_count'] == 1,
                 'PATCH_NOT_AUTHORIZED')
            allowed = (args == ['patch', 'deployment', 'gateway', '--type=json', '--patch-file', str(CONTROL / 'PATCH.json'), '-o', 'json']
                       and body is None and private_read(CONTROL / 'PATCH.json') == canonical(cas_patch(self.before, self.expected)))
        need(allowed, 'COMMAND_SCOPE')
        kind = self.command_kind(args)
        self.report['last_command_kind'] = kind
        limit = min(limit, POD_LIST_LIMIT if args == POD_LIST_ARGS else (65536 if args[:1] == ['exec'] else 4194304))
        self.commands += 1
        remaining = TOTAL_SECONDS - (time.monotonic() - self.started)
        need(self.commands <= 2600 and remaining > 0, 'OPERATOR_BUDGET')
        timeout = min(timeout, remaining)
        prefix = list(KUBE)
        prefix[2] = '--request-timeout=%ds' % max(20, min(120, int(timeout) - 2))
        with tempfile.TemporaryFile() as source, tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            if body is not None:
                need(len(body) <= 4194304, 'COMMAND_INPUT_BOUND')
                source.write(body)
                source.seek(0)
            process = subprocess.Popen(prefix + args, stdin=source if body is not None else subprocess.DEVNULL,
                stdout=output, stderr=errors, env={'PATH': SAFE_PATH, 'HOME': '/root', 'LANG': 'C.UTF-8'},
                start_new_session=os.name == 'posix')
            until = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    self.check_output_bound(kind, os.fstat(output.fileno()).st_size, os.fstat(errors.fileno()).st_size, limit)
                    need(time.monotonic() < until, 'COMMAND_TIMEOUT')
                    time.sleep(0.05)
                self.check_output_bound(kind, os.fstat(output.fileno()).st_size, os.fstat(errors.fileno()).st_size, limit)
                need(process.returncode in ((0, 1) if args[:1] == ['exec'] else (0,)), 'KUBECTL_COMMAND_FAILED')
                output.seek(0)
                raw = output.read(limit + 1)
                return raw.decode('utf-8') if args == POD_LIST_ARGS else json.loads(raw)
            finally:
                if process.poll() is None:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait(timeout=5)

    def command_kind(self, args):
        if args == POD_LIST_ARGS:
            return 'LIST_GATEWAY_POD_IDENTITIES'
        fixed = {('get', 'deployment'): 'GET_GATEWAY_DEPLOYMENT', ('get', 'service'): 'GET_GATEWAY_SERVICE',
                 ('get', 'configmap'): 'GET_FIXED_CONFIGMAP', ('get', 'pod'): 'GET_GATEWAY_POD',
                 ('get', 'replicaset'): 'GET_GATEWAY_REPLICASET', ('get', 'endpointslices'): 'LIST_GATEWAY_ENDPOINTS'}
        if tuple(args[:2]) in fixed:
            return fixed[tuple(args[:2])]
        return {'exec': 'READONLY_GATEWAY_QUALIFICATION', 'create': 'CREATE_NEW_BUDGET_CONFIGMAP',
                'patch': 'SINGLE_GATEWAY_SPEC_CAS_PATCH'}[args[0]]

    def check_output_bound(self, kind, stdout_bytes, stderr_bytes, limit):
        for stream, observed, maximum in (('stdout', stdout_bytes, limit), ('stderr', stderr_bytes, 131072)):
            if observed > maximum:
                self.report['command_output_failure'] = {'command_kind': kind, 'stream': stream,
                    'limit': maximum, 'observed_bytes': observed}
                raise Refused('COMMAND_OUTPUT_BOUND')

    def get(self, kind, name):
        return self.command(['get', kind, name, '-o', 'json'])

    def qualification(self, pod, candidate=False):
        payload = {'mode': 'qualify' if candidate else 'verify', 'sources': self.plan['sources'],
            'config': {'bytes': OLD_CONFIG_BYTES if candidate else self.plan['new_config_bytes'],
                       'sha256': OLD_CONFIG_SHA if candidate else self.plan['new_config_sha256'],
                       'scheduler_recursion_limit': 300 if candidate else 10000}}
        if candidate:
            payload['candidate'] = {'bytes': self.plan['new_config_bytes'], 'sha256': self.plan['new_config_sha256'],
                'scheduler_recursion_limit': 10000, 'base64': base64.b64encode(self.manifest['data']['config.yaml'].encode('utf-8')).decode('ascii')}
        result = self.command(['exec', '-i', pod, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', self.child],
                              body=canonical(payload), timeout=40, limit=65536)
        if result.get('status') != 'SCHEDULER_BUDGET_READONLY_QUALIFIED':
            self.report['qualification_error_class'] = result.get('error_class') if re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}', str(result.get('error_class', ''))) else 'UNKNOWN'
            self.report['qualification_error_code'] = result.get('error_code') if re.fullmatch('[A-Z][A-Z0-9_]{0,95}', str(result.get('error_code', ''))) else 'CHILD_QUALIFICATION_FAILED'
        need(result.get('status') == 'SCHEDULER_BUDGET_READONLY_QUALIFIED' and result.get('stable_readback') is True,
             'READONLY_CONFIG_QUALIFICATION_FAILED')
        return result

    def snapshot_maps(self):
        return {name: self.get('configmap', name) for name in [OLD_CM] + sorted(OVERLAYS)}

    def check_maps(self):
        current = self.snapshot_maps()
        need({name: cm_identity(obj) for name, obj in current.items()} == self.plan['old_configmaps'],
             'ORIGINAL_CONFIGMAP_CLOSURE_CHANGED')
        return current

    def preflight(self):
        self.journal('READ_GATEWAY_415_AND_FIXED_CONFIGMAPS')
        before = self.get('deployment', 'gateway')
        need(identity(before) == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION, 'spec_sha256': BEFORE_SPEC_SHA},
             'GATEWAY_BASELINE_CHANGED')
        guard_deployment(before, before['spec'], BEFORE_GENERATION, True)
        maps = self.snapshot_maps()
        original = maps[OLD_CM]
        raw = original.get('data', {}).get('config.yaml', '').encode('utf-8')
        candidate = replace_budget(raw)
        name = 'deer-flow-scheduler-budget-10000-' + sha(candidate)[:16]
        self.before, self.expected = before, build_spec(before, name)
        self.manifest = new_configmap(original, name, self.operation, candidate)
        sources = source_binding(before['spec'], {key: maps[key] for key in OVERLAYS})
        service = self.get('service', 'gateway')
        need(service['metadata'].get('namespace') == NS and not service['metadata'].get('deletionTimestamp')
             and service['spec'].get('selector') == before['spec']['selector'].get('matchLabels'), 'GATEWAY_SERVICE_SELECTOR')
        self.plan = {'schema': 'scheduler-budget-private-plan.v1', 'operation': self.operation,
            'before': identity(before), 'after_spec_sha256': sha(canonical(self.expected)), 'new_configmap': name,
            'new_config_bytes': len(candidate), 'new_config_sha256': sha(candidate), 'sources': sources,
            'old_configmaps': {key: cm_identity(value) for key, value in maps.items()},
            'service': {'uid': service['metadata']['uid'], 'spec_sha256': sha(canonical(service['spec']))},
            'before_file_sha256': sha(canonical(before)), 'candidate_spec_sha256': sha(canonical(self.expected)),
            'candidate_configmap_sha256': sha(canonical(self.manifest)), 'child_sha256': sha(self.child.encode('utf-8'))}
        rows = parse_pod_list(self.command(POD_LIST_ARGS))
        need(len(rows) == 70, 'BASELINE_ACTIVE_POD_COUNT')
        self.known_pods.update(row['metadata']['name'] for row in rows)
        selected = rows[0]
        sample = self.get('pod', selected['metadata']['name'])
        need(sample['metadata']['uid'] == selected['metadata']['uid'], 'PREFLIGHT_LISTED_POD_REPLACED')
        rs_name = owner(sample, 'ReplicaSet')['name']
        self.known_rs.add(rs_name)
        rs = self.get('replicaset', rs_name)
        stamp = pod_guard(sample, rs, before)
        self.plan['image_id'] = sample['status']['containerStatuses'][0]['imageID']
        qualification = self.qualification(sample['metadata']['name'], True)
        need(pod_guard(self.get('pod', sample['metadata']['name']), rs, before) == stamp, 'PREFLIGHT_SAMPLE_CHANGED')
        self.check_maps()
        fresh = self.get('deployment', 'gateway')
        guard_deployment(fresh, before['spec'], BEFORE_GENERATION, True)
        self.before = fresh
        self.plan['before_file_sha256'] = sha(canonical(fresh))
        self.report.update(before=identity(fresh), sample=stamp, qualification={key: qualification[key]
            for key in ('config_sha256', 'candidate_sha256', 'scheduler_recursion_limit', 'candidate_scheduler_recursion_limit',
                        'max_recursion_limit', 'source_files_verified', 'versions', 'semantic_change_only_scheduler_recursion_limit')},
            candidate_config_sha256=sha(candidate), candidate_spec_sha256=sha(canonical(self.expected)))
        return maps

    def claim(self, maps):
        safe(CONTROL)
        need(not CONTROL.exists(), 'ONCE_DIRECTORY_EXISTS_REVIEW_REQUIRED')
        os.mkdir(str(CONTROL), 0o700)
        self.report.update(once_claimed=True, persistent_files_modified=True)
        private_write(CONTROL / 'BEFORE.json', canonical(self.before))
        private_write(CONTROL / 'ORIGINAL-CONFIGMAPS.json', canonical(maps))
        private_write(CONTROL / 'CANDIDATE-SPEC.json', canonical(self.expected))
        private_write(CONTROL / 'CANDIDATE-CONFIGMAP.json', canonical(self.manifest))
        private_write(CONTROL / 'PLAN.json', canonical(self.plan))
        self.report['plan_sha256'] = sha(canonical(self.plan))
        self.journal('CLAIMED_PRIVATE_BACKUP_COMPLETE')

    def cutover(self):
        self.report['configmap_create_requests'] = 1
        self.journal('CREATE_ONLY_NEW_IMMUTABLE_CONFIGMAP')
        created = self.command(['create', '-f', '-', '-o', 'json'], body=canonical(self.manifest))
        need(all(created.get(key) == self.manifest.get(key) for key in ('data', 'binaryData', 'immutable'))
             and created['metadata']['name'] == self.plan['new_configmap']
             and created['metadata'].get('labels', {}).get(LABEL) == self.operation, 'CREATED_CONFIGMAP_MISMATCH')
        private_write(CONTROL / 'CREATED-CONFIGMAP-IDENTITY.json', canonical(cm_identity(created)))
        self.journal('PRE_PATCH_REVALIDATION')
        self.check_maps()
        fresh = self.get('deployment', 'gateway')
        guard_deployment(fresh, self.before['spec'], BEFORE_GENERATION, True)
        self.before = fresh
        private_write(CONTROL / 'PATCH.json', canonical(cas_patch(fresh, self.expected)))
        self.report.update(patch_request_count=1, patch_may_have_applied=True)
        self.journal('SINGLE_FULL_SPEC_CAS_PATCH_REQUEST')
        after = self.command(['patch', 'deployment', 'gateway', '--type=json', '--patch-file', str(CONTROL / 'PATCH.json'), '-o', 'json'])
        guard_deployment(after, self.expected, BEFORE_GENERATION + 1)
        self.report['patch_acknowledged'] = True
        self.journal('WAIT_EXISTING_STRATEGY_GATEWAY_416_ROLLOUT')

    def rollout(self):
        until, last = time.monotonic() + ROLLOUT_SECONDS, 0
        while True:
            current = self.get('deployment', 'gateway')
            guard_deployment(current, self.expected, BEFORE_GENERATION + 1)
            status = current.get('status', {})
            if time.monotonic() - last >= 30:
                print(json.dumps({'phase': 'WAIT_GATEWAY_416_ROLLOUT', 'desired': 70,
                    'observed_generation': status.get('observedGeneration', 0),
                    'updated': status.get('updatedReplicas', 0), 'ready': status.get('readyReplicas', 0),
                    'available': status.get('availableReplicas', 0)}), flush=True)
                last = time.monotonic()
            if is_converged(current, BEFORE_GENERATION + 1):
                return current
            need(not any(row.get('type') == 'Progressing' and row.get('status') == 'False'
                         and row.get('reason') == 'ProgressDeadlineExceeded' for row in status.get('conditions', [])),
                 'ROLLOUT_PROGRESS_DEADLINE')
            need(time.monotonic() < until, 'ROLLOUT_WAIT_TIMEOUT')
            time.sleep(10)

    def load_plan(self):
        directory = safe(CONTROL).stat()
        need(stat.S_ISDIR(directory.st_mode) and directory.st_uid == 0 and stat.S_IMODE(directory.st_mode) == 0o700,
             'CONTROL_DIRECTORY_OWNER_MODE')
        raw = private_read(CONTROL / 'PLAN.json')
        self.plan = json.loads(raw)
        need(self.plan.get('schema') == 'scheduler-budget-private-plan.v1'
             and re.fullmatch('[0-9a-f]{32}', self.plan.get('operation', ''))
             and self.plan['before'] == {'uid': DEPLOYMENT_UID, 'generation': BEFORE_GENERATION, 'spec_sha256': BEFORE_SPEC_SHA}
             and self.plan['child_sha256'] == sha(self.child.encode('utf-8')), 'PRIVATE_PLAN_BINDING')
        self.operation = self.plan['operation']
        self.report.update(operation=self.operation, plan_sha256=sha(raw), original_receipts_modified=False,
                           repeat_readonly_check_safe=True)
        self.before = json.loads(private_read(CONTROL / 'BEFORE.json'))
        self.expected = json.loads(private_read(CONTROL / 'CANDIDATE-SPEC.json'))
        self.manifest = json.loads(private_read(CONTROL / 'CANDIDATE-CONFIGMAP.json'))
        maps = json.loads(private_read(CONTROL / 'ORIGINAL-CONFIGMAPS.json'))
        need(sha(canonical(self.before)) == self.plan['before_file_sha256']
             and sha(canonical(self.expected)) == self.plan['candidate_spec_sha256']
             and sha(canonical(self.manifest)) == self.plan['candidate_configmap_sha256']
             and build_spec(self.before, self.plan['new_configmap']) == self.expected
             and {name: cm_identity(value) for name, value in maps.items()} == self.plan['old_configmaps'], 'PRIVATE_BACKUP_BINDING')
        rebuilt = replace_budget(maps[OLD_CM]['data']['config.yaml'].encode('utf-8'))
        need(new_configmap(maps[OLD_CM], self.plan['new_configmap'], self.operation, rebuilt) == self.manifest
             and source_binding(self.before['spec'], {key: maps[key] for key in OVERLAYS}) == self.plan['sources'], 'PRIVATE_CANDIDATE_BINDING')
        journal_paths = sorted(CONTROL.glob('JOURNAL-*.json'))
        need(0 < len(journal_paths) <= 32 and all(re.fullmatch(r'JOURNAL-\d{4}\.json', p.name) for p in journal_paths), 'JOURNAL_SET')
        records = [json.loads(private_read(path, 262144)) for path in journal_paths]
        need(all(row.get('operation') == self.operation and row.get('plan_sha256') == sha(raw) for row in records), 'JOURNAL_PLAN_BINDING')
        need(any(row.get('patch_request_count') == 1 and row.get('patch_may_have_applied') is True
                 and row.get('last_step') == 'SINGLE_FULL_SPEC_CAS_PATCH_REQUEST' for row in records), 'NO_RECORDED_PATCH_REQUEST')
        self.report['accepted_original_patch_journal'] = {
            'operation': self.operation, 'plan_sha256': sha(raw),
            'journal_count': len(records), 'last_journal_sha256': sha(canonical(records[-1])),
            'patch_request_recorded': True}
        patch = json.loads(private_read(CONTROL / 'PATCH.json'))
        need(patch[:1] == [{'op': 'test', 'path': '/metadata/uid', 'value': DEPLOYMENT_UID}]
             and len(patch) == 5 and patch[2] == {'op': 'test', 'path': '/metadata/generation', 'value': BEFORE_GENERATION}
             and set(patch[1]) == {'op', 'path', 'value'} and patch[1]['op'] == 'test'
             and patch[1]['path'] == '/metadata/resourceVersion' and isinstance(patch[1]['value'], str)
             and patch[3] == {'op': 'test', 'path': '/spec', 'value': self.before['spec']}
             and patch[4] == cas_patch(self.before, self.expected)[4], 'PRIVATE_PATCH_SCOPE')

    def verify(self, deployment):
        self.journal('READONLY_VERIFY_SEVENTY_GATEWAY_PODS')
        guard_deployment(deployment, self.expected, BEFORE_GENERATION + 1, True)
        self.check_maps()
        new_cm = self.get('configmap', self.plan['new_configmap'])
        expected_identity = json.loads(private_read(CONTROL / 'CREATED-CONFIGMAP-IDENTITY.json'))
        need(cm_identity(new_cm) == expected_identity and all(new_cm.get(key) == self.manifest.get(key)
             for key in ('data', 'binaryData', 'immutable')), 'NEW_CONFIGMAP_CHANGED')
        rows = parse_pod_list(self.command(POD_LIST_ARGS))
        need(len(rows) == 70, 'FINAL_ACTIVE_POD_COUNT')
        self.known_pods.update(row['metadata']['name'] for row in rows)
        cache, accepted, verified_pods = {}, [], []
        for index, selected in enumerate(rows):
            name = selected['metadata']['name']
            pod = self.get('pod', name)
            need(pod['metadata']['uid'] == selected['metadata']['uid'], 'LISTED_FLEET_POD_REPLACED')
            rs_name = owner(pod, 'ReplicaSet')['name']
            self.known_rs.add(rs_name)
            if rs_name not in cache:
                cache[rs_name] = self.get('replicaset', rs_name)
            stamp = pod_guard(pod, cache[rs_name], deployment, self.plan['image_id'])
            self.qualification(name)
            need(pod_guard(self.get('pod', name), cache[rs_name], deployment, self.plan['image_id']) == stamp,
                 'POD_CHANGED_DURING_QUALIFICATION')
            accepted.append(stamp)
            verified_pods.append(pod)
            if index % 10 == 9:
                print(json.dumps({'phase': 'READONLY_VERIFY_GATEWAY_416', 'verified_pods': index + 1, 'expected': 70}), flush=True)
        need(len(cache) == 1, 'MULTIPLE_FINAL_REPLICASETS')
        service = self.get('service', 'gateway')
        need({'uid': service['metadata']['uid'], 'spec_sha256': sha(canonical(service['spec']))} == self.plan['service'], 'SERVICE_CHANGED')
        expected_endpoints = {(row['name'], row['uid']) for row in accepted}
        need(endpoints(self.command(['get', 'endpointslices', '-l', 'kubernetes.io/service-name=gateway', '-o', 'json']),
                       service, verified_pods) == expected_endpoints, 'SERVICE_ENDPOINT_SET_MISMATCH')
        final_rows = parse_pod_list(self.command(POD_LIST_ARGS))
        need({(row['metadata']['name'], row['metadata']['uid']) for row in final_rows} == expected_endpoints,
             'FINAL_FLEET_CHANGED')
        for selected in final_rows:
            pod = self.get('pod', selected['metadata']['name'])
            need(pod['metadata']['uid'] == selected['metadata']['uid'], 'FINAL_LISTED_POD_REPLACED')
            need(pod_guard(pod, cache[owner(pod, 'ReplicaSet')['name']], deployment, self.plan['image_id'])
                 == next(row for row in accepted if row['uid'] == pod['metadata']['uid']), 'FINAL_RUNTIME_CHANGED')
        for name, cached in cache.items():
            fresh_rs = self.get('replicaset', name)
            need(fresh_rs['metadata']['uid'] == cached['metadata']['uid'] and fresh_rs['spec'] == cached['spec'],
                 'FINAL_REPLICASET_CHANGED')
        final = self.get('deployment', 'gateway')
        guard_deployment(final, self.expected, BEFORE_GENERATION + 1, True)
        self.check_maps()
        self.report.update(status='SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED', after=identity(final),
            all_gateway_pods_verified=True, verified_pods=70, verified_source_files_per_pod=13,
            service_backends_verified=True, verified_fleet_sha256=sha(canonical(accepted)),
            original_configmaps_unchanged=True, loaded_application_modules_verified=False,
            business_acceptance_pending=True)
        self.journal('RUNTIME_ACCEPTED_FUTURE_SCHEDULED_RUN_ACCEPTANCE_PENDING')

    def run(self):
        if self.readonly:
            self.load_plan()
            current = self.get('deployment', 'gateway')
            if identity(current) == self.plan['before'] and current['spec'] == self.before['spec']:
                self.report.update(status='SCHEDULER_BUDGET_PATCH_NOT_OBSERVED_REVIEW_REQUIRED',
                                   current=identity(current), error_code='RECORDED_PATCH_NOT_OBSERVED_NO_RETRY')
                return
            guard_deployment(current, self.expected, BEFORE_GENERATION + 1)
            self.report['current'] = dict(identity(current), **{key: current.get('status', {}).get(key, 0)
                for key in ('observedGeneration', 'replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'terminatingReplicas')})
            if not is_converged(current, BEFORE_GENERATION + 1):
                self.report['status'] = 'GATEWAY_416_ROLLOUT_IN_PROGRESS_NO_CHANGE'
                return
            self.verify(current)
        else:
            maps = self.preflight()
            self.claim(maps)
            self.cutover()
            self.verify(self.rollout())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--activate-scheduler-budget-once', action='store_true')
    group.add_argument('--verify-readonly', action='store_true')
    options = parser.parse_args(argv)
    operator = Operator('', readonly=options.verify_readonly)
    try:
        need(sys.version_info >= (3, 7), 'PYTHON_37_REQUIRED')
        need(os.name == 'posix' and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_REQUIRED')
        need(os.geteuid() == 0, 'ROOT_REQUIRED')
        os.umask(0o077)
        operator.child = load_child()
        operator.run()
    except KeyboardInterrupt:
        operator.report.update(error_class='KeyboardInterrupt', error_code='OPERATOR_INTERRUPTED_ROLLOUT_MAY_CONTINUE',
                               verify_readonly_available=operator.report['patch_may_have_applied'])
    except Exception as exc:
        operator.report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'LOCAL_FAILURE')
        if isinstance(exc, Refused):
            if isinstance(getattr(exc, 'field', None), str) and re.fullmatch('[A-Za-z][A-Za-z0-9]{0,63}', exc.field):
                operator.report['error_field'] = exc.field
            if isinstance(getattr(exc, 'path', None), str) and re.fullmatch(r'/[A-Za-z0-9_~./-]{1,1023}', exc.path):
                operator.report['error_path'] = exc.path
    finally:
        operator.report.update(commands=operator.commands, elapsed_seconds=round(time.monotonic() - operator.started, 3),
                               observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
        if operator.report['once_claimed'] and not operator.readonly:
            try:
                operator.journal('FINAL_OPERATOR_RESULT')
                report_raw = canonical(operator.report)
                private_write(CONTROL / 'REPORT.json', report_raw)
                operator.report['report_path'] = str(CONTROL / 'REPORT.json')
                operator.report['report_bytes'] = len(report_raw)
                operator.report['report_sha256'] = sha(report_raw)
            except Exception as exc:
                operator.report['report_write_error_class'] = type(exc).__name__
        print(json.dumps(operator.report, sort_keys=True), flush=True)
    return 0 if operator.report['status'] in ('SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED', 'GATEWAY_416_ROLLOUT_IN_PROGRESS_NO_CHANGE') else 1


if __name__ == '__main__':
    sys.exit(main())
