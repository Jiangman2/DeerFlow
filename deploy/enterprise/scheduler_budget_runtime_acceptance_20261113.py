"""Offline interpretation of the operator's readonly Gateway 416 receipt.

This never contacts Kubernetes, loads the application, or replays a scheduled run.
The source-seal's earlier candidate state remains immutable.
"""
import hashlib
import json

ARCHIVE = 'release/scheduler-budget-20261113-r5/'
RECEIPT = ARCHIVE + 'OPERATOR-READONLY-ACCEPTANCE-20261113.json'
RECEIPT_SHA256 = 'e5ba3dd0857d2b7a13f8ef4027b36cd91a1e19ae78c9d9f82ac8756d9fbcf289'
PROVENANCE = ARCHIVE + 'ACCEPTANCE-PROVENANCE-20261113.json'
PROVENANCE_SHA256 = '845257c3e0cf4a38c785a5059c19134ae0affea48157c31f0ac7edb7f528dc82'
OPERATOR = ARCHIVE + 'activate-scheduler-budget-20261113-r5.py'
OPERATOR_SHA256 = '2645f26db25d81e1809e2d6249cad6f9ac7b6f71987f0fdf80df49b82a4a5705'
UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
SPEC = '3924fcb409ba6982b1d0acb909a276529c86ccd9d814f85af22a838479113df6'
PLAN = 'd5432e73ccb749580ce698e4d98b0e812f041f9bfdfc295eba76da0373caeaad'


def require(ok, code):
    if not ok:
        raise ValueError(code)


def verify(read):
    raw = read(RECEIPT)
    require(hashlib.sha256(raw).hexdigest() == RECEIPT_SHA256, 'SCHEDULER_RECEIPT_CHANGED')
    require(hashlib.sha256(read(OPERATOR)).hexdigest() == OPERATOR_SHA256, 'SCHEDULER_OPERATOR_CHANGED')
    provenance_raw = read(PROVENANCE)
    require(hashlib.sha256(provenance_raw).hexdigest() == PROVENANCE_SHA256, 'SCHEDULER_PROVENANCE_CHANGED')
    data, provenance = json.loads(raw), json.loads(provenance_raw)
    require(provenance['schema'] == 'scheduler-budget-operator-evidence-provenance.v1'
            and provenance['evidence_kind'] == 'operator_pasted_readonly_stdout_json'
            and provenance['server_control_directory_downloaded'] is False
            and provenance['server_report_reconstructed_claimed'] is False
            and provenance['local_production_access'] is False,
            'SCHEDULER_PROVENANCE_SCOPE')
    require(data['schema'] == 'scheduler-budget-activation.v1' and data['tool_revision'] == 'r5'
            and data['readonly'] is True
            and data['status'] == 'SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED'
            and data['future_scheduled_runs_budget'] == data['global_max_recursion_limit'] == 10000,
            'SCHEDULER_RUNTIME_SCOPE')
    current = data['current']
    require(current['uid'] == UID and current['spec_sha256'] == SPEC
            and current['generation'] == current['observedGeneration'] == 416
            and all(current[key] == 70 for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and current['terminatingReplicas'] == 0
            and data['after'] == {'uid': UID, 'generation': 416, 'spec_sha256': SPEC}
            and data['verified_pods'] == 70 and data['verified_source_files_per_pod'] == 13
            and data['all_gateway_pods_verified'] is True and data['service_backends_verified'] is True,
            'SCHEDULER_FLEET_NOT_ACCEPTED')
    original = data['accepted_original_patch_journal']
    require(data['plan_sha256'] == original['plan_sha256'] == PLAN
            and data['operation'] == original['operation'] == 'b904a96416294639ae3acb1245728084'
            and original['patch_request_recorded'] is True and original['journal_count'] == 6,
            'SCHEDULER_ORIGINAL_JOURNAL_BINDING')
    require(all(data[key] is False for key in (
        'persistent_files_modified', 'original_receipts_modified', 'other_deployments_modified',
        'existing_run_budgets_modified', 'existing_runs_replayed', 'credentials_exported',
        'ca_secret_contents_read', 'loaded_application_modules_verified'))
        and all(data[key] == 0 for key in (
            'patch_request_count', 'configmap_create_requests', 'explicit_database_queries',
            'secret_api_reads', 'provider_requests', 'work_messages_sent'))
        and data['business_acceptance_pending'] is True
        and data['all_existing_gateway_spec_fields_except_configmap_name_preserved'] is True
        and data['ca_secret_references_preserved'] is True
        and data['original_configmaps_unchanged'] is True,
        'SCHEDULER_READONLY_OR_ACCEPTANCE_OVERCLAIM')
    return {'scheduler_budget_deployed': True, 'gateway_generation': 416, 'verified_pods': 70,
            'verified_source_files_per_pod': 13, 'service_backends_verified': True,
            'future_scheduled_runs_budget': 10000, 'global_max_recursion_limit': 10000,
            'observed_at': data['observed_at'], 'evidence_basis': provenance['evidence_kind'],
            'new_run_business_acceptance_pending': True, 'loaded_application_modules_verified': False,
            'production_access': False}
