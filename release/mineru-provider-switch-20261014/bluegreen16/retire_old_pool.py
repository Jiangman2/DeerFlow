#!/usr/bin/env python3
"""Operator-approved idle-checked retirement, NOT a lossless drain protocol.

Python3.7+. Two one-shot CAS replica patches, dispatcher before workers.
Never change candidate capacity, public routing, queues, credentials or images.
The operator accepted the residual check-to-scale direct-old-Pod arrival race.
"""
import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import types

ROOT = Path('/var/lib/mineru-old-pool-retire-20261016-r1')
AUDIT = Path('/var/lib/mineru-old-pool-audit-s1cvik21/RESULT.json')
AUDIT_SHA = 'b21f7a861aa11ca2a3004a0b34f325c2ff32906ae8f2521a875d73bc0d5a1323'
AUDIT_CODE_SHA = '2d43207e9f9e091c3dd49305d573d77bac1407dcb346ff881f2d9dd70a390b40'
AUDIT_CODE = '__EMBED_REVIEWED_AUDIT__'
RUNTIME_CODE = '__EMBED_RETIRE_RUNTIME__'
WORKER = 'deer-flow-mineru-adapter'
DISPATCHER = 'deer-flow-mineru-dispatcher'
TARGETS = (DISPATCHER, WORKER)
ORIGINAL_COUNTS = {DISPATCHER: 1, WORKER: 9}
ROLLBACK_IMAGE = 'deerflow.local/deer-flow-mineru-adapter:20261015-dedicated-rollback-3c3f5d07bc290206'
PHASE = 'BOOTSTRAP'
a = sw = s = None


class RetireFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise RetireFailure(label)


