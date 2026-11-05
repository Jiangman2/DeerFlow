"""Package the full OA switch against accepted receipts and explicit authorization."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    spec = importlib.util.spec_from_file_location('preflight_package', HERE / 'package-oa-v2-integration-preflight.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    common = module.build()
    field = json.loads((HERE / 'oa-v2-integration-preflight-field-20261023-r1.json').read_text(encoding='utf-8'))
    assert field['reported_exitcode'] == 0 and field['reported_status'] == 'OA_PREFLIGHT_OBSERVED_NOT_BUSINESS_ACCEPTED'
    assert field['selected_mode'] == 'FULL_API_AND_CALLBACK_USER_REQUESTED'
    observations = {}
    for role, filename, accepted_field in (
            ('gateway', 'oa-feature-activation-field-20261023-r1.json', 'runtime_accepted'),
            ('frontend', 'data-qa-token-frontend-activation-field-20261023-r1.json', 'stage_passed')):
        value = json.loads((HERE / filename).read_text(encoding='utf-8'))['observation']
        assert value[accepted_field] is True
        observations[role] = {'path': value['report_path'], 'bytes': value['report_bytes'],
                              'sha256': value['report_sha256'], 'status': value['status'], 'accepted_field': accepted_field}
    front = json.loads((HERE / 'data-qa-token-frontend-activation-field-20261023-r1.json').read_text(encoding='utf-8'))['observation']
    assert field['deployments'] == {role: front['after'][role] for role in field['deployments']}
    source = (HERE / 'enable-oa-v2-integration.py').read_text(encoding='utf-8')
    for marker, value in (("COMMON = ''", common), ('BASELINE_CONFIG = {}', field['common_configuration']),
                          ('RECEIPTS = {}', observations)):
        assert source.count(marker) == 1
        source = source.replace(marker, marker.split(' = ')[0] + ' = ' + repr(value), 1)
    ast.parse(source, feature_version=(3, 7))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = build().encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'production_modified': False}))
