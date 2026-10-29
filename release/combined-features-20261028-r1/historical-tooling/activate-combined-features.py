"""Receipt-bound Gateway then Frontend image rollout; V4.1 and Work stay disabled."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import threading
import time
import uuid

HELPERS = None
BASELINES = None
TARGETS = None
RECEIPTS = None
OLD_SOURCES = None
NEW_SOURCES = None
FRONTEND_PINS = None
FRONTEND_HELPER = None
NODE_GUARD = None
RUNTIME_CHILD = None
CONFIG_REFS_HELPER = None

CONTROL = '/var/lib/portal-combined-features-activation-20261028-r1'
ACCEPTED = 'COMBINED_FEATURES_RUNTIME_ACCEPTED_BROWSER_PENDING_V41_DISABLED'
KEY_RECEIPT = {'path': '/var/lib/portal-combined-tavily-key-20261028-r1/REPORT.json', 'bytes': 2706,
               'sha256': 'ef3f55bcd138566c401b819edab0557df17f0638c05126bcf0175100a814e95d'}
SECRET_UID = 'e04430c4-8cad-457d-a2f3-3583fa5e29ba'
SECRET_RV = '27358267'
SERVICES = ('frontend', 'gateway', 'gateway-run-dispatcher', 'nginx', 'nginx-nodeport', 'ppt-master-runner',
            'provisioner', 'weknora-app', 'weknora-frontend', 'skillhub-server', 'skillhub-web')


def save(report):
    if not report.get('once_claimed'): return
    p = Path(CONTROL) / 'REPORT.tmp'
    fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        json.dump(report, f, sort_keys=True); f.write('\n'); f.flush(); os.fsync(f.fileno())
    os.replace(str(p), str(Path(CONTROL) / 'REPORT.json'))
    fd = os.open(CONTROL, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def read_receipt(pin):
    p = Path(pin['path'])
    require(str(p.resolve()) == str(p), 'RECEIPT_SYMLINK')
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and s.st_size == pin['bytes'] and s.st_size <= 2097152, 'RECEIPT_METADATA_CHANGED')
        raw = f.read(pin['bytes'] + 1)
    require(len(raw) == pin['bytes'] and hashlib.sha256(raw).hexdigest() == pin['sha256'], 'RECEIPT_HASH_CHANGED')
    return json.loads(raw)


def evidence():
    installed = read_receipt(RECEIPTS['installed'])
    require(installed.get('status') == 'COMBINED_IMAGES_ACCEPTED_NOT_DEPLOYED'
            and installed.get('production_workload_modified') is False and installed.get('schema_migrated') is False,
            'INSTALLED_IMAGE_ACCEPTANCE_REQUIRED')
    checks = installed.get('checks', [])
    require(len(checks) == 2 and {c['deployment'] for c in checks} == {'ecs-zdz-pro-0018', 'ecs-zdz-pro-0021'}, 'INSTALLED_NODE_COVERAGE')
    for c in checks:
        o = c.get('observation', {})
        require(c.get('status') == 'PASS' and o.get('installed_source_files') == 588
                and o.get('tests') == {'collection_errors': 0, 'exitcode': 0, 'failed': 0, 'passed': 251, 'skipped': 0}
                and c['pods'][0]['containers'][0]['imageID'] == TARGETS['gateway']['image'], 'INSTALLED_CHECK_INCOMPLETE')
    storage = read_receipt(RECEIPTS['storage'])
    require(storage.get('status') == 'CROSS_NODE_STORE_ACCEPTED_NOT_DEPLOYED' and storage.get('canary_removed') is True
            and storage.get('production_display_access') is False and len(storage.get('checks', [])) == 8
            and all(c.get('status') == 'PASS' for c in storage['checks']), 'SHARED_STORE_ACCEPTANCE_REQUIRED')
    key = read_receipt(KEY_RECEIPT)
    require(key.get('status') == 'TAVILY_SECRET_UPDATED_GATEWAY_ROLLOUT_PENDING'
            and key.get('secret_readback_accepted') is True and key.get('other_secret_fields_preserved') is True
            and key.get('protected_deployments_preserved') is True and key.get('secret_item') == 'TAVILY_API_KEY'
            and key.get('secret_name') == 'deer-flow-env' and key.get('secret_uid') == SECRET_UID
            and key.get('secret_resource_version') == SECRET_RV and key.get('v41_enabled') is False,
            'TAVILY_ACCEPTANCE_REQUIRED')
    return dict(RECEIPTS, tavily=KEY_RECEIPT)


def stable(d):
    s = d.get('status', {})
    return (not d['spec'].get('paused') and s.get('observedGeneration') == d['metadata']['generation']
            and all(s.get(k, 0) == d['spec']['replicas'] for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and not s.get('terminatingReplicas') and not s.get('unavailableReplicas'))


def validate_baselines(reader):
    deps = {d['metadata']['name']: d for d in reader.get('deployment')['items']}
    for name, pin in BASELINES.items():
        require(name in deps and binding(deps[name]) == pin and stable(deps[name]), 'BASELINE_CHANGED_' + name)
    return deps


def configuration_metadata(reader, refs):
    result = {}
    for kind, name in refs:
        raw = reader.read(BASE + ['get', kind, name, '-o', 'jsonpath={.metadata.uid}{"\\n"}{.metadata.resourceVersion}'], cap=4096)
        parts = raw.decode('ascii').splitlines()
        require(len(parts) == 2 and all(parts), 'CONFIG_METADATA_SHAPE')
        result[kind + '/' + name] = parts
    return result


def service_bindings(reader):
    return {n: binding(reader.get('service', n)) for n in SERVICES}


def secret_hash(reader):
    # Project only this key. Keep its value and fingerprint out of every report.
    raw = reader.read(BASE + ['get', 'secret', 'deer-flow-env', '-o',
        'jsonpath={.metadata.uid}{"\\n"}{.metadata.resourceVersion}{"\\n"}{.data.TAVILY_API_KEY}'], cap=4096)
    parts = raw.decode('ascii').splitlines()
    require(len(parts) == 3 and parts[:2] == [SECRET_UID, SECRET_RV], 'TAVILY_SECRET_CHANGED')
    key = base64.b64decode(parts[2], validate=True)
    require(0 < len(key) <= 512, 'TAVILY_SECRET_EMPTY')
    return hashlib.sha256(key).hexdigest()


def nodes_gate(reader, deps):
    namespace = {'__name__': 'combined_node_guard'}; exec(NODE_GUARD, namespace)
    nodes = reader.get('node')['items']
    try:
        result = namespace['assess_nodes'](nodes, {n: deps[n] for n in TARGETS}, TARGETS)
    except namespace['NodeGateRefused'] as exc:
        raise Refused(str(exc))
    expected = {n: u for t in TARGETS.values() for n, u in t['nodes'].items()}
    for n in nodes:
        if n['metadata']['name'] not in expected: continue
        conditions = {c['type']: c['status'] for c in n.get('status', {}).get('conditions', [])}
        require(conditions.get('MemoryPressure') == 'False' and conditions.get('PIDPressure') == 'False'
                and not n.get('spec', {}).get('unschedulable')
                and n.get('status', {}).get('nodeInfo', {}).get('architecture') == 'arm64', 'PRELOADED_NODE_UNHEALTHY')
    return result


def owned(reader, deployment):
    sets = reader.get('replicaset')['items']
    # Selector is used only to limit payload, never as ownership authority.
    selector = deployment['spec']['selector']
    require(not selector.get('matchExpressions') and selector.get('matchLabels'), 'POD_SELECTOR_SHAPE')
    labels = ','.join(k + '=' + v for k, v in sorted(selector['matchLabels'].items()))
    pods = reader.get('pods', selectors=['-l', labels])['items']
    return sorted(owned_pods(deployment, sets, pods), key=lambda p: p['metadata']['name'])


def normalized_image_id(value):
    value = (value or '').rsplit('@', 1)[-1]
    return value[len('containerd://'):] if value.startswith('containerd://') else value


def accepted_pods(reader, deployment, role, candidate):
    pods = owned(reader, deployment)
    if len(pods) != deployment['spec']['replicas']: return None
    expected_image = TARGETS[role]['image'] if candidate else TARGETS[role]['old_image']
    for p in pods:
        containers = p['spec'].get('containers', [])
        statuses = p.get('status', {}).get('containerStatuses', [])
        if (p['metadata'].get('deletionTimestamp') or p.get('status', {}).get('phase') != 'Running'
                or p['spec'].get('nodeName') not in TARGETS[role]['nodes'] or len(containers) != 1
                or containers[0]['image'] != expected_image or len(statuses) != 1 or not statuses[0].get('ready')):
            return None
        if candidate:
            require(normalized_image_id(statuses[0].get('imageID')) in (expected_image.split('@')[1], TARGETS[role]['config']), 'RUNTIME_IMAGE_CHANGED')
    slices = reader.get('endpointslices', selectors=['-l', 'kubernetes.io/service-name=' + role])['items']
    eps = [e for s in slices for e in s.get('endpoints', [])]
    ready = {e.get('targetRef', {}).get('uid') for e in eps if e.get('conditions', {}).get('ready')
             and not e.get('conditions', {}).get('terminating')}
    if ready != {p['metadata']['uid'] for p in pods}: return None
    if any(e.get('conditions', {}).get('serving') and e.get('conditions', {}).get('terminating') for e in eps): return None
    return pods


def nginx_gate(reader, deps):
    service = reader.get('service', 'nginx')
    require(service['metadata']['uid'] == '968e2c68-6635-4e60-9eba-9b13ae10647c'
            and digest(service['spec']) == '4466eebc5928640ecee0982ed1b90131857897e1a7a59a8e7deee6381c1a8b67', 'NGINX_SERVICE_CHANGED')
    pods = owned(reader, deps['nginx-security-heai-20261012-r1'])
    require(len(pods) == 1 and not pods[0]['metadata'].get('deletionTimestamp'), 'SERVING_NGINX_COUNT')
    p = pods[0]
    eps = reader.get('endpointslices', selectors=['-l', 'kubernetes.io/service-name=nginx'])['items']
    uids = {e.get('targetRef', {}).get('uid') for s in eps for e in s.get('endpoints', [])
            if e.get('conditions', {}).get('ready') and not e.get('conditions', {}).get('terminating')}
    require(uids == {p['metadata']['uid']}, 'NGINX_SERVING_OWNERSHIP_CHANGED')
    raw = reader.read(BASE + ['exec', p['metadata']['name'], '-c', 'nginx', '--', 'cat', '/tmp/nginx.conf'], cap=262144)
    require(hashlib.sha256(raw).hexdigest() == '10b8e85a7f19ecd572a99e1b5969cd953a29784cb590125d406cd0fced74511b', 'NGINX_RUNTIME_CHANGED')
    return dict(sample=identity(p), runtime_sha256=hashlib.sha256(raw).hexdigest())


def run_child(reader, pod, data, timeout=110):
    before = identity(pod)
    require(identity(reader.get('pod', pod['metadata']['name'])) == before, 'RUNTIME_SAMPLE_CHANGED')
    raw = reader.read(BASE + ['exec', '-i', pod['metadata']['name'], '-c', 'gateway', '--',
        '/app/backend/.venv/bin/python', '-I', '-B', '-c', RUNTIME_CHILD], json.dumps(data).encode(), timeout=timeout, cap=65536)
    result = json.loads(raw)
    require(identity(reader.get('pod', pod['metadata']['name'])) == before, 'RUNTIME_SAMPLE_CHANGED')
    return result


def check_frontend_cache(reader, report):
    helper = {'__name__': 'combined_frontend_checks'}; exec(FRONTEND_HELPER, helper)
    for i, node in enumerate(sorted(TARGETS['frontend']['nodes'])):
        name = 'portal-combined-front-' + report['operation'][:12] + '-' + str(i)
        spec = helper['job_spec'](name, node, TARGETS['frontend']['image'], FRONTEND_PINS)
        row = dict(name=name, node=node, creation_may_have_applied=True, status='INCOMPLETE')
        report['temporary_jobs'].append(row); save(report)
        # Never retry CREATE after an uncertain outcome.
        raw = reader.read(BASE + ['create', '-f', '-', '-o', 'json'], json.dumps(spec).encode(), cap=262144)
        job = json.loads(raw)
        require(job.get('kind') == 'Job' and job['metadata']['name'] == name and job['metadata'].get('uid'), 'CHECK_JOB_IDENTITY')
        row['uid'] = job['metadata']['uid']; save(report)
        print(json.dumps(dict(phase='VERIFY_FRONTEND_CANDIDATE_ON_NODE', node=node, job=name)), flush=True)
        deadline = time.monotonic() + 240
        while True:
            current = json.loads(reader.read(BASE + ['get', 'job', name, '-o', 'json'], cap=262144))
            require(current['metadata']['uid'] == job['metadata']['uid'], 'CHECK_JOB_REPLACED')
            ps = reader.get('pods', selectors=['-l', 'batch.kubernetes.io/controller-uid=' + job['metadata']['uid']])['items']
            require(len(ps) <= 1, 'CHECK_POD_COUNT')
            observations = [helper['inspect_check_pod'](p, job, spec, TARGETS['frontend']['image'].split('@')[1], TARGETS['frontend']['config']) for p in ps]
            row['pods'] = observations
            conditions = current.get('status', {}).get('conditions', [])
            require(not any(c.get('type') == 'Failed' and c.get('status') == 'True' for c in conditions)
                    and not any(p.get('status', {}).get('phase') == 'Failed' for p in ps), 'FRONTEND_CACHE_CHECK_FAILED')
            if any(c.get('type') == 'Complete' and c.get('status') == 'True' for c in conditions):
                require(len(ps) == 1 and ps[0].get('status', {}).get('phase') == 'Succeeded', 'CHECK_COMPLETION_MISSING')
                require(observations[0].get('completion_accepted') is True, 'CHECK_CONTAINER_COMPLETION_NOT_ACCEPTED')
                outcome = json.loads(reader.read(BASE + ['logs', ps[0]['metadata']['name'], '-c', 'check', '--limit-bytes=16384'], cap=16384))
                require(outcome.get('status') == 'LOCAL_PRELOADED_IMAGE_EXECUTED' and outcome.get('sources_verified') is True
                        and outcome.get('files_verified') == len(FRONTEND_PINS), 'FRONTEND_CACHE_SOURCE_MISMATCH')
                row.update(status='PASS', observation=outcome); save(report); break
            require(time.monotonic() < deadline, 'FRONTEND_CACHE_CHECK_DEADLINE')
            time.sleep(5)


def plan(deployment, role):
    require(binding(deployment) == BASELINES[role] and stable(deployment), 'DEPLOYMENT_BASELINE_CHANGED')
    cs = deployment['spec']['template']['spec']['containers']
    require(len(cs) == 1 and cs[0]['name'] == role and cs[0]['image'] == TARGETS[role]['old_image'], 'ROLLOUT_CONTAINER_CHANGED')
    require(cs[0].get('imagePullPolicy', 'IfNotPresent') in ('Never', 'IfNotPresent'), 'OFFLINE_IMAGE_PULL_POLICY_REQUIRED')
    spec = copy.deepcopy(deployment['spec']); spec['template']['spec']['containers'][0]['image'] = TARGETS[role]['image']
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': deployment['metadata']['uid']},
             {'op': 'test', 'path': '/metadata/resourceVersion', 'value': deployment['metadata']['resourceVersion']},
             {'op': 'test', 'path': '/spec', 'value': deployment['spec']},
             {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': TARGETS[role]['image']}]
    target = dict(uid=deployment['metadata']['uid'], generation=deployment['metadata']['generation'] + 1, spec_sha256=digest(spec))
    return patch, target


def patch_once(reader, role, patch, planned, report):
    require(not any(p['deployment'] == role for p in report['patches']), 'SECOND_PATCH_FORBIDDEN')
    row = dict(deployment=role, planned=planned, patch_may_have_applied=True, patch_requests=1)
    report['patches'].append(row); report['production_workload_mutation_may_have_occurred'] = True; save(report)
    try:
        reader.read(BASE + ['patch', 'deployment', role, '--type=json', '--patch-file=/dev/stdin', '-o', 'name'],
                    json.dumps(patch).encode(), cap=4096)
        row['command_acknowledged'] = True
    except Refused:
        row['command_acknowledged'] = False
    d = reader.get('deployment', role)
    require(binding(d) == planned, 'PATCH_READBACK_NOT_ACCEPTED')
    row['readback_accepted'] = True; report['production_workload_modified'] = True; save(report)


def wait_rollout(reader, role, planned, report):
    deadline, progress = time.monotonic() + (5400 if role == 'gateway' else 900), 0
    while True:
        d = reader.get('deployment', role)
        require(binding(d) == planned and not d['spec'].get('paused'), 'DEPLOYMENT_CHANGED_DURING_ROLLOUT')
        require(not any(c.get('type') == 'Progressing' and c.get('status') == 'False'
                        and c.get('reason') == 'ProgressDeadlineExceeded' for c in d.get('status', {}).get('conditions', [])), 'ROLLOUT_PROGRESS_DEADLINE')
        if stable(d):
            ps = accepted_pods(reader, d, role, True)
            if ps is not None: return d, ps
        require(time.monotonic() < deadline, 'ROLLOUT_OBSERVATION_TIMEOUT')
        if time.monotonic() - progress >= 20:
            counts = {k: d.get('status', {}).get(k, 0) for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'terminatingReplicas')}
            report['last_rollout_counts'][role] = counts; save(report)
            print(json.dumps(dict(phase='OBSERVE_' + role.upper(), counts=counts)), flush=True); progress = time.monotonic()
        time.sleep(5)


def preservation(reader, report, plans, refs):
    deps = {d['metadata']['name']: d for d in reader.get('deployment')['items']}
    expected = dict(report['before_deployments'], **plans)
    require({n: binding(d) for n, d in deps.items()} == expected, 'PROTECTED_DEPLOYMENT_CHANGED')
    require(service_bindings(reader) == report['before_services'], 'PROTECTED_SERVICE_CHANGED')
    require(configuration_metadata(reader, refs) == report['before_configuration_metadata'], 'CONFIGURATION_METADATA_CHANGED')
    nodes_gate(reader, deps)
    require(nginx_gate(reader, deps) == report['serving_nginx'], 'SERVING_NGINX_CHANGED')
    return deps


def execute(reader, report):
    report['phase'] = 'READ_ACCEPTANCE_RECEIPTS'
    require(socket.gethostname().split('.')[0] == 'datax-01' and os.geteuid() == 0, 'WRONG_HOST_OR_UID')
    require(BASELINES and TARGETS and RUNTIME_CHILD and FRONTEND_HELPER and len(NEW_SOURCES) == 588, 'PACKAGED_TOOL_REQUIRED')
    require(not os.path.lexists(CONTROL), 'ONCE_ALREADY_CLAIMED')
    parent = Path(CONTROL).parent
    require(str(parent.resolve()) == str(parent) and parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o022, 'UNSAFE_CONTROL_PARENT')
    report['acceptance_receipts'] = evidence()
    report['phase'] = 'CHECK_CURRENT_BASELINES_AND_BINDINGS'
    deps = validate_baselines(reader)
    refs = configuration_refs(deps)
    report.update(before_deployments={n: binding(d) for n, d in deps.items()}, before_services=service_bindings(reader),
                  before_configuration_metadata=configuration_metadata(reader, refs), node_coverage=nodes_gate(reader, deps),
                  serving_nginx=nginx_gate(reader, deps))
    key_hash = secret_hash(reader)
    report['phase'] = 'VERIFY_CURRENT_GATEWAY_RUNTIME'
    old = accepted_pods(reader, deps['gateway'], 'gateway', False)
    require(old is not None and accepted_pods(reader, deps['frontend'], 'frontend', False) is not None, 'BASELINE_PODS_OR_ENDPOINTS_NOT_READY')
    preflight = run_child(reader, old[0], dict(mode='preflight', expected=OLD_SOURCES))
    report['preflight_runtime'] = preflight
    require(preflight.get('status') == 'COMBINED_PREFLIGHT_ACCEPTED', 'RUNTIME_PREFLIGHT_NOT_ACCEPTED')
    os.mkdir(CONTROL, 0o700); report['once_claimed'] = True; save(report)
    report['phase'] = 'VERIFY_PRELOADED_FRONTEND_IMAGES'
    check_frontend_cache(reader, report)
    deps = preservation(reader, report, {}, refs)
    evidence(); require(secret_hash(reader) == key_hash, 'TAVILY_SECRET_CHANGED')
    plans = {}
    for role in ('gateway', 'frontend'):
        report['phase'] = 'PATCH_' + role.upper()
        patch, planned = plan(reader.get('deployment', role), role)
        patch_once(reader, role, patch, planned, report)
        plans[role] = planned
        report['phase'] = 'OBSERVE_' + role.upper()
        deployment, ps = wait_rollout(reader, role, planned, report)
        report['phase'] = 'VERIFY_' + role.upper() + '_RUNTIME'
        report['runtime'][role] = []
        helper = {'__name__': 'combined_frontend_checks'}; exec(FRONTEND_HELPER, helper)
        for i, p in enumerate(ps):
            if role == 'gateway':
                outcome = run_child(reader, p, dict(mode='runtime', expected=NEW_SOURCES, tavily_key_sha256=key_hash,
                                                    database=i == 0, check_api=i == 0))
                require(outcome.get('status') == 'COMBINED_RUNTIME_ACCEPTED', 'GATEWAY_RUNTIME_NOT_ACCEPTED')
            else:
                before = identity(p)
                raw = reader.read(BASE + ['exec', p['metadata']['name'], '-c', 'frontend', '--', 'node', '-e',
                    helper['runtime_code'](), json.dumps(FRONTEND_PINS)], timeout=45, cap=65536)
                outcome = json.loads(raw)
                require(outcome.get('status') == 'PASS' and outcome.get('sources_verified') is True, 'FRONTEND_RUNTIME_NOT_ACCEPTED')
                require(identity(reader.get('pod', p['metadata']['name'])) == before, 'FRONTEND_SAMPLE_CHANGED')
            report['runtime'][role].append(dict(sample=identity(p), observation=outcome)); save(report)
            if i % 5 == 0 or i + 1 == len(ps):
                print(json.dumps(dict(phase='ACCEPT_' + role.upper(), checked=i + 1, total=len(ps))), flush=True)
        deps = preservation(reader, report, plans, refs)
        final = accepted_pods(reader, deps[role], role, True)
        require(final is not None and [identity(p) for p in final] == [identity(p) for p in ps], 'ACCEPTED_POD_SET_CHANGED')
    gateway = accepted_pods(reader, deps['gateway'], 'gateway', True)
    require(gateway is not None, 'FINAL_GATEWAY_NOT_READY')
    report['phase'] = 'VERIFY_NGINX_ENTRY'
    report['edge_check'] = run_child(reader, gateway[0], dict(mode='edge'), timeout=110)
    require(report['edge_check'].get('status') == 'COMBINED_EDGE_HTTP_ACCEPTED', 'NGINX_EDGE_NOT_ACCEPTED')
    report['phase'] = 'FINAL_IDENTITY_AND_PRESERVATION_CHECK'
    deps = preservation(reader, report, plans, refs)
    for role in ('gateway', 'frontend'):
        require(stable(deps[role]), 'FINAL_DEPLOYMENT_NOT_STABLE')
        final_pods = accepted_pods(reader, deps[role], role, True)
        require(final_pods is not None and [identity(p) for p in final_pods]
                == [r['sample'] for r in report['runtime'][role]], 'FINAL_RUNTIME_POD_SET_CHANGED')
    require(secret_hash(reader) == key_hash, 'FINAL_TAVILY_SECRET_CHANGED')
    report.update(status=ACCEPTED, phase='COMPLETE', after=plans, runtime_accepted=True,
                  other_deployments_preserved=True, service_and_configuration_metadata_preserved=True,
                  tavily_gateway_environment_accepted=True, default_model_preserved=True,
                  candidate_images_deployed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate-combined-features-once', required=True, action='store_true')
    parser.add_argument('--allow-active-task-interruption', required=True, action='store_true')
    parser.parse_args(); os.umask(0o077)
    report = dict(schema='portal-combined-features-activation.v1', status='INCOMPLETE', operation=uuid.uuid4().hex,
                  once_claimed=False, patches=[], temporary_jobs=[], runtime={}, last_rollout_counts={},
                  automatic_retry=False, automatic_rollback=False, provider_requests=0, credential_values_exported=False,
                  production_workload_modified=False, production_workload_mutation_may_have_occurred=False,
                  active_task_interruption_operator_authorized=True, schema_migration_requested=False,
                  v41_enabled=False, work_update_included=False, runner_rollout_requested=False,
                  browser_acceptance_pending=True, business_acceptance_pending=True)
    started = time.monotonic()
    try: execute(Reader(), report)
    except BaseException as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'ACTIVATION_INCOMPLETE', raw_errors_exported=False)
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    if report['once_claimed']:
        save(report)
        raw = (Path(CONTROL) / 'REPORT.json').read_bytes()
        report.update(report_path=CONTROL + '/REPORT.json', report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    summary = {k: v for k, v in report.items() if k not in ('before_deployments', 'before_services', 'before_configuration_metadata', 'runtime')}
    summary['accepted_pods'] = {k: len(v) for k, v in report['runtime'].items()}
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if report['status'] == ACCEPTED else 1


if __name__ == '__main__': raise SystemExit(main())
