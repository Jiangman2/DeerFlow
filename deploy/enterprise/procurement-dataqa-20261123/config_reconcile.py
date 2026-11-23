"""Clone the mounted config object; change only Data QA connection settings."""
import copy
import hashlib
import json

CONFIG_PATH = '/app/backend/config.yaml'


def binding(deployment):
    spec = deployment['spec']['template']['spec']
    containers = [c for c in spec['containers'] if c['name'] == 'gateway']
    if len(containers) != 1:
        raise RuntimeError('GATEWAY_CONTAINER_NOT_UNIQUE')
    mounts = [m for m in containers[0].get('volumeMounts', []) if m['mountPath'] == CONFIG_PATH]
    if len(mounts) != 1 or not mounts[0].get('subPath') or mounts[0].get('subPathExpr'):
        raise RuntimeError('CONFIG_FILE_SUBPATH_MOUNT_REQUIRED')
    mount = mounts[0]
    volumes = [v for v in spec.get('volumes', []) if v['name'] == mount['name']]
    if len(volumes) != 1 or 'configMap' not in volumes[0]:
        raise RuntimeError('CONFIGMAP_VOLUME_REQUIRED')
    volume = volumes[0]
    entries = volume['configMap'].get('items')
    keys = [e['key'] for e in entries if e['path'] == mount['subPath']] if entries is not None else [mount['subPath']]
    if len(keys) != 1:
        raise RuntimeError('CONFIG_KEY_NOT_UNIQUE')
    return mount, volume, keys[0]


def prepare_config(root, record, deployment, pod, kube):
    _, volume, key = binding(deployment)
    before = kube(record, 'config-before', ['get', 'configmap', volume['configMap']['name'], '-o', 'json'])
    if before.get('binaryData') or key not in before.get('data', {}):
        raise RuntimeError('CONFIGMAP_TEXT_KEY_REQUIRED')
    raw = before['data'][key]
    expected = hashlib.sha256(raw.encode()).hexdigest()
    transformed = kube(record, 'config-candidate', ['exec', '-i', pod, '-c', 'gateway', '--',
        '/app/backend/.venv/bin/python', '-c', (root / 'reconcile_runtime.py').read_text(encoding='utf-8')],
        json.dumps({'source': raw, 'before_sha256': expected}, ensure_ascii=False).encode())
    if transformed['before_sha256'] != expected or not transformed['validated']:
        raise RuntimeError('CONFIG_TRANSFORMATION_NOT_VERIFIED')
    candidate = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {
        'name': 'gateway-dataqa-jeg6ud59-' + transformed['after_sha256'][:12], 'namespace': 'deer-flow'},
        'immutable': True, 'data': copy.deepcopy(before['data'])}
    candidate['data'][key] = transformed['source']
    if hashlib.sha256(candidate['data'][key].encode()).hexdigest() != transformed['after_sha256']:
        raise RuntimeError('CONFIG_CANDIDATE_HASH_MISMATCH')
    return candidate, {k: transformed[k] for k in ('before_sha256', 'after_sha256', 'validated')}


def add_config_patch(deployment, patch, name):
    mount, volume, _ = binding(deployment)
    result = copy.deepcopy(patch)
    volumes = next(op['value'] for op in result if op['path'] == '/spec/template/spec/volumes')
    mounts = next(op['value'] for op in result if op['path'].endswith('/volumeMounts'))
    if any(v['name'] == name for v in volumes):
        raise RuntimeError('CONFIG_CLONE_VOLUME_ALREADY_PRESENT')
    new_volume = copy.deepcopy(volume)
    new_volume['name'] = name
    new_volume['configMap']['name'] = name
    volumes.append(new_volume)
    matches = [m for m in mounts if m['mountPath'] == CONFIG_PATH]
    if len(matches) != 1 or matches[0] != mount:
        raise RuntimeError('CONFIG_MOUNT_CHANGED_DURING_PATCH')
    matches[0]['name'] = name
    return result