def emit(row):
    print(json.dumps(row, sort_keys=True), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sync_dir(path):
    a.safe_dir(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(name, value):
    require(re.fullmatch('[A-Za-z0-9_.-]{1,180}', name), 'invalid_evidence_name')
    a.safe_dir(ROOT)
    raw = (json.dumps(value, indent=2, sort_keys=True)+'\n').encode('utf-8')
    fd = os.open(str(ROOT/name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    sync_dir(ROOT)
    return sha(raw)


def bootstrap():
    global a, sw, s
    raw = AUDIT_CODE.encode('utf-8')
    require(sha(raw) == AUDIT_CODE_SHA, 'embedded_audit_hash_changed')
    a = types.ModuleType('reviewed_old_pool_audit')
    a.__file__ = str(Path(__file__).absolute())
    exec(compile(raw, a.__file__, 'exec'), a.__dict__)
    route_code = a.bootstrap()  # Frozen helper call() remains GET/diagnostic exec only.
    sw, s = a.sw, a.sw.s
    context = sw.bind()
    receipt = json.loads(a.locked(AUDIT, AUDIT_SHA))
    require(receipt.get('old_pool_audit') == 'COMPLETE'
            and receipt.get('terminal_runtime_clear') is True
            and receipt.get('public_route') == 'candidate'
            and receipt.get('observation') == 'QUIET_SAMPLES_ONLY'
            and receipt.get('production_workloads_mutated') is False
            and len(receipt.get('samples', [])) == 3
            and all(x.get('summary', {}).get('idle_observed') is True for x in receipt['samples']),
            'prior_idle_audit_not_accepted')
    require(RUNTIME_CODE != '__EMBED_'+'RETIRE_RUNTIME__', 'runtime_probe_not_packaged')
    return context, receipt, RUNTIME_CODE, route_code


def baseline_spec(obj, count):
    require(type(count) is int and count >= 0, 'invalid_replica_count')
    spec = copy.deepcopy(obj['spec'])
    spec['replicas'] = count
    return spec


def deployment_matches(now, before, count):
    return bool(now and now.get('kind') == 'Deployment'
        and now.get('metadata', {}).get('name') == before['metadata']['name']
        and now['metadata'].get('namespace') == 'deer-flow'
        and now['metadata'].get('uid') == before['metadata']['uid']
        and not now['metadata'].get('deletionTimestamp')
        and now.get('spec') == baseline_spec(before, count))


def original_snapshots(context):
    result = {}
    for name in s.core.inv.BASELINES:
        raw = s.core.pf.read_private(s.PREPARED/(name+'.before.private.json'))
        obj = json.loads(raw)
        s.core.inv.baseline(obj, name)
        require(obj['metadata']['name'] == name and obj['metadata'].get('namespace') == 'deer-flow',
                'original_snapshot_identity')
        result[name] = obj
    require(all(type(result[n]['spec'].get('replicas')) is int
                and result[n]['spec']['replicas'] == ORIGINAL_COUNTS[n] for n in TARGETS),
            'original_replica_plan_changed')
    return result


def node_leases(inventory):
    names = sorted({r['node'] for k in ('targets', 'pending', 'terminal') for r in inventory[k]})
    nodes = {n['metadata']['name']: n for n in s.core.inv.get('nodes')['items']}
    rows = []
    for name in names:
        node = nodes.get(name)
        require(node and name in s.core.guard.NODES
                and node['metadata']['uid'] == s.core.guard.NODES[name]['uid'], 'old_node_identity_changed')
        cond = {r['type']: r['status'] for r in node.get('status', {}).get('conditions', [])}
        require(all(cond.get(k) == v for k, v in [('Ready', 'True'), ('MemoryPressure', 'False'),
            ('DiskPressure', 'False'), ('PIDPressure', 'False')]), 'old_node_unhealthy:'+name)
        raw = s.core.inv.bounded_capture(['kubectl', '--request-timeout=20s', '-n', 'kube-node-lease',
            'get', 'lease', name, '--show-managed-fields=false', '-o', 'json'])
        lease = json.loads(raw)
        require(lease.get('metadata', {}).get('name') == name
                and lease['metadata'].get('namespace') == 'kube-node-lease', 'node_lease_identity')
        renewed = datetime.datetime.fromisoformat(lease['spec']['renewTime'].replace('Z', '+00:00'))
        require(renewed.tzinfo is not None, 'node_lease_timezone')
        age = (datetime.datetime.now(datetime.timezone.utc)-renewed).total_seconds()
        require(-30 <= age <= 60, 'node_lease_stale:'+name)
        rows.append(dict(node=name, uid=node['metadata']['uid'], lease_age_seconds=round(age, 2)))
    return rows


def historical_record_matches(pod, inventory):
    rows = [r for r in inventory['terminal'] if r['uid'] == pod['metadata']['uid']]
    return len(rows) == 1 and a.terminal_record(pod, 'adapter') == rows[0]


def owned_state(dep, inventory, retired):
    name = dep['metadata']['name']
    require(name in TARGETS, 'old_inventory_target')
    container = 'dispatcher' if name == DISPATCHER else 'adapter'
    live_rows = [r for r in inventory['targets'] if r['container'] == container]
    pending = inventory['pending'] if name == WORKER else []
    terminal = inventory['terminal'] if name == WORKER else []
    allowed = {r['uid'] for r in live_rows+pending+terminal}
    pods = s.core.inv.owned_pods(dep)
    require(all(p['metadata']['uid'] in allowed for p in pods), 'unexpected_old_pod_requires_review:'+name)
    if not retired:
        require({p['metadata']['uid'] for p in pods} == allowed, 'old_pod_inventory_changed:'+name)
        by_uid = {r['uid']: r for r in live_rows}
        for pod in pods:
            uid = pod['metadata']['uid']
            if uid in by_uid:
                require(a.pod_identity(pod, container) == by_uid[uid], 'old_process_changed')
            elif uid in {r['uid'] for r in pending}:
                cs = pod.get('status', {}).get('containerStatuses', [])
                require(not pod['metadata'].get('deletionTimestamp') and pod['status'].get('phase') == 'Pending'
                        and len(cs) == 1 and not cs[0].get('containerID') and cs[0].get('restartCount') == 0
                        and cs[0].get('state', {}).get('waiting', {}).get('reason') in
                        ('ErrImageNeverPull', 'ImagePullBackOff', 'ErrImagePull'), 'pending_old_pod_started')
            else:
                require(historical_record_matches(pod, inventory), 'failed_old_record_changed')
    remaining = [p for p in pods if p['metadata']['uid'] not in {r['uid'] for r in terminal}]
    for pod in pods:
        if pod['metadata']['uid'] in {r['uid'] for r in terminal}:
            require(historical_record_matches(pod, inventory), 'failed_old_record_changed')
    return remaining


def candidate_ca_hash(candidates):
    """Read the actual mounted CA from the live replacement, never the retired pool."""
    workers = sorted((r for r in candidates if r['container'] == 'adapter'), key=lambda r: r['name'])
    require(len(workers) == 6, 'candidate_ca_worker_count')
    row = workers[0]
    before = s.get('pods', row['name'])
    require(before and before['metadata']['uid'] == row['uid']
            and any(c.get('name') == 'adapter' and c.get('containerID') == row['container_id']
                    and c.get('ready') is True for c in before.get('status', {}).get('containerStatuses', [])),
            'candidate_ca_process_changed')
    code = ("import hashlib,json,os,stat;from pathlib import Path;"
            "p=Path(os.environ.get('MINERU_CA_BUNDLE') or os.environ.get('REQUESTS_CA_BUNDLE') or os.environ['SSL_CERT_FILE']);"
            "v=p.stat();assert stat.S_ISREG(v.st_mode) and 0<v.st_size<=262144;"
            "b=p.read_bytes();assert len(b)==v.st_size;"
            "print(json.dumps({'ca_sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)}))")
    raw = s.call(['exec', row['name'], '-c', 'adapter', '--', '/app/.venv/bin/python', '-I', '-B', '-c', code], timeout=30)
    require(len(raw) <= 1024, 'candidate_ca_receipt_limit')
    result = json.loads(raw)
    after = s.get('pods', row['name'])
    require(after and after['metadata']['uid'] == row['uid']
            and before.get('status', {}).get('containerStatuses') == after.get('status', {}).get('containerStatuses'),
            'candidate_ca_process_changed')
    require(type(result.get('bytes')) is int and 0 < result['bytes'] <= 262144
            and isinstance(result.get('ca_sha256'), str)
            and re.fullmatch('[0-9a-f]{64}', result['ca_sha256']), 'candidate_ca_receipt_shape')
    return result['ca_sha256']


def invariants(context, originals, counts, inventory):
    # Only the two replica fields may differ from the frozen pre-switch specs.
    # Do NOT call the old all-running source guard after intentionally scaling.
    require(not (sw.BACK/'PATCH-REQUESTED.json').exists(), 'rollback_intent_present')
    deps = {n: s.core.inv.get('deployment', n) for n in originals}
    for name, before in originals.items():
        count = counts.get(name, before['spec']['replicas'])
        require(deployment_matches(deps[name], before, count), 'deployment_changed:'+name)
        if name not in TARGETS:
            require(deps[name].get('status', {}).get('readyReplicas', 0) >= 1, 'protected_workload_not_ready')
        else:
            owned_state(deps[name], inventory, count == 0)
    for name, obj in context['created'].items():
        now = s.get('deployments' if obj['kind'] == 'Deployment' else 'services', name)
        require(now and now['metadata']['uid'] == obj['metadata']['uid'] and now['spec'] == obj['spec']
                and not now['metadata'].get('deletionTimestamp'), 'candidate_object_changed:'+name)
    candidates = sw.candidate_ready(context)
    combined = dict(deps, **{n: context['created'][n] for n in s.WORKERS+[s.DISPATCHER]})
    s.core.pf.controls_snapshot(combined, sw.controls_for(context, 'candidate'))
    require(s.prep.live_policies() == context['prepared']['policies'], 'policy_changed')
    s.credential_identity()
    require(candidate_ca_hash(candidates) == context['prepared']['ca_sha256'], 'ca_changed')
    s.endpoints(sw.public_route(context, 'candidate'), [r for r in candidates if r['container'] == 'dispatcher'])
    return deps, candidates


def quiet(targets, label):
    samples = []
    for number in range(3):
        rows = [a.observe(r) for r in targets]
        summary = a.aggregate(rows)
        emit(dict(old_pool_retire='IDLE_CHECK', phase=label, sample=number+1, **summary))
        samples.append(dict(utc=utc(), summary=summary, rows=rows))
        require(summary['idle_observed'], 'old_activity_or_connections_observed_keep_remaining_pool')
        if number < 2:
            time.sleep(5)
    save(label+'.idle.json', samples)
    return samples


def scale_patch(before):
    name = before['metadata']['name']
    require(name in TARGETS and before['metadata'].get('namespace') == 'deer-flow'
            and type(before['spec'].get('replicas')) is int
            and before['spec']['replicas'] == ORIGINAL_COUNTS[name], 'scale_target_not_original')
    return [dict(op='test', path='/metadata/uid', value=before['metadata']['uid']),
        dict(op='test', path='/metadata/resourceVersion', value=before['metadata']['resourceVersion']),
        dict(op='test', path='/spec', value=before['spec']),
        dict(op='replace', path='/spec/replicas', value=0)]


def patch_call(name, path, dry):
    require(name in TARGETS and path.parent == ROOT and path.name == name+'.scale.patch.private.json',
            'live_write_out_of_scope')
    # The only live mutation in this program. Fixed namespace/kind/name and a
    # locally constructed patch; no --force, delete, rollout or shell command.
    args = ['kubectl', '--request-timeout=20s', '-n', 'deer-flow', 'patch', 'deployment', name,
        '--type=json', '--patch-file', str(path), '-o', 'json']
    if dry:
        args.append('--dry-run=server')
    return json.loads(s.core.inv.bounded_capture(args))


def retire_once(name, context, originals, counts, inventory, report):
    deps, _ = invariants(context, originals, counts, inventory)
    before = deps[name]
    patch = scale_patch(before)
    path = ROOT/(name+'.scale.patch.private.json')
    patch_sha = save(path.name, patch)
    dry = patch_call(name, path, True)
    require(deployment_matches(dry, before, 0), 'dry_run_changed_more_than_replicas')
    save(name+'.scale.dry-run.private.json', dry)
    node_leases(inventory)
    # Dispatcher shutdown requires the whole old pool to be quiet. Afterwards
    # only the unchanged old workers remain. Sampling is NOT an admission lock.
    relevant = [r for r in inventory['targets'] if counts[DISPATCHER] != 0 or r['container'] == 'adapter']
    quiet(relevant, name)
    deps, _ = invariants(context, originals, counts, inventory)
    require(deps[name]['metadata']['resourceVersion'] == before['metadata']['resourceVersion'],
            'cas_resource_version_changed_no_retry')
    save(name+'.PATCH-REQUESTED.json', dict(utc=utc(), target=name, source_replicas=ORIGINAL_COUNTS[name],
        target_replicas=0, uid=before['metadata']['uid'], patch_sha256=patch_sha,
        source_spec_sha256=s.core.inv.digest(before['spec']),
        target_spec_sha256=s.core.inv.digest(baseline_spec(before, 0)),
        residual_direct_old_pod_arrival_risk='OPERATOR_ACKNOWLEDGED'))
    report['patches_attempted'].append(name)
    emit(dict(old_pool_retire='CAS_SCALE_ZERO', deployment=name, candidate_untouched=True))
    answer = patch_call(name, path, False)
    save(name+'.PATCH-ACK.private.json', answer)
    require(deployment_matches(answer, before, 0), 'patch_acknowledgement_shape_unexpected')
    report['patches_acknowledged'].append(name)
    counts[name] = 0
    return answer


def wait_retired(name, context, originals, counts, inventory):
    deadline = time.monotonic()+180
    stable = 0
    observations = []
    while True:
        deps, _ = invariants(context, originals, counts, inventory)
        dep = deps[name]
        remaining = owned_state(dep, inventory, True)
        status = dep.get('status', {})
        converged = (status.get('observedGeneration', 0) >= dep['metadata']['generation']
            and all(status.get(k, 0) == 0 for k in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas'))
            and not remaining)
        if name == WORKER and converged:
            slices = s.get('endpointslices.discovery.k8s.io', selector='kubernetes.io/service-name='+WORKER+'-workers')['items']
            converged = not any(x.get('endpoints', []) for x in slices)
        stable = stable+1 if converged else 0
        observation = dict(deployment=name, remaining_owned_nonhistorical_pods=len(remaining),
            controller_observed_generation=status.get('observedGeneration'), converged=converged,
            stable_samples=stable, elapsed_seconds=round(180-(deadline-time.monotonic())))
        observations.append(observation)
        emit(dict(old_pool_retire='WAIT_TERMINATION', **observation))
        if stable == 3:
            node_leases(inventory)
            save(name+'.TERMINATION.json', dict(samples=observations, utc=utc(),
                control_plane_termination='VERIFIED', node_process_scan='NOT_PERFORMED_ON_DATAX02_03'))
            return
        require(time.monotonic() < deadline, 'termination_deadline_no_force_no_retry:'+name)
        time.sleep(5)


def restore_review(originals):
    receipt_path, digest = s.core.pf.RECEIPTS['rollback']
    r = json.loads(a.locked(Path(receipt_path), digest))
    require(r.get('mineru_rollback_alias_coverage') == 'PASS' and r.get('rollback_image') == ROLLBACK_IMAGE
            and r.get('rollback_config') == 'sha256:bfbdd4d3309110be003549b2959d5080204b27bcd6440e33d4c67d80ee3775e9'
            and r.get('rollback_manifest') == 'sha256:3c3f5d07bc290206b80bb2ca2af46101b874e4137fe00970498104ad062e7b64',
            'rollback_alias_receipt_changed')
    plans = {}
    for name in (WORKER, DISPATCHER):
        obj = originals[name]
        spec = copy.deepcopy(obj['spec'])
        require(len(spec['template']['spec']['containers']) == 1, 'restore_container_shape')
        spec['template']['spec']['containers'][0]['image'] = ROLLBACK_IMAGE
        manifest = dict(apiVersion='apps/v1', kind='Deployment', metadata=dict(name=name, namespace='deer-flow'), spec=spec)
        save(name+'.restore.proposal.private.json', manifest)
        current = s.core.inv.get('deployment', name)
        require(deployment_matches(current, obj, ORIGINAL_COUNTS[name]), 'restore_review_current_baseline_changed')
        # Admission rehearsal ONLY. Live restore needs fresh capacity/image/state
        # review and a newly bound CAS; never execute this stale patch later.
        dry_patch = [dict(op='test', path='/metadata/uid', value=current['metadata']['uid']),
            dict(op='test', path='/metadata/resourceVersion', value=current['metadata']['resourceVersion']),
            dict(op='test', path='/spec', value=current['spec']),
            dict(op='replace', path='/spec/template/spec/containers/0/image', value=ROLLBACK_IMAGE)]
        filename = name+'.restore.dry-run.patch.private.json'
        save(filename, dry_patch)
        answer = json.loads(s.core.inv.bounded_capture(['kubectl', '--request-timeout=20s', '-n', 'deer-flow',
            'patch', 'deployment', name, '--type=json', '--dry-run=server',
            '--patch-file', str(ROOT/filename), '-o', 'json']))
        expected = copy.deepcopy(obj)
        expected['spec'] = spec
        require(deployment_matches(answer, expected, ORIGINAL_COUNTS[name]), 'restore_dry_run_changed_unexpected_fields')
        save(name+'.restore.dry-run.private.json', answer)
        plans[name] = dict(uid=obj['metadata']['uid'], proposed_spec_sha256=s.core.inv.digest(spec),
                          desired_replicas=ORIGINAL_COUNTS[name], server_dry_run='PASS',
                          runtime_restore_rehearsed=False, fresh_image_coverage_required=True)
    save('RESTORE-PLAN.json', dict(automatic_restore=False, ready_to_execute=False, plans=plans,
        rollback_image=ROLLBACK_IMAGE, rollback_receipt_sha256=digest,
        original_missing_image_tags='DO_NOT_RESTORE_WITH_UNVERIFIED_ORIGINAL_TAG',
        old_route_only_rollback='INVALID_AFTER_RETIREMENT_DO_NOT_RUN_OLD_SWITCH_ROLLBACK',
        required_order=['KEEP_PUBLIC_ROUTE_CANDIDATE_AND_NEW_CAPACITY_AT_6',
            'RECHECK_EXACT_STATE_FRESH_NODE_CAPACITY_AND_PINNED_ALIAS_ON_ELIGIBLE_NODES',
            'SERVER_DRY_RUN_THEN_CAS_RESTORE_WORKERS_WITH_PINNED_ALIAS',
            'VERIFY_READY_WORKERS_AND_OLD_PRIVATE_SERVICE_THEN_RESTORE_DISPATCHER',
            'VERIFY_OLD_RUNTIME_AND_CA_THEN_SEPARATELY_REVIEW_PUBLIC_ROUTE_SWITCH'],
        before_16_expansion='RESTORE_PLAN_MUST_BE_REVIEWED_AGAIN_NOT_RUN_ALONGSIDE_16'))


def run(report, context, prior, stage_code, route_code):
    global PHASE
    PHASE = 'FRESH_BASELINE_AND_IDLE_AUDIT'
    deps = sw.invariants(context, 'candidate')
    originals = original_snapshots(context)
    inventory = a.old_inventory(deps)
    require(inventory == prior['inventory'], 'old_inventory_changed_since_accepted_audit')
    terminal = a.terminal_runtime(inventory['terminal'])
    require(terminal.get('no_activity_observed') is True, 'terminal_runtime_requires_review')
    report['terminal_runtime_before'] = terminal
    report['node_leases_before'] = node_leases(inventory)
    before_hashes = {n: save(n+'.before.private.json', deps[n]) for n in TARGETS}
    save('STATE.json', dict(utc=utc(), audit_sha256=AUDIT_SHA, before_hashes=before_hashes, inventory=inventory,
        candidate_capacity=6, explicit_operator_approval='IDLE_CHECKED_SCALE_WITH_RESIDUAL_DIRECT_POD_RACE'))
    restore_review(originals)
    candidates = sw.candidate_ready(context)
    workers = [r for r in candidates if r['container'] == 'adapter']
    report['candidate_runtime'] = [s.probe_pod(r, stage_code, workers, ROOT) for r in candidates]
    report['public_health_before'] = sw.public_health(workers[0], route_code, 'candidate', 'candidate', ROOT, 0)
    counts = dict(ORIGINAL_COUNTS)
    for i, name in enumerate(TARGETS):
        PHASE = 'RETIRE_'+name
        retire_once(name, context, originals, counts, inventory, report)
        wait_retired(name, context, originals, counts, inventory)
        report['termination_verified'].append(name)
        report['public_health_checks'].append(sw.public_health(workers[0], route_code, 'candidate', 'candidate', ROOT, i+1))
    PHASE = 'FINAL_CONVERGENCE_REVIEW'
    invariants(context, originals, counts, inventory)
    terminal = a.terminal_runtime(inventory['terminal'])
    require(terminal.get('no_activity_observed') is True, 'terminal_runtime_requires_review')
    report['terminal_runtime_after'] = terminal
    report['node_leases_after'] = node_leases(inventory)
    report.update(mineru_old_pool_retire='PASS', old_desired_replicas=counts,
        old_pool_control_plane_retired=True, public_route='candidate', new_pool_slots=6,
        datax02_03_runtime_absence='NOT_INDEPENDENTLY_SCANNED',
        old_request_completion='PRE_SCALE_IDLE_OBSERVED_NOT_ATOMIC_LOSSLESS_DRAIN_PROOF',
        ready_for_expansion_review=True, ready_to_expand_16=False,
        remaining_gates=['OLD_NODE_RUNTIME_AND_FRESH_CAPACITY_REVIEW', 'SEPARATE_16_SLOT_EXPANSION_REVIEW'])


def status(context):
    originals = original_snapshots(context)
    rows = []
    for name in TARGETS:
        now = s.get('deployments', name)
        mode = ('ORIGINAL' if deployment_matches(now, originals[name], ORIGINAL_COUNTS[name]) else
                'SCALED_ZERO' if deployment_matches(now, originals[name], 0) else 'UNKNOWN_REQUIRES_REVIEW')
        pods = s.core.inv.owned_pods(now) if now and mode != 'UNKNOWN_REQUIRES_REVIEW' else []
        rows.append(dict(deployment=name, state=mode, desired=(now or {}).get('spec', {}).get('replicas'),
            owned_pods=[dict(name=p['metadata']['name'], uid=p['metadata']['uid'], phase=p.get('status', {}).get('phase'),
                deleting=bool(p['metadata'].get('deletionTimestamp'))) for p in pods],
            patch_requested=(ROOT/(name+'.PATCH-REQUESTED.json')).exists(),
            patch_acknowledged=(ROOT/(name+'.PATCH-ACK.private.json')).exists()))
    service = s.get('services', WORKER)
    route = sw.route_state(service, context)
    record = None
    if (ROOT/'RESULT.json').exists():
        raw = s.core.pf.read_private(ROOT/'RESULT.json')
        value = json.loads(raw)
        record = dict(sha256=sha(raw), fields={k: value.get(k) for k in ('mineru_old_pool_retire',
            'failed_stage', 'failed_check', 'patches_attempted', 'patches_acknowledged', 'termination_verified')})
    emit(dict(mineru_retire_status='READ_ONLY', old_deployments=rows, public_route=route, result=record,
        files_modified=False, production_writes=0, pod_exec=False, provider_requests=0,
        automatic_retry=False, instruction='SEND_STATUS_NO_REPEAT_NO_OLD_ROUTE_ROLLBACK'))


def main():
    global PHASE
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--retire', action='store_true')
    modes.add_argument('--status', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    context, prior, stage_code, route_code = bootstrap()
    if args.status:
        status(context)
        return 0
    a.safe_dir(ROOT.parent)
    require(not ROOT.exists() and not ROOT.is_symlink(), 'operation_exists_use_status_do_not_repeat')
    ROOT.mkdir(mode=0o700)
    sync_dir(ROOT.parent)
    report = dict(mineru_old_pool_retire='UNCONFIRMED', patches_attempted=[], patches_acknowledged=[],
        termination_verified=[], public_health_checks=[], utc_started=utc(), prior_audit_sha256=AUDIT_SHA,
        candidate_workloads_modified=False, public_route_changed=False, credentials_modified=False,
        queues_modified=False, provider_requests=0, documents_submitted=0, final_16_expansion=False,
        automatic_retry=False, automatic_rollback=False, forced_pod_deletion=False,
        ready_to_expand_16=False, old_pool_control_plane_retired=False,
        residual_direct_old_pod_arrival_risk='OPERATOR_ACKNOWLEDGED',
        business_upload_acceptance='USER_REPORTED_BOTH_PATHS_SUCCESS')
    emit(dict(mineru_retire_evidence=str(ROOT), scope='TWO_OLD_DEPLOYMENT_REPLICA_FIELDS_ONLY',
        order=list(TARGETS), public_route_changed=False, queue_empty_required=False, new_pool_slots=6))
    try:
        run(report, context, prior, stage_code, route_code)
    except BaseException as exc:
        known = isinstance(exc, RetireFailure) or type(exc).__name__ in (
            'AuditFailure', 'StageFailure', 'SwitchFailure', 'CheckFailure', 'PreflightFailure', 'LoadFailure')
        text = str(exc)
        report.update(failed_stage=PHASE, error_type=type(exc).__name__, raw_error_printed=False,
            failed_check=text if known and re.fullmatch('[A-Za-z0-9_:./-]{1,240}', text) else 'SAFE_DIAGNOSTIC_REQUIRED',
            last_operation=a.safe_last_operation())
    report['utc_finished'] = utc()
    digest = save('RESULT.json', report)
    emit({k: report.get(k) for k in ('mineru_old_pool_retire', 'failed_stage', 'failed_check',
        'patches_attempted', 'patches_acknowledged', 'termination_verified', 'old_pool_control_plane_retired',
        'public_route', 'new_pool_slots', 'ready_for_expansion_review', 'ready_to_expand_16')})
    emit(dict(report=str(ROOT/'RESULT.json'), report_sha256=digest, automatic_retry=False,
        automatic_rollback=False, final_16_expansion=False,
        status_command='sudo python3 -I -B '+str(Path(__file__).absolute())+' --status',
        instruction='SEND_REPORT_ON_UNCERTAINTY_STATUS_ONLY_NO_REPEAT'))
    return 0 if report['mineru_old_pool_retire'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(mineru_old_pool_retire='UNCONFIRMED', error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, RetireFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            raw_error_printed=False, automatic_retry=False, automatic_rollback=False,
            instruction='USE_STATUS_DO_NOT_REPEAT_RETIRE'))
        sys.exit(1)
