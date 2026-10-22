#!/usr/bin/env python3
"""Python 3.7+ release PREPARATION ONLY; no live Kubernetes writes.

Bind the accepted 8/16 mixed load receipts, retain the failed 32 result, and
server-dry-run a separate pool. Never restart the in-memory old dispatcher.
This is not a switch command, a drain proof, or a provider submission tool.
"""
import argparse
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
import types

TOOL = Path('/var/lib/mineru-stepped-load-fix1-tool-xnk0Pett')
CORE_SHA = '601778bd77ebfee7a222f1c33cd71a7bc8506fbf754625ba94980ac01c414916'
MANIFEST_SHA = '37986574650ef20418be921fbbf4a8d28ee82bd939c629335727f8f299840295'
LOAD = Path('/var/lib/mineru-stepped-load-20261015-r1-fix3')
LOAD_SHA = '6c390d559d4a5cb7dd518deb6ef190b674981942867c9c9451d5cc69fcf6c17f'
WORKER = 'deer-flow-mineru-adapter'
DISPATCHER = 'deer-flow-mineru-dispatcher'
NEW_DISPATCHER = 'deer-flow-mineru-dedicated-dispatch-r1'
NEW_WORKERS = 'deer-flow-mineru-dedicated-workers-r1'
NEW_PREVIEW = 'deer-flow-mineru-dedicated-preview-r1'
RELEASE = 'a5c21e7fbe155ea3'
LABEL = 'deerflow-mineru-release'
ROLE = 'deerflow-mineru-role'
BASE_URL = 'https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-02'
TASK_URL = BASE_URL + '/dynamic/member1/mineru-for-zy-agent/tasks'
RESOURCES = {k: {'cpu': '2', 'memory': '4Gi', 'ephemeral-storage': '4Gi'}
             for k in ('requests', 'limits')}
TARGET = 16
core = None
STAGE = 'START'


class PrepareFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise PrepareFailure(label)


def emit(row):
    print(json.dumps(row, sort_keys=True), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_dir(path):
    require(path.is_absolute(), 'absolute_directory_required')
    for p in reversed([path] + list(path.parents)):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_directory')


def read_locked(path, digest):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size < 2*1024**2,
                'unsafe_locked_file')
        raw = f.read(2*1024**2)
        after = os.fstat(f.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                          s.st_mode, s.st_uid, s.st_gid, s.st_nlink)
    require(identity(before) == identity(after) == identity(path.lstat())
            and len(raw) == before.st_size and sha(raw) == digest, 'locked_file_changed')
    return raw


def load_core():
    read_locked(TOOL/'PACKAGE.SHA256', MANIFEST_SHA)
    raw = read_locked(TOOL/'stepped_load.py', CORE_SHA)
    module = types.ModuleType('frozen_mineru_load_for_prepare')
    module.__file__ = str(TOOL/'stepped_load.py')
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    module.bootstrap()
    return module


def load_acceptance():
    report = json.loads(read_locked(LOAD/'RESULT.json', LOAD_SHA))
    require(report.get('production_workloads_mutated') is False
            and report.get('failed_stage_count') == 32
            and report.get('failed_check') == 'controller_failed_no_resubmission'
            and [r.get('count') for r in report.get('stages', [])] == [8, 16],
            'load_report_boundary_changed')
    accepted = []
    for row in report['stages']:
        count = row['count']
        core.accept(row, count)
        root = LOAD/('stage-' + str(count))
        raw = core.pf.read_private(root/'RUNTIME-RESULT.json')
        require(json.loads(raw) == row, 'runtime_receipt_not_bound_to_report')
        passed = json.loads(core.pf.read_private(root/'PASS.json'))
        require(passed.get('stage') == count and passed.get('runtime') == 'PASS'
                and passed.get('cleanup') == 'CONFIRMED_ABSENT', 'stage_not_complete')
        accepted.append(dict(concurrency=count, runtime_sha256=sha(raw),
                             metrics=core.statistics(row), acceptance='PASS'))
    return dict(source_report_sha256=LOAD_SHA, accepted_stages=accepted,
                selected_global_slots=TARGET, original_32_failure='RETAINED_NOT_REWRITTEN',
                uncertain_submit=dict(stage=32, worker='worker-0', profile='conversation',
                    submitted_utc='2026-10-15T12:20:32.328Z', accepted_task_id=None,
                    provider_outcome='UNKNOWN', resubmission_allowed=False,
                    pending_flag_is_not_provider_activity_proof=True),
                concurrent_32='NOT_ACCEPTED', concurrent_64='NOT_TESTED',
                business_document_quality='NOT_TESTED',
                quality_policy='synthetic-footer-chart-warning-20261015-r1')


