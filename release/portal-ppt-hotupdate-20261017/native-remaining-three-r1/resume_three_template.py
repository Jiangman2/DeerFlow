#!/usr/bin/env python3
"""Continue the three remaining pinned image unpacks once on datax-02."""
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
SUPERVISOR_Z64 = '__SUPERVISOR_Z64__'
SUCCESS_REPORT_SHA = '__SUCCESS_REPORT_SHA__'
SUCCESS_REPORT_BYTES = 6504
SUCCESS_EVIDENCE = Path('__SUCCESS_EVIDENCE__')
OP = 'abf96dde50174b1eb7caf70a4e6711b1'
ROOT = Path('/var/lib')
PARTIAL_REPORT_SHA = '__PARTIAL_REPORT_SHA__'
PARTIAL_REPORT_BYTES = 8881
PARTIAL_EVIDENCE = Path('__PARTIAL_EVIDENCE__')
PARTIAL_ONCE = Path('/var/lib/portal-ppt-remaining-preload-r1-' + OP + '.STARTED.json')
ONCE = ROOT / ('portal-ppt-remaining-three-r1-' + OP + '.STARTED.json')
ROLES = ('ppt-master-runner-forward',
         'ppt-master-runner-rollback-compatible', 'ppt-master-agent-host-forward')
PREVIOUS = ('frontend', 'gateway-forward', 'gateway-rollback-compatible')
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


def parse_snapshot_table(raw):
    text = raw.decode('utf-8')
    require(not any(ord(char) < 32 and char != '\n' for char in text), 'snapshot_table_control_character')
    lines = text.splitlines()
    require(lines and re.fullmatch(r'KEY +PARENT +KIND *', lines[0]), 'snapshot_table_header')
    parent_column = lines[0].index('PARENT'); kind_column = lines[0].index('KIND')
    rows = {}
    for line in lines[1:]:
        require(len(line) > kind_column and line[parent_column-1] == ' ' and line[kind_column-1] == ' ', 'snapshot_table_column_shape')
        key = line[:parent_column].rstrip(' ')
        parent = line[parent_column:kind_column].rstrip(' ')
        kind = line[kind_column:].rstrip(' ')
        require(key and key == key.strip() and parent == parent.strip() and kind in ('Committed', 'Active', 'View')
                and key not in rows and len(key) <= 4096 and len(parent) <= 4096, 'snapshot_table_row_shape')
        rows[key] = dict(parent=parent, kind=kind)
    return rows


def snapshot_gate(supervisor, module, image):
    raw = module.B.capture(module.B.CTR + ['snapshots', '--snapshotter', 'overlayfs', 'ls'],
                           'ROLE_SNAPSHOT_GATE', timeout=30, limit=8*1024**2)
    rows = parse_snapshot_table(raw)
    chains = {layer['chain_id']: layer for layer in image['layers']}
    require(len(chains) == len(image['layers']) and bool(chains), 'snapshot_candidate_chain_shape')
    found = {key: row for key, row in rows.items() if key in chains}
    for key, row in found.items():
        require(row['parent'] == chains[key]['parent'], 'snapshot_candidate_parent_changed')
        require(row['kind'] == 'Committed', 'candidate_key_active_or_view_requires_review')
    prefix = 0
    for layer in image['layers']:
        if layer['chain_id'] not in found: break
        prefix += 1
    require(prefix == len(found), 'snapshot_candidate_committed_prefix_not_closed')
    parent_only_children = 0
    for key, row in rows.items():
        if row['kind'] not in ('Active', 'View'): continue
        extract = re.fullmatch(r'extract-\S+ (sha256:[a-f0-9]{64})', key)
        if extract and extract.group(1) in chains:
            raise ValueError('candidate_extraction_key_requires_review')
        if set(key.split()) & set(chains):
            raise ValueError('unclassified_candidate_key_requires_review')
        if row['parent'] in chains:
            require(re.fullmatch('[a-f0-9]{64}', key) is not None and row['kind'] == 'Active', 'unclassified_active_child_requires_review')
            require(row['parent'] in found, 'active_child_parent_not_committed')
            parent_only_children += 1
    return dict(layers=len(chains), committed_prefix=prefix, committed_total=len(found),
                active_or_view_conflict=False, already_committed=prefix == len(chains),
                ordinary_active_children_of_committed_parents=parent_only_children,
                key_and_parent_columns_distinguished=True)



