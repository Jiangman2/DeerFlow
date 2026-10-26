"""One Gateway image/env rollout, only after completed native viewer enrollment."""
# The packager embeds the previously qualified helpers in COMMON below.
# ruff: noqa: F821
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import time

COMMON = ''
NODE_GUARD = ''
RUNTIME_CHILD = ''
TASK_CHILD = ''
EXPECTED = {}
RECEIPTS = {}
OVERRIDES = []
SOURCES = {}
OLD_IMAGE = ''
CONTROL = '/var/lib/portal-weknora-company-activation-20261024-r1'
ACCEPTED = 'WEKNORA_COMPANY_AUTO_ENROLLMENT_RUNTIME_ACCEPTED_BROWSER_PENDING'
BULK_COMPLETE = 'BULK_VIEWER_ENROLLMENT_ACCEPTED_AUTO_ENROLLMENT_NOT_DEPLOYED'
ORG = 'b9cfbf7f-7281-4f62-a72b-6a22379efe60'
KB = 'bd83e659-9d9c-429a-acf1-ead01037cfa1'

exec(COMMON, globals())


def command(args, value=None, raw=False):
    require(args and args[0] in ('get', 'exec', 'patch'), 'COMMAND_FORBIDDEN')
    require((args[0] == 'patch') == (value is not None), 'COMMAND_PAYLOAD_FORBIDDEN')
    if value is not None:
        require(args == ['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/dev/stdin', '-o', 'json'], 'PATCH_TARGET_FORBIDDEN')
    base = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
            '--request-timeout=' + ('60s' if args[0] == 'exec' else '15s'), '-n', 'deer-flow']
    result = subprocess.run(base + args, input=json.dumps(value).encode() if value is not None else None,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=70 if args[0] == 'exec' else 25)
    require(result.returncode == 0 and len(result.stdout) <= (65536 if args[0] == 'exec' else 16 * 1024**2), 'KUBERNETES_COMMAND_FAILED')
    return result.stdout if raw else json.loads(result.stdout)


def evidence():
    require(set(RECEIPTS) == {'image', 'bulk'}, 'FINAL_ENROLLMENT_RECEIPT_REQUIRED')
    values = {}
    for name, pin in RECEIPTS.items():
        path = Path(pin['path'])
        require(os.path.realpath(str(path)) == str(path), 'RECEIPT_SYMLINK')
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1 and not info.st_mode & 0o022
                    and info.st_size == pin['bytes'] and 0 < info.st_size <= 1048576, 'RECEIPT_METADATA_CHANGED')
            raw = stream.read(pin['bytes'] + 1)
        require(len(raw) == pin['bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'], 'RECEIPT_HASH_CHANGED')
        values[name] = json.loads(raw)
    validate_evidence(values)
    return {k: dict(v) for k, v in RECEIPTS.items()}


def validate_evidence(values):
    image, bulk = values['image'], values['bulk']
    require(image.get('status') == 'WEKNORA_COMPANY_IMAGE_ACCEPTED_NOT_DEPLOYED'
            and len(image.get('checks', [])) == 1 and image['checks'][0].get('status') == 'PASS'
            and image['checks'][0]['pods'][0]['containers'][0]['imageID'] == EXPECTED['gateway']['candidate_image'], 'IMAGE_NOT_ACCEPTED')
    check = image['checks'][0]['observation']
    require(check.get('status') == 'INSTALLED_WEKNORA_COMPANY_IMAGE_ACCEPTED'
            and len(check.get('checks', [])) == 13 and check.get('real_http_requests') == 0, 'IMAGE_CHECK_INCOMPLETE')
    observation = bulk.get('observation', {})
    require(bulk.get('schema') == 'portal-weknora-company-bulk-enrollment-host.v1' and bulk.get('status') == BULK_COMPLETE
            and bulk.get('once_claimed') is True and bulk.get('production_workload_mutation') is False
            and observation.get('status') == BULK_COMPLETE and observation.get('phase') == 'COMPLETE'
            and observation.get('organization_id') == ORG and observation.get('knowledge_base_id') == KB
            and observation.get('remaining_workspace_memberships') == 0
            and type(observation.get('post_responses_unconfirmed')) is int and observation['post_responses_unconfirmed'] >= 0
            and observation.get('uncertain_posts_resolved_by_readback') == observation['post_responses_unconfirmed']
            and observation.get('database_direct_writes') == 0 and observation.get('accounts_created') == 0
            and observation.get('account_roles_changed') is False, 'BULK_ENROLLMENT_NOT_COMPLETE_DO_NOT_ROLLOUT')


def nodes_gate(gateway):
    guard = {'__name__': 'company_node_coverage'}
    exec(NODE_GUARD, guard)
    nodes = command(['get', 'nodes', '-o', 'json'])['items']
    try:
        return guard['assess_nodes'](nodes, {'gateway': gateway}, {'gateway': EXPECTED['gateway']})
    except guard['NodeGateRefused'] as exc:
        raise Refused(str(exc), exc.observation)