def surge_count(dep):
    spec = dep['spec']
    replicas = spec.get('replicas', 1)
    require(type(replicas) is int and 0 < replicas < TARGET, 'old_replicas_out_of_range')
    strategy = spec.get('strategy', {})
    if strategy.get('type', 'RollingUpdate') == 'Recreate':
        return 0
    require(strategy.get('type', 'RollingUpdate') == 'RollingUpdate', 'unknown_old_strategy')
    value = strategy.get('rollingUpdate', {}).get('maxSurge', '25%')
    if type(value) is int:
        require(0 <= value < TARGET, 'invalid_old_surge')
        return value
    require(isinstance(value, str) and re.fullmatch(r'\d{1,3}%', value), 'invalid_old_surge')
    percent = int(value[:-1])
    require(percent <= 100, 'old_surge_too_large')
    return int(math.ceil(replicas * percent / 100.0))


def transition_budget(dep, pods):
    """Reserve desired + surge + terminating, not merely a busy/ready snapshot."""
    active = [p for p in pods if p.get('status', {}).get('phase') not in ('Succeeded', 'Failed')]
    terminating = sum(bool(p['metadata'].get('deletionTimestamp')) for p in active)
    desired = dep['spec'].get('replicas', 1)
    reserve = max(desired + surge_count(dep), len(active) - terminating) + terminating
    require(0 < reserve < TARGET, 'no_overlap_capacity_under_global_16')
    return dict(target_slots=TARGET, old_desired=desired, old_surge=surge_count(dep),
                old_reserved_slots=reserve, new_initial_slots=TARGET-reserve,
                new_final_slots=TARGET, old_pods_restarted=False,
                queue_empty_required=False, old_inflight_finish_required_before_retirement=True,
                final_expansion_requires_old_admission_closed_and_old_pool_retired=True,
                cap_scope='LOCAL_ADAPTER_PARSE_SLOTS_NOT_PROVIDER_JOB_COUNT',
                external_admin_changes_require_new_review=True)


def make_plan(rows, count, controller_nodes):
    candidates = [r for r in rows if r['node'] in controller_nodes
                  and r['additional_isolated_worker_slots_planning_only'] > 0]
    require(candidates, 'no_eligible_dispatcher_resource_slot')
    selected = min(candidates, key=lambda r: (-r['additional_isolated_worker_slots_planning_only'], r['node']))['node']
    remaining = copy.deepcopy(rows)
    for r in remaining:
        if r['node'] == selected:
            r['additional_isolated_worker_slots_planning_only'] -= 1
    distributed = core.pf.distribute(remaining, count)
    require(distributed is not None, 'insufficient_fresh_resource_headroom')
    flattened = [node for node, number in sorted(distributed.items()) for _ in range(number)]
    require(len(flattened) == count, 'resource_allocation_incomplete')
    return dict(controller=selected, workers=flattened, total_slots=count+1)


def labels(role):
    require(role in ('worker', 'dispatcher'), 'invalid_new_role')
    return {'app': 'deer-flow-mineru-dedicated-' + role + '-r1', LABEL: RELEASE, ROLE: role}


def env_set(container, replacements):
    names = [e['name'] for e in container.get('env', [])]
    require(len(names) == len(set(names)), 'duplicate_environment')
    values = copy.deepcopy(replacements)
    container['env'] = [values.pop(e['name']) if e['name'] in values else e
                        for e in container.get('env', [])]
    container['env'].extend(values[k] for k in sorted(values))


