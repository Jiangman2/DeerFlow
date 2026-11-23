"""Non-secret Deployment patch plus stdin-only runtime credential creation."""
import copy
import base64
import hashlib
import json
import re
import subprocess

VALUES = {
    'APPFORGE_DATA_QA_API_URL': 'http://10.77.124.142:9080',
    'APPFORGE_DATA_QA_APP_ID': 'app-jeg6ud59',
    'APPFORGE_DATA_QA_CONVERSATION_PREFIX': 'SquareWorkflow_',
}
KEY = 'APPFORGE_DATA_QA_API_KEY'


def entries(secret):
    return [*({'name': k, 'value': v} for k, v in VALUES.items()),
            {'name': KEY, 'valueFrom': {'secretKeyRef': {'name': secret, 'key': 'api-key'}}}]


def updated_env(container, secret):
    env = container.get('env', [])
    changed = {*VALUES, KEY}
    if any(sum(e['name'] == name for e in env) > 1 for name in changed):
        raise RuntimeError('DUPLICATE_DATAQA_ENV')
    result = copy.deepcopy(env)
    for entry in entries(secret):
        index = next((i for i, e in enumerate(result) if e['name'] == entry['name']), None)
        if index is None:
            result.append(entry)
        else:
            result[index] = entry
    return result


def add_dataqa_patch(deployment, patch, secret):
    containers = deployment['spec']['template']['spec']['containers']
    index = next(i for i, c in enumerate(containers) if c['name'] == 'gateway')
    container = containers[index]
    updated_env(container, secret)  # reject duplicate entries
    path = f'/spec/template/spec/containers/{index}/env'
    if 'env' not in container:
        return patch + [{'op': 'add', 'path': path, 'value': entries(secret)}]
    operations = list(patch)
    for entry in entries(secret):
        position = next((i for i, e in enumerate(container['env']) if e['name'] == entry['name']), None)
        operations.append({'op': 'add' if position is None else 'replace',
                           'path': path + ('/-' if position is None else '/' + str(position)), 'value': entry})
    return operations


def validate_planned(before, after, secret):
    expected = copy.deepcopy(before)
    expected['env'] = updated_env(before, secret)
    if {k: v for k, v in expected.items() if k != 'volumeMounts'} != {k: v for k, v in after.items() if k != 'volumeMounts'}:
        raise RuntimeError('UNRELATED_CONTAINER_SETTING_CHANGED')


def target_proof(before, key_sha):
    result = copy.deepcopy(before)
    result.update(api_url=VALUES['APPFORGE_DATA_QA_API_URL'], app_id=VALUES['APPFORGE_DATA_QA_APP_ID'],
                  conversation_prefix=VALUES['APPFORGE_DATA_QA_CONVERSATION_PREFIX'], key_sha256=key_sha)
    return result


def secret_name_for(record):
    return 'dataqa-jeg6ud59-' + hashlib.sha256(str(record).encode()).hexdigest()[:16]


def create_key_secret(kube, name, key, *, record=None):
    if len(name) > 63 or re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', name) is None:
        raise RuntimeError('INVALID_DATAQA_SECRET_NAME')
    # No credential in argv, local package, console or command output log.
    obj = {'apiVersion': 'v1', 'kind': 'Secret', 'metadata': {'name': name, 'namespace': 'deer-flow'},
           'immutable': True, 'type': 'Opaque', 'stringData': {'api-key': key}}
    result = subprocess.run(kube + ['create', '-f', '-', '-o', 'name'],
                            input=json.dumps(obj).encode(), capture_output=True, timeout=60)
    if result.returncode:
        if record is not None:
            detail = result.stderr.decode('utf-8', errors='replace')
            for value in (key, base64.b64encode(key.encode()).decode()):
                if value:
                    detail = detail.replace(value, '[REDACTED]')
            with (record / 'secret-create.PRIVATE.err').open('w', encoding='utf-8') as stream:
                stream.write(detail)
        raise RuntimeError('DATAQA_SECRET_CREATE_FAILED_NO_DEPLOYMENT_PATCH')
