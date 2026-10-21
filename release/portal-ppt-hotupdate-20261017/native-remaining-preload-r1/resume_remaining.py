#!/usr/bin/env python3
"""Continue the four remaining pinned image unpacks once on datax-02."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import types
import uuid
import zlib

SUPERVISOR_SHA = '13b18107c4be2018d74e6b5d4fea00ab36b46ff091a4a601958da593dfbb5cec'
SUPERVISOR_Z64 = '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'
SUCCESS_REPORT_SHA = 'c0732346dcb41b9a979984b1036bf67c05ce314c030551246f5c7ec3f20ab662'
SUCCESS_REPORT_BYTES = 6504
SUCCESS_EVIDENCE = Path('/var/lib/portal-ppt-gateway-single-recovery-3b0f7389229d4e2fb39f534b23eff1b4')
OP = 'abf96dde50174b1eb7caf70a4e6711b1'
ROOT = Path('/var/lib')
ONCE = ROOT / ('portal-ppt-remaining-preload-r1-' + OP + '.STARTED.json')
ROLES = ('gateway-rollback-compatible', 'ppt-master-runner-forward',
         'ppt-master-runner-rollback-compatible', 'ppt-master-agent-host-forward')
PREVIOUS = ('frontend', 'gateway-forward')
LEGACY_NAMES = 'Exact reviewed supervisor uses GATEWAY-IMPORT-* names inside each actual role directory; receipt role is authoritative.'


def require(value, code):
    if not value: raise ValueError(code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def load_supervisor():
    require(re.fullmatch('[a-f0-9]{64}', SUCCESS_REPORT_SHA) and SUCCESS_EVIDENCE.is_absolute(), 'success_boundary_not_bound')
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(SUPERVISOR_Z64, validate=True), 65537)
    require(len(raw) <= 65536 and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
            and sha(raw) == SUPERVISOR_SHA, 'supervisor_identity')
    value = types.ModuleType('reviewed_gateway_supervisor')
    exec(compile(raw, 'reviewed_gateway_supervisor.py', 'exec'), value.__dict__)
    return value


def expected_completion(row):
    return dict(role=row['role'], tag=row['tag'], digest_reference=row['digest_reference'],
                manifest_digest=row['manifest_digest'], config_digest=row['config_digest'], platform='linux/arm64',
                archive_sha256=row['archive_sha256'], content_and_unpack='PASS', cri_visibility='PASS')


def success_boundary(supervisor, module, profile, host):
    module.B.safe_dir(SUCCESS_EVIDENCE)
    wanted = {'STARTED.json', 'REPORT.json', 'GATEWAY-IMPORT-REQUESTED.json', 'GATEWAY-IMPORT-RESULT.json',
              'stdout.private.log', 'stderr.private.log'}
    require(set(os.listdir(SUCCESS_EVIDENCE)) == wanted, 'gateway_success_directory_changed')
    raw = module.private_bytes(SUCCESS_EVIDENCE / 'REPORT.json', 65536)
    require(len(raw) == SUCCESS_REPORT_BYTES and sha(raw) == SUCCESS_REPORT_SHA, 'gateway_success_report_changed')
    value = module.B.parse(raw)
    require(value.get('schema') == 'portal-native-gateway-single-recovery.v1'
            and value.get('status') == 'GATEWAY_COMPLETE_REVIEW_REQUIRED' and value.get('operation') == OP
            and value.get('evidence') == str(SUCCESS_EVIDENCE) and value.get('host_identity') == host
            and value.get('gateway_import_requested') is True and value.get('import_process_started') is True
            and value.get('import_returncode') == 0 and value.get('image_import_may_be_partial') is False
            and value.get('prior_receipts') == supervisor.PINS, 'gateway_success_semantics')
    rows = {row['role']: row for row in profile['images']}
    require(value.get('gateway_completion') == expected_completion(rows['gateway-forward'])
            and value.get('frontend_read_only_verification') == expected_completion(rows['frontend']), 'gateway_success_image_binding')
    snapshot = value['gateway_after']
    require(snapshot['already_committed'] is True and snapshot['active_or_view_conflict'] is False
            and snapshot['layers'] == snapshot['committed_prefix'] == snapshot['committed_total'], 'gateway_success_snapshot_binding')
    receipt = value['import_receipt']
    require(receipt['role'] == 'gateway-forward' and receipt['returncode'] == 0 and receipt['process_started'] is True
            and receipt['capture_complete'] is True and receipt['output_limit_exceeded'] is False
            and receipt['automatic_retry'] is False and 'error_code' not in receipt, 'gateway_success_import_binding')
    require(module.private_bytes(SUCCESS_EVIDENCE / 'GATEWAY-IMPORT-RESULT.json', 65536) == module.B.encoded(receipt),
            'gateway_success_result_receipt_changed')
    request = dict(role='gateway-forward', phase='PHASE-B', automatic_retry=False)
    require(module.private_bytes(SUCCESS_EVIDENCE / 'GATEWAY-IMPORT-REQUESTED.json', 65536) == module.B.encoded(request),
            'gateway_success_request_receipt_changed')
    once = dict(operation=OP, evidence=str(SUCCESS_EVIDENCE), automatic_retry=False)
    require(module.private_bytes(supervisor.ONCE, 65536) == module.B.encoded(once), 'gateway_success_once_changed')
    started = module.B.parse(module.private_bytes(SUCCESS_EVIDENCE / 'STARTED.json', 65536))
    require(started['schema'] == value['schema'] and started['operation'] == OP and started['evidence'] == str(SUCCESS_EVIDENCE)
            and started['host_identity'] == host and started['prior_receipts'] == supervisor.PINS
            and started['status'] == 'INCOMPLETE' and started['gateway_import_requested'] is False,
            'gateway_success_started_binding')
    require(set(receipt['logs']) == {'stdout.private.log', 'stderr.private.log'}, 'gateway_success_log_set')
    for name, record in receipt['logs'].items():
        require(record['path'] == str(SUCCESS_EVIDENCE / name) and record['root_private'] is True
                and record['truncated'] is False and record['observed_bytes'] == record['saved_bytes'], 'gateway_success_log_binding')
        log = module.private_bytes(SUCCESS_EVIDENCE / name, supervisor.LIMIT)
        require(len(log) == record['saved_bytes'] and sha(log) == record['sha256'], 'gateway_success_log_changed')
    return dict(report_sha256=SUCCESS_REPORT_SHA, report_bytes=len(raw), evidence=str(SUCCESS_EVIDENCE),
                import_returncode=0, linked_request_result_and_logs_verified=True)


def snapshot_gate(supervisor, module, image):
    # The exact reviewed checker is generic over the supplied layer chain.
    try:
        return supervisor.gateway_snapshot_gate(module, image)
    except ValueError as exc:
        raise ValueError(str(exc).replace('gateway_', 'role_')) from None


def captured_role(supervisor, module, row, fd, profile, plan, sealed_fd, item):
    parent = module.STATE
    module.STATE = module.B.fresh(parent / row['role'])
    item['evidence'] = str(module.STATE)
    capture = dict(gateway_import_requested=False, import_process_started=False, image_import_may_be_partial=False,
                   import_returncode=None, safe_error_phrases=None)
    try:
        supervisor.captured_import(module, row, fd, profile, plan, sealed_fd, capture)
    finally:
        item.update(import_requested=capture['gateway_import_requested'], import_process_started=capture['import_process_started'],
                    image_import_may_be_partial=capture['image_import_may_be_partial'], import_returncode=capture['import_returncode'],
                    safe_error_phrases=capture['safe_error_phrases'])
        if 'import_receipt' in capture: item['import_receipt'] = capture['import_receipt']
        module.STATE = parent


def raise_if_interrupted(exc, module):
    code = exc.code if isinstance(exc, module.B.Failure) else str(exc) if isinstance(exc, ValueError) else None
    if code in ('interrupted_no_retry', 'interrupted_no_automatic_retry', 'total_deadline'): raise exc


def continue_four(supervisor, module, profile, plan, sealed_fd, metadata, result):
    rows = {row['role']: row for row in profile['images']}
    images = {image['role']: image for image in plan['images']}
    require(set(rows) == set(PREVIOUS + ROLES) and set(images) == set(rows), 'image_role_set_changed')
    module.all_content_present(plan); module.verify_references(profile); module.B.collision_check(profile)
    for role in PREVIOUS:
        completion, config = module.B.complete_row(rows[role]); del config
        require(snapshot_gate(supervisor, module, images[role])['already_committed'], 'previous_image_unpacked_state_changed')
        result['previous_read_only_verification'].append(completion)
    for role in ROLES:
        module.B.progress('REMAINING_PRELOAD', role=role)
        item = dict(role=role, status='INCOMPLETE', import_requested=False, import_process_started=False,
                    import_returncode=None, image_import_may_be_partial=False)
        result['role_results'].append(item)
        module.all_content_present(plan); module.verify_references(profile); module.B.collision_check(profile)
        item['before'] = snapshot_gate(supervisor, module, images[role])
        module.storage_check(profile, plan, 'BEFORE_PHASE_B', sealed_fd)
        if not item['before']['already_committed']:
            try:
                captured_role(supervisor, module, rows[role], metadata[role], profile, plan, sealed_fd, item)
            finally:
                result['target_import_requested'] = any(value['import_requested'] for value in result['role_results'])
                result['image_import_may_be_partial'] = any(value['image_import_may_be_partial'] for value in result['role_results'])
        completion, config = module.B.complete_row(rows[role])
        item['completion'] = completion
        if role == 'ppt-master-agent-host-forward':
            try: result['host_runtime_inputs'] = module.B.host_projection(config, rows[role]['config_digest'])
            except module.B.Failure as exc:
                raise_if_interrupted(exc, module)
                result['host_runtime_inputs'] = dict(state='NOT_COLLECTED', error_code=exc.code)
        del config
        item['after'] = snapshot_gate(supervisor, module, images[role])
        require(item['after']['already_committed'], 'role_final_snapshot_incomplete')
        item.update(status='COMPLETE', image_import_may_be_partial=False)
        module.write_receipt(role + '.PASS.json', item)
    module.all_content_present(plan); module.verify_references(profile); module.B.collision_check(profile)
    for row in profile['images']:
        completion, config = module.B.complete_row(row); del config
        require(snapshot_gate(supervisor, module, images[row['role']])['already_committed'], 'final_snapshot_incomplete')
        result['final_six_images'].append(completion)
    try: result['postgres_cache'] = module.B.postgres_cache(profile)
    except Exception as exc:
        raise_if_interrupted(exc, module)
        result['postgres_cache'] = dict(state='NOT_COLLECTED', error_code=exc.code if isinstance(exc, module.B.Failure) else 'METADATA_UNAVAILABLE',
                                       scope='LOCAL_NODE_CACHE_ONLY_NOT_CLUSTER_ABSENCE')
    module.write_receipt('REMAINING-FOUR-PASS.json', dict(all_six_complete=True, imported_roles=[item['role'] for item in result['role_results'] if item['import_requested']]))
    result.update(status='COMPLETE_PRELOAD_REVIEW_REQUIRED', image_import_may_be_partial=False, phase_b='PASS')


def safe_error(exc, module):
    code = exc.code if module and isinstance(exc, module.B.Failure) else str(exc) if isinstance(exc, ValueError) else 'REMAINING_PRELOAD_FAILED'
    return code if re.fullmatch('[A-Za-z0-9_]{1,120}', code) else 'REMAINING_PRELOAD_FAILED'


def run():
    module = lock = sealed_fd = evidence = None
    metadata = {}
    result = dict(schema='portal-native-remaining-preload.v1', operation=OP, status='INCOMPLETE', phase_b='NOT_REQUESTED',
                  gateway_success_report_sha256=SUCCESS_REPORT_SHA, supervisor_sha256=SUPERVISOR_SHA,
                  automatic_retry=False, ready_for_rollout=False, native_runtime_passed=False,
                  target_import_requested=None, image_import_may_be_partial=None, roles_requested=list(ROLES),
                  role_results=[], previous_read_only_verification=[], final_six_images=[],
                  frontend_reimported=False, gateway_forward_reimported=False, phase_a_repeated=False,
                  old_evidence_modified=False, existing_images_deleted=False, workload_mutation=False,
                  database_access=False, model_access=False, ssh_scp=False, raw_output_handoff=False,
                  host_runtime_inputs=dict(state='NOT_COLLECTED'), postgres_cache=dict(state='NOT_COLLECTED'),
                  legacy_capture_receipt_names=LEGACY_NAMES)
    rc = 1
    try:
        def interrupted(signum, frame): raise ValueError('interrupted_no_retry')
        for sig in (signal.SIGTERM, signal.SIGHUP): signal.signal(sig, interrupted)
        os.umask(0o077)
        supervisor = load_supervisor()
        guard, module, profile, host = supervisor.load(); result['host_identity'] = host
        lock = module.common_lock()
        result['prior_failure_receipts'] = supervisor.old_boundary(module)
        result['gateway_success_boundary'] = success_boundary(supervisor, module, profile, host)
        plan, receipts = guard.inspect_checkpoint(module, profile)
        require(not os.path.lexists(ONCE), 'remaining_preload_already_started')
        result.update(target_import_requested=False, image_import_may_be_partial=False)
        sealed_fd = guard.open_sealed(module)
        images = {value['role']: value for value in plan['images']}
        rows = {row['role']: row for row in profile['images']}
        require(set(rows) == set(PREVIOUS + ROLES) and set(images) == set(rows), 'image_role_set_changed')
        for role in ROLES:
            metadata[role] = guard.metadata_fd(module, sealed_fd, rows[role], images[role], receipts[role + '.SEALED.json'], plan['allocation']['maximum_metadata_import_bytes'])
        evidence = module.B.fresh(ROOT / ('portal-ppt-remaining-preload-' + uuid.uuid4().hex))
        result['evidence'] = str(evidence)
        module.B.write_new(ONCE, module.B.encoded(dict(operation=OP, evidence=str(evidence), automatic_retry=False)))
        module.STATE = evidence; module.REPORT = module.base_report(OP)
        module.write_receipt('STARTED.json', result)
        result['phase_b'] = 'REQUESTED'
        continue_four(supervisor, module, profile, plan, sealed_fd, metadata, result)
        rc = 0
    except (Exception, KeyboardInterrupt) as exc:
        result['error_code'] = safe_error(exc, module)
    finally:
        try:
            if module is not None and module.REPORT is not None: result['storage_checks'] = module.REPORT.get('storage_checks', [])
            raw = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode()
            if evidence is not None:
                try: module.B.write_new(evidence / 'REPORT.json', raw)
                except Exception: pass
            summary = dict(schema=result['schema'], status=result['status'], report_bytes=len(raw), report_sha256=sha(raw), exitcode=rc,
                           target_import_requested=result['target_import_requested'], automatic_retry=False, ready_for_rollout=False,
                           image_import_may_be_partial=result['image_import_may_be_partial'],
                           completed_roles=[row['role'] for row in result['role_results'] if row['status'] == 'COMPLETE'],
                           import_returncodes={row['role']: row['import_returncode'] for row in result['role_results']},
                           safe_error_phrases={row['role']: row.get('safe_error_phrases') for row in result['role_results']})
            if result.get('error_code'): summary['error_code'] = result['error_code']
            try:
                require(module is not None, 'handoff_module_unavailable')
                summary['download_report'] = module.handoff_report(raw)
            except Exception:
                summary['download_report_status'] = 'UNAVAILABLE_SAFE_REPORT_PRINTED'
                sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
            print(json.dumps(summary, sort_keys=True), flush=True)
        finally:
            for fd in list(metadata.values()) + [sealed_fd, lock]:
                if fd is not None: os.close(fd)
    return rc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-remaining-once', action='store_true', required=True); parser.parse_args()
    raise SystemExit(run())