def deployment(old, role, node, replicas, minimum):
    container_name = 'adapter' if role == 'worker' else 'dispatcher'
    require(node in core.guard.NODES and type(replicas) is int and 0 < replicas <= TARGET,
            'deployment_allocation_invalid')
    pod = copy.deepcopy(old['spec']['template']['spec'])
    require(len(pod['containers']) == 1 and pod['containers'][0]['name'] == container_name
            and not pod.get('initContainers') and not pod.get('ephemeralContainers'),
            'unexpected_production_containers')
    require(not any(pod.get(k, False) for k in ('hostNetwork', 'hostPID', 'hostIPC'))
            and not pod.get('nodeName'), 'unexpected_host_or_node_binding')
    for key in ('podAffinity', 'podAntiAffinity'):
        require(not pod.get('affinity', {}).get(key, {}).get('requiredDuringSchedulingIgnoredDuringExecution'),
                'required_pod_affinity_requires_review')
    old_preferences = {k: pod.get('affinity', {}).pop(k, None) for k in ('podAffinity', 'podAntiAffinity')}
    container = pod['containers'][0]
    require(container['image'] == core.pf.host.OLD_IMAGE
            and container.get('imagePullPolicy') == 'Never', 'old_container_identity_changed')
    container['image'] = core.inv.CANDIDATE['image']
    container['resources'] = copy.deepcopy(RESOURCES)
    replacements = {'MINERU_ADAPTER_MODE': {'name': 'MINERU_ADAPTER_MODE', 'value': role}}
    if role == 'worker':
        replacements.update({k: {'name': k, 'value': v} for k, v in {
            'MINERU_API_URL': BASE_URL, 'MINERU_TASK_QUERY_URL': TASK_URL,
            'MINERU_REQUEST_MODE': 'async', 'MINERU_TRUST_ENV': 'false'}.items()})
        replacements['MINERU_API_TOKEN'] = {'name': 'MINERU_API_TOKEN', 'valueFrom': {
            'secretKeyRef': {'name': core.SECRET, 'key': 'MINERU_API_TOKEN'}}}
    else:
        replacements.update({k: {'name': k, 'value': v} for k, v in {
            'MINERU_DISPATCH_BACKEND_HOST': NEW_WORKERS + '.deer-flow.svc.cluster.local',
            'MINERU_DISPATCH_MIN_BACKENDS': str(minimum)}.items()})
    env_set(container, replacements)
    pod.setdefault('nodeSelector', {})['kubernetes.io/hostname'] = node
    # Exact reviewed per-node allocation replaces spread heuristics only in NEW
    # Deployments. Old deployments, selectors and resources remain untouched.
    old_spread = pod.pop('topologySpreadConstraints', [])
    new_labels = labels(role)
    suffix = node.replace('ecs-zdz-pro-', 'ecs-')
    name = ('deer-flow-mineru-dedicated-r1-w-' + suffix) if role == 'worker' else NEW_DISPATCHER
    selector = dict(new_labels, **{'deerflow-mineru-placement': node})
    obj = {'apiVersion': 'apps/v1', 'kind': 'Deployment',
           'metadata': {'name': name, 'namespace': 'deer-flow', 'labels': selector},
           'spec': {'replicas': replicas, 'strategy': {'type': 'Recreate'},
                    'revisionHistoryLimit': 2, 'progressDeadlineSeconds': 600,
                    'selector': {'matchLabels': selector},
                    'template': {'metadata': {'labels': selector,
                        'annotations': copy.deepcopy(old['spec']['template']['metadata'].get('annotations', {}))},
                                 'spec': pod}}}
    return obj, dict(name=name, role=role, node=node, replicas=replicas,
        old_spread_replaced_by_exact_node_plan=bool(old_spread), resources=RESOURCES,
        old_preferred_pod_affinity_replaced=any(old_preferences.values()),
        strategy='Recreate_ONLY_NEW_IDLE_POOL_NO_ROLLING_DRAIN_GUARANTEE')


def service(name, role, headless, ports):
    spec = {'type': 'ClusterIP', 'selector': labels(role), 'ports': copy.deepcopy(ports)}
    if headless:
        spec['clusterIP'] = 'None'
    return dict(apiVersion='v1', kind='Service',
                metadata=dict(name=name, namespace='deer-flow', labels={LABEL: RELEASE}), spec=spec)


