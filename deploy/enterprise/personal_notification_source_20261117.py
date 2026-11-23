"""Qualify the accepted 420 sender and retain finite immutable 419 parents."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath

_procurement_spec = importlib.util.spec_from_file_location('procurement_review_source_20261118', Path(__file__).with_name('procurement_review_source_20261118.py'))
procurement = importlib.util.module_from_spec(_procurement_spec)
_procurement_spec.loader.exec_module(procurement)

ARCHIVE = 'release/personal-notification-source-20261117/'
MANIFEST = ARCHIVE + 'SOURCE.json'
MANIFEST_SHA256 = 'fbcd87ec17018ce8966f38ea376c34b60c6af1313bdabee210afbbfa3cda831d'
RUNTIME = 'backend/app/scheduler/work_notification_delivery.py'
RELEASE = 'release/energy-personal-notification-ready-20261116-r1/'
PARENT = 'a2148ed200b211a4e57832990390d9d4bc48b406'
OPERATION = '55f53add31c741b5b859ba0dba3c61ec'
SPEC_SHA = 'd4b61d0bc09cda8550f3d1564a899cb8d44e17033ae9bf58afb1147268c238a9'
DEPLOYED_SHA = '7eb938e2a77dcbb708e8177237be282687f2f202ff41caee015194e6560a1b5c'


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check(read, path, row):
    pure = PurePosixPath(path)
    require(not pure.is_absolute() and pure.as_posix() == path and '..' not in pure.parts
            and '\\' not in path and ':' not in path, 'PN420_UNSAFE_PATH')
    try:
        raw = read(path)
    except (KeyError, FileNotFoundError) as exc:
        raise ValueError('PN420_FILE_MISSING: ' + path) from exc
    require(len(raw) == row['bytes'] and sha(raw) == row['sha256'], 'PN420_FILE_CHANGED: ' + path)
    return raw


def normalized_ast(raw):
    tree = ast.parse(raw)
    imports, other = [], []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            node.names.sort(key=lambda n: (n.name, n.asname or ''))
            imports.append(node)
        else:
            other.append(node)
    tree.body = sorted(imports, key=ast.dump) + other
    return ast.dump(tree, include_attributes=False)


def qualify(read, full=False):
    read, _ = procurement.historical_view(read)
    try:
        raw = read(MANIFEST)
    except (KeyError, FileNotFoundError):
        try:
            read(RELEASE + 'SOURCE.json')
        except (KeyError, FileNotFoundError):
            try:
                current = read(RUNTIME)
            except (KeyError, FileNotFoundError):
                return None
            require(b'async def _ready_daily_message(' not in current, 'PN420_MANIFEST_REQUIRED')
            return None
        raise ValueError('PN420_MANIFEST_REQUIRED')
    require(sha(raw) == MANIFEST_SHA256, 'PN420_MANIFEST_CHANGED')
    seal = json.loads(raw)
    require(seal['schema'] == 'personal-notification-source.v1' and seal['parent_commit'] == PARENT
            and seal['gateway_generation'] == 420 and seal['gateway_spec_sha256'] == SPEC_SHA
            and seal['operation'] == OPERATION and seal['personal_work_business_accepted'] is True
            and seal['scheduler_concurrency'] == 32 and seal['scheduler_budget'] == 10000
            and seal['production_actions_by_pr'] == 0 and seal['credentials_included'] is False,
            'PN420_SCOPE_CHANGED')
    names = set(seal['core_files']) | {RUNTIME}
    if full:
        names.update(seal['files'])
    for path in sorted(names):
        check(read, path, seal['files'][path])
    for path, row in seal['parent_files'].items():
        require(row['snapshot'] == ARCHIVE + 'parent/' + path, 'PN420_PARENT_PATH')
        check(read, row['snapshot'], row)
    deployed = check(read, ARCHIVE + 'deployed-overlay/' + RUNTIME,
                     {'bytes': 8848, 'sha256': DEPLOYED_SHA})
    current = check(read, RUNTIME, seal['files'][RUNTIME])
    require(normalized_ast(current) == normalized_ast(deployed), 'PN420_DEPLOYED_SEMANTICS_CHANGED')
    verify_receipts(read)
    return seal


def verify_receipts(read):
    activation_raw = read(ARCHIVE + 'OPERATOR-ACTIVATION-REPORT.json')
    require(len(activation_raw) == 3517 and sha(activation_raw) == '3f213d29113448de68fde26944a5e4fb7ff0e9ac9ed7682e8d2f8236460c2e0b',
            'PN420_ACTIVATION_RECEIPT_CHANGED')
    activation = json.loads(activation_raw)
    runtime = json.loads(read(ARCHIVE + 'OPERATOR-RUNTIME-ACCEPTANCE-20261117.json'))
    business = json.loads(read(ARCHIVE + 'OPERATOR-BUSINESS-ACCEPTANCE-20261117.json'))
    require(activation['operation'] == runtime['operation'] == business['operation'] == OPERATION
            and activation['candidate_source_sha256'] == DEPLOYED_SHA
            and activation['candidate_spec_sha256'] == runtime['after']['spec_sha256'] == SPEC_SHA
            and runtime['accepted_original_patch_journal']['plan_sha256'] == activation['plan_sha256']
            and runtime['accepted_original_patch_journal']['last_journal_sha256'] == sha(activation_raw),
            'PN420_RECEIPT_BINDING')
    require(runtime['status'] == 'PERSONAL_NOTIFICATION_READER_LINK_DEPLOYED_RUNTIME_ACCEPTED'
            and runtime['all_gateway_pods_verified'] is True and runtime['service_backends_verified'] is True
            and runtime['verified_pods'] == 70 and runtime['verified_source_files_per_pod'] == 17
            and runtime['loaded_application_modules_verified'] is False
            and runtime['readonly'] is True and runtime['patch_request_count'] == 0
            and runtime['work_messages_sent'] == 0, 'PN420_RUNTIME_ACCEPTANCE_CHANGED')
    counts = runtime['current']
    require(counts['generation'] == counts['observedGeneration'] == 420
            and all(counts[key] == 70 for key in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and counts['terminatingReplicas'] == 0, 'PN420_FLEET_NOT_CONVERGED')
    require(business['status'] == 'PERSONAL_WORK_HTML_LINK_ONLY_OPERATOR_ACCEPTED'
            and business['accepted_on'] == '2026-11-17' and business['gateway_generation'] == 420
            and business['gateway_spec_sha256'] == SPEC_SHA
            and business['business_run_id_supplied'] is False,
            'PN420_BUSINESS_ACCEPTANCE_CHANGED')
    require(activation['scheduler_max_concurrent_runs_before'] == activation['scheduler_max_concurrent_runs_after'] == 32
            and activation['future_scheduled_runs_budget'] == activation['global_max_recursion_limit'] == 10000
            and activation['resources_modified'] is False and activation['skill_modified'] is False
            and activation['group_webhook_modified'] is False, 'PN420_SCOPE_EXCEEDED')


def historical_view(read):
    read, _ = procurement.historical_view(read)
    seal = qualify(read)
    if seal is None:
        return read, None

    def previous(path):
        if path == MANIFEST or path in seal['added_files']:
            raise KeyError(path)
        if path in seal['parent_files']:
            # A scoped Git archive can omit unrelated support files. When it
            # carries the current path, it must match this exact accepted seal.
            try:
                read(path)
            except (KeyError, FileNotFoundError):
                pass
            else:
                check(read, path, seal['files'][path])
            return read(seal['parent_files'][path]['snapshot'])
        return read(path)

    return previous, seal
