"""Enable OA machine APIs, explicit v2 results, and existing HMAC callback."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import types

COMMON = ''
BASELINE_CONFIG = {}
RECEIPTS = {}
CONTROL = '/var/lib/portal-oa-v2-integration-enable-20261023-r1'
OVERRIDES = [{'name': 'OA_POLICY_FLOW_ENABLED', 'value': 'true'},
             {'name': 'OA_POLICY_FLOW_MULTI_RESULT_ENABLED', 'value': 'true'},
             {'name': 'OA_POLICY_FLOW_CALLBACK_ENABLED', 'value': 'true'}]
CALLBACK_HEAD = '''import httpx
    try:
        with httpx.Client(verify=config.callback_ca_bundle or True, trust_env=False,
                          follow_redirects=False, timeout=10.0) as client:
            with client.stream('HEAD', config.callback_url) as response:
                result['callback_transport'] = {'http_status': response.status_code,
                    'scope': 'UNAUTHENTICATED_HEAD_NOT_CALLBACK_ACCEPTANCE',
                    'tls_verification': 'REQUIRED', 'proxy_used': False,
                    'redirects_followed': False, 'credentials_sent': False,
                    'body_exported': False, 'head_requests': 1}
    except Exception as exc:
        result['callback_transport'] = {'status': 'INCOMPLETE', 'head_requests': 1,
            'error_class': type(exc).__name__, 'raw_error_exported': False}
    result['local_route'] = {'status': 'NOT_REQUESTED', 'requests': 0}'''


class Refused(Exception):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def load_common():
    need(COMMON and BASELINE_CONFIG and RECEIPTS, 'PACKAGE_INCOMPLETE')
    module = types.ModuleType('_oa_preflight')
    exec(compile(COMMON, '<oa-preflight>', 'exec'), module.__dict__)
    module.require = need
    return module


def receipts():
    for name, pin in RECEIPTS.items():
        path = Path(pin['path'])
        need(str(path.resolve()) == str(path), 'RECEIPT_PATH_CHANGED')
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            s = os.fstat(stream.fileno())
            need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                 and s.st_size == pin['bytes'] and 0 < s.st_size <= 1048576, 'RECEIPT_METADATA')
            raw = stream.read(1048577)
        need(hashlib.sha256(raw).hexdigest() == pin['sha256'], 'RECEIPT_DIGEST')
        value = json.loads(raw)
        need(value.get('status') == pin['status'] and value.get(pin['accepted_field']) is True,
             'RECEIPT_NOT_ACCEPTED_' + name)


def validate_config(observation, enabled, head=False):
    wanted = dict(BASELINE_CONFIG, multi_result_enabled=enabled, callback_enabled=enabled)
    need(all(observation.get(k) == v for k, v in wanted.items()), 'OA_CONFIG_CHANGED')
    need(observation.get('sources_verified') is True and observation.get('configuration_modified') is False
         and observation.get('job_submissions') == observation.get('provider_requests') == 0
         and observation.get('callback_requests') == int(head),
         'OA_RUNTIME_CHECK_FAILED')


def callback_preflight(common, samples, checked):
    source = common.CHILD
    load = 'config = module.load_oa_policy_flow_config()'
    marker = "result['local_route'] = local_probe(config)"
    need(source.count(load) == source.count(marker) == source.count('callback_requests=0') == 1,
         'CALLBACK_PREFLIGHT_SOURCE_CHANGED')
    source = source.replace(load, "os.environ.update({x['name']: x['value'] for x in " + repr(OVERRIDES) + '})\n    ' + load)
    source = source.replace(marker, CALLBACK_HEAD).replace('callback_requests=0', 'callback_requests=1')
    for sample in samples:
        pin = sample['identity']
        need(common.identity(common.query(['get', 'pod', pin['pod'], '-o', 'json'])) == pin, 'POD_CHANGED_BEFORE_HEAD')
        observation = common.query(['exec', pin['pod'], '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B',
                                    '-c', source, json.dumps(common.SOURCE_PINS)], timeout=45, limit=32768)
        validate_config(observation, True, head=True)
        transport = observation.get('callback_transport', {})
        checked.append({'pod': pin['pod'], 'transport': transport})
        code = transport.get('http_status', 0)
        need(type(code) is int and 200 <= code < 500 and not 300 <= code < 400
             and transport.get('tls_verification') == 'REQUIRED' and transport.get('head_requests') == 1,
             'CALLBACK_HTTPS_HEAD_NOT_ACCEPTED')
        need(common.identity(common.query(['get', 'pod', pin['pod'], '-o', 'json'])) == pin, 'POD_CHANGED_AFTER_HEAD')
    return checked


def validate_route(observation):
    route = observation.get('local_route', {})
    need(route.get('status') == 'AUTHENTICATED_ROUTE_ACCEPTED'
         and route.get('expected_missing_job') is True and route.get('http_status') == 404,
         'OA_AUTHENTICATED_ROUTE_FAILED')


def plan(common, deployment):
    need(common.binding(deployment) == {k: common.EXPECTED['gateway'][k] for k in ('uid', 'generation', 'spec_sha256')}
         and common.ready(deployment), 'GATEWAY_BASELINE_CHANGED')
    containers = deployment['spec']['template']['spec']['containers']
    indexes = [i for i, c in enumerate(containers) if c['name'] == 'gateway']
    need(len(indexes) == 1, 'GATEWAY_CONTAINER_NOT_UNIQUE')
    i = indexes[0]
    need(containers[i]['image'] == common.EXPECTED['gateway']['image'], 'GATEWAY_IMAGE_CHANGED')
    need(not any(e['name'].startswith('OA_POLICY_FLOW_') for e in containers[i].get('env', [])), 'EXPLICIT_OA_ENV_CHANGED')
    need(common.env_sources(deployment)['env_from'] == [{'kind': 'secretRef', 'name': 'deer-flow-env', 'prefix': ''}],
         'ENV_FROM_CHANGED')
    desired = copy.deepcopy(deployment)
    desired['spec']['template']['spec']['containers'][i].setdefault('env', []).extend(copy.deepcopy(OVERRIDES))
    desired['metadata']['generation'] += 1
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': deployment['metadata']['uid']},
             {'op': 'test', 'path': '/metadata/resourceVersion', 'value': deployment['metadata']['resourceVersion']}]
    path = '/spec/template/spec/containers/{}/env'.format(i)
    if 'env' in containers[i]:
        patch.extend({'op': 'add', 'path': path + '/-', 'value': row} for row in copy.deepcopy(OVERRIDES))
    else:
        patch.append({'op': 'add', 'path': path, 'value': copy.deepcopy(OVERRIDES)})
    return common.binding(desired), patch


def read_deployment(common, role):
    return common.query(['get', 'deployment', role, '-o', 'json'])


def other_services(common):
    result = {}
    for role in ('frontend', 'ppt-master-runner'):
        value = read_deployment(common, role)
        need(common.binding(value) == common.EXPECTED[role] and common.ready(value), 'OTHER_DEPLOYMENT_CHANGED_' + role)
        result[role] = common.binding(value)
    return result


def current_pods(common, deployment):
    rs = common.query(['get', 'replicasets', '-l', 'app=gateway', '-o', 'json'])['items']
    rows = common.query(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items']
    valid = set()
    for item in rs:
        ref = common.owner(item)
        template = copy.deepcopy(item['spec']['template'])
        template.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
        if ref.get('kind') == 'Deployment' and ref.get('uid') == deployment['metadata']['uid'] and template == deployment['spec']['template']:
            valid.add(item['metadata']['uid'])
    found = []
    for item in rows:
        ref = common.owner(item)
        if ref.get('kind') == 'ReplicaSet' and ref.get('uid') in valid and not item['metadata'].get('deletionTimestamp'):
            found.append(common.identity(item))
    need(len(found) == deployment['spec']['replicas'] == 70
         and len({p['uid'] for p in found}) == 70 and len({p['node'] for p in found}) >= 2, 'ALL_GATEWAY_PODS_REQUIRED')
    return sorted(found, key=lambda x: x['pod'])


def verify_pods(common, deployment, progress=lambda value: None):
    initial = current_pods(common, deployment)
    checked, route_nodes = [], set()
    marker = "result['local_route'] = local_probe(config)"
    need(common.CHILD.count(marker) == 1, 'CHILD_SHAPE_CHANGED')
    without_http = common.CHILD.replace(marker, "result['local_route'] = {'status': 'NOT_REQUESTED', 'requests': 0}")
    for pin in initial:
        use_http = len(route_nodes) < 2 and pin['node'] not in route_nodes
        child = common.CHILD if use_http else without_http
        observation = common.query(['exec', pin['pod'], '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B',
                                    '-c', child, json.dumps(common.SOURCE_PINS)], timeout=45, limit=32768)
        validate_config(observation, True)
        if use_http:
            validate_route(observation)
            route_nodes.add(pin['node'])
        checked.append({'identity': pin, 'sources_verified': True, 'v2_enabled': True,
                        'callback_enabled': True, 'local_route': observation['local_route']})
        progress({'phase': 'VERIFY_OA_V2_RUNTIME', 'checked': len(checked), 'total': len(initial)})
    need(len(route_nodes) == 2 and current_pods(common, deployment) == initial, 'PODS_CHANGED_DURING_VERIFICATION')
    return checked


def write_new(path, raw):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def save(report):
    directory = Path(CONTROL)
    temporary = directory / 'REPORT.tmp'
    raw = (json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2) + '\n').encode()
    write_new(temporary, raw)
    os.replace(str(temporary), str(directory / 'REPORT.json'))
    return {'report_path': str(directory / 'REPORT.json'), 'report_bytes': len(raw),
            'report_sha256': hashlib.sha256(raw).hexdigest()}


def dispatch_patch(path):
    # One CAS request only. A nonzero exit or timeout does not prove non-application.
    command = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=30s', '-n', 'deer-flow',
               'patch', 'deployment', 'gateway', '--type=json', '--patch-file', str(path), '-o', 'name']
    try:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=40)
        return {'command_returncode': result.returncode, 'command_timed_out': False}
    except subprocess.TimeoutExpired:
        return {'command_returncode': None, 'command_timed_out': True}


def wait_ready(common, expected, deadline, progress, now=time.monotonic, sleep=time.sleep):
    while True:
        deployment = read_deployment(common, 'gateway')
        need(common.binding(deployment) == expected, 'GATEWAY_CHANGED_AFTER_PATCH')
        if common.ready(deployment):
            return deployment
        need(now() < deadline, 'ROLLOUT_OBSERVATION_TIMEOUT')
        s = deployment.get('status', {})
        progress({'phase': 'OBSERVE_GATEWAY', 'updated': s.get('updatedReplicas', 0), 'ready': s.get('readyReplicas', 0), 'desired': 70})
        sleep(5)


def enable(common, report, progress):
    receipts()
    inspection = common.inspect_cluster()
    for row in inspection['samples']:
        validate_config(row['observation'], False)
        validate_route(row['observation'])
    report['callback_preflight'] = []
    callback_preflight(common, inspection['samples'], report['callback_preflight'])
    deployment = read_deployment(common, 'gateway')
    expected, patch = plan(common, deployment)
    preserved = other_services(common)
    report.update(before=inspection['deployments'], planned_gateway=expected, preflight=inspection,
                  frontend_runner_before=preserved, environment_override=OVERRIDES)
    need(not os.path.lexists(CONTROL), 'ONCE_DIRECTORY_EXISTS_KEEP_REPORT')
    need(str(Path(CONTROL).parent.resolve()) == '/var/lib', 'CONTROL_PARENT_CHANGED')
    os.mkdir(CONTROL, 0o700)
    report['once_claimed'] = True
    save(report)
    path = Path(CONTROL) / 'PATCH.json'
    write_new(path, json.dumps(patch).encode())
    # The API resourceVersion test covers every concurrent deployment change.
    need(common.binding(read_deployment(common, 'gateway')) == common.binding(deployment), 'GATEWAY_CHANGED_BEFORE_PATCH')
    other_services(common)
    report.update(patch_request_count=1, patch_may_have_applied=True, production_workload_mutation_may_have_occurred=True)
    save(report)
    report['patch_command'] = dispatch_patch(path)
    save(report)
    observed = read_deployment(common, 'gateway')
    need(common.binding(observed) == expected, 'PATCH_NOT_ACKNOWLEDGED_KEEP_REPORT')
    report.update(patch_acknowledged=True, production_workload_mutation=True)
    save(report)
    after = wait_ready(common, expected, time.monotonic() + 1800, progress)
    report['runtime'] = verify_pods(common, after, progress)
    need(other_services(common) == preserved, 'OTHER_DEPLOYMENT_CHANGED')
    final = read_deployment(common, 'gateway')
    need(common.binding(final) == expected and common.ready(final), 'FINAL_GATEWAY_CHANGED')
    report.update(after={'gateway': common.binding(final), **preserved}, status='OA_V2_INTEGRATION_ENABLED_BUSINESS_PENDING',
                  accepted_pods=len(report['runtime']), v2_enabled=True, callback_enabled=True,
                  gateway_image_preserved=True, gateway_other_spec_preserved=True,
                  frontend_runner_preserved=True, exitcode=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable-oa-v2-integration-once', action='store_true', required=True)
    parser.add_argument('--allow-active-task-interruption', action='store_true', required=True)
    parser.parse_args()
    report = {'schema': 'portal-oa-v2-integration-enable.v1', 'status': 'INCOMPLETE', 'exitcode': 1,
              'automatic_retry': False, 'automatic_rollback': False, 'once_claimed': False,
              'patch_request_count': 0, 'patch_may_have_applied': False, 'patch_acknowledged': False,
              'production_workload_mutation': False, 'schema_migration_requested': False,
              'secret_mutation_requested': False, 'configmap_mutation_requested': False,
              'credentials_exported': False, 'job_submissions': 0, 'provider_requests': 0,
              'business_callback_requests_by_tool': 0, 'maximum_callback_head_requests': 2,
              'ordinary_worker_callbacks_enabled_by_rollout': True,
              'maximum_loopback_get_requests': 4, 'oa_business_acceptance_pending': True,
              'active_task_interruption_operator_authorized': True}
    last = [0.0]
    def progress(value):
        if time.monotonic() - last[0] >= 20:
            print(json.dumps(value, sort_keys=True), flush=True)
            last[0] = time.monotonic()
    try:
        need(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname() == 'datax-01'
             and sys.flags.isolated and sys.flags.dont_write_bytecode, 'DATAX01_ISOLATED_ROOT_REQUIRED')
        enable(load_common(), report, progress)
    except Exception as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'DETAILS_WITHHELD')
    receipt = save(report) if report['once_claimed'] else {}
    summary = {k: v for k, v in report.items() if k not in ('runtime', 'preflight')}
    summary.update(receipt)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return report['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