def routes_disjoint(objects, old_services):
    new_pods = [o['spec']['template']['metadata']['labels'] for o in objects if o['kind'] == 'Deployment']
    for old in old_services:
        selector = old['spec'].get('selector')
        if selector:
            require(not any(all(p.get(k) == v for k, v in selector.items()) for p in new_pods),
                    'new_pool_selected_by_existing_service:' + old['metadata']['name'])


def manifests(deps, plan, budget, controls, all_services):
    rows, objects = [], []
    for node in sorted(set(plan['workers'])):
        obj, row = deployment(deps[WORKER], 'worker', node,
                              plan['workers'].count(node), budget['new_initial_slots'])
        rows.append(row)
        objects.append(obj)
    obj, row = deployment(deps[DISPATCHER], 'dispatcher', plan['controller'], 1,
                          budget['new_initial_slots'])
    objects.append(obj)
    rows.append(row)
    services = {s['name']: s for s in controls['services']}
    for name, role, headless, original in [(NEW_WORKERS, 'worker', True, WORKER+'-workers'),
                                          (NEW_PREVIEW, 'dispatcher', False, WORKER)]:
        ports = services[original]['spec']['ports']
        require(len(ports) == 1 and ports[0]['port'] == 50052
                and ports[0].get('protocol', 'TCP') == 'TCP', 'service_port_requires_review')
        objects.append(service(name, role, headless, ports))
    routes_disjoint(objects, all_services)
    require(sum(r['replicas'] for r in rows if r['role'] == 'worker') == budget['new_initial_slots'],
            'worker_total_mismatch')
    return objects, rows


def subset(expected, actual, path='spec'):
    """Kubernetes defaults may add fields; supplied values/lists must not change."""
    if isinstance(expected, dict):
        require(isinstance(actual, dict), 'admission_type:' + path)
        for key, value in expected.items():
            # Empty maps may be omitted by API serialization (e.g. annotations).
            subset(value, actual.get(key, {} if value == {} else None), path+'.'+key)
    elif isinstance(expected, list):
        require(isinstance(actual, list) and len(expected) == len(actual), 'admission_list:' + path)
        for i, value in enumerate(expected):
            subset(value, actual[i], path+'.'+str(i))
    else:
        require(type(expected) is type(actual) and expected == actual, 'admission_changed:' + path)


def validate_admission(obj, answer):
    require(answer.get('kind') == obj['kind'] and answer.get('metadata', {}).get('name') == obj['metadata']['name'],
            'dry_run_object_identity')
    subset(obj['spec'], answer.get('spec'))
    if obj['kind'] == 'Deployment':
        before, after = obj['spec']['template']['spec'], answer['spec']['template']['spec']
        for key in ('initContainers', 'ephemeralContainers', 'volumes', 'hostAliases'):
            require(after.get(key, []) == before.get(key, []), 'admission_extra:' + key)
        for key in ('hostNetwork', 'hostPID', 'hostIPC'):
            require(not after.get(key, False), 'admission_host_access:' + key)
        require(len(after['containers']) == 1, 'admission_extra_container')
        for key in ('env', 'envFrom', 'volumeMounts', 'securityContext', 'command', 'args', 'resources'):
            require(after['containers'][0].get(key) == before['containers'][0].get(key),
                    'admission_container_changed:' + key)
        require(after.get('securityContext') == before.get('securityContext'), 'admission_security_context')
    else:
        require(not answer['spec'].get('externalIPs') and not answer['spec'].get('externalName')
                and not any(p.get('nodePort') for p in answer['spec']['ports']), 'admission_external_service')


def read_resource(resource, name=None):
    allowed = {'services', 'networkpolicies', 'resourcequotas', 'limitranges', 'deployments'}
    require(resource in allowed, 'read_resource_out_of_scope')
    args = ['kubectl', '--request-timeout=20s', '-n', 'deer-flow', 'get', resource]
    if name:
        require(re.fullmatch('[a-z0-9-]{1,63}', name), 'invalid_resource_name')
        args += [name, '--ignore-not-found']
    raw = core.inv.bounded_capture(args + ['--show-managed-fields=false', '-o', 'json'])
    return json.loads(raw) if raw.strip() else None


