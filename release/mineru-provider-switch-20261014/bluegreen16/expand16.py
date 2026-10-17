#!/usr/bin/env python3
"""One-shot replica-only expansion of six reviewed worker Deployments to 16.

No dispatcher restart, old-pool restore, route/Secret change, queue mutation,
provider test submissions, automatic retry or scale-down rollback.
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
import traceback
import types

REVIEW_CODE = '__EMBED_PINNED_EXPANSION_REVIEW__'
REVIEW_SHA = '4078e976bb93bcd16d710938c1cc9c2e5cbe2facb436688385b38310894cd2ae'
PROBE_CODE = '__EMBED_EXPANSION_RUNTIME__'
REVIEW = Path('/var/lib/mineru-expansion-review-eitp8we2/RESULT.json')
REVIEW_RESULT_SHA = '2c558e0e54ea601ba7042203e41d4b53de4edac0c609f742133f93dae9ea3ff0'
ROOT = Path('/var/lib/mineru-expand16-20261016-r1')
PLAN = {'deer-flow-mineru-dedicated-r1-w-ecs-%04d' % n: (3 if n < 15 else 2) for n in range(11, 17)}
NODE_RECEIPTS = {
    'datax-02': dict(report='/var/lib/mineru-expansion-review-f639fign/RESULT.json',
        sha256='b6651ca6aecc440b1f0ea54273f1d140e6d1262271d45554842b881bdae0d856',
        sample_end='2026-10-16T01:56:37.253784+00:00', observed_clear_samples=2),
    'datax-03': dict(report='/var/lib/mineru-expansion-review-8d7hkooq/RESULT.json',
        sha256='bd502fe157cc56b3463c42c2f2612dba43a9690052ed1f3b74ea2bd03f1fc06c',
        sample_end='2026-10-16T01:57:00.180783+00:00', observed_clear_samples=2),
}
PHASE = 'BOOTSTRAP'
e = c = r = s = None
PATCH_CAPTURE = None


class ExpandFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise ExpandFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def bootstrap():
    global e, c, r, s, PATCH_CAPTURE
    require(hashlib.sha256(REVIEW_CODE.encode()).hexdigest() == REVIEW_SHA, 'review_tool_hash_changed')
    e = types.ModuleType('pinned_expansion_review')
    e.__file__ = str(Path(__file__).absolute())
    exec(compile(REVIEW_CODE, '<pinned-expansion-review>', 'exec'), e.__dict__)
    c, r = e.load_reconcile()
    context, accepted, _, _ = r.bootstrap()
    s = r.s
    PATCH_CAPTURE = s.core.inv.bounded_capture
    s.core.inv.bounded_capture = e.capacity_capture(PATCH_CAPTURE, c)
    r.patch_call = r.retire_once = r.run = r.restore_review = c.deny_mutation
    r.ROOT = ROOT
    return context, accepted


def bind_review(context, accepted):
    report = json.loads(r.a.locked(REVIEW, REVIEW_RESULT_SHA))
    wanted = {'ecs-zdz-pro-%04d' % n: (3 if n < 15 else 2) for n in range(11, 17)}
    require(report.get('expansion_review') == 'PASS' and report.get('node') == 'datax-01'
            and report.get('current_slots') == 6 and report.get('target_slots') == 16
            and report.get('proposed_final_worker_counts') == wanted
            and report.get('script_sha256') == REVIEW_SHA
            and report.get('retirement_receipt_sha256') == e.ACCEPTED_SHA, 'expansion_review_changed')
    state = c.bind_evidence()
    require(state['inventory'] == accepted['inventory'], 'retirement_inventory_changed')
    e.bind_runtime_ids(state['inventory'])
    originals = r.original_snapshots(context)
    c.accept_acks(originals, state)
    # The two remote reports were supplied directly by the operator in this
    # conversation. Do not claim their file bytes were re-read from datax-01.
    require(set(PLAN) == set(s.WORKERS) and sum(PLAN.values()) == 16, 'worker_plan_changed')
    return originals, state['inventory']


def replica_spec(original, count):
    spec = copy.deepcopy(original['spec'])
    spec['replicas'] = count
    return spec


def same_deployment(now, before, count):
    return r.deployment_matches(now, before, count)


def running_rows(dep, pins, pending):
    """Original processes must remain identical; new ones may briefly start."""
    pods = s.core.inv.owned_pods(dep)
    target = dep['spec']['replicas']
    require(len(pods) <= target, 'unexpected_surge_or_extra_pods')
    rows = []
    for pod in pods:
        require(not pod['metadata'].get('deletionTimestamp')
                and pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded'), 'worker_terminated')
        statuses = pod.get('status', {}).get('containerStatuses', [])
        require(not any(x.get('restartCount', 0) or x.get('state', {}).get('terminated') for x in statuses),
                'worker_process_restarted')
        reasons = [x.get('state', {}).get('waiting', {}).get('reason') for x in statuses]
        require(not any(x in ('ErrImageNeverPull', 'ImagePullBackOff', 'ErrImagePull',
            'CrashLoopBackOff', 'CreateContainerConfigError', 'CreateContainerError') for x in reasons),
            'worker_start_failure')
        ready = (len(statuses) == 1 and statuses[0].get('ready') is True
                 and pod.get('status', {}).get('phase') == 'Running')
        if ready:
            rows.append(s.validate_pod(dep, pod))
        else:
            require(pending and pod['metadata']['uid'] not in pins, 'existing_process_unready')
    for uid, pin in pins.items():
        if pin['deployment'] == dep['metadata']['name']:
            require(next((row for row in rows if row['uid'] == uid), None) == pin, 'original_process_replaced')
    st = dep.get('status', {})
    converged = (len(rows) == len(pods) == target
        and st.get('observedGeneration', 0) >= dep['metadata']['generation']
        and all(st.get(k, 0) == target for k in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas')))
    require(pending or converged, 'worker_controller_not_converged')
    return rows, {p['metadata']['uid'] for p in pods}, converged


def current(context, originals, inventory, counts, pending=False):
    require(set(counts) == set(PLAN)
            and all(type(counts[n]) is int and counts[n] in (1, PLAN[n]) for n in PLAN), 'invalid_progress_counts')
    require(not (r.sw.BACK/'PATCH-REQUESTED.json').exists(), 'old_route_rollback_intent_present')
    old = {n: s.core.inv.get('deployment', n) for n in originals}
    for name, before in originals.items():
        count = 0 if name in r.TARGETS else before['spec']['replicas']
        require(same_deployment(old[name], before, count), 'protected_deployment_changed:'+name)
        if name in r.TARGETS:
            c.check_controller(old[name])
            require(not r.owned_state(old[name], inventory, True), 'old_pods_reappeared')
        else:
            require(old[name].get('status', {}).get('readyReplicas', 0) >= 1, 'protected_workload_not_ready')
    actual = {}
    for name, before in context['created'].items():
        dep = before['kind'] == 'Deployment'
        now = s.get('deployments' if dep else 'services', name)
        if dep:
            require(same_deployment(now, before, counts.get(name, 1)), 'new_pool_spec_changed:'+name)
        else:
            require(now and now['metadata']['uid'] == before['metadata']['uid']
                    and now['spec'] == before['spec'] and not now['metadata'].get('deletionTimestamp'),
                    'new_service_changed:'+name)
        actual[name] = now
    combined = dict(old, **{n: actual[n] for n in s.WORKERS+[s.DISPATCHER]})
    s.core.pf.controls_snapshot(combined, r.sw.controls_for(context, 'candidate'))
    require(s.prep.live_policies() == context['prepared']['policies'], 'network_policies_changed')
    s.credential_identity()
    original_pins = {p['uid']: p for p in context['staged']['pods']}
    require(len(original_pins) == 7, 'original_new_pool_process_count')
    pins = context.setdefault('_expansion_pins', dict(original_pins))
    require(all(pins.get(uid) == value for uid, value in original_pins.items()), 'original_process_pins_changed')
    rows, uids, converged = [], set(), True
    for name in s.WORKERS+[s.DISPATCHER]:
        found, owned, ready = running_rows(actual[name], pins, pending and name in PLAN)
        rows.extend(found)
        uids.update(owned)
        converged = converged and ready
    require({p['metadata']['uid'] for p in s.get('pods', selector=s.prep.LABEL+'='+s.prep.RELEASE)['items']} == uids,
            'unexpected_release_pod')
    dispatcher = [p for p in rows if p['container'] == 'dispatcher']
    require(len(dispatcher) == 1, 'dispatcher_identity_count')
    s.endpoints(r.sw.public_route(context, 'candidate'), dispatcher)
    s.endpoints(actual[s.SERVICES[1]], dispatcher)
    # Retired code's CA helper deliberately receives only the original six
    # workers; each additional worker is checked by the expansion runtime probe.
    require(r.candidate_ca_hash(list(original_pins.values())) == context['prepared']['ca_sha256'], 'ca_changed')
    return actual, rows, converged


def capacity(context, originals, inventory, counts, names):
    inv, pf = s.core.inv, s.core.pf
    nodes = {n['metadata']['name']: n for n in inv.get('nodes')['items']}
    result = []
    for name in names:
        spec = context['created'][name]['spec']['template']['spec']
        node_name = spec['nodeSelector']['kubernetes.io/hostname']
        node = nodes[node_name]
        require(node['metadata']['uid'] == s.core.guard.NODES[node_name]['uid'], 'worker_node_uid_changed')
        emit(dict(expansion_capacity='RUNNING', node=node_name, additional=PLAN[name]-counts[name]))
        pods = pf.node_pods(node_name)
        measured = pf.metrics(inv.get('node_summary', node_name), node_name, pf.utc())
        row = e.slot_budget(pf, node, pods, set(), spec, measured)
        require(row['node_eligible'] and row['additional_workers_planning_only'] >= PLAN[name]-counts[name],
                'fresh_capacity_insufficient:'+node_name)
        result.append(row)
    r.node_leases(inventory)
    return result


def scale_patch(before, target):
    name = before['metadata']['name']
    require(name in PLAN and type(target) is int and target == PLAN[name]
            and type(before['spec'].get('replicas')) is int and before['spec']['replicas'] == 1
            and before['metadata'].get('namespace') == 'deer-flow', 'patch_target_out_of_scope')
    return [dict(op='test', path='/metadata/uid', value=before['metadata']['uid']),
        dict(op='test', path='/metadata/resourceVersion', value=before['metadata']['resourceVersion']),
        dict(op='test', path='/spec', value=before['spec']),
        dict(op='replace', path='/spec/replicas', value=target)]


def patch_call(name, path, digest, dry):
    require(name in PLAN and path == ROOT/(name+'.patch.private.json'), 'patch_path_out_of_scope')
    patch = json.loads(r.a.locked(path, digest))
    before = json.loads(s.core.pf.read_private(ROOT/(name+'.before.private.json')))
    require(patch == scale_patch(before, PLAN[name]), 'patch_bytes_not_exact_replica_change')
    args = ['kubectl', '--request-timeout=20s', '-n', 'deer-flow', 'patch', 'deployment',
        name, '--type=json', '--patch-file', str(path), '-o', 'json']
    if dry:
        args.append('--dry-run=server')
    return json.loads(PATCH_CAPTURE(args))


def apply_one(name, context, originals, inventory, counts, report):
    actual, _, _ = current(context, originals, inventory, counts)
    report['resource_checks'].append(dict(deployment=name,
        nodes=capacity(context, originals, inventory, counts, [name])))
    before = s.get('deployments', name)
    require(same_deployment(before, context['created'][name], 1), 'pre_patch_source_changed')
    r.save(name+'.before.private.json', before)
    patch_sha = r.save(name+'.patch.private.json', scale_patch(before, PLAN[name]))
    path = ROOT/(name+'.patch.private.json')
    emit(dict(mineru_expansion='SERVER_DRY_RUN', deployment=name, target=PLAN[name]))
    dry = patch_call(name, path, patch_sha, True)
    require(same_deployment(dry, before, PLAN[name]), 'admission_changed_more_than_replicas')
    r.save(name+'.dry-run.private.json', dry)
    # Revalidate original Pod identities, old-zero state, route and controls.
    current(context, originals, inventory, counts)
    r.save(name+'.PATCH-REQUESTED.json', dict(uid=before['metadata']['uid'],
        source_replicas=1, target_replicas=PLAN[name], patch_sha256=patch_sha,
        source_spec_sha256=s.core.inv.digest(before['spec']), utc=r.utc()))
    report['patches_attempted'].append(name)
    emit(dict(mineru_expansion='CAS_WORKER_REPLICAS', deployment=name, source=1, target=PLAN[name],
              dispatcher_restarted=False, queue_empty_required=False))
    # Once and only once. A timeout may mean the server applied it: status only.
    answer = patch_call(name, path, patch_sha, False)
    r.save(name+'.PATCH-ACK.private.json', answer)
    require(same_deployment(answer, before, PLAN[name]), 'patch_acknowledgement_not_exact')
    report['patches_acknowledged'].append(name)
    counts[name] = PLAN[name]


def wait_pool(context, originals, inventory, counts):
    started, stable = time.monotonic(), 0
    while True:
        actual, rows, converged = current(context, originals, inventory, counts, pending=True)
        stable = stable+1 if converged else 0
        emit(dict(mineru_expansion='WAIT_WORKERS', desired=sum(counts.values()),
                  ready_workers=sum(p['container'] == 'adapter' for p in rows),
                  stable_samples=stable, elapsed_seconds=int(time.monotonic()-started)))
        if stable >= 2:
            s.wait_endpoints(actual[s.SERVICES[0]], [p for p in rows if p['container'] == 'adapter'])
            return rows
        require(time.monotonic()-started < 600, 'worker_ready_deadline_keep_partial_pool')
        time.sleep(10)


def probe(row, workers, label):
    before = s.get('pods', row['name'])
    require(before and before['metadata']['uid'] == row['uid']
            and any(x.get('containerID') == row['container_id'] and x.get('ready') is True
                for x in before.get('status', {}).get('containerStatuses', [])), 'probe_process_changed')
    role = 'worker' if row['container'] == 'adapter' else 'dispatcher'
    args = ['exec', row['name'], '-c', row['container'], '--', '/app/.venv/bin/python',
            '-I', '-B', '-c', PROBE_CODE, '--role', role, '--expected-workers', str(len(workers))]
    if role == 'dispatcher':
        args += ['--worker-ips', ','.join(sorted(w['ip'] for w in workers))]
    emit(dict(mineru_expansion='RUNTIME_PROBE', role=role, pod=row['name'],
              expected_workers=len(workers), documents_submitted=0))
    raw = s.call(args, timeout=110)
    require(len(raw) <= 32768, 'runtime_receipt_too_large')
    result = json.loads(raw)
    r.save(label+'.'+row['name']+'.runtime.json', result)
    after = s.get('pods', row['name'])
    require(after and after['metadata']['uid'] == row['uid']
            and after.get('status', {}).get('containerStatuses') == before.get('status', {}).get('containerStatuses'),
            'probe_process_changed')
    require(result.get('expansion_runtime') == 'PASS' and result.get('role') == role
            and result.get('expected_workers') == len(workers)
            and result.get('source_sha256') == s.core.inv.CANDIDATE['source_sha256']
            and result.get('provider_requests') == result.get('documents_submitted') == 0,
            'runtime_acceptance_failed')
    return dict(pod=row['name'], uid=row['uid'], receipt=result)


def expand(context, accepted, report):
    global PHASE
    PHASE = 'BIND_REVIEW_AND_OLD_ZERO_BASELINE'
    originals, inventory = bind_review(context, accepted)
    counts = {n: 1 for n in PLAN}
    _, rows, _ = current(context, originals, inventory, counts)
    report['operator_supplied_node_receipts'] = NODE_RECEIPTS
    report['remote_receipt_bytes_reread'] = False
    r.save('STATE.json', dict(review_sha256=REVIEW_RESULT_SHA, plan=PLAN, original_processes=rows,
        operator_supplied_node_receipts=NODE_RECEIPTS, remote_receipt_bytes_reread=False))
    PHASE = 'FRESH_CAPACITY_AND_RUNTIME'
    report['resource_checks'] = [dict(phase='all_nodes', nodes=capacity(context, originals, inventory, counts, list(PLAN)))]
    workers = [p for p in rows if p['container'] == 'adapter']
    report['runtime_before'] = [probe(p, workers, 'before') for p in rows]
    known = {p['uid'] for p in rows}
    report['stage_acceptance'] = []
    for index, name in enumerate(PLAN):
        PHASE = 'EXPAND_'+name
        apply_one(name, context, originals, inventory, counts, report)
        rows = wait_pool(context, originals, inventory, counts)
        workers = [p for p in rows if p['container'] == 'adapter']
        check = [p for p in rows if p['uid'] not in known or p['container'] == 'dispatcher']
        report['stage_acceptance'].append(dict(slots=len(workers),
            probes=[probe(p, workers, 'step%d' % index) for p in check]))
        context['_expansion_pins'] = {p['uid']: p for p in rows}
        r.save('step%d.processes.json' % index, rows)
        known.update(p['uid'] for p in rows)
    PHASE = 'FINAL_16_RUNTIME_AND_CONTROL_RECHECK'
    _, rows, _ = current(context, originals, inventory, counts)
    workers = [p for p in rows if p['container'] == 'adapter']
    require(len(workers) == 16, 'final_worker_count_not_16')
    report['runtime_after'] = [probe(p, workers, 'final') for p in rows]
    current(context, originals, inventory, counts)
    report.update(mineru_expand16='PASS', final_worker_counts=counts, new_pool_slots=16,
        old_pool_desired=0, dispatcher_original_process_preserved=True,
        runtime_acceptance='PASS', public_route='candidate',
        end_to_end_acceptance='USER_PREVIOUSLY_PASSED_AT_6_SLOTS_POST_EXPANSION_CHECK_RECOMMENDED',
        slot_scope='SERIALIZED_LOCAL_PARSE_PROCESSES_NOT_PROVIDER_JOB_GUARANTEE')


def status(context):
    rows = []
    for name in list(PLAN)+[s.DISPATCHER]:
        dep = s.get('deployments', name)
        rows.append(dict(deployment=name, uid=(dep or {}).get('metadata', {}).get('uid'),
            desired=(dep or {}).get('spec', {}).get('replicas'),
            ready=(dep or {}).get('status', {}).get('readyReplicas', 0),
            template_unchanged=bool(dep and dep['spec'].get('template') == context['created'][name]['spec']['template']),
            patch_requested=(ROOT/(name+'.PATCH-REQUESTED.json')).exists(),
            patch_acknowledged=(ROOT/(name+'.PATCH-ACK.private.json')).exists()))
    evidence = None
    if (ROOT/'RESULT.json').exists():
        raw = s.core.pf.read_private(ROOT/'RESULT.json')
        value = json.loads(raw)
        evidence = dict(sha256=hashlib.sha256(raw).hexdigest(),
            fields={k: value.get(k) for k in ('mineru_expand16', 'failed_stage', 'failed_check',
                'patches_attempted', 'patches_acknowledged', 'new_pool_slots')})
    emit(dict(mineru_expansion_status='READ_ONLY', deployments=rows, result=evidence,
        public_route=r.sw.route_state(s.get('services', s.prep.WORKER), context),
        files_modified=False, production_writes=0, pod_exec=False, provider_requests=0,
        instruction='NO_REPEAT_NO_AUTOMATIC_SCALEDOWN;SEND_STATUS_FOR_RECONCILIATION'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--expand', action='store_true')
    mode.add_argument('--status', action='store_true')
    args = parser.parse_args()
    require(sys.platform.startswith('linux') and os.geteuid() == 0
            and socket.gethostname().split('.')[0] == 'datax-01', 'root_linux_datax01_required')
    os.umask(0o077)
    context, accepted = bootstrap()
    if args.status:
        status(context)
        return 0
    r.a.safe_dir(ROOT.parent)
    require(not ROOT.exists() and not ROOT.is_symlink(), 'operation_exists_use_status_do_not_repeat')
    ROOT.mkdir(mode=0o700)
    r.sync_dir(ROOT.parent)
    report = dict(mineru_expand16='UNCONFIRMED', utc_started=r.utc(),
        patches_attempted=[], patches_acknowledged=[], automatic_retry=False, automatic_rollback=False,
        dispatcher_modified=False, public_route_modified=False, credentials_modified=False,
        old_pool_modified=False, queues_modified=False, provider_requests=0, documents_submitted=0,
        images_imported=False, weknora_modified=False, queue_empty_required=False)
    emit(dict(mineru_expand16_evidence=str(ROOT), target_slots=16, dispatcher_restarted=False))
    try:
        expand(context, accepted, report)
    except BaseException as exc:
        label = str(exc)
        allowed = type(exc).__name__ in ('ExpandFailure', 'ReviewFailure', 'ReconcileFailure',
            'RetireFailure', 'AuditFailure', 'StageFailure', 'SwitchFailure',
            'PreflightFailure', 'LoadFailure', 'CheckFailure')
        report.update(failed_stage=PHASE, error_type=type(exc).__name__,
            failed_check=label if allowed and re.fullmatch('[A-Za-z0-9_:./-]{1,240}', label)
                else 'SAFE_DIAGNOSTIC_REQUIRED',
            code_frames=[dict(file=Path(f.filename).name, function=f.name, line=f.lineno)
                         for f in traceback.extract_tb(exc.__traceback__)[-10:]], raw_error_printed=False)
    report['utc_finished'] = r.utc()
    report['production_writes_attempted'] = len(report['patches_attempted'])
    digest = r.save('RESULT.json', report)
    emit({k: report.get(k) for k in ('mineru_expand16', 'failed_stage', 'failed_check',
        'patches_attempted', 'patches_acknowledged', 'new_pool_slots',
        'dispatcher_original_process_preserved', 'runtime_acceptance', 'public_route')})
    emit(dict(report=str(ROOT/'RESULT.json'), report_sha256=digest,
        status_command='sudo python3 -I -B '+str(Path(__file__).absolute())+' --status',
        automatic_retry=False, automatic_rollback=False))
    return 0 if report['mineru_expand16'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(mineru_expand16='UNCONFIRMED', failed_stage=PHASE, error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, ExpandFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            raw_error_printed=False, automatic_retry=False, automatic_rollback=False))
        sys.exit(1)
