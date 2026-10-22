#!/usr/bin/env python3
"""Single CAS public Service selector switch; NEVER retire or restart either pool.

Python 3.7+. Explicit rollback switches only the route after old-pool checks.
No queue-empty requirement. Endpoint convergence is NOT proof of old HTTP drain.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import time

HERE = Path(__file__).absolute().parent
STAGED = Path('/var/lib/mineru-bluegreen16-stage-20261015-r1-fix1')
STAGED_SHA = '344c3b216135b994344c9aaa88c3797def297b6a1b6141ae9ef194941b558975'
STAGE_SOURCE_SHA = '7818d32892d0bb51dd249f6c430dd27a32304972f798d40104ef383c6c2f83af'
ROOT = Path('/var/lib/mineru-bluegreen16-switch-20261015-r1')
BACK = Path('/var/lib/mineru-bluegreen16-switch-20261015-r1-rollback1')
# Filled by the packager in its generated copy; never loaded from user input.
ROUTE_PROBE_SHA = '__ROUTE_PROBE_SHA256__'
PHASE = 'BOOTSTRAP'
s = None


class SwitchFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise SwitchFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def bootstrap():
    global s
    # Only the package-local frozen module import precedes its locked reader;
    # starter verifies the entire root-private package SHA before execution.
    import importlib.util
    spec = importlib.util.spec_from_file_location('switch_frozen_stage', HERE/'stage_bluegreen16.py')
    s = importlib.util.module_from_spec(spec)
    # Do not execute an unchecked helper even if run without the starter.
    path = HERE/'stage_bluegreen16.py'
    import stat
    for part in reversed([HERE]+list(HERE.parents)):
        st = part.lstat()
        require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'unsafe_tool_ancestor')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        st = os.fstat(f.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1
                and not st.st_mode & 0o022 and 0 < st.st_size < 262144, 'unsafe_stage_module')
        raw = f.read(262144)
    require(sha(raw) == STAGE_SOURCE_SHA, 'frozen_stage_module_hash')
    exec(compile(raw, str(path), 'exec'), s.__dict__)
    stage_code = s.bootstrap()
    route_code = s.locked(HERE/'switch_route_probe.py', ROUTE_PROBE_SHA).decode('utf-8')
    return stage_code, route_code


def report_contract(report):
    require(report.get('mineru_bluegreen16_stage') == 'PASS'
            and report.get('ready_for_switch_review') is True
            and report.get('public_route_changed') is False
            and report.get('existing_workloads_modified') is False
            and report.get('new_worker_slots') == 6 and report.get('new_dispatchers') == 1
            and report.get('provider_http_requests') == 0 and report.get('documents_submitted') == 0,
            'stage_not_accepted')
    created = report.get('created', [])
    require(len(created) == 9 and {r['name']: r['kind'] for r in created} == s.ALLOWED,
            'stage_creation_receipts_incomplete')
    require(len(report.get('pods', [])) == 7 and len(report.get('runtime', [])) == 7
            and all(r.get('receipt', {}).get('stage_runtime') == 'PASS' for r in report['runtime']),
            'stage_runtime_receipts_incomplete')


def bind():
    staged = json.loads(s.locked(STAGED/'RESULT.json', STAGED_SHA))
    report_contract(staged)
    prepared = json.loads(s.locked(s.PREPARED/'RESULT.json', s.REPORT_SHA))
    s.report_contract(prepared)
    require(staged['prepare_report_sha256'] == s.REPORT_SHA
            and staged['concurrency_plan'] == prepared['concurrency_plan'], 'stage_prepare_binding_changed')
    load = s.prep.load_acceptance()
    require(load == staged['load_review'] == prepared['load_review'], 'accepted_16_load_changed')
    bindings, controls = s.core.pf.receipt_bindings()
    require(bindings == prepared['bindings'], 'prior_receipt_chain_changed')
    created = {}
    for row in staged['created']:
        name = row['name']
        obj = json.loads(s.core.pf.read_private(STAGED/(name+'.CREATED.private.json')))
        require(obj.get('kind') == row['kind'] and obj['metadata']['name'] == name
                and obj['metadata'].get('namespace') == 'deer-flow'
                and obj['metadata']['uid'] == row['uid']
                and s.core.inv.digest(obj['spec']) == row['spec_sha256'], 'staged_snapshot_changed')
        s.validate_target(obj)
        created[name] = obj
    before = json.loads(s.core.pf.read_private(s.PREPARED/'PUBLIC-SERVICE.before.private.json'))
    require(before['metadata']['name'] == s.prep.WORKER
            and before['metadata']['uid'] == prepared['route_plan']['uid']
            and s.core.inv.digest(before['spec']) == prepared['route_plan']['original_spec_sha256'],
            'original_route_snapshot_changed')
    candidate_spec = copy.deepcopy(before['spec'])
    candidate_spec['selector'] = s.prep.labels('dispatcher')
    require(s.core.inv.digest(candidate_spec) == prepared['route_plan']['proposed_spec_sha256'],
            'candidate_route_snapshot_changed')
    return dict(staged=staged, prepared=prepared, controls=controls, created=created,
                uid=before['metadata']['uid'], specs={'original': before['spec'], 'candidate': candidate_spec})


def route_state(service, context):
    if not service or service.get('metadata', {}).get('uid') != context['uid']:
        return 'UNKNOWN'
    if service['metadata'].get('deletionTimestamp'):
        return 'UNKNOWN'
    for name, spec in context['specs'].items():
        if service.get('spec') == spec:
            return name
    return 'UNKNOWN'


def public_route(context, expected):
    service = s.get('services', s.prep.WORKER)
    require(route_state(service, context) == expected, 'public_service_changed_requires_review')
    return service


def controls_for(context, mode):
    controls = copy.deepcopy(context['controls'])
    found = 0
    for row in controls['services']:
        if row['name'] == s.prep.WORKER:
            require(row['uid'] == context['uid'] and row['spec'] == context['specs']['original']
                    and row['spec_sha256'] == s.core.inv.digest(context['specs']['original']),
                    'control_public_baseline_changed')
            row.update(spec=context['specs'][mode], spec_sha256=s.core.inv.digest(context['specs'][mode]))
            found += 1
    require(found == 1, 'control_public_service_missing')
    for name in s.SERVICES:
        obj = context['created'][name]
        controls['services'].append(dict(name=name, uid=obj['metadata']['uid'], spec=obj['spec'],
            spec_sha256=s.core.inv.digest(obj['spec'])))
    return controls


def invariants(context, mode):
    # No core.baselines() after switch: its old EndpointSlice assertion would
    # incorrectly reject the intentionally changed route. Bind that ONE service
    # to the precise reviewed target instead; all other controls stay locked.
    s.core.guard.source_guard()
    deps = {n: s.core.inv.get('deployment', n) for n in s.core.inv.BASELINES}
    for name, dep in deps.items():
        s.core.inv.baseline(dep, name)
    current = {}
    for name, original in context['created'].items():
        now = s.get('deployments' if original['kind'] == 'Deployment' else 'services', name)
        require(now and now['metadata']['uid'] == original['metadata']['uid']
                and now['spec'] == original['spec'] and not now['metadata'].get('deletionTimestamp'),
                'staged_object_changed:'+name)
        s.validate_target(now)
        current[name] = now
    combined = dict(deps, **{n: current[n] for n in s.WORKERS+[s.DISPATCHER]})
    s.core.pf.controls_snapshot(combined, controls_for(context, mode))
    require(s.prep.live_policies() == context['prepared']['policies'], 'policies_changed_requires_review')
    budget = s.prep.transition_budget(deps[s.prep.WORKER], s.core.inv.owned_pods(deps[s.prep.WORKER]))
    require(budget == context['prepared']['concurrency_plan'], 'overlap_local_slot_budget_changed')
    others = [v for v in s.get('services')['items']
              if v['metadata']['name'] not in s.SERVICES+[s.prep.WORKER]]
    s.prep.routes_disjoint(list(current.values()), others)
    s.credential_identity()
    require(sha(s.core.ca_bytes()) == context['prepared']['ca_sha256'], 'ca_changed')
    public_route(context, mode)
    return deps


def candidate_ready(context):
    rows = []
    for name in s.WORKERS+[s.DISPATCHER]:
        dep = s.deployment_current(context['created'][name])
        pods = s.core.inv.owned_pods(dep)
        require(len(pods) == 1 and dep.get('status', {}).get('availableReplicas') == 1
                and dep['status'].get('observedGeneration', 0) >= dep['metadata']['generation'],
                'candidate_deployment_not_ready:'+name)
        rows.append(s.validate_pod(dep, pods[0]))
    require(sorted(rows, key=lambda x: x['uid']) == sorted(context['staged']['pods'], key=lambda x: x['uid']),
            'candidate_pod_or_process_replaced_requires_review')
    require({x['metadata']['uid'] for x in s.get('pods', selector=s.prep.LABEL+'='+s.prep.RELEASE)['items']}
            == {x['uid'] for x in rows}, 'unexpected_release_pod')
    workers = [r for r in rows if r['container'] == 'adapter']
    dispatch = [r for r in rows if r['container'] == 'dispatcher']
    require(len(workers) == 6 and len(dispatch) == 1, 'candidate_role_counts')
    s.endpoints(context['created'][s.SERVICES[0]], workers)
    s.endpoints(context['created'][s.SERVICES[1]], dispatch)
    return rows


def old_activity(deps):
    activity = s.core.pf.activity(deps)
    require(activity['ready_health_complete'], 'old_pool_health_incomplete_keep_both_pools')
    return activity


def old_dispatcher_rows(deps):
    rows = []
    for pod in s.core.inv.owned_pods(deps[s.prep.DISPATCHER]):
        status = pod.get('status', {}).get('containerStatuses', [])
        if pod['metadata'].get('deletionTimestamp') or pod.get('status', {}).get('phase') != 'Running':
            continue
        matching = [c for c in status if c.get('name') == 'dispatcher' and c.get('ready') is True]
        if len(matching) != 1:
            continue
        row = matching[0]
        require(row.get('imageID', '').split('://')[-1] in s.core.inv.BASE_IDS, 'old_dispatcher_image_changed')
        rows.append(dict(name=pod['metadata']['name'], uid=pod['metadata']['uid'],
            ip=pod['status']['podIP'], container='dispatcher', container_id=row.get('containerID'),
            node=pod['spec']['nodeName']))
    require(len(rows) == 1 and deps[s.prep.DISPATCHER]['spec']['replicas'] == 1, 'original_dispatcher_identity')
    return rows


def public_health(row, code, pool, caller, root, number):
    before = s.get('pods', row['name'])
    require(before and before['metadata']['uid'] == row['uid']
            and any(c.get('name') == row['container'] and c.get('containerID') == row['container_id']
                    and c.get('ready') is True for c in before.get('status', {}).get('containerStatuses', [])),
            'public_health_probe_caller_changed')
    emit(dict(mineru_switch='PUBLIC_HEALTH_GET', node=row['node'], expected_pool=pool, documents_submitted=0))
    raw = s.call(['exec', row['name'], '-c', row['container'], '--', '/app/.venv/bin/python', '-I', '-B',
                  '-c', code, '--pool', pool, '--caller', caller], timeout=30)
    require(len(raw) <= 32768, 'public_health_receipt_too_large')
    result = json.loads(raw)
    after = s.get('pods', row['name'])
    require(after and after['metadata']['uid'] == row['uid']
            and before.get('status', {}).get('containerStatuses') == after.get('status', {}).get('containerStatuses'),
            'public_probe_process_changed')
    require(result.get('public_service_health') == 'PASS' and result.get('expected_pool') == pool
            and result.get('provider_requests') == 0 and result.get('documents_submitted') == 0,
            'public_service_health_failed')
    s.core.save(root, 'public-health-%02d.json' % number, result)
    return dict(node=row['node'], pod=row['name'], receipt=result)


def make_patch(service, context, source, target):
    require(source != target and {source, target} == {'original', 'candidate'}, 'invalid_route_direction')
    require(route_state(service, context) == source, 'cas_source_route_changed')
    patch = s.prep.route_patch(service, context['specs'][target]['selector'])
    require([x['op'] for x in patch] == ['test', 'test', 'test', 'replace']
            and [x['path'] for x in patch] == ['/metadata/uid', '/metadata/resourceVersion', '/spec', '/spec/selector'],
            'cas_patch_shape_changed')
    return patch


def accept_route_answer(answer, context, target):
    require(answer.get('kind') == 'Service' and answer.get('metadata', {}).get('name') == s.prep.WORKER
            and answer['metadata'].get('namespace') == 'deer-flow'
            and route_state(answer, context) == target, 'route_patch_changed_more_than_selector')


def patch_once(root, service, context, source, target, report):
    patch = make_patch(service, context, source, target)
    s.core.save(root, 'PUBLIC-SERVICE.before.private.json', service)
    s.core.save(root, 'selector.patch.private.json', patch)
    args = ['patch', 'service', s.prep.WORKER, '--type=json', '--patch-file',
            str(root/'selector.patch.private.json'), '-o', 'json']
    emit(dict(mineru_switch='SERVER_DRY_RUN', target=target, live_patch=False))
    answer = json.loads(s.call(args+['--dry-run=server']))
    accept_route_answer(answer, context, target)
    s.core.save(root, 'selector.admitted.private.json', answer)
    # Re-read without rebasing. Any actor changing resourceVersion forces review.
    now = public_route(context, source)
    require(now['metadata']['resourceVersion'] == service['metadata']['resourceVersion'], 'cas_resource_version_changed')
    intent = dict(service=s.prep.WORKER, uid=context['uid'], source=source, target=target,
        resource_version=service['metadata']['resourceVersion'], stage_report_sha256=STAGED_SHA,
        patch_sha256=s.core.inv.digest(patch), target_spec_sha256=s.core.inv.digest(context['specs'][target]))
    s.core.save(root, 'PATCH-REQUESTED.json', intent)
    report['live_patch_attempted'] = True
    emit(dict(mineru_switch='CAS_PUBLIC_SELECTOR', source=source, target=target, old_pool_kept=True))
    # Exactly one live mutating request. Never auto retry/rollback an unknown response.
    answer = json.loads(s.call(args))
    s.core.save(root, 'PATCH-ACK.private.json', answer)
    accept_route_answer(answer, context, target)
    report['live_patch_acknowledged'] = True
    return answer


def confirm_endpoints(context, target, expected):
    good = []
    for index in range(3):
        service = public_route(context, target)
        row = s.wait_endpoints(service, expected)
        good.append(row)
        if index < 2:
            time.sleep(5)
    return dict(samples=good, old_http_connections_drained=False,
                meaning='ENDPOINT_CONTROL_PLANE_ONLY_NOT_ALL_EXISTING_CONNECTIONS')


def rollback_intent(context):
    path = ROOT/'PATCH-REQUESTED.json'
    require(path.exists(), 'no_forward_patch_intent_rollback_not_authorized_by_evidence')
    intent = json.loads(s.core.pf.read_private(path))
    require(intent.get('stage_report_sha256') == STAGED_SHA and intent.get('service') == s.prep.WORKER
            and intent.get('uid') == context['uid'] and intent.get('source') == 'original'
            and intent.get('target') == 'candidate'
            and intent.get('target_spec_sha256') == s.core.inv.digest(context['specs']['candidate']),
            'forward_patch_intent_changed')


def switch(root, context, stage_code, route_code, rollback, report):
    global PHASE
    source, target = ('candidate', 'original') if rollback else ('original', 'candidate')
    PHASE = 'FRESH_BOTH_POOL_AND_ROUTING_REVIEW'
    if rollback:
        rollback_intent(context)
    deps = invariants(context, source)
    report['old_activity_before'] = old_activity(deps)
    original_rows = old_dispatcher_rows(deps)
    candidate_rows = None
    if not rollback:
        candidate_rows = candidate_ready(context)
        s.endpoints(public_route(context, source), original_rows)
        report['runtime_before'] = []
        workers = [r for r in candidate_rows if r['container'] == 'adapter']
        for row in candidate_rows:
            report['runtime_before'].append(s.probe_pod(row, stage_code, workers, root))
        report['resource_health_before'] = s.core.monitoring(context['prepared']['initial_plan'])
    # Active old requests / old waiting work are explicitly allowed.
    PHASE = 'FINAL_PRE_PATCH_RECHECK'
    invariants(context, source)
    if not rollback:
        candidate_ready(context)
    before = public_route(context, source)
    PHASE = 'CAS_PUBLIC_SELECTOR'
    patch_once(root, before, context, source, target, report)
    PHASE = 'ROUTE_CONVERGENCE'
    target_rows = original_rows if rollback else [r for r in candidate_rows if r['container'] == 'dispatcher']
    report['endpoint_convergence'] = confirm_endpoints(context, target, target_rows)
    PHASE = 'PUBLIC_HEALTH_AND_OLD_WORK_OBSERVATION'
    callers = original_rows if rollback else [candidate_rows[0], candidate_rows[2]]
    report['public_health'] = [public_health(row, route_code, target,
        'original' if rollback else 'candidate', root, i) for i, row in enumerate(callers)]
    deps = invariants(context, target)
    report['old_activity_after'] = old_activity(deps)
    if not rollback:
        candidate_ready(context)
    # Snapshot counters are not a lease/admission closure proof; never scale old here.
    report.update(mineru_bluegreen16_switch='PASS', public_route=target,
        runtime_acceptance='PASS', release_acceptance='PENDING_REAL_DOCUMENT_END_TO_END',
        new_pool_slots=6, old_reserved_slots=10, local_slot_ceiling=16,
        old_admission_closed='NOT_PROVEN', old_http_drain='NOT_PROVEN',
        old_pool_retired=False, final_16_expansion=False,
        remaining_gates=['POST_SWITCH_WEKNORA_AND_CONVERSATION_END_TO_END',
            'VERIFY_OLD_ADMISSION_CLOSURE_AND_HTTP_INFLIGHT_FINISH',
            'REVIEW_OLD_POOL_RETIREMENT_BEFORE_FINAL_16_EXPANSION'])


def status(context):
    service = s.get('services', s.prep.WORKER)
    records = []
    for root in (ROOT, BACK):
        rows = {}
        for name in ('PATCH-REQUESTED.json', 'PATCH-ACK.private.json', 'RESULT.json'):
            path = root/name
            if path.exists():
                raw = s.core.pf.read_private(path)
                rows[name] = dict(present=True, sha256=sha(raw))
                if name == 'RESULT.json':
                    result = json.loads(raw)
                    rows[name]['safe_fields'] = {k: result.get(k) for k in
                        ('mineru_bluegreen16_switch', 'failed_stage', 'failed_check', 'live_patch_attempted',
                         'live_patch_acknowledged', 'public_route', 'direction')}
            else:
                rows[name] = dict(present=False)
        records.append(dict(directory=str(root), records=rows))
    endpoints = s.get('endpointslices.discovery.k8s.io', selector='kubernetes.io/service-name='+s.prep.WORKER)['items']
    safe_endpoints = [dict(pod=e.get('targetRef', {}).get('name'), pod_uid=e.get('targetRef', {}).get('uid'),
        ready=e.get('conditions', {}).get('ready'), terminating=e.get('conditions', {}).get('terminating', False))
        for item in endpoints for e in item.get('endpoints', [])]
    emit(dict(mineru_switch_status='READ_ONLY', public_route=route_state(service, context),
        endpoints=safe_endpoints, records=records, production_writes=0, provider_requests=0,
        files_modified=False, pod_exec=False, old_http_drain='NOT_PROVEN'))


def diagnostic(exc):
    row = s.diagnose(exc) if s is not None and hasattr(s, 'diagnose') else dict(error_type=type(exc).__name__)
    if isinstance(exc, SwitchFailure):
        row['failed_check'] = str(exc) if re.fullmatch('[A-Za-z0-9_:./-]{1,240}', str(exc)) else 'SAFE_DIAGNOSTIC_REQUIRED'
    row['failed_stage'] = PHASE
    row['raw_error_printed'] = False
    return row


def main():
    global PHASE
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--switch', action='store_true')
    modes.add_argument('--rollback', action='store_true')
    modes.add_argument('--status', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    stage_code, route_code = bootstrap()
    context = bind()
    if args.status:
        status(context)
        return 0
    root = BACK if args.rollback else ROOT
    s.safe_dir(root.parent)
    require(not root.exists() and not root.is_symlink(), 'operation_exists_use_status_do_not_repeat')
    # A previously attempted rollback forbids trying forward with a fresh root.
    require(args.rollback or not BACK.exists(), 'rollback_already_started_requires_review')
    root.mkdir(mode=0o700)
    report = dict(mineru_bluegreen16_switch='UNCONFIRMED', direction='rollback' if args.rollback else 'forward',
        stage_report_sha256=STAGED_SHA, live_patch_attempted=False, live_patch_acknowledged=False,
        deployments_modified=False, pods_restarted=False, queues_modified=False,
        credentials_modified=False, provider_requests=0, documents_submitted=0,
        old_pool_retired=False, final_16_expansion=False, automatic_retry=False,
        automatic_rollback=False, automatic_cleanup=False, old_evidence_modified=False,
        business_document_quality='NOT_TESTED', end_to_end_acceptance='REQUIRED')
    emit(dict(mineru_switch_evidence=str(root), direction=report['direction'],
        scope='ONE_SERVICE_SELECTOR_ONLY', queue_empty_required=False, both_pools_kept=True))
    try:
        switch(root, context, stage_code, route_code, args.rollback, report)
    except BaseException as exc:
        report.update(diagnostic(exc))
    report['utc'] = s.core.pf.utc().isoformat()
    s.core.save(root, 'RESULT.json', report)
    raw = s.core.pf.read_private(root/'RESULT.json')
    emit({k: report.get(k) for k in ('mineru_bluegreen16_switch', 'direction', 'failed_stage', 'failed_check',
        'live_patch_attempted', 'live_patch_acknowledged', 'public_route', 'runtime_acceptance',
        'release_acceptance', 'new_pool_slots', 'old_reserved_slots', 'old_pool_retired', 'final_16_expansion')})
    emit(dict(report=str(root/'RESULT.json'), report_sha256=sha(raw), automatic_retry=False,
        automatic_rollback=False, instruction='SEND_REPORT_ON_UNCERTAINTY_USE_STATUS_DO_NOT_REPEAT',
        status_command='sudo python3 -I -B '+str(Path(__file__).absolute())+' --status'))
    return 0 if report['mineru_bluegreen16_switch'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(mineru_bluegreen16_switch='UNCONFIRMED', automatic_retry=False,
                  automatic_rollback=False, **diagnostic(exc)))
        sys.exit(1)