def secret_metadata():
    # kubectl formats only metadata and key NAMES; secret values never enter this
    # Python process, reports, command arguments, or prepared manifests.
    template = ('{"uid":"{{.metadata.uid}}","immutable":{{if .immutable}}true{{else}}false{{end}},'
                '"type":"{{.type}}","keys":[{{range $key,$value := .data}}"{{$key}}",{{end}}""]}')
    raw = core.inv.bounded_capture(['kubectl', '--request-timeout=20s', '-n', 'deer-flow',
        'get', 'secret', core.SECRET, '-o', 'go-template='+template])
    row = json.loads(raw)
    require(row == {'uid': core.SECRET_UID, 'immutable': True, 'type': 'Opaque',
                    'keys': ['MINERU_API_TOKEN', '']}, 'staged_secret_identity_changed')
    refs = ('{range .items[*]}{.metadata.name}{"\\t"}{..secretKeyRef.name}{" "}'
            '{..secretRef.name}{" "}{..secretName}{" "}{..secret.name}{"\\n"}{end}')
    raw = core.inv.bounded_capture(['kubectl', '--request-timeout=20s', '-n', 'deer-flow', 'get',
        'pods,deployments.apps,statefulsets.apps,daemonsets.apps,jobs.batch,cronjobs.batch,replicasets.apps',
        '-o', 'jsonpath='+refs])
    for line in raw.decode('utf-8').splitlines():
        require('\t' in line and core.SECRET not in line.split('\t', 1)[1].split(),
                'new_secret_already_live_requires_review')
    return dict(name=core.SECRET, uid=core.SECRET_UID, immutable=True, values_read_into_tool=False)


def dry_create(obj, root):
    name, kind = obj['metadata']['name'], obj['kind']
    require(kind in ('Deployment', 'Service'), 'dry_run_kind_out_of_scope')
    require(read_resource('deployments' if kind == 'Deployment' else 'services', name) is None,
            'candidate_name_already_exists:' + name)
    filename = name+'.proposed.private.json'
    core.save(root, filename, obj)
    emit(dict(bluegreen_prepare='RUNNING', stage='SERVER_DRY_RUN_ONLY', kind=kind, name=name))
    # No user parameter can remove dry-run, and no apply/create call exists.
    raw = core.inv.bounded_capture(['kubectl', '--request-timeout=20s', '-n', 'deer-flow',
        'create', '--dry-run=server', '-f', str(root/filename), '-o', 'json'])
    answer = json.loads(raw)
    validate_admission(obj, answer)
    core.save(root, name+'.admitted.private.json', answer)
    return dict(name=name, kind=kind, server_dry_run='PASS',
                proposed_spec_sha256=core.inv.digest(obj['spec']),
                admitted_spec_sha256=core.inv.digest(answer['spec']))


def route_patch(old, new_selector):
    require(old['metadata']['name'] == WORKER and old['spec'].get('type', 'ClusterIP') == 'ClusterIP',
            'public_route_target_changed')
    return [{'op': 'test', 'path': '/metadata/uid', 'value': old['metadata']['uid']},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': old['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/spec', 'value': old['spec']},
            {'op': 'replace', 'path': '/spec/selector', 'value': new_selector}]


def dry_route(old, root):
    core.save(root, 'PUBLIC-SERVICE.before.private.json', old)
    patch = route_patch(old, labels('dispatcher'))
    core.save(root, 'PUBLIC-SERVICE.forward.dry-run.private.json', patch)
    raw = core.inv.bounded_capture(['kubectl', '--request-timeout=20s', '-n', 'deer-flow',
        'patch', 'service', WORKER, '--type=json', '--dry-run=server', '--patch-file',
        str(root/'PUBLIC-SERVICE.forward.dry-run.private.json'), '-o', 'json'])
    result = json.loads(raw)
    expected = copy.deepcopy(old['spec'])
    expected['selector'] = labels('dispatcher')
    require(result.get('metadata', {}).get('uid') == old['metadata']['uid']
            and result.get('spec') == expected, 'route_dry_run_changed_more_than_selector')
    core.save(root, 'PUBLIC-SERVICE.forward.admitted.private.json', result)
    return dict(server_dry_run='PASS', uid=old['metadata']['uid'],
                original_spec_sha256=core.inv.digest(old['spec']),
                proposed_spec_sha256=core.inv.digest(expected),
                service_ip_ports_preserved=True, live_route_changed=False,
                rollback='CAS_SELECTOR_BACK_TO_UNMODIFIED_OLD_POOL_AFTER_LIVE_HEALTH_CHECK',
                old_pool_retirement_invalidates_immediate_route_only_rollback=True)


