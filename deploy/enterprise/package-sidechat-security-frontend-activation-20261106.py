"""Package Frontend 241 -> 242 only after actual baseline and cache evidence.

This generator makes no production requests. Original datax-02 preload failure
is retained; its independent successful read-only recheck is a different receipt.
Missing evidence is not replaced by a synthesized successful production result.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import types

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / 'release/dashboard-navigation-20261103-r2/activation-20261104-r1/activate-portal-frontend-dashboard-navigation-20261104-r1.py'
TEMPLATE_SHA = 'c7837771aa6259f39642777d733a14befcb7eebabf49fa03163b97ed6a23d11b'
FRONT_EXPECTED = dict(uid='bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', generation=241,
    spec_sha256='f48dcd7f2a54bc8867fcfa77f84682c22d3e6993fd0091779d1e639f8fee313a')
OLD = 'deerflow.local/deer-flow-frontend@sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d'
TAG = 'deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1'
MANIFEST = 'sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3'
CONFIG = 'sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821'
NAME = 'activate-portal-frontend-sidechat-security-20261106-r1.py'
PRELOAD_SCHEMA = 'portal-sidechat-security-preload-field.v1'
BASELINE_STATUS = 'FRONTEND_SIDECHAT_BASELINE_OBSERVED_NO_CHANGE'
NODES = {'datax-02': '3148d4c1-6a97-42ed-a78c-950dcea5cbec',
         'ecs-zdz-pro-0015': 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'}
ROLES = ['frontend', 'gateway', 'ppt-master-runner', 'provisioner', 'nginx', 'nginx-security-heai-20261012-r1']
BROWSER_PIN = {'bytes': 679, 'sha256': '673e2c4d0a5ee4fbe96b81ea7e1ed8eda6faeb217feb5406c0d12b2b0d04f0f3'}
REQUIRED_MARKERS = {'new_chat_default_model', 'history_label', 'history_english', 'section_runs',
    'section_users', 'section_knowledge', 'section_models', 'section_oa', 'failure_range',
    'responsive_navigation', 'custom_range', 'date_bounds', 'failure_preview_bound',
    'personal_heatmap', 'weekly_heatmap', 'account_activity', 'model_api', 'oa_monitor',
    'web_opt_in', 'multiline_draft', 'single_material_row', 'sidecar_panel_opaque',
    'sidecar_draft_full_width', 'sidecar_draft_left_scroll', 'sidecar_composer_opaque',
    'sidecar_footer_opaque', 'sidecar_attachment_button'}
PRELOAD_PINS = {
    'ecs-zdz-pro-0015': {'bytes': 118975, 'sha256': '4442dbd901bbb96e19dd22c8011aac8407ab318dcf9fc4f1168af765fb5eb51e',
        'operation': 'f7ea0c04a2ce4d03a1de272c7578518f'},
    'datax-02': {'bytes': 115986, 'sha256': 'b23b2d1f07eb5b2d5c00d72b8393e5b9503c1a51b90107e1d7179c1097139120',
        'operation': '281ac71347fa44a59ef78d56c690c8f3'},
}
RECHECK_PIN = {'bytes': 21915, 'sha256': 'd3d50cb5cea8f7c233c166cd131e4c4f06ea9c744f6bd08f0c45cc694ca6cb99',
    'operation': '41d307e6ef074c2abadb1dd4f819f70f'}
RECHECK_PATH = '/var/lib/portal-frontend-sidechat-security-recheck-20261106-r1-' + RECHECK_PIN['operation'] + '/REPORT.json'
PRELOAD_PATHS = {host: prefix + '/portal-frontend-sidechat-security-preload-20261105-r1/REPORT.json'
    for host, prefix in [('datax-02', '/var/lib'), ('ecs-zdz-pro-0015', '/mnt/data')]}


def pin(raw):
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def require(ok, code):
    if not ok:
        raise ValueError(code)


def parse(raw):
    def unique(rows):
        result = {}
        for key, value in rows:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
    require(type(value) is dict, 'json_object_required')
    return value


def read_evidence(path, limit=24 * 1024 * 1024):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit,
        'evidence_file_size_or_type:' + path.name)
    raw = path.read_bytes()
    require(0 < len(raw) <= limit, 'evidence_size_changed:' + path.name)
    return raw


def replace(source, name, replacement, function=False):
    tree = ast.parse(source)
    matches = [n for n in tree.body if
        (function and isinstance(n, ast.FunctionDef) and n.name == name) or
        (not function and isinstance(n, ast.Assign) and
         any(isinstance(t, ast.Name) and t.id == name for t in n.targets))]
    require(len(matches) == 1, 'replacement_count:' + name)
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement + '\n' + ''.join(lines[node.end_lineno:])


def baseline_binding(record):
    require(record.get('schema') == 'portal-frontend-sidechat-baseline.v1'
        and record.get('status') == BASELINE_STATUS and record.get('stable_readback') is True,
        'accepted_fresh_baseline_required')
    require(record.get('workload_modified') is False and record.get('database_access') is False
        and record.get('provider_requests') == 0, 'readonly_baseline_required')
    deployments = record.get('deployments', {})
    require(type(deployments) is dict and set(deployments) == set(ROLES), 'six_deployment_baselines_required')
    result = {}
    for name in ROLES:
        desired = 2 if name == 'frontend' else 70 if name == 'gateway' else 1
        row = deployments[name]
        require(all(isinstance(row.get(k), str) and row[k] for k in ('uid', 'spec_sha256', 'image'))
            and re.fullmatch('[a-f0-9]{64}', row['spec_sha256'])
            and type(row.get('generation')) is int and row['generation'] > 0,
            'baseline_identity:' + name)
        require(row.get('paused') is False and row.get('observed_generation') == row['generation']
            and all(row.get(k) == desired for k in ('desired', 'total', 'updated', 'ready', 'available'))
            and row.get('terminating') == 0, 'baseline_not_converged:' + name)
        result[name] = {k: row[k] for k in ('uid', 'generation', 'spec_sha256')}
    require(result['frontend'] == FRONT_EXPECTED and deployments['frontend']['image'] == OLD,
        'frontend_241_required')
    require(deployments['gateway']['uid'] == 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
        and re.fullmatch(r'deerflow\.local/deer-flow-gateway@sha256:[a-f0-9]{64}', deployments['gateway']['image']),
        'gateway_identity_required')
    return result, deployments['gateway']['image']


VALIDATE_PRELOADS = '''def validate_preloads(rows, rechecks=None):
    if rechecks is None:
        rechecks = RECHECKS
    require(type(rows) is dict and set(rows) == set(NODES)
        and type(rechecks) is dict and set(rechecks) == {'datax-02'}, 'actual_cache_receipts_required')
    for host, receipt in rows.items():
        expected = PRELOAD_PINS[host]
        require(receipt.get('schema') == 'portal-frontend-ui-cache-preload.v1'
            and receipt.get('hostname') == host and receipt.get('expected_node_uid') == NODES[host]
            and receipt.get('operation') == expected['operation']
            and receipt.get('report_path') == PRELOAD_PATHS[host]
            and receipt.get('report_bytes') == expected['bytes']
            and receipt.get('report_sha256') == expected['sha256'], 'preload_identity_or_status')
        require(receipt.get('manifest_digest') == MANIFEST and receipt.get('config_digest') == CONFIG
            and receipt.get('preclaim_passed') is True and receipt.get('mutation_once_claimed') is True
            and receipt.get('image_import_requested') is True
            and receipt.get('phase_a') == receipt.get('phase_b') == 'PASS'
            and receipt.get('production_workload_mutation') is False
            and set(receipt.get('canonical_names_created', [])) == {NEW, TAG}, 'preload_image_or_preservation')
    success = rows['ecs-zdz-pro-0015']
    require(success.get('status') == 'COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED'
        and success.get('exitcode') == 0 and success.get('error_code') is None
        and success.get('content_and_unpack') == success.get('cri_visibility') == 'PASS'
        and success.get('old_references_preserved') is True
        and success.get('image_import_may_be_partial') is False, '0015_preload_not_accepted')
    failed = rows['datax-02']
    require(failed.get('status') == 'INCOMPLETE' and failed.get('exitcode') == 1
        and failed.get('error_code') == 'command_limit' and failed.get('error_class') == 'Refused'
        and failed.get('failure_phase') == 'IMPORT_AND_UNPACK'
        and failed.get('image_import_may_be_partial') is True
        and all(failed.get(k) is None for k in ('content_and_unpack', 'cri_visibility', 'old_references_preserved')),
        'original_datax02_failure_must_be_preserved')
    receipt = rechecks['datax-02']
    require(receipt.get('schema') == 'portal-frontend-sidechat-cache-recheck.v1'
        and receipt.get('status') == 'FRONTEND_SIDECHAT_CACHE_RECHECK_ACCEPTED_NOT_DEPLOYED'
        and receipt.get('hostname') == 'datax-02' and receipt.get('expected_node_uid') == NODES['datax-02']
        and receipt.get('operation') == RECHECK_PIN['operation']
        and receipt.get('report_path') == RECHECK_PATH and receipt.get('report_bytes') == RECHECK_PIN['bytes']
        and receipt.get('report_sha256') == RECHECK_PIN['sha256']
        and receipt.get('exitcode') == 0 and receipt.get('manifest_digest') == MANIFEST
        and receipt.get('config_digest') == CONFIG, 'datax02_recheck_identity_or_status')
    require(receipt.get('queries') == 80 and receipt.get('content_and_unpack') == receipt.get('cri_visibility') == 'PASS'
        and receipt.get('protected_reference_count') == 31 and receipt.get('snapshot_chains_verified') == 11
        and receipt.get('content_blobs_present') == 13 and receipt.get('old_references_preserved') is True
        and receipt.get('temporary_reference_preserved') is True and receipt.get('stable_readback') is True
        and receipt.get('report_directory_created') is True and receipt.get('last_step') == 'stable_prior_receipt',
        'datax02_cache_not_accepted')
    require(all(receipt.get(k) is False for k in ('automatic_retry', 'image_import_requested',
        'image_retag_requested', 'image_deleted', 'production_workload_mutation', 'configuration_modified',
        'database_access', 'original_receipt_modified', 'ready_for_rollout', 'kubernetes_node_uid_freshly_checked',
        'layer_payloads_rehashed_now', 'storage_capacity_rechecked'))
        and receipt.get('provider_requests') == 0 and receipt.get('original_marker_preserved') is True,
        'datax02_recheck_scope_changed')
    prior = receipt.get('prior_receipt', {})
    expected = PRELOAD_PINS['datax-02']
    require(prior.get('path') == PRELOAD_PATHS['datax-02'] and prior.get('bytes') == expected['bytes']
        and prior.get('sha256') == expected['sha256'] and prior.get('operation') == expected['operation']
        and prior.get('status') == 'INCOMPLETE' and prior.get('error_code') == 'command_limit'
        and prior.get('commands') == 200 and prior.get('verified_stop') == 'final_reference_readback',
        'datax02_failed_receipt_binding_changed')
'''


def build(qualified, field, baseline):
    raw = read_evidence(TEMPLATE)
    require(pin(raw)['sha256'] == TEMPLATE_SHA, 'activation_template_changed')
    source = raw.decode('utf-8')
    baseline_raw = read_evidence(baseline)
    bindings, gateway_image = baseline_binding(parse(baseline_raw))
    package_raw = read_evidence(qualified / 'PACKAGE.json')
    result_raw = read_evidence(qualified / 'RESULT.json')
    source_raw = read_evidence(qualified / 'SOURCE.json')
    assets_raw = read_evidence(qualified / 'COMPILED-ASSETS.json')
    browser_raw = read_evidence(qualified / 'BROWSER-QUALIFICATION.json')
    package, result, assets, browser = map(parse, (package_raw, result_raw, assets_raw, browser_raw))
    require(package.get('schema') == 'portal-sidechat-security-frontend-package.v1'
        and package.get('tag') == TAG and package.get('manifest') == MANIFEST and package.get('config') == CONFIG
        and result.get('image', {}).get('id') == MANIFEST and result['image'].get('tag') == TAG
        and result.get('frontend_slim') == 'LOCAL_PASS' and result.get('platform') == 'linux/arm64'
        and result.get('production_writes') == 0 and result.get('images_pushed') is False
        and package.get('qualification_pin') == pin(result_raw), 'qualified_sidechat_image_required')
    require(package.get('source_inventory_pin') == pin(source_raw), 'source_inventory_changed')
    require(assets.get('schema') == 'portal-sidechat-security-frontend-assets.v1'
        and assets.get('status') == 'COMPILED_MARKERS_PINNED' and assets.get('image') == MANIFEST
        and assets.get('qualification_sha256') == pin(result_raw)['sha256']
        and assets.get('source_inventory_sha256') == pin(source_raw)['sha256']
        and type(assets.get('source_pins')) is dict and 3 <= len(assets['source_pins']) <= 32,
        'compiled_asset_proof_required')
    for path, value in assets['source_pins'].items():
        require(isinstance(path, str) and (re.fullmatch(r'/app/frontend/\.next/static/chunks/[A-Za-z0-9_.-]+\.(js|css)', path)
            or path in ('/app/frontend/.next/BUILD_ID', '/app/frontend/.next/routes-manifest.json',
                        '/app/frontend/public/images/cnooc-workspace-concept-bg.png'))
            and type(value) is dict and type(value.get('bytes')) is int and 0 < value['bytes'] <= 2 * 1024 * 1024
            and re.fullmatch('[a-f0-9]{64}', value.get('sha256', '')), 'compiled_asset_pin_invalid')
    markers = assets.get('feature_markers', {})
    require(type(markers) is dict and set(markers) == REQUIRED_MARKERS
        and all(type(paths) is list and paths and all(path in assets['source_pins'] for path in paths)
            for paths in markers.values()), 'required_sidechat_and_preserved_markers_missing')
    require(pin(browser_raw) == BROWSER_PIN and browser.get('schema') == 'portal-sidechat-final-image-browser.v1'
        and browser.get('image') == MANIFEST and browser.get('tests_passed') == 10
        and browser.get('tests_failed') == 0 and browser.get('provider_requests') == 0
        and browser.get('production_acceptance') is False, 'archived_browser_qualification_required')
    field_raw = read_evidence(field)
    record = parse(field_raw)
    require(record.get('schema') == PRELOAD_SCHEMA, 'preload_schema')
    rows, rechecks = record.get('preloads'), record.get('rechecks')
    provenance = {'template': pin(raw), 'package': pin(package_raw), 'result': pin(result_raw),
        'assets': pin(assets_raw), 'browser_summary': pin(browser_raw), 'source': pin(source_raw),
        'operator_cache_field': pin(field_raw), 'fresh_operator_baseline': pin(baseline_raw),
        'baseline_scope': 'fresh_readonly_deployment_identity',
        'preload_evidence_scope': 'operator_supplied_summaries_with_exact_private_report_pins',
        'browser_evidence_scope': 'archived_local_summary_not_structured_browser_report',
        'nginx_configuration_semantics_verified': False, 'skillhub_native_auth_remediation_included': False}
    values = dict(PRELOADS=rows, PRELOAD_PATHS=PRELOAD_PATHS, FRONT=bindings['frontend'], OLD=OLD,
        MANIFEST=MANIFEST, CONFIG=CONFIG, TAG=TAG, NEW='deerflow.local/deer-flow-frontend@' + MANIFEST,
        SOURCE_PINS=assets['source_pins'], PROVENANCE=provenance,
        PRIOR_RECEIPT={'scope': 'operator_observed_deployment_identity',
            'frontend': bindings['frontend'], 'gateway': bindings['gateway']},
        GATEWAY=bindings['gateway'], GATEWAY_IMAGE=gateway_image,
        ONCE='portal-frontend-sidechat-security-activation-20261106-r1.once',
        ACCEPTED='FRONTEND_SIDECHAT_SECURITY_RUNTIME_ACCEPTED_BROWSER_PENDING')
    for key, value in values.items():
        source = replace(source, key, key + ' = ' + repr(value))
    source = replace(source, 'validate_preloads', VALIDATE_PRELOADS, function=True)
    execute_node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'execute')
    execute = ast.unparse(execute_node)
    require(execute.count('r = e.report') == 1, 'execute_report_binding_changed')
    execute = execute.replace('r = e.report', "r = e.report\n    r.update(nginx_configuration_semantics_verified=False, ppt_guard_configuration_semantics_verified=False, skillhub_native_auth_remediation_included=False, preload_evidence_scope='operator_summaries_and_fresh_dual_node_image_execution')", 1)
    anchor = "check_backend_contract(e, before['gateway'])"
    require(execute.count(anchor) == 1, 'execute_baseline_anchor_changed')
    execute = execute.replace(anchor,
        "require({k: binding(v) for k, v in before.items()} == PROTECTED_BASELINE, 'protected_baseline_changed')\n    " + anchor, 1)
    anchor = "'preloads_sha256': pin(canonical(PRELOADS))['sha256']"
    require(execute.count(anchor) == 1, 'claim_receipt_anchor_changed')
    execute = execute.replace(anchor, anchor + ", 'rechecks_sha256': pin(canonical(RECHECKS))['sha256']", 1)
    source = replace(source, 'execute', execute, function=True)
    replacements = {
        "'portal-dashboard-nav-cache-'": "'portal-sidechat-security-cache-'",
        "'portal-dashboard-nav-cache-check'": "'portal-sidechat-security-cache-check'",
        "'portal-frontend-dashboard-navigation-activation.v1'": "'portal-frontend-sidechat-security-activation.v1'",
        "'--activate-frontend-dashboard-navigation-once'": "'--activate-frontend-sidechat-security-once'",
        "'portal-frontend-dashboard-navigation-activation-20261104-r1-'": "'portal-frontend-sidechat-security-activation-20261106-r1-'",
    }
    for old, new in replacements.items():
        require(source.count(old) == 1, 'literal_replacement_count:' + old)
        source = source.replace(old, new)
    constants = dict(RECHECKS=rechecks, PRELOAD_PINS=PRELOAD_PINS, RECHECK_PIN=RECHECK_PIN,
        RECHECK_PATH=RECHECK_PATH, PROTECTED_BASELINE=bindings)
    # Add declarations before the historical main guard, never after its run().
    guard = next(n for n in ast.parse(source).body if isinstance(n, ast.If)
        and isinstance(n.test, ast.Compare) and isinstance(n.test.left, ast.Name) and n.test.left.id == '__name__')
    lines = source.splitlines(keepends=True)
    source = ''.join(lines[:guard.lineno - 1]) + '\n'.join(k + ' = ' + repr(v) for k, v in constants.items()) + '\n\n' + ''.join(lines[guard.lineno - 1:])
    ast.parse(source, feature_version=(3, 7))
    module = types.ModuleType('packaged_sidechat_security_activation')
    exec(compile(source, '<packaged-sidechat-security>', 'exec'), module.__dict__)
    require(module.NODES == NODES and module.ROLES == ROLES, 'node_or_role_binding_changed')
    module.validate_preloads(rows, rechecks)
    module.read_prior(None)
    module.load_modules()
    return source.encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('qualified', 'field', 'baseline', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    content = build(args.qualified, args.field, args.baseline)
    require(args.output.name == NAME, 'fixed_delivery_filename_required')
    with args.output.open('xb') as stream:
        stream.write(content)
    print(json.dumps(dict(file=str(args.output), **pin(content), production_commands_executed=False)))
