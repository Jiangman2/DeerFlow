"""Enable the verified optional V4.1 model with one Gateway-only config rollout."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import time
import types

LIBRARY = None  # Packaged bounded reader; no provider-check main is executed.
CHILD = None
CONTROL = '/var/lib/portal-v41-glm-activation-20261029-r1'
RECEIPT = '/var/lib/portal-v41-glm-check-20261029-r1/REPORT.json'
RECEIPT_SHA = '9fb9f3e11de46b4f2441abd1e317e349537553eba963515f9bec7c0c2a0b58df'
BASE_CONFIG_SHA = 'dbde51e71421be4ee4544684faa77baf18a522ecd87db663b2bcb4280589c36b'
PROTECTED = ('frontend', 'ppt-master-runner', 'nginx-security-heai-20261012-r1')
BASELINES = {
    'gateway': {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 389,
                'spec_sha256': '32ff8cd158844287f0f34d2d9d48d2dda816922dc2dc0082bfd5f97ca77ee6f3'},
    'frontend': {'uid': 'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 'generation': 231,
                 'spec_sha256': 'a5842fa6f280bd331de31908de1f0d4914bf38133a312be0a9e747f76d8d3f14'},
    'ppt-master-runner': {'uid': '4115f178-0599-445e-89c3-9d9452ddc26a', 'generation': 55,
                         'spec_sha256': 'c117a0ddee39a06ad4563359da502302e3936f15f869de6111cca72ba4a137cd'},
    'nginx-security-heai-20261012-r1': {'uid': 'ce96a382-140e-4baf-9a85-d623b7df6e3d', 'generation': 2,
                                     'spec_sha256': 'f065f125b1f8417010f3108ae682de3739c4a267040e6d803f26ce78363f6141'},
}


class Refused(RuntimeError): pass


def require(ok, code):
    if not ok: raise Refused(code)


def digest(raw): return hashlib.sha256(raw).hexdigest()


def packed(value): return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def binding(obj):
    return {'uid': obj['metadata']['uid'], 'generation': obj['metadata'].get('generation'),
            'spec_sha256': digest(packed(obj['spec']))}


def cm_binding(cm):
    return {'uid': cm['metadata']['uid'], 'payload_sha256': digest(packed({
        k: cm.get(k) for k in ('data', 'binaryData', 'immutable')}))}


def config_volume(deployment):
    spec = deployment['spec']['template']['spec']
    containers = [c for c in spec['containers'] if c['name'] == 'gateway']
    require(len(containers) == 1, 'GATEWAY_CONTAINER_CHANGED')
    mounts = [m for m in containers[0].get('volumeMounts', []) if m['mountPath'] == '/app/backend/config.yaml']
    require(len(mounts) == 1 and mounts[0].get('readOnly') is True and
            mounts[0].get('subPath') == 'config.yaml', 'CONFIG_MOUNT_CHANGED')
    name = mounts[0]['name']
    rows = [(i, v) for i, v in enumerate(spec['volumes']) if v['name'] == name]
    require(len(rows) == 1 and 'configMap' in rows[0][1], 'CONFIG_VOLUME_CHANGED')
    require(not rows[0][1]['configMap'].get('optional'), 'OPTIONAL_CONFIG_REFUSED')
    items = rows[0][1]['configMap'].get('items')
    if items is not None:
        require(any(i.get('key') == i.get('path') == 'config.yaml' for i in items), 'CONFIG_ITEM_CHANGED')
    return rows[0][0], rows[0][1]['configMap']['name']


def candidate_cm(old, raw):
    text = raw.decode('utf-8')
    require(len(raw) <= 1048576, 'CANDIDATE_SIZE')
    data = copy.deepcopy(old.get('data', {})); data['config.yaml'] = text
    body = {'apiVersion': 'v1', 'kind': 'ConfigMap', 'immutable': True, 'data': data}
    if 'binaryData' in old: body['binaryData'] = copy.deepcopy(old['binaryData'])
    name = 'deer-flow-config-v41-glm-20261029-' + digest(packed(body))[:12]
    body['metadata'] = {'name': name, 'namespace': 'deer-flow',
                        'labels': {'app.kubernetes.io/managed-by': 'portal-v41-glm-activation'},
                        'annotations': {'portal.cnooc/source-configmap-uid': old['metadata']['uid'],
                                        'portal.cnooc/provider-receipt-sha256': RECEIPT_SHA}}
    return body


def plan(deployment, name):
    index, _ = config_volume(deployment)
    after = copy.deepcopy(deployment['spec'])
    after['template']['spec']['volumes'][index]['configMap']['name'] = name
    tests = [{'op': 'test', 'path': '/apiVersion', 'value': 'apps/v1'},
             {'op': 'test', 'path': '/kind', 'value': 'Deployment'}]
    for key in ('uid', 'resourceVersion', 'generation'):
        tests.append({'op': 'test', 'path': '/metadata/' + key, 'value': deployment['metadata'][key]})
    tests.append({'op': 'test', 'path': '/spec', 'value': deployment['spec']})
    tests.append({'op': 'replace', 'path': '/spec/template/spec/volumes/%d/configMap/name' % index, 'value': name})
    return after, tests


def stable(deployment, generation):
    s = deployment.get('status', {})
    return (deployment['spec'].get('replicas') == 70 and s.get('observedGeneration') == generation and
            all(s.get(k, 0) == 70 for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')) and
            s.get('terminatingReplicas', 0) == 0)


def private_write(name, raw):
    path = Path(CONTROL) / name
    with path.open('xb') as handle:
        os.chmod(str(path), 0o600); handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def save(result):
    if not result['once_claimed']: return
    path = Path(CONTROL) / 'REPORT.tmp'
    with path.open('wb') as handle:
        os.chmod(str(path), 0o600); handle.write(packed(result) + b'\n'); handle.flush(); os.fsync(handle.fileno())
    os.replace(str(path), str(Path(CONTROL) / 'REPORT.json'))
    fd = os.open(CONTROL, os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


def read_receipt(lib):
    fd = os.open(RECEIPT, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022 and s.st_size == 7445,
                'PROVIDER_RECEIPT_METADATA_CHANGED')
        with os.fdopen(fd, 'rb', closefd=False) as handle: raw = handle.read(7446)
    finally: os.close(fd)
    require(digest(raw) == RECEIPT_SHA, 'PROVIDER_RECEIPT_CHANGED')
    result = json.loads(raw)
    require(result['status'] == 'V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED' and lib.accepted(result['observation']),
            'PROVIDER_RECEIPT_NOT_ACCEPTED')
    return {'path': RECEIPT, 'bytes': len(raw), 'sha256': RECEIPT_SHA}


def probe(reader, lib, pod, mode, sha):
    raw = reader.read(lib.BASE + ['exec', '-i', pod['metadata']['name'], '-c', 'gateway', '--',
                      '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', CHILD],
                      data=packed({'mode': mode, 'expected_sha256': sha}), timeout=75,
                      cap=2097152 if mode == 'prepare' else 65536, step='config-probe-' + mode)
    result = json.loads(raw)
    require(result.get('status') == 'PASS' and result.get('provider_requests') == 0 and
            result.get('credential_source') == 'GLM_5_3_FLASH_API_KEY' and
            result.get('default_model') == 'deepseek-v4-flash', 'CONFIG_PROBE_NOT_ACCEPTED')
    return result


def patch_once(reader, lib, patch, expected, result):
    result.update(patch_requested=True, patch_may_have_applied=True, patch_request_count=1, v41_enabled=None)
    save(result)
    try:
        reader.read(lib.BASE + ['patch', 'deployment', 'gateway', '--type=json',
                    '--patch-file=' + str(Path(CONTROL) / 'patch.private.json'), '-o', 'name'],
                    timeout=30, cap=65536, step='gateway-config-patch-once')
        result['patch_command_acknowledged'] = True
    except Exception:
        # Resolve an uncertain reply with reads. Never submit the mutation again.
        result['patch_command_acknowledged'] = False
    observed = reader.get('deployment', 'gateway')
    require(observed['metadata']['uid'] == BASELINES['gateway']['uid'] and observed['spec'] == expected and
            observed['metadata']['generation'] == BASELINES['gateway']['generation'] + 1, 'PATCH_READBACK_NOT_ACCEPTED')
    result['patch_readback_accepted'] = True; save(result)
    return observed


def verify_endpoints(reader, lib, uids):
    raw = reader.read(lib.BASE + ['get', 'endpointslices', '-l', 'kubernetes.io/service-name=gateway',
                                 '--show-managed-fields=false', '-o', 'json'], cap=1048576, step='gateway-endpoints')
    actual = set()
    for sl in json.loads(raw)['items']:
        for ep in sl.get('endpoints', []):
            conditions = ep.get('conditions', {})
            require(conditions.get('ready') is True and conditions.get('terminating') is not True,
                    'ENDPOINT_NOT_READY')
            ref = ep.get('targetRef', {})
            require(ref.get('kind') == 'Pod' and ref.get('uid') in uids, 'ENDPOINT_OWNER_CHANGED')
            actual.add(ref['uid'])
    require(actual == uids, 'ENDPOINT_SET_CHANGED')


def execute(reader, lib, result):
    result['provider_receipt'] = read_receipt(lib)
    before = {name: reader.get('deployment', name) for name in BASELINES}
    require({n: binding(o) for n, o in before.items()} == BASELINES, 'DEPLOYMENT_BASELINE_CHANGED')
    gateway = before['gateway']
    require(stable(gateway, 389), 'GATEWAY_NOT_STABLE')
    _, old_name = config_volume(gateway)
    old_cm = reader.resource('configmap', old_name)
    old_raw = old_cm.get('data', {}).get('config.yaml', '').encode('utf-8')
    require(digest(old_raw) == BASE_CONFIG_SHA, 'SOURCE_CONFIGMAP_CHANGED')
    services = {n: reader.resource('service', n) for n in ('gateway', 'nginx', 'frontend')}
    sample = lib.select_pod(reader, gateway)
    print(json.dumps({'phase': 'VALIDATE_OPTIONAL_V41_CONFIGURATION', 'provider_requests': 0}), flush=True)
    prepared = probe(reader, lib, sample, 'prepare', BASE_CONFIG_SHA)
    raw = base64.b64decode(prepared.pop('private_candidate_b64'), validate=True)
    require(digest(raw) == prepared['config_sha256'], 'CANDIDATE_DIGEST_CHANGED')
    require(lib.identity(reader.get('pod', sample['metadata']['name'])) == lib.identity(sample), 'SAMPLE_CHANGED')
    cm = candidate_cm(old_cm, raw); name = cm['metadata']['name']
    expected, patch = plan(gateway, name)
    # All preparation above is read-only; the durable one-use claim precedes writes.
    os.mkdir(CONTROL, 0o700); result['once_claimed'] = True
    result.update(before=BASELINES['gateway'], candidate_configmap=name, source_configmap=old_name,
                  source_configmap_binding=cm_binding(old_cm), candidate_config_sha256=digest(raw),
                  prepared=prepared, sample_before=lib.identity(sample))
    save(result)
    private_write('before.private.json', packed({'deployments': before, 'services': services, 'configmap': old_cm}))
    private_write('candidate.private.json', packed(cm))
    private_write('patch.private.json', packed(patch))
    private_write('planned-spec.private.json', packed(expected))
    # Server admission must accept precisely this one-field template mutation.
    dry = json.loads(reader.read(lib.BASE + ['patch', 'deployment', 'gateway', '--type=json',
                     '--patch-file=' + str(Path(CONTROL) / 'patch.private.json'), '--dry-run=server', '-o', 'json'],
                     timeout=30, step='gateway-server-dryrun'))
    require(dry['spec'] == expected, 'ADMISSION_MUTATES_PLAN')
    result['server_dryrun_accepted'] = True
    result['configmap_create_requested'] = True; save(result)
    reader.read(lib.BASE + ['create', '-f', str(Path(CONTROL) / 'candidate.private.json'), '-o', 'name'],
                timeout=30, cap=65536, step='create-immutable-configmap-once')
    created = reader.resource('configmap', name)
    require(created.get('immutable') is True and created.get('data') == cm['data'] and
            created.get('binaryData') == cm.get('binaryData'), 'CANDIDATE_CONFIGMAP_READBACK_FAILED')
    result['candidate_configmap_uid'] = created['metadata']['uid']
    require(cm_binding(reader.resource('configmap', old_name)) == cm_binding(old_cm), 'SOURCE_CONFIGMAP_DRIFT')
    require({n: binding(reader.get('deployment', n)) for n in BASELINES} == BASELINES, 'PREPATCH_DEPLOYMENT_DRIFT')
    print(json.dumps({'phase': 'ROLL_GATEWAY_CONFIGURATION', 'desired_replicas': 70,
                      'default_model_changed': False, 'image_changed': False}), flush=True)
    after = patch_once(reader, lib, patch, expected, result)
    generation = after['metadata']['generation']
    deadline = time.monotonic() + 4200
    while True:
        require(after['spec'] == expected and after['metadata']['uid'] == gateway['metadata']['uid'] and
                after['metadata']['generation'] == generation, 'ROLLOUT_DRIFT')
        result['last_counts'] = {k: after.get('status', {}).get(k, 0) for k in
            ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'terminatingReplicas')}
        save(result)
        print(json.dumps({'phase': 'OBSERVE_GATEWAY', 'counts': result['last_counts']}), flush=True)
        if stable(after, generation): break
        require(time.monotonic() < deadline, 'ROLLOUT_TIMEOUT')
        time.sleep(20); after = reader.get('deployment', 'gateway')
    rows = reader.pod_index(after)
    require(len(rows) == 70, 'RUNNING_POD_COUNT_CHANGED')
    sets = {}; accepted = []
    result['accepted_pods'] = accepted
    for i, row in enumerate(rows):
        pod = reader.get('pod', row['name'])
        require(pod['metadata']['uid'] == row['uid'], 'POD_IDENTITY_CHANGED')
        owners = [o for o in pod['metadata'].get('ownerReferences', []) if o.get('controller')]
        require(len(owners) == 1 and owners[0]['kind'] == 'ReplicaSet', 'POD_OWNER_CHANGED')
        owner = owners[0]
        if owner['uid'] not in sets: sets[owner['uid']] = reader.get('replicaset', owner['name'])
        rs = sets[owner['uid']]
        require(rs['metadata']['uid'] == owner['uid'] and lib.owned_ready_pods(after, [rs], [pod]) == [pod],
                'POD_NOT_READY_OR_NOT_OWNED')
        wrapped = {'spec': {'template': {'spec': pod['spec']}}}
        require(config_volume(wrapped)[1] == name, 'POD_CONFIGMAP_CHANGED')
        observation = probe(reader, lib, pod, 'runtime', digest(raw))
        require(observation.get('config_sha256') == digest(raw), 'RUNTIME_CONFIG_CHANGED')
        require(lib.identity(reader.get('pod', row['name'])) == lib.identity(pod), 'RUNTIME_POD_CHANGED')
        accepted.append({'pod': row['name'], 'uid': row['uid'], 'node': pod['spec']['nodeName'],
                         'config_sha256': digest(raw), 'status': 'PASS'})
        save(result)
        print(json.dumps({'phase': 'VERIFY_GATEWAY_MODEL_CONFIG_AND_HEALTH', 'checked': i + 1, 'total': 70}), flush=True)
    final = reader.get('deployment', 'gateway')
    require(final['spec'] == expected and stable(final, generation), 'FINAL_GATEWAY_CHANGED')
    final_rows = reader.pod_index(final)
    require(final_rows == rows, 'FINAL_POD_SET_CHANGED')
    verify_endpoints(reader, lib, {r['uid'] for r in accepted})
    require(all(binding(reader.get('deployment', n)) == BASELINES[n] for n in PROTECTED), 'PROTECTED_DEPLOYMENT_CHANGED')
    require(all(binding(reader.resource('service', n)) == binding(s) for n, s in services.items()), 'SERVICE_CHANGED')
    require(cm_binding(reader.resource('configmap', old_name)) == cm_binding(old_cm), 'SOURCE_CONFIGMAP_CHANGED')
    require(cm_binding(reader.resource('configmap', name)) == cm_binding(created), 'CANDIDATE_CONFIGMAP_CHANGED')
    result.update(status='V41_OPTIONAL_MODEL_RUNTIME_ACCEPTED_BROWSER_PENDING', after=binding(final),
                  runtime_accepted=True, v41_enabled=True, accepted_pod_count=len(accepted),
                  protected_deployments_preserved=True, source_configmap_preserved=True, services_preserved=True,
                  browser_acceptance_pending=True, application_database_migration_requested=False)
    save(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-v41-optional-once', action='store_true', required=True); parser.parse_args()
    result = dict(schema='portal-v41-glm-activation.v1', status='INCOMPLETE', once_claimed=False,
                  automatic_retry=False, automatic_rollback=False, provider_requests=0, secret_api_reads=0,
                  credentials_exported=False, database_access_by_tool=False, default_model_changed=False,
                  image_changed=False, other_workload_mutation=False, patch_requested=False,
                  patch_may_have_applied=False, patch_request_count=0, configmap_create_requested=False,
                  active_task_interruption_operator_authorized=True, v41_enabled=False)
    try:
        require(socket.gethostname().split('.')[0] == 'datax-01' and os.geteuid() == 0, 'WRONG_HOST_OR_UID')
        require(isinstance(LIBRARY, str) and isinstance(CHILD, str), 'PACKAGED_TOOL_REQUIRED')
        require(not os.path.lexists(CONTROL), 'ONCE_ALREADY_CLAIMED')
        os.umask(0o077)
        lib = types.ModuleType('sealed_reader'); exec(compile(LIBRARY, '<sealed-reader>', 'exec'), lib.__dict__)
        class API(lib.Reader):
            def resource(self, kind, name):
                require(kind in ('service', 'configmap') and lib.valid_name(name), 'NAMED_RESOURCE_REQUIRED')
                return json.loads(self.read(lib.BASE + ['get', kind, name, '--show-managed-fields=false', '-o', 'json'],
                                           step='get-' + kind + ':' + name))
        reader = API(); result['commands'] = reader.observations
        execute(reader, lib, result)
    except Exception as exc:
        result.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) or
                      (type(exc).__name__ == 'Refused') else 'ACTIVATION_FAILED', raw_errors_exported=False)
    save(result)
    if result['once_claimed']:
        raw = (Path(CONTROL) / 'REPORT.json').read_bytes()
        result.update(report_path=CONTROL + '/REPORT.json', report_bytes=len(raw), report_sha256=digest(raw))
    # Failed post-patch checks leave v41_enabled unconfirmed, not proof of disabled state.
    if result['patch_may_have_applied'] and not result.get('runtime_accepted'):
        result['v41_enablement_requires_readback'] = True
    public = {k: v for k, v in result.items() if k not in ('commands', 'accepted_pods')}
    public['commands_executed'] = len(result.get('commands', []))
    if result.get('commands') and not result.get('runtime_accepted'):
        public['last_command'] = result['commands'][-1]
    print(json.dumps(public, sort_keys=True))
    return 0 if result.get('runtime_accepted') else 1


if __name__ == '__main__': raise SystemExit(main())