def snapshot(target_gateway=None):
    deployments = {name: command(['get', 'deployment', name, '-o', 'json']) for name in EXPECTED}
    for name, deployment in deployments.items():
        pin = target_gateway if name == 'gateway' and target_gateway else {k: EXPECTED[name][k] for k in ('uid', 'generation', 'spec_sha256')}
        require(binding(deployment) == pin and ready(deployment) and not deployment['spec'].get('paused'), 'DEPLOYMENT_BASELINE_CHANGED_' + name)
    coverage = nodes_gate(deployments['gateway'])
    ui = command(['get', 'deployment', 'weknora-frontend', '-o', 'json'])
    require(ready(ui) and not ui['spec'].get('paused'), 'WEKNORA_FRONTEND_NOT_STABLE')
    metadata = configuration_metadata(configuration_refs(dict(deployments, weknora_frontend=ui)))
    service = command(['get', 'service', 'gateway', '-o', 'json'])
    return deployments, {'deployments': {n: binding(d) for n, d in deployments.items()},
        'configuration_metadata': metadata, 'node_coverage': coverage, 'weknora_frontend': binding(ui),
        'gateway_service': {'uid': service['metadata']['uid'], 'spec_sha256': digest(service['spec'])}}


def plan(deployment):
    pin = EXPECTED['gateway']
    require(binding(deployment) == {k: pin[k] for k in ('uid', 'generation', 'spec_sha256')}
            and ready(deployment) and not deployment['spec'].get('paused'), 'GATEWAY_BASELINE_CHANGED')
    containers = deployment['spec']['template']['spec']['containers']
    require(len(containers) == 1 and containers[0]['name'] == 'gateway' and containers[0]['image'] == OLD_IMAGE, 'GATEWAY_CONTAINER_CHANGED')
    spec = copy.deepcopy(deployment['spec'])
    container = spec['template']['spec']['containers'][0]
    env = container.setdefault('env', [])
    names = [row['name'] for row in env]
    require(len(names) == len(set(names)), 'DUPLICATE_ENV_KEYS')
    for row in OVERRIDES:
        if row['name'] in names:
            env[names.index(row['name'])] = copy.deepcopy(row)
        else:
            env.append(copy.deepcopy(row))
    container['image'] = pin['candidate_image']
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': deployment['metadata']['uid']},
             {'op': 'test', 'path': '/metadata/resourceVersion', 'value': deployment['metadata']['resourceVersion']},
             {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
             {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': pin['candidate_image']},
             {'op': 'add', 'path': '/spec/template/spec/containers/0/env', 'value': env}]
    return patch, spec


def inspect_tasks(pod, drained):
    result = command(['exec', pod['metadata']['name'], '-c', 'gateway', '--',
        '/app/backend/.venv/bin/python', '-I', '-B', '-c', TASK_CHILD])
    require(result.get('status') == 'PASS' and result.get('database_readonly') is True
            and result.get('revisions') == ['0021_oa_result_sets'], 'TASK_INSPECTION_FAILED')
    require(all(type(result.get(k)) is int and result[k] >= 0 for k in ('active_runs', 'active_oa_jobs', 'active_callback_leases')), 'TASK_COUNT_SHAPE')
    if drained:
        require(not any(result[k] for k in ('active_runs', 'active_oa_jobs', 'active_callback_leases')), 'TASKS_STILL_ACTIVE')
    return result


def runtime(pod):
    result = command(['exec', pod['metadata']['name'], '-c', 'gateway', '--',
        '/app/backend/.venv/bin/python', '-I', '-B', '-c', RUNTIME_CHILD, json.dumps(SOURCES)])
    require(result.get('status') == 'PASS' and result.get('sources_verified') is True and result.get('auto_enrollment_enabled') is True
            and result.get('organization_ids') == [ORG] and result.get('local_health_http_status') == 200
            and result.get('provider_requests') == 0 and result.get('membership_grants') == 0, 'RUNTIME_CHECK_FAILED')
    return result


def save(report):
    directory = Path(CONTROL)
    path = directory / 'REPORT.tmp'
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(report, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(path), str(directory / 'REPORT.json'))
    fd = os.open(CONTROL, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def execute(report, drained):
    require(COMMON and NODE_GUARD and RUNTIME_CHILD and TASK_CHILD and EXPECTED and SOURCES and OVERRIDES, 'PACKAGE_INCOMPLETE')
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
    require(not os.path.lexists(CONTROL), 'PRIOR_ACTIVATION_EXISTS_DO_NOT_REPLAY')
    report['acceptance_receipts'] = evidence()
    deployments, before = snapshot()
    old = pods_for(deployments['gateway'], EXPECTED['gateway'], OLD_IMAGE)
    endpoints('gateway', old)
    report['before'] = before
    report['task_observation'] = inspect_tasks(old[0], drained)
    fresh, second = snapshot()
    require(second == before and identities(pods_for(fresh['gateway'], EXPECTED['gateway'], OLD_IMAGE)) == identities(old), 'BASELINE_CHANGED_DURING_PREFLIGHT')
    # A finished report is checked again immediately before the single CAS.
    require(evidence() == report['acceptance_receipts'], 'ACCEPTANCE_RECEIPT_CHANGED')
    patch, spec = plan(fresh['gateway'])
    planned = {'uid': EXPECTED['gateway']['uid'], 'generation': EXPECTED['gateway']['generation'] + 1, 'spec_sha256': digest(spec)}
    parent = Path(CONTROL).parent
    require(str(parent.resolve()) == str(parent) and parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o022, 'UNSAFE_CONTROL_PARENT')
    os.mkdir(CONTROL, 0o700)
    report.update(once_claimed=True, planned_gateway=planned, environment_override=OVERRIDES,
                  patch_requested=True, patch_may_have_applied=True, patch_request_count=1,
                  production_workload_mutation=None)
    save(report)
    try:
        command(['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/dev/stdin', '-o', 'json'], patch)
        report['patch_command_acknowledged'] = True
    except Exception as exc:
        report['patch_command_error_class'] = type(exc).__name__
    # A timeout cannot justify a second PATCH. Read actual state once.
    applied = command(['get', 'deployment', 'gateway', '-o', 'json'])
    require(binding(applied) == planned, 'PATCH_NOT_CONFIRMED_DO_NOT_REPLAY')
    report.update(patch_acknowledged_by_readback=True, production_workload_mutation=True)
    save(report)
    deadline, last_progress = time.monotonic() + 5400, 0
    while True:
        current = command(['get', 'deployment', 'gateway', '-o', 'json'])
        require(binding(current) == planned and not current['spec'].get('paused'), 'GATEWAY_CHANGED_DURING_ROLLOUT')
        if ready(current):
            try:
                pods = pods_for(current, EXPECTED['gateway'], EXPECTED['gateway']['candidate_image'])
                endpoints('gateway', pods)
                break
            except Refused as exc:
                require(str(exc) in ('POD_COUNT_CHANGED', 'POD_NOT_READY', 'SERVICE_ENDPOINTS_NOT_READY'), str(exc))
        require(time.monotonic() < deadline, 'ROLLOUT_OBSERVATION_TIMEOUT')
        if time.monotonic() - last_progress >= 20:
            counts = current.get('status', {})
            report['last_rollout_counts'] = {k: counts.get(k, 0) for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'terminatingReplicas')}
            print(json.dumps(dict(report['last_rollout_counts'], phase='OBSERVE_COMPANY_GATEWAY', desired=current['spec']['replicas'])), flush=True)
            save(report)
            last_progress = time.monotonic()
        time.sleep(10)
    for pod in pods:
        report['runtime'].append({'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'observation': runtime(pod)})
        save(report)
        if len(report['runtime']) % 10 == 0:
            print(json.dumps({'phase': 'VERIFY_COMPANY_GATEWAY', 'accepted': len(report['runtime']), 'total': len(pods)}), flush=True)
    final, after = snapshot(planned)
    require(identities(pods_for(final['gateway'], EXPECTED['gateway'], EXPECTED['gateway']['candidate_image'])) == identities(pods), 'CONTAINERS_CHANGED_DURING_VERIFICATION')
    endpoints('gateway', pods)
    require(all(after[k] == before[k] for k in ('configuration_metadata', 'gateway_service', 'weknora_frontend')), 'UNRELATED_CONFIGURATION_CHANGED')
    report.update(after=after, status=ACCEPTED, runtime_accepted=True, auto_enrollment_deployed=True,
                  other_deployments_preserved=True, configuration_metadata_preserved=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate-company-knowledge-once', action='store_true', required=True)
    policy = parser.add_mutually_exclusive_group(required=True)
    policy.add_argument('--tasks-drained', action='store_true')
    policy.add_argument('--allow-active-task-interruption', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    report = {'schema': 'portal-weknora-company-activation.v1', 'status': 'INCOMPLETE', 'runtime': [], 'once_claimed': False,
              'patch_requested': False, 'patch_may_have_applied': False, 'patch_request_count': 0, 'production_workload_mutation': False,
              'automatic_retry': False, 'automatic_rollback': False, 'schema_migration_requested': False,
              'membership_grants_by_tool': 0, 'provider_requests': 0, 'credentials_exported': False, 'auto_enrollment_deployed': False,
              'browser_acceptance_pending': True, 'knowledge_qa_business_acceptance_pending': True,
              'ordinary_sso_auto_enrollment_enabled_by_rollout': True,
              'active_task_interruption_operator_authorized': args.allow_active_task_interruption,
              'tasks_drained_operator_attested': args.tasks_drained}
    try:
        execute(report, args.tasks_drained)
    except BaseException as exc:
        code = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        report['error_code'] = code if re.fullmatch('[A-Za-z0-9_-]{1,100}', code) else 'UNCLASSIFIED_FAILURE'
    if report['once_claimed']:
        save(report)
        raw = (Path(CONTROL) / 'REPORT.json').read_bytes()
        report.update(report_path=CONTROL + '/REPORT.json', report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    summary = {k: v for k, v in report.items() if k not in ('before', 'after', 'runtime')}
    summary['accepted_pods'] = len(report['runtime'])
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if report['status'] == ACCEPTED else 1


if __name__ == '__main__':
    raise SystemExit(main())
