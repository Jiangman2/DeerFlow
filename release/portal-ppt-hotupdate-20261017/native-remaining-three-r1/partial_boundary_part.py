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

