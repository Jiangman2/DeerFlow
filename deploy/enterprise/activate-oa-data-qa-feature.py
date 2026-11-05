"""Roll out the accepted OA/Data QA Gateway image after the committed expansion."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

COMMON = ''
EXPECTED = {}
FRONT = {}
FEATURE = {}
PRELOADS = []
EXPANSION = {}
ACCEPTANCE = {}
SCHEMA_CHILD = ''
FEATURE_CHILD = ''
V2_PREFLIGHT = "import os,json; disabled=os.getenv('OA_POLICY_FLOW_MULTI_RESULT_ENABLED','').strip().lower() not in {'1','true','yes','on'}; print(json.dumps({'status':'PASS' if disabled else 'INCOMPLETE','v2_enabled':not disabled,'provider_requests':0,'configuration_modified':False}))"

exec(COMMON, globals())

CONTROL = '/var/lib/portal-gateway-oa-data-qa-activation-20261022-r1'
DATABASE_FINGERPRINT = 'b0fdaf30f3160cd52abf01520da71a20570c23795938bf73165153083fae9d91'
TARGET = '0021_oa_result_sets'
ACCEPTED = 'GATEWAY_OA_DATA_QA_RUNTIME_ACCEPTED_FRONTEND_AND_OA_PENDING'


def command(args, value=None, raw=False):
    require(args and args[0] in ('get', 'exec', 'patch'), 'COMMAND_FORBIDDEN')
    require((args[0] == 'patch') == (value is not None), 'COMMAND_PAYLOAD_FORBIDDEN')
    if args[0] == 'patch':
        require(args == ['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/dev/stdin', '-o', 'json'],
                'PATCH_TARGET_FORBIDDEN')
    attempts = 3 if args[0] == 'get' else 1
    base = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
            '--request-timeout=' + ('180s' if args[0] == 'exec' else '15s'), '-n', 'deer-flow']
    for attempt in range(attempts):
        try:
            p = subprocess.run(base + args, input=json.dumps(value).encode() if value is not None else None,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=210 if args[0] == 'exec' else 45)
            require(len(p.stdout) <= (65536 if args[0] == 'exec' else 16 * 1024**2), 'KUBERNETES_OUTPUT_LIMIT')
            if args[0] == 'exec' and p.returncode in (1, 124):
                failed = json.loads(p.stdout)
                require(isinstance(failed, dict) and failed.get('status') == 'INCOMPLETE'
                        and failed.get('raw_errors_exported') is False and failed.get('provider_requests') == 0,
                        'CHILD_FAILURE_RECEIPT_INVALID')
                return failed
            require(p.returncode == 0, 'KUBERNETES_COMMAND_FAILED')
            return p.stdout if raw else json.loads(p.stdout)
        except (Refused, subprocess.TimeoutExpired):
            if attempt + 1 == attempts:
                raise
            time.sleep(2)


def receipt(pin):
    path = Path(pin['path'])
    require(os.path.realpath(str(path)) == str(path), 'RECEIPT_SYMLINK')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1
                and st.st_size == pin['bytes'] and 0 < st.st_size <= 65536, 'RECEIPT_METADATA')
        raw = f.read(pin['bytes'] + 1)
    require(hashlib.sha256(raw).hexdigest() == pin['sha256'], 'RECEIPT_HASH')
    return json.loads(raw)


def evidence():
    result = receipt(EXPANSION)
    require(result.get('status') == 'OA_SCHEMA_EXPANSION_ACCEPTED_FEATURE_NOT_DEPLOYED'
            and result.get('schema_revision') == TARGET and result.get('schema_migrated') is True
            and result.get('migration_outcome') == 'committed' and result.get('expansion_acknowledged') is True
            and result.get('old_consumer_compatibility_verified') is True
            and result.get('feature_deployed') is False and result.get('provider_requests') == 0
            and result.get('configuration_metadata_preserved') is True and result.get('frontend_preserved') is True,
            'EXPANSION_NOT_ACCEPTED')
    bindings = {name: {k: pin[k] for k in ('uid', 'generation', 'spec_sha256')} for name, pin in EXPECTED.items()}
    require(result.get('before') == result.get('after') and result.get('after', {}).get('deployments') == bindings
            and result.get('after', {}).get('frontend') == FRONT, 'EXPANSION_BASELINE_CHANGED')
    require(len(result.get('inspect', [])) == 6 and len(result.get('verify', [])) == 6
            and all(row.get('observation', {}).get('status') == 'PASS' for row in result['verify']),
            'EXPANSION_VERIFICATION_INCOMPLETE')
    installed = receipt(ACCEPTANCE)
    checks = installed.get('checks', [])
    require(installed.get('status') == 'OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED'
            and installed.get('production_database_access') is False
            and installed.get('production_workload_modified') is False and installed.get('provider_requests') == 0
            and len(checks) == 1 and checks[0].get('status') == 'PASS'
            and checks[0].get('observation', {}).get('status') == 'INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED'
            and checks[0]['pods'][0]['containers'][0]['imageID'] == FEATURE['candidate_image'],
            'FEATURE_IMAGE_NOT_ACCEPTED')
    require(len(PRELOADS) == 5 and {p['hostname'] for p in PRELOADS} == set(EXPECTED['gateway']['nodes']),
            'PRELOAD_NODE_COVERAGE')
    for row in PRELOADS:
        require(row.get('node_preload_passed') is True and row.get('old_references_preserved') is True
                and row.get('image_import_may_be_partial') is False
                and row.get('expected_node_uid') == EXPECTED['gateway']['nodes'][row['hostname']],
                'PRELOAD_NOT_ACCEPTED')
        roles = row.get('roles', [])
        require(len(roles) == 1 and roles[0].get('manifest_digest') == FEATURE['candidate_image'].split('@')[1]
                and roles[0].get('config_digest') == FEATURE['config_digest']
                and all(roles[0].get(k) == 'PASS' for k in ('phase_a', 'phase_b', 'content_and_unpack', 'cri_visibility')),
                'PRELOAD_IMAGE_CHANGED')
    return result['after']['configuration_metadata']


def pin_for(role, feature=False):
    pin = copy.deepcopy(EXPECTED[role])
    if feature:
        pin.update(candidate_image=FEATURE['candidate_image'], config_digest=FEATURE['config_digest'])
    return pin


def checked_pods(deployment, role, feature=False):
    pin = pin_for(role, feature)
    image = pin['candidate_image']
    return pods_for(deployment, pin, image)


def snapshot(expected_gateway=None):
    deployments = {name: command(['get', 'deployment', name, '-o', 'json']) for name in EXPECTED}
    for name, d in deployments.items():
        target = expected_gateway if name == 'gateway' and expected_gateway is not None else {
            k: EXPECTED[name][k] for k in ('uid', 'generation', 'spec_sha256')}
        require(binding(d) == target and ready(d), 'DEPLOYMENT_BASELINE_CHANGED')
    front = command(['get', 'deployment', 'frontend', '-o', 'json'])
    require(binding(front) == FRONT and ready(front), 'FRONTEND_BASELINE_CHANGED')
    nodes = nodes_gate(deployments)
    services = {name: command(['get', 'service', name, '-o', 'json']) for name in EXPECTED}
    metadata = configuration_metadata(configuration_refs(dict(deployments, frontend=front)))
    return deployments, {'deployments': {n: binding(d) for n, d in deployments.items()},
        'frontend': binding(front), 'node_coverage': nodes, 'configuration_metadata': metadata,
        'services': {n: {'uid': d['metadata']['uid'], 'spec_sha256': digest(d['spec'])} for n, d in services.items()}}


def schema_check(pod, role):
    args = {'action': 'verify', 'pin': EXPECTED[role], 'expected_database_fingerprint': DATABASE_FINGERPRINT}
    value = command(['exec', pod['metadata']['name'], '-c', pod['spec']['containers'][0]['name'], '--',
        '/app/backend/.venv/bin/python', '-I', '-B', '-c', SCHEMA_CHILD, json.dumps(args)])
    require(value.get('status') == 'PASS' and value.get('revisions') == [TARGET]
            and value.get('database_readonly') is True and value.get('compatibility_verified') is True
            and value.get('database_fingerprint') == DATABASE_FINGERPRINT
            and value.get('source_hashes') == EXPECTED[role]['sources'], 'COMPATIBILITY_DATABASE_CHECK_FAILED')
    return {'status': 'PASS', 'schema_revision': TARGET, 'database_readonly': True,
            'database_fingerprint': DATABASE_FINGERPRINT}


def feature_check(pod, action):
    args = {'action': action, 'pin': FEATURE, 'expected_database_fingerprint': DATABASE_FINGERPRINT}
    value = command(['exec', pod['metadata']['name'], '-c', pod['spec']['containers'][0]['name'], '--',
        '/app/backend/.venv/bin/python', '-I', '-B', '-c', FEATURE_CHILD, json.dumps(args)])
    require(value.get('status') == 'PASS' and value.get('provider_requests') == 0
            and value.get('oa_callback_requests') == 0 and value.get('configuration_modified') is False,
            'FEATURE_RUNTIME_CHECK_FAILED')
    if action == 'runtime':
        require(value.get('sources_verified') is True and value.get('v2_enabled') is False
                and value.get('scheduler_budget') == 300 and value.get('oa_budget') == 1000
                and value.get('local_health_http_status') == 200, 'FEATURE_RUNTIME_CONTRACT_CHANGED')
    else:
        require(value.get('database_readonly') is True and value.get('revisions') == [TARGET]
                and value.get('database_fingerprint') == DATABASE_FINGERPRINT, 'FEATURE_DATABASE_CHECK_FAILED')
    return value


def v2_preflight(pod):
    value = command(['exec', pod['metadata']['name'], '-c', pod['spec']['containers'][0]['name'], '--',
                     '/app/backend/.venv/bin/python', '-I', '-B', '-c', V2_PREFLIGHT])
    require(value == {'status': 'PASS', 'v2_enabled': False, 'provider_requests': 0, 'configuration_modified': False},
            'V2_MUST_BE_DISABLED_BEFORE_ROLLOUT')


def save(report):
    directory = Path(CONTROL)
    temporary = directory / 'REPORT.tmp'
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f, sort_keys=True)
        f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(str(temporary), str(directory / 'REPORT.json'))
    fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def claim(report):
    parent = Path(CONTROL).parent
    require(os.path.realpath(str(parent)) == str(parent), 'CONTROL_PARENT_SYMLINK')
    st = parent.stat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'UNSAFE_CONTROL_PARENT')
    os.mkdir(CONTROL, 0o700)
    report['once_claimed'] = True
    st = Path(CONTROL).lstat()
    require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and stat.S_IMODE(st.st_mode) == 0o700, 'UNSAFE_CONTROL_DIRECTORY')
    fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def plan(deployment):
    pin = EXPECTED['gateway']
    require(binding(deployment) == {k: pin[k] for k in ('uid', 'generation', 'spec_sha256')}
            and ready(deployment), 'GATEWAY_BASELINE_CHANGED')
    containers = deployment['spec']['template']['spec']['containers']
    require(len(containers) == 1 and containers[0]['image'] == pin['candidate_image'], 'GATEWAY_CONTAINER_CHANGED')
    spec = copy.deepcopy(deployment['spec'])
    spec['template']['spec']['containers'][0]['image'] = FEATURE['candidate_image']
    return [{'op': 'test', 'path': '/metadata/uid', 'value': deployment['metadata']['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': deployment['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
            {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': FEATURE['candidate_image']}], spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate-gateway-oa-data-qa-once', action='store_true', required=True)
    parser.add_argument('--allow-interrupt-active-tasks', action='store_true', required=True)
    parser.parse_args()
    report = {'schema': 'portal-gateway-oa-data-qa-activation.v1', 'status': 'INCOMPLETE', 'once_claimed': False,
              'patch_requested': False, 'patch_may_have_applied': False, 'patch_request_count': 0,
              'patch_acknowledged': False, 'production_workload_mutation': False, 'schema_migration_requested': False,
              'schema_revision': TARGET, 'automatic_retry': False, 'automatic_rollback': False,
              'active_task_interruption_operator_authorized': True, 'tasks_drained_operator_attested': False,
              'task_policy': 'ALLOW_ACTIVE_TASK_INTERRUPTION', 'provider_requests': 0, 'oa_callback_requests': 0,
              'frontend_acceptance_pending': True, 'oa_business_acceptance_pending': True,
              'requested_v2_enabled': False, 'v2_enabled': None,
              'runtime': [], 'preflight_compatibility': [], 'acceptance_receipts': {'expansion': EXPANSION, 'image': ACCEPTANCE}}
    patch_dispatched = False
    try:
        require(os.geteuid() == 0 and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
        require(not os.path.lexists(CONTROL), 'ACTIVATION_ALREADY_CLAIMED')
        require(bool(FEATURE_CHILD) and bool(SCHEMA_CHILD), 'PACKAGE_INCOMPLETE')
        accepted_configuration = evidence()
        deployments, before = snapshot()
        require(before['configuration_metadata'] == accepted_configuration, 'CONFIGURATION_CHANGED_SINCE_EXPANSION')
        old = {role: checked_pods(d, role) for role, d in deployments.items()}
        for role in EXPECTED:
            endpoints(role, old[role])
        report['before'] = before
        for pod in old['gateway']:
            v2_preflight(pod)
        report['preflight_v2_disabled_pods'] = len(old['gateway'])
        for role in ('gateway', 'ppt-master-runner'):
            result = schema_check(old[role][0], role)
            report['preflight_compatibility'].append({'role': role, 'observation': result})
        fresh, unchanged = snapshot()
        require(unchanged == before, 'LIVE_STATE_CHANGED_DURING_PREFLIGHT')
        require(all(identities(checked_pods(fresh[role], role)) == identities(old[role]) for role in EXPECTED),
                'CONTAINERS_CHANGED_DURING_PREFLIGHT')
        patch, spec = plan(fresh['gateway'])
        claim(report)
        report.update(patch_requested=True, patch_may_have_applied=True, patch_request_count=1,
                      production_workload_mutation=None, expected_spec_sha256=digest(spec))
        save(report)
        patch_dispatched = True
        acknowledged = command(['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/dev/stdin', '-o', 'json'], patch)
        require(acknowledged['metadata']['uid'] == EXPECTED['gateway']['uid'] and acknowledged['spec'] == spec
                and acknowledged['metadata']['generation'] == EXPECTED['gateway']['generation'] + 1,
                'PATCH_ACK_CHANGED')
        after_gateway = binding(acknowledged)
        report.update(patch_acknowledged=True, production_workload_mutation=True, gateway_after=after_gateway)
        save(report)
        deadline, last_progress = time.monotonic() + 4200, 0
        while True:
            gateway = command(['get', 'deployment', 'gateway', '-o', 'json'])
            require(binding(gateway) == after_gateway, 'GATEWAY_CHANGED_DURING_ROLLOUT')
            runner = command(['get', 'deployment', 'ppt-master-runner', '-o', 'json'])
            require(binding(runner) == before['deployments']['ppt-master-runner'] and ready(runner), 'RUNNER_CHANGED')
            report['node_coverage'] = nodes_gate(dict(deployments, gateway=gateway))
            if ready(gateway):
                try:
                    pods = checked_pods(gateway, 'gateway', feature=True)
                    endpoints('gateway', pods)
                    break
                except Refused as exc:
                    require(str(exc) in ('POD_COUNT_CHANGED', 'POD_NOT_READY', 'SERVICE_ENDPOINTS_NOT_READY'), str(exc))
            require(time.monotonic() < deadline, 'ROLLOUT_DEADLINE')
            if time.monotonic() - last_progress >= 20:
                print(json.dumps({'phase': 'OA_DATA_QA_GATEWAY_ROLLOUT', 'desired': gateway['spec']['replicas'],
                    'updated': gateway.get('status', {}).get('updatedReplicas', 0),
                    'ready': gateway.get('status', {}).get('readyReplicas', 0)}), flush=True)
                last_progress = time.monotonic()
            time.sleep(10)
        for pod in pods:
            report['runtime'].append({'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
                                      'observation': feature_check(pod, 'runtime')})
            save(report)
            if len(report['runtime']) % 10 == 0:
                print(json.dumps({'phase': 'OA_DATA_QA_RUNTIME_ACCEPTANCE', 'accepted': len(report['runtime']), 'total': len(pods)}), flush=True)
        report['database_after'] = feature_check(pods[0], 'database')
        final, after = snapshot(after_gateway)
        require(identities(checked_pods(final['gateway'], 'gateway', feature=True)) == identities(pods), 'GATEWAY_CONTAINERS_CHANGED')
        require(identities(checked_pods(final['ppt-master-runner'], 'ppt-master-runner')) == identities(old['ppt-master-runner']),
                'RUNNER_CONTAINERS_CHANGED')
        report['runner_compatibility_after'] = schema_check(old['ppt-master-runner'][0], 'ppt-master-runner')
        for role in EXPECTED:
            endpoints(role, pods if role == 'gateway' else old[role])
        require(all(after[k] == before[k] for k in ('frontend', 'configuration_metadata', 'services', 'node_coverage')),
                'UNRELATED_STATE_CHANGED')
        report.update(status=ACCEPTED, after=after, frontend_preserved=True, runner_preserved=True,
                      configuration_metadata_preserved=True, gateway_other_spec_preserved=True,
                      runtime_accepted=True, feature_image_deployed=True, v2_enabled=False)
    except Exception as exc:
        if not patch_dispatched:
            report.update(patch_requested=False, patch_may_have_applied=False, patch_request_count=0,
                          production_workload_mutation=False)
        code = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        report['error_code'] = code if re.fullmatch(r'[A-Za-z0-9_]{1,96}', code) else 'UNCLASSIFIED_FAILURE'
        if isinstance(exc, Refused) and exc.observation is not None:
            report['node_gate_failure'] = exc.observation
    if report['once_claimed']:
        try:
            save(report)
        except Exception:
            report.update(status='INCOMPLETE', report_update_failed=True)
    raw = (Path(CONTROL) / 'REPORT.json').read_bytes() if report['once_claimed'] and (Path(CONTROL) / 'REPORT.json').is_file() else None
    summary = {k: v for k, v in report.items() if k not in ('before', 'after', 'runtime')}
    summary.update(accepted_pod_count=len(report['runtime']), report_path=CONTROL + '/REPORT.json' if raw else None,
                   report_bytes=len(raw) if raw else None, report_sha256=hashlib.sha256(raw).hexdigest() if raw else None,
                   preflight_retry_available=not report['once_claimed'])
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if report['status'] == ACCEPTED else 1


if __name__ == '__main__':
    raise SystemExit(main())