def partial_boundary(supervisor, module, profile, host):
    module.B.safe_dir(PARTIAL_EVIDENCE)
    role = 'gateway-rollback-compatible'; state = PARTIAL_EVIDENCE / role
    require(set(os.listdir(PARTIAL_EVIDENCE)) == {'STARTED.json', 'REPORT.json', role + '.PASS.json', role}, 'partial_evidence_directory_changed')
    module.B.safe_dir(state)
    require(set(os.listdir(state)) == {'GATEWAY-IMPORT-REQUESTED.json', 'GATEWAY-IMPORT-RESULT.json', 'stdout.private.log', 'stderr.private.log'},
            'partial_capture_directory_changed')
    raw = module.private_bytes(PARTIAL_EVIDENCE / 'REPORT.json', 65536)
    require(len(raw) == PARTIAL_REPORT_BYTES and sha(raw) == PARTIAL_REPORT_SHA, 'partial_report_changed')
    value = module.B.parse(raw)
    require(value['schema'] == 'portal-native-remaining-preload.v1' and value['operation'] == OP
            and value['status'] == 'INCOMPLETE' and value['error_code'] == 'role_active_or_view_snapshot_requires_review'
            and value['evidence'] == str(PARTIAL_EVIDENCE) and value['host_identity'] == host
            and value['gateway_success_report_sha256'] == SUCCESS_REPORT_SHA
            and value['supervisor_sha256'] == SUPERVISOR_SHA and value['prior_failure_receipts'] == supervisor.PINS,
            'partial_report_binding')
    require(value['roles_requested'] == [role] + list(ROLES) and len(value['role_results']) == 2, 'partial_role_scope')
    completed, untouched = value['role_results']
    require(untouched == dict(role='ppt-master-runner-forward', status='INCOMPLETE', import_requested=False,
                              import_process_started=False, import_returncode=None, image_import_may_be_partial=False), 'runner_was_already_requested')
    rows = {row['role']: row for row in profile['images']}
    require(completed['role'] == role and completed['status'] == 'COMPLETE' and completed['import_requested'] is True
            and completed['import_process_started'] is True and completed['import_returncode'] == 0
            and completed['image_import_may_be_partial'] is False and completed['evidence'] == str(state)
            and completed['completion'] == expected_completion(rows[role]), 'partial_completed_image_binding')
    after = completed['after']
    require(after['already_committed'] is True and after['active_or_view_conflict'] is False
            and after['layers'] == after['committed_prefix'] == after['committed_total'], 'partial_completed_snapshot_binding')
    require(module.private_bytes(PARTIAL_EVIDENCE / (role + '.PASS.json'), 65536) == module.B.encoded(completed), 'partial_pass_receipt_changed')
    receipt = completed['import_receipt']
    require(receipt['role'] == role and receipt['returncode'] == 0 and receipt['process_started'] is True
            and receipt['capture_complete'] is True and receipt['output_limit_exceeded'] is False
            and receipt['automatic_retry'] is False and 'error_code' not in receipt, 'partial_capture_binding')
    require(module.private_bytes(state / 'GATEWAY-IMPORT-RESULT.json', 65536) == module.B.encoded(receipt), 'partial_capture_result_changed')
    require(module.private_bytes(state / 'GATEWAY-IMPORT-REQUESTED.json', 65536) == module.B.encoded(dict(role=role, phase='PHASE-B', automatic_retry=False)),
            'partial_capture_request_changed')
    require(module.private_bytes(PARTIAL_ONCE, 65536) == module.B.encoded(dict(operation=OP, evidence=str(PARTIAL_EVIDENCE), automatic_retry=False)),
            'partial_once_changed')
    started = module.B.parse(module.private_bytes(PARTIAL_EVIDENCE / 'STARTED.json', 65536))
    require(started['schema'] == value['schema'] and started['operation'] == OP and started['evidence'] == str(PARTIAL_EVIDENCE)
            and started['host_identity'] == host and started['prior_failure_receipts'] == supervisor.PINS
            and started['roles_requested'] == [role] + list(ROLES) and started['role_results'] == []
            and started['status'] == 'INCOMPLETE' and started['target_import_requested'] is False, 'partial_started_binding')
    require(set(receipt['logs']) == {'stdout.private.log', 'stderr.private.log'}, 'partial_log_set')
    for name, record in receipt['logs'].items():
        require(record['path'] == str(state / name) and record['root_private'] is True and record['truncated'] is False
                and record['saved_bytes'] == record['observed_bytes'], 'partial_log_binding')
        log = module.private_bytes(state / name, supervisor.LIMIT)
        require(len(log) == record['saved_bytes'] and sha(log) == record['sha256'], 'partial_log_changed')
    return dict(report_sha256=PARTIAL_REPORT_SHA, report_bytes=len(raw), evidence=str(PARTIAL_EVIDENCE),
                completed_role=role, import_returncode=0, runner_import_not_requested=True,
                linked_pass_request_result_and_logs_verified=True)



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


