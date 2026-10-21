#!/usr/bin/env python3
"""Read-only Kubernetes retirement reconciliation. Only NEW local evidence is written.

No scaling, deletion, restart, credentials change, provider call or expansion.
Original failed receipt and operator-installed retirement tool remain untouched.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import traceback
import types

RETIRE_CODE = '__EMBED_CORRECTED_RETIRE__'
ORIGINAL_TOOL = Path('/var/lib/mineru-old-pool-retire-tool-OxT9Syut/retire.py')
ORIGINAL_TOOL_SHA = '3789f99a2f4064b3cdb76a06497ae997a8efe3c4977c1a36c12bcb2e0e98b2d9'
PRIOR = Path('/var/lib/mineru-old-pool-retire-20261016-r1')
PRIOR_SHA = '5478f05844f8b683388518e49a6e509b92b610a4d87a2849d16b8bafc8ec44bf'
_CRI = ('crictl', '--runtime-endpoint', 'unix:///run/containerd/containerd.sock',
        '--image-endpoint', 'unix:///run/containerd/containerd.sock', '--timeout', '20s')
RUNTIME_READ_COMMANDS = (_CRI+('pods', '-o', 'json'), _CRI+('ps', '--all', '-o', 'json'),
    ('ctr', '--address', '/run/containerd/containerd.sock', '--namespace', 'k8s.io', 'tasks', 'list'))
PHASE = 'BOOTSTRAP'
r = None


class ReconcileFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise ReconcileFailure(label)


def emit(row):
    print(json.dumps(row, sort_keys=True), flush=True)


def deny_mutation(*args, **kwargs):
    raise ReconcileFailure('reconciliation_mutation_forbidden')


def readonly_capture(original):
    def call(args):
        # Permit only Kubernetes GETs and the three exact local inventory argv
        # used by the frozen historical-runtime audit. Never generic ctr/crictl.
        # Diagnostic Pod exec is separately guarded by the frozen audit module.
        kubernetes_get = (len(args) >= 6 and args[:2] == ['kubectl', '--request-timeout=20s']
                and args[2] == '-n' and args[3] in ('deer-flow', 'kube-node-lease')
                and args[4] == 'get')
        require(kubernetes_get or tuple(args) in RUNTIME_READ_COMMANDS, 'read_only_capture_violation')
        return original(args)
    return call


def bind_evidence():
    r.a.locked(ORIGINAL_TOOL, ORIGINAL_TOOL_SHA)
    prior = json.loads(r.a.locked(PRIOR/'RESULT.json', PRIOR_SHA))
    require(prior.get('mineru_old_pool_retire') == 'UNCONFIRMED'
            and prior.get('error_type') == 'LoadFailure'
            and prior.get('failed_stage') == 'RETIRE_'+r.WORKER
            and prior.get('patches_attempted') == list(r.TARGETS)
            and prior.get('patches_acknowledged') == list(r.TARGETS)
            and prior.get('termination_verified') == [r.DISPATCHER], 'prior_failure_boundary_changed')
    for key in ('candidate_workloads_modified', 'public_route_changed', 'credentials_modified',
                'queues_modified', 'final_16_expansion'):
        require(prior.get(key) is False, 'prior_scope_changed')
    state = json.loads(r.s.core.pf.read_private(PRIOR/'STATE.json'))
    require(state.get('audit_sha256') == r.AUDIT_SHA and state.get('candidate_capacity') == 6
            and set(state.get('before_hashes', {})) == set(r.TARGETS), 'retire_state_binding_changed')
    return state


def accept_acks(originals, state):
    for name in r.TARGETS:
        before = json.loads(r.a.locked(PRIOR/(name+'.before.private.json'), state['before_hashes'][name]))
        require(r.deployment_matches(before, originals[name], r.ORIGINAL_COUNTS[name]), 'before_snapshot_changed')
        intent = json.loads(r.s.core.pf.read_private(PRIOR/(name+'.PATCH-REQUESTED.json')))
        expected = r.baseline_spec(before, 0)
        require(intent.get('target') == name and intent.get('uid') == before['metadata']['uid']
                and intent.get('source_replicas') == r.ORIGINAL_COUNTS[name]
                and intent.get('target_replicas') == 0
                and intent.get('source_spec_sha256') == r.s.core.inv.digest(before['spec'])
                and intent.get('target_spec_sha256') == r.s.core.inv.digest(expected), 'intent_identity_changed')
        patch = json.loads(r.a.locked(PRIOR/(name+'.scale.patch.private.json'), intent['patch_sha256']))
        require(len(patch) == 4 and patch[0] == dict(op='test', path='/metadata/uid', value=before['metadata']['uid'])
                and patch[1].get('op') == 'test' and patch[1].get('path') == '/metadata/resourceVersion'
                and isinstance(patch[1].get('value'), str) and patch[1]['value']
                and patch[2] == dict(op='test', path='/spec', value=before['spec'])
                and patch[3] == dict(op='replace', path='/spec/replicas', value=0), 'scale_patch_changed')
        ack = json.loads(r.s.core.pf.read_private(PRIOR/(name+'.PATCH-ACK.private.json')))
        require(r.deployment_matches(ack, before, 0), 'scale_ack_changed')


def legacy_ca_dependency():
    # Reproduce the exact legacy READ-ONLY lookup after both old Deployments
    # have been verified at zero. Do not mask a different failure.
    try:
        r.s.core.ca_bytes()
    except Exception as exc:
        require(type(exc).__name__ == 'LoadFailure' and str(exc) == 'no_ready_ca_source',
                'legacy_ca_lookup_failed_differently')
        return dict(source_review='OLD_WORKER_READY_POD_DEPENDENCY',
                    reproduced_error='LoadFailure:no_ready_ca_source',
                    replacement='SAME_PINNED_CA_HASH_FROM_EXACT_LIVE_CANDIDATE')
    raise ReconcileFailure('legacy_ca_source_unexpectedly_present')


def check_controller(dep):
    status = dep.get('status', {})
    require(dep['spec'].get('replicas') == 0
            and status.get('observedGeneration', 0) >= dep['metadata']['generation']
            and all(status.get(k, 0) == 0 for k in ('replicas', 'readyReplicas', 'availableReplicas', 'updatedReplicas')),
            'old_controller_not_converged')


def accept_historical_runtime(report, historical):
    # Preserve the safe partial diagnostic instead of reporting an unavailable
    # read as observed activity. Either outcome still blocks acceptance.
    report['historical_runtime'] = historical
    require(historical.get('check') in ('COMPLETE', 'NOT_REQUIRED'), 'historical_runtime_read_incomplete')
    require(historical.get('no_activity_observed') is True, 'historical_runtime_activity_observed')


def reconcile(report, context, accepted, stage_code, route_code):
    global PHASE
    PHASE = 'BIND_ACKNOWLEDGED_SCALE_AND_CURRENT_STATE'
    state = bind_evidence()
    require(state.get('inventory') == accepted['inventory'], 'accepted_inventory_changed')
    originals = r.original_snapshots(context)
    accept_acks(originals, state)
    inventory, counts = state['inventory'], {n: 0 for n in r.TARGETS}
    deps, candidates = r.invariants(context, originals, counts, inventory)
    for name in r.TARGETS:
        check_controller(deps[name])
        require(not r.owned_state(deps[name], inventory, True), 'remaining_old_pods_requires_review')
    PHASE = 'LEGACY_CA_DEPENDENCY_REPRODUCTION'
    report['failure_diagnosis'] = legacy_ca_dependency()
    emit(dict(retirement_reconcile='DIAGNOSIS', **report['failure_diagnosis']))
    PHASE = 'READ_ONLY_TERMINATION_AND_ROUTE_RECHECK'
    for name in r.TARGETS:
        r.wait_retired(name, context, originals, counts, inventory)
    historical = r.a.terminal_runtime(inventory['terminal'])
    accept_historical_runtime(report, historical)
    report['node_leases'] = r.node_leases(inventory)
    workers = [p for p in candidates if p['container'] == 'adapter']
    PHASE = 'LIVE_CANDIDATE_CONFIG_CA_AND_HEALTH'
    report['candidate_runtime'] = [r.s.probe_pod(p, stage_code, workers, r.ROOT) for p in candidates]
    report['public_health'] = r.sw.public_health(workers[0], route_code, 'candidate', 'candidate', r.ROOT, 0)
    deps, _ = r.invariants(context, originals, counts, inventory)
    for name in r.TARGETS:
        check_controller(deps[name])
        require(not r.owned_state(deps[name], inventory, True), 'old_pods_reappeared')
    # Capture exact retired process identities for subsequent node-local scans.
    r.save('RETIRED-PROCESS-IDENTITIES.json', dict(targets=inventory['targets'], pending=inventory['pending'],
        terminal=inventory['terminal'], source_audit_sha256=r.AUDIT_SHA, prior_failure_sha256=PRIOR_SHA))
    report.update(mineru_retirement_reconcile='PASS', old_pool_control_plane_retired=True,
        old_desired_replicas=counts, public_route='candidate', new_pool_slots=6,
        candidate_runtime_acceptance='PASS', ready_for_expansion_review=True,
        node_local_runtime_absence_datax02_03='NOT_INDEPENDENTLY_SCANNED',
        remaining_gates=['OLD_NODE_RUNTIME_ABSENCE', 'FRESH_CAPACITY_AND_SEPARATE_16_SLOT_REVIEW'])


def main():
    global r, PHASE
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    require(RETIRE_CODE != '__EMBED_'+'CORRECTED_RETIRE__', 'reconciliation_not_packaged')
    r = types.ModuleType('readonly_retirement_reconciliation')
    r.__file__ = str(Path(__file__).absolute())
    exec(compile(RETIRE_CODE, '<reviewed-retirement>', 'exec'), r.__dict__)
    context, accepted, stage_code, route_code = r.bootstrap()
    r.patch_call = r.retire_once = r.run = r.restore_review = deny_mutation
    r.s.core.inv.bounded_capture = readonly_capture(r.s.core.inv.bounded_capture)
    r.a.safe_dir(Path('/var/lib'))
    r.ROOT = Path(tempfile.mkdtemp(prefix='mineru-retirement-reconcile-', dir='/var/lib'))
    r.sync_dir(r.ROOT.parent)
    report = dict(mineru_retirement_reconcile='UNCONFIRMED', utc_started=r.utc(),
        prior_failure_sha256=PRIOR_SHA, original_evidence_modified=False,
        production_writes=0, production_workloads_mutated=False, provider_requests=0,
        new_private_evidence_only=True, ready_to_expand_16=False, final_16_expansion=False,
        automatic_retry=False, documents_submitted=0, raw_error_printed=False)
    emit(dict(retirement_reconcile_evidence=str(r.ROOT), production_writes=0, provider_requests=0))
    try:
        reconcile(report, context, accepted, stage_code, route_code)
    except BaseException as exc:
        allowed = type(exc).__name__ in ('ReconcileFailure', 'RetireFailure', 'AuditFailure', 'StageFailure',
            'SwitchFailure', 'CheckFailure', 'PreflightFailure', 'LoadFailure')
        label = str(exc)
        report.update(failed_stage=PHASE, error_type=type(exc).__name__,
            failed_check=label if allowed and re.fullmatch('[A-Za-z0-9_:./-]{1,240}', label) else 'SAFE_DIAGNOSTIC_REQUIRED',
            code_frames=[dict(file=Path(f.filename).name, function=f.name, line=f.lineno)
                         for f in traceback.extract_tb(exc.__traceback__)[-10:]],
            last_operation=r.a.safe_last_operation())
    report['utc_finished'] = r.utc()
    digest = r.save('RESULT.json', report)
    emit({k: report.get(k) for k in ('mineru_retirement_reconcile', 'failed_stage', 'failed_check',
        'error_type', 'code_frames', 'old_pool_control_plane_retired', 'public_route', 'new_pool_slots',
        'candidate_runtime_acceptance', 'ready_for_expansion_review', 'ready_to_expand_16')})
    emit(dict(report=str(r.ROOT/'RESULT.json'), report_sha256=digest, production_writes=0,
        original_evidence_modified=False, provider_requests=0, final_16_expansion=False))
    return 0 if report['mineru_retirement_reconcile'] == 'PASS' else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(mineru_retirement_reconcile='UNCONFIRMED', stage=PHASE,
            error_type=type(exc).__name__, raw_error_printed=False,
            failed_check=str(exc) if isinstance(exc, ReconcileFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            production_writes=0, provider_requests=0, ready_to_expand_16=False))
        sys.exit(1)
