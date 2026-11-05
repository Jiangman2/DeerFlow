"""Bind the v2-only switch to the accepted Gateway 385 / Frontend 227 baseline."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def replace(source, name, value, empty='{}'):
    marker = name + ' = ' + empty
    assert source.count(marker) == 1, 'package_marker_changed_' + name
    return source.replace(marker, name + ' = ' + repr(value), 1)


def build():
    accepted = read('nonconfidential-notice-frontend-activation-field-20261023-r3.json')['observation']
    assert accepted['stage_passed'] is True
    assert accepted['status'] == 'FRONTEND_NOTICE_RUNTIME_ACCEPTED_BROWSER_PENDING'
    expected = {role: dict(value) for role, value in accepted['after'].items()}
    assert {role: value['generation'] for role, value in expected.items()} == {
        'gateway': 385, 'frontend': 227, 'ppt-master-runner': 55, 'nginx': 116, 'provisioner': 85}
    pins = read('oa-data-qa-installed-image-pins-20261022.json')
    images = read('production-image-binding-20261023.json')['deployments']
    gateway = next(item for item in images if item['name'] == 'gateway')
    assert gateway['generation'] == expected['gateway']['generation']
    assert gateway['image'] == pins['candidate_image'] and gateway['ready'] == 70
    expected['gateway']['image'] = gateway['image']
    names = ('oa_policy_flow_config.py', 'oa_policy_flow_results.py',
             'oa_policy_flow_service.py', 'routers/oa_policy_flow.py')
    sources = {'/app/backend/app/gateway/' + name:
               pins['sources']['app/backend/app/gateway/' + name] for name in names}
    common = (HERE / 'inspect-oa-v2-integration.py').read_text(encoding='utf-8')
    common = replace(replace(common, 'EXPECTED', expected), 'SOURCE_PINS', sources)
    field = read('oa-v2-integration-preflight-field-20261023-r1.json')['common_configuration']
    assert field['enabled'] is True and field['multi_result_enabled'] is field['callback_enabled'] is False
    receipts = {}
    for role, value, flag in (
            ('gateway', read('oa-feature-activation-field-20261023-r1.json')['observation'], 'runtime_accepted'),
            ('frontend', accepted, 'stage_passed')):
        assert value[flag] is True
        receipts[role] = {'path': value['report_path'], 'bytes': value['report_bytes'],
                          'sha256': value['report_sha256'], 'status': value['status'], 'accepted_field': flag}
    source = (HERE / 'enable-oa-v2-multi-only.py').read_text(encoding='utf-8')
    source = replace(source, 'COMMON', common, "''")
    source = replace(replace(source, 'BASELINE_CONFIG', field), 'RECEIPTS', receipts)
    ast.parse(source, feature_version=(3, 7))
    ast.parse(common, feature_version=(3, 7))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = build().encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                      'production_modified': False, 'callback_enabled': False}))