def live_policies():
    result = {}
    for kind in ('networkpolicies', 'resourcequotas', 'limitranges'):
        items = read_resource(kind)['items']
        require(len(items) <= 128, 'policy_inventory_limit')
        result[kind] = [dict(name=x['metadata']['name'], uid=x['metadata']['uid'],
                             spec_sha256=core.inv.digest(x['spec']), spec=x['spec']) for x in items]
        result[kind].sort(key=lambda x: x['name'])
    return result


def diagnostic(exc):
    data = core.safe_diagnostic(exc) if core is not None else {'error_type': type(exc).__name__}
    if isinstance(exc, PrepareFailure) and re.fullmatch(r'[A-Za-z0-9_:./-]{1,240}', str(exc)):
        data['failed_check'] = str(exc)
    data.update(failed_stage=STAGE, raw_error_printed=False)
    return data


def main():
    global core, STAGE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-only', action='store_true', required=True)
    parser.parse_args()
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    safe_dir(Path('/var/lib'))
    root = Path(tempfile.mkdtemp(prefix='mineru-bluegreen16-prepare-', dir='/var/lib'))
    report = dict(mineru_bluegreen16_prepare='FAIL', ready_for_rollout=False,
        ready_for_stage_review=False, production_workloads_mutated=False,
        production_configuration_modified=False, provider_requests=0,
        old_queue_mutated=False, credentials_printed=False, credentials_saved=False,
        old_pool_restarted=False, images_imported=False, concurrency_changed=False,
        dry_run_only=True, automatic_retry=False, rollout='NOT_PERFORMED')
    emit(dict(bluegreen_prepare_evidence=str(root), mode='READS_AND_SERVER_DRY_RUN_ONLY'))
    try:
        STAGE = 'LOCKED_HELPERS_AND_ACCEPTED_16'
        core = load_core()
        report['load_review'] = load_acceptance()
        report['bindings'], controls = core.pf.receipt_bindings()
        report['candidate'] = core.inv.CANDIDATE
        STAGE = 'LIVE_PRODUCTION_BASELINE'
        deps, pool = core.baselines(controls)
        for name, dep in deps.items():
            core.save(root, name+'.before.private.json', dep)
        report['baselines'] = {n: dict(uid=d['metadata']['uid'], generation=d['metadata']['generation'],
            spec_sha256=core.inv.digest(d['spec'])) for n, d in deps.items()}
        report['activity'] = core.pf.activity(deps)
        require(report['activity']['ready_health_complete'], 'old_health_incomplete')
        require(not controls['hpas'], 'autoscaler_coordination_required')
        budget = transition_budget(deps[WORKER], core.inv.owned_pods(deps[WORKER]))
        report['concurrency_plan'] = budget
        emit(dict(bluegreen_prepare='RUNNING', stage='FRESH_RESOURCE_REVIEW', **budget))
        STAGE = 'FRESH_RESOURCE_REVIEW'
        report['resources'] = core.resources(deps)
        # Existing estimator reserves 1 GiB/fixture. Production proposal reserves
        # 4 GiB of disk per new Pod, without borrowing the node's safety reserve.
        for row in report['resources']:
            disk_slots = [max(0, (f['available_bytes']-f['planning_reserve_bytes'])//(4*core.pf.GIB))
                          if f['inodes_headroom'] else 0 for f in row['metrics']['filesystems'].values()]
            row['additional_isolated_worker_slots_planning_only'] = min(
                [row['additional_isolated_worker_slots_planning_only']] + disk_slots)
            row['new_production_disk_budget_GiB'] = 4
        nodes = core.inv.get('nodes')['items']
        controller_nodes = [n['metadata']['name'] for n in nodes
            if n['metadata']['name'] in core.guard.NODES
            and core.inv.node_rules_match(n, deps[DISPATCHER]['spec']['template']['spec'])]
        plan = make_plan(report['resources'], budget['new_initial_slots'], controller_nodes)
        report['initial_plan'] = plan
        # Planning only: never scale a new full-16 pool alongside the old pool.
        final = core.pf.distribute(report['resources'], TARGET+1)
        report['full_16_resource_reserve_planning'] = final
        report['full_16_scheduling_guaranteed'] = False
        report['initial_resource_checks'] = core.monitoring(plan)
        for row in report['initial_resource_checks']:
            number = plan['workers'].count(row['node']) + int(plan['controller'] == row['node'])
            require(all(f['available_bytes'] >= f['planning_reserve_bytes'] + number*4*core.pf.GIB
                        for f in row['metrics']['filesystems'].values()), 'production_disk_headroom_changed')
        STAGE = 'CREDENTIAL_CA_AND_POLICY_REVIEW'
        report['credential'] = secret_metadata()
        report['ca_sha256'] = sha(core.ca_bytes())
        report['policies'] = live_policies()
        report['termination'] = core.pf.termination_and_routing(deps)
        all_services = read_resource('services')['items']
        objects, report['new_pool'] = manifests(deps, plan, budget, controls, all_services)
        core.save(root, 'OBJECTS.proposed.private.json', objects)
        STAGE = 'SERVER_DRY_RUN_ONLY'
        report['dry_runs'] = [dry_create(o, root) for o in objects]
        old = next(s for s in all_services if s['metadata']['name'] == WORKER)
        report['route_plan'] = dry_route(old, root)
        STAGE = 'FINAL_BASELINE_RECHECK'
        after, pool_after = core.baselines(controls)
        require(pool_after == pool and all(after[n]['spec'] == deps[n]['spec'] for n in deps),
                'baseline_changed_during_prepare')
        require(live_policies() == report['policies'], 'policies_changed_during_prepare')
        routes_disjoint(objects, read_resource('services')['items'])
        report['remaining_gates'] = [
            'REVIEW_NEW_LABEL_NETWORK_POLICY_AND_RESOURCE_PLAN',
            'STAGE_NEW_POOL_WITHOUT_CHANGING_PUBLIC_ROUTE',
            'EXACT_POD_IMAGE_CA_CONFIG_AND_PRIVATE_SERVICE_READINESS',
            'CAS_PUBLIC_SELECTOR_SWITCH_KEEP_OLD_POOL_ALIVE',
            'VERIFY_ROUTE_CONVERGENCE_AND_OLD_HTTP_INFLIGHT_FINISH',
            'REVIEW_OLD_POOL_RETIREMENT_BEFORE_FINAL_16_EXPANSION',
            'POST_SWITCH_WEKNORA_AND_CONVERSATION_END_TO_END']
        if final is None:
            report['remaining_gates'].insert(0, 'FULL_16_RESOURCE_HEADROOM_REVIEW')
        report.update(mineru_bluegreen16_prepare='PASS', ready_for_stage_review=True,
                      production_baseline='UNCHANGED')
    except BaseException as exc:
        report.update(diagnostic(exc))
    report['utc'] = core.pf.utc().isoformat() if core is not None else None
    # Independent exclusive save works even when frozen helper loading fails.
    raw = (json.dumps(report, sort_keys=True, indent=2)+'\n').encode('utf-8')
    safe_dir(root)
    fd = os.open(str(root/'RESULT.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    emit({k: report.get(k) for k in ('mineru_bluegreen16_prepare', 'failed_stage', 'failed_check',
        'concurrency_plan', 'ready_for_stage_review', 'remaining_gates')})
    emit(dict(report=str(root/'RESULT.json'), report_sha256=sha(raw),
              production_workloads_mutated=False, provider_requests=0, ready_for_rollout=False,
              private_manifests_exported=False, automatic_retry=False))
    # Sanitized report only. If /home/cnooc is unsafe, retain private evidence;
    # never chmod a user's home or weaken root evidence to make downloads work.
    if core is not None:
        try:
            print('download_directory='+core.pf.download_copy(raw), flush=True)
        except Exception as exc:
            emit(dict(download_copy='UNAVAILABLE_PRIVATE_REPORT_RETAINED', error_type=type(exc).__name__))
    return 0 if report['mineru_bluegreen16_prepare'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(mineru_bluegreen16_prepare='FAIL', **diagnostic(exc),
                  production_workloads_mutated=False, provider_requests=0, automatic_retry=False))
        sys.exit(1)
