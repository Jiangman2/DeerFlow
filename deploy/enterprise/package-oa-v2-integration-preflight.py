"""Bind the non-mutating OA preflight to the accepted frontend/backend baseline."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def build():
    accepted = json.loads((HERE / 'data-qa-token-frontend-activation-field-20261023-r1.json').read_text(encoding='utf-8'))['observation']
    assert accepted['status'] == 'FRONTEND_DATA_QA_TOKEN_RUNTIME_ACCEPTED_BROWSER_PENDING'
    assert accepted['stage_passed'] and accepted['after']['gateway']['generation'] == 384
    assert accepted['after']['frontend']['generation'] == 226 and accepted['after']['ppt-master-runner']['generation'] == 55
    expected = {k: dict(accepted['after'][k]) for k in ('gateway', 'frontend', 'ppt-master-runner')}
    pins = json.loads((HERE / 'oa-data-qa-installed-image-pins-20261022.json').read_text(encoding='utf-8'))
    expected['gateway']['image'] = pins['candidate_image']
    names = ('oa_policy_flow_config.py', 'oa_policy_flow_results.py', 'oa_policy_flow_service.py', 'routers/oa_policy_flow.py')
    sources = {'/app/backend/app/gateway/' + name: pins['sources']['app/backend/app/gateway/' + name] for name in names}
    source = (HERE / 'inspect-oa-v2-integration.py').read_text(encoding='utf-8')
    for name, value in (('EXPECTED', expected), ('SOURCE_PINS', sources)):
        marker = name + ' = {}'
        assert source.count(marker) == 1
        source = source.replace(marker, name + ' = ' + repr(value), 1)
    ast.parse(source, feature_version=(3, 7))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = build().encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(pin(raw), production_modified=False)))
