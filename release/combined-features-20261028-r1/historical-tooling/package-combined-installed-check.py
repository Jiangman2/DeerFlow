"""Seal installed-image regression qualification, without production data or credentials."""
import argparse
import ast
import base64
import hashlib
import json
from pathlib import Path
import zlib

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RELEASE = ROOT/'release/combined-features-20261028-r1'
TARGETS = ['test_employee_square_analytics.py', 'test_competition_usage.py',
    'test_competition_display.py', 'test_datalake_task_boundary.py', 'test_local_data_processing.py',
    'test_appforge_tools.py', 'test_account_token_summary.py', 'test_cnooc_deepseek_v41.py', 'test_input_sanitization_middleware.py']


def replace(source, old, new):
    assert source.count(old) == 1, 'WRAPPER_SHAPE_CHANGED'
    return source.replace(old, new, 1)


def build(output):
    source_seal = json.loads((RELEASE/'SOURCE.json').read_bytes())
    build = json.loads((RELEASE/'GATEWAY-BUILD.json').read_bytes())
    field = json.loads((RELEASE/'GATEWAY-PRELOADS-FIELD.json').read_bytes())
    baseline = json.loads((RELEASE/'FIELD-BASELINE.json').read_bytes())['observation']
    assert source_seal['work_update_included'] is False and len(field['observations']) == 5
    role = build['profile']['roles'][0]
    assert {r['hostname'] for r in field['observations']} == set(build['profile']['nodes'])
    for receipt in field['observations']:
        assert receipt['expected_node_uid'] == build['profile']['nodes'][receipt['hostname']]['expected_node_uid']
        assert receipt['node_preload_passed'] and receipt['old_references_preserved'] and not receipt['image_import_may_be_partial']
        assert receipt['roles'][0]['manifest_digest'] == role['manifest_digest']
        assert receipt['roles'][0]['config_digest'] == role['config_digest']
        assert all(receipt['roles'][0][k] == 'PASS' for k in ('phase_a', 'phase_b', 'content_and_unpack', 'cri_visibility'))
    pins = {r['path'].removeprefix('backend/'):r['sha256'] for r in source_seal['source_inventories']['gateway']}
    files = {'backend/tests/'+name: (ROOT/'backend/tests'/name).read_text(encoding='utf-8')
             for name in TARGETS + ['conftest.py', 'test_console_router.py', '_router_auth_helpers.py']}
    files['deploy/enterprise/deepseek-v4.1-flash-1017.model.yaml'] = (HERE/'deepseek-v4.1-flash-1017.model.yaml').read_text(encoding='utf-8')
    bundle = json.dumps(files, ensure_ascii=False, sort_keys=True).encode()
    child = (HERE/'verify-combined-installed-child.py').read_text(encoding='utf-8')
    child = replace(child, 'TEST_BUNDLE = None', 'TEST_BUNDLE = '+repr(base64.b64encode(zlib.compress(bundle, 9)).decode()))
    child = replace(child, 'TEST_SHA256 = None', 'TEST_SHA256 = '+repr(hashlib.sha256(bundle).hexdigest()))
    child = replace(child, 'TARGETS = None', 'TARGETS = '+repr(TARGETS))
    expected = {n:{k:d[k] for k in ('uid','generation','spec_sha256')} for n,d in baseline['deployments'].items()}
    cases = {}
    for node in ('ecs-zdz-pro-0018', 'ecs-zdz-pro-0021'):
        cases[node] = dict(node=node, node_uid=build['profile']['nodes'][node]['expected_node_uid'],
            candidate_image='deerflow.local/deer-flow-gateway@'+role['manifest_digest'], config_digest=role['config_digest'], sources=pins)
    source = (HERE/'verify-oa-compat-images.py').read_text(encoding='utf-8')
    source = replace(source, 'EXPECTED = {}', 'EXPECTED = '+repr(expected))
    source = replace(source, "CHILD = ''", 'CHILD = '+repr(child)+'\nCASES = '+repr(cases))
    source = replace(source, "for deployment, pin in EXPECTED.items():\n            name =", "for deployment, pin in CASES.items():\n            name =")
    source = source.replace('portal-oa-compat-image-check-20261022-r1', 'portal-combined-installed-check-20261028-r1')
    source = source.replace('portal-oa-compat-check-', 'portal-combined-check-').replace('portal-oa-image-check', 'portal-combined-check')
    source = source.replace('portal-oa-compat-installed-images.v1', 'portal-combined-installed-images.v1')
    source = source.replace('COMPAT_IMAGES_ACCEPTED_NOT_DEPLOYED', 'COMBINED_IMAGES_ACCEPTED_NOT_DEPLOYED')
    source = source.replace('INSTALLED_COMPAT_IMAGE_CHECK', 'INSTALLED_COMBINED_IMAGE_CHECK')
    source = source.replace("'activeDeadlineSeconds': 180", "'activeDeadlineSeconds': 360").replace('time.monotonic() + 240', 'time.monotonic() + 420')
    source = source.replace("'memory': '512Mi'", "'memory': '1Gi'")
    source = source.replace("'sizeLimit': '64Mi'", "'sizeLimit': '128Mi'")
    source = replace(source, "and not status.get('terminatingReplicas') and not status.get('unavailableReplicas')", "and not spec.get('paused') and not status.get('terminatingReplicas') and not status.get('unavailableReplicas')")
    source = replace(source, "and not node.get('spec', {}).get('unschedulable')", "and conditions.get('MemoryPressure') == 'False' and not node.get('spec', {}).get('unschedulable')")
    source = replace(source, "all(t.get('effect') in ('NoSchedule', 'PreferNoSchedule') for t in taints)", "all(t.get('effect') == 'NoSchedule' and t.get('key') == 'dedicated' and t.get('value') == 'gateway' for t in taints)")
    source = replace(source, "and row['pods'][0]['containers'][0]['imageID'], 'MISSING_RUNTIME_IMAGE_RECEIPT')", "and row['pods'][0]['containers'][0]['imageID'] and row['pods'][0]['phase'] == 'Succeeded'\n                    and row['pods'][0]['containers'][0]['exit_code'] == 0, 'MISSING_RUNTIME_IMAGE_RECEIPT')")
    source = replace(source, "outcome.get('status') == 'INSTALLED_COMPAT_IMAGE_ACCEPTED'\n                    and len(outcome.get('checks', [])) == 5 and outcome.get('production_database_access') is False", "outcome.get('status') == 'INSTALLED_COMBINED_FEATURES_ACCEPTED'\n                    and outcome.get('installed_source_files') == 588 and outcome.get('tests', {}).get('passed', 0) >= 120\n                    and outcome.get('production_database_access') is False and outcome.get('provider_requests') == 0\n                    and outcome.get('production_configuration_used') is False and outcome.get('temporary_files_cleaned') is True")
    source = replace(source, "('status', 'error_class', 'production_database_access')", "('status', 'phase', 'error_class', 'error_code', 'tests', 'production_database_access')")
    # Reuse the bounded pipe-draining transport; no raw stderr is exported.
    transport = (HERE/'inspect-combined-features-production.py').read_text(encoding='utf-8')
    reader = next(n for n in ast.parse(transport).body if isinstance(n, ast.ClassDef) and n.name == 'Reader')
    reader_text = '\n'.join(transport.splitlines()[reader.lineno-1:reader.end_lineno]).replace('remaining = 300 -', 'remaining = 1000 -').replace('self.commands < 60', 'self.commands < 220')
    source = replace(source, 'import time\n', 'import time\nimport threading\n')
    old = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'command')
    lines = source.splitlines(True)
    lines[old.lineno-1:old.end_lineno] = [reader_text+'\nREADER = Reader()\n\ndef command(args, value=None, raw=False):\n    data = json.dumps(value).encode() if value is not None else None\n    result = READER.read(BASE + args, data, cap=2097152)\n    return result if raw else json.loads(result)\n']
    source = ''.join(lines)
    assert len(child.encode()) < 120000 and len(json.dumps(pins).encode()) < 120000
    ast.parse(source, feature_version=(3,7))
    output.mkdir(exist_ok=False)
    (output/'verify-portal-combined-installed-images-20261028-r1.py').write_text(source, encoding='utf-8', newline='\n')
    (output/'child.py').write_text(child, encoding='utf-8', newline='\n')
    (output/'sources.json').write_text(json.dumps(pins, sort_keys=True), encoding='utf-8')
    record = dict(schema='portal-combined-installed-package.v1', script_sha256=hashlib.sha256(source.encode()).hexdigest(),
        script_bytes=len(source.encode()), child_sha256=hashlib.sha256(child.encode()).hexdigest(),
        test_bundle_sha256=hashlib.sha256(bundle).hexdigest(), candidate_manifest=role['manifest_digest'],
        production_deployed=False, source_file_count=len(pins), nodes=list(cases), targets=TARGETS)
    (output/'PACKAGE.json').write_text(json.dumps(record, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(record))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