def continue_three(supervisor, module, profile, plan, sealed_fd, metadata, result):
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
    module.write_receipt('REMAINING-THREE-PASS.json', dict(all_six_complete=True, imported_roles=[item['role'] for item in result['role_results'] if item['import_requested']]))
    result.update(status='COMPLETE_PRELOAD_REVIEW_REQUIRED', image_import_may_be_partial=False, phase_b='PASS')


def safe_error(exc, module):
    code = exc.code if module and isinstance(exc, module.B.Failure) else str(exc) if isinstance(exc, ValueError) else 'REMAINING_PRELOAD_FAILED'
    return code if re.fullmatch('[A-Za-z0-9_]{1,120}', code) else 'REMAINING_PRELOAD_FAILED'


def run():
    module = lock = sealed_fd = evidence = None
    metadata = {}
    result = dict(schema='portal-native-remaining-three.v1', operation=OP, status='INCOMPLETE', phase_b='NOT_REQUESTED',
                  gateway_success_report_sha256=SUCCESS_REPORT_SHA, supervisor_sha256=SUPERVISOR_SHA,
                  automatic_retry=False, ready_for_rollout=False, native_runtime_passed=False,
                  target_import_requested=None, image_import_may_be_partial=None, roles_requested=list(ROLES),
                  role_results=[], previous_read_only_verification=[], final_six_images=[],
                  frontend_reimported=False, gateway_forward_reimported=False, gateway_rollback_reimported=False, phase_a_repeated=False,
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
        result['remaining_four_boundary'] = partial_boundary(supervisor, module, profile, host)
        plan, receipts = guard.inspect_checkpoint(module, profile)
        require(not os.path.lexists(ONCE), 'remaining_preload_already_started')
        result.update(target_import_requested=False, image_import_may_be_partial=False)
        sealed_fd = guard.open_sealed(module)
        images = {value['role']: value for value in plan['images']}
        rows = {row['role']: row for row in profile['images']}
        require(set(rows) == set(PREVIOUS + ROLES) and set(images) == set(rows), 'image_role_set_changed')
        for role in ROLES:
            metadata[role] = guard.metadata_fd(module, sealed_fd, rows[role], images[role], receipts[role + '.SEALED.json'], plan['allocation']['maximum_metadata_import_bytes'])
        evidence = module.B.fresh(ROOT / ('portal-ppt-remaining-three-' + uuid.uuid4().hex))
        result['evidence'] = str(evidence)
        module.B.write_new(ONCE, module.B.encoded(dict(operation=OP, evidence=str(evidence), automatic_retry=False)))
        module.STATE = evidence; module.REPORT = module.base_report(OP)
        module.write_receipt('STARTED.json', result)
        result['phase_b'] = 'REQUESTED'
        continue_three(supervisor, module, profile, plan, sealed_fd, metadata, result)
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
    parser.add_argument('--resume-three-once', action='store_true', required=True); parser.parse_args()
    raise SystemExit(run())
