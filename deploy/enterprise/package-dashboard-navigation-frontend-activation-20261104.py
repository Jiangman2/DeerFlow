"""Package one Frontend 239 -> 240 image CAS; never execute production commands."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / 'release/dashboard-navigation-20261103-r2'
TEMPLATE = RELEASE / 'activation-template/frontend-239-activation.py'
TEMPLATE_SHA = '72044eb5fdaa904b16663e72a76f940607f1b51f3a9b6ba3d1def5cd1ffd7d33'
FRONT = dict(uid='bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', generation=239,
             spec_sha256='5643948b676d786e7c28e889a21c637a1e2dc47414eaf010c287bcb39b1fa9f5')
GATEWAY = dict(uid='a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', generation=406,
               spec_sha256='6f66293bf974b8943ddc057d240c5fa5be2c6e965206824d53b7518e613fb901')
OLD = 'deerflow.local/deer-flow-frontend@sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1'
GATEWAY_IMAGE = 'deerflow.local/deer-flow-gateway@sha256:f8faded6240cbc6d98c7dbfe30388a7ff4c3f4df243f0d6409b1f6a026c19b38'
MANIFEST = 'sha256:74c2e66ecc1c2e9b483faac428fc9211ff43bc0e0ed59ee141de379c678fdfb5'
NAME = 'activate-portal-frontend-dashboard-navigation-20261104-r1.py'


def pin(raw):
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def replace(source, name, replacement, function=False):
    tree = ast.parse(source)
    if function:
        matches = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    else:
        matches = [n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert len(matches) == 1, ('replacement_count', name)
    node = matches[0]
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + replacement + '\n' + ''.join(lines[node.end_lineno:])


def build(field=RELEASE / 'PRELOAD-FIELD.json'):
    raw = TEMPLATE.read_bytes()
    assert pin(raw)['sha256'] == TEMPLATE_SHA, 'ACTIVATION_TEMPLATE_CHANGED'
    source = raw.decode()
    qualified = RELEASE / 'qualification'
    package = json.loads((qualified / 'PACKAGE.json').read_bytes())
    result_raw = (qualified / 'RESULT.json').read_bytes()
    result = json.loads(result_raw)
    assets = json.loads((qualified / 'COMPILED-ASSETS.json').read_bytes())
    browser = json.loads((qualified / 'LOCAL-VALIDATION.json').read_bytes())
    assert package['manifest'] == result['image']['id'] == assets['image'] == browser['image'] == MANIFEST
    assert result['frontend_slim'] == 'LOCAL_PASS' and package['qualification_pin'] == pin(result_raw)
    assert assets['qualification_sha256'] == pin(result_raw)['sha256']
    assert assets['status'] == 'COMPILED_MARKERS_PINNED' and len(assets['source_pins']) == 10
    assert len(assets['feature_markers']) == 20 and all(assets['feature_markers'].values())
    assert browser['status'] == 'LOCAL_IMAGE_ACCEPTED_NOT_DEPLOYED'
    assert sum(x['passed'] for x in browser['browser']) == 26 and all(x['failed'] == 0 for x in browser['browser'])
    record = json.loads(field.read_bytes())
    assert record['schema'] == 'portal-dashboard-navigation-preload-field.v1'
    rows = record['preloads']
    paths = {host: prefix + '/portal-frontend-dashboard-navigation-preload-20261103-r2/REPORT.json'
             for host, prefix in [('datax-02', '/var/lib'), ('ecs-zdz-pro-0015', '/mnt/data')]}
    # The November 3 observation supplies identity, not a claim of complete Gateway business acceptance.
    provenance = {p.relative_to(ROOT).as_posix(): pin(p.read_bytes()) for p in
                  (TEMPLATE, qualified / 'PACKAGE.json', qualified / 'RESULT.json',
                   qualified / 'COMPILED-ASSETS.json', qualified / 'LOCAL-VALIDATION.json')}
    provenance['operator_preload_field'] = pin(field.read_bytes())
    provenance['baseline_scope'] = 'operator_observed_gateway406_frontend239_deployment_identity'
    values = dict(PRELOADS=rows, PRELOAD_PATHS=paths, FRONT=FRONT, OLD=OLD,
                  MANIFEST=MANIFEST, CONFIG=package['config'], TAG=package['tag'],
                  NEW='deerflow.local/deer-flow-frontend@' + MANIFEST,
                  SOURCE_PINS=assets['source_pins'], PROVENANCE=provenance,
                  PRIOR_RECEIPT={'scope': 'operator_observed_deployment_identity',
                                 'gateway': GATEWAY, 'frontend': FRONT},
                  GATEWAY=GATEWAY, GATEWAY_IMAGE=GATEWAY_IMAGE,
                  ONCE='portal-frontend-dashboard-navigation-activation-20261104-r1.once',
                  ACCEPTED='FRONTEND_DASHBOARD_NAVIGATION_RUNTIME_ACCEPTED_BROWSER_PENDING')
    for key, value in values.items():
        source = replace(source, key, key + ' = ' + repr(value))
    for key in ('CORE_PLAN', 'BASELINE_PINS', 'GATEWAY_NODES', 'GATEWAY_RECEIPT', 'COMPAT_SOURCE', 'COMPAT_SOURCE_PIN'):
        source = replace(source, key, '')
    for key in ('core_plan', 'validate_prior_acceptance', 'read_gateway_private'):
        source = replace(source, key, '', function=True)
    source = replace(source, 'read_prior', '''def read_prior(helper):
    require(PRIOR_RECEIPT.get('scope') == 'operator_observed_deployment_identity'
        and PRIOR_RECEIPT.get('gateway') == GATEWAY and PRIOR_RECEIPT.get('frontend') == FRONT,
        'observed_baseline_binding_changed')
    return PRIOR_RECEIPT''', function=True)
    source = replace(source, 'check_backend_contract', '''def check_backend_contract(e, deployment):
    read_prior(e.H)
    require(not deployment.get('spec', {}).get('paused', False), 'gateway_paused_no_frontend_change')
    require_backend_ready(deployment)
    e.report['gateway_observed_binding'] = GATEWAY
    e.report['gateway_deployment_rechecked'] = True
    e.report['gateway_business_acceptance_claimed'] = False''', function=True)
    replacements = {
        "'portal-usage-heatmap-cache-'": "'portal-dashboard-nav-cache-'",
        "'portal-usage-heatmap-cache-check'": "'portal-dashboard-nav-cache-check'",
        "'portal-frontend-usage-heatmap-activation.v2'": "'portal-frontend-dashboard-navigation-activation.v1'",
        "'tool_revision': 'r2'": "'tool_revision': 'r1'",
        "'--activate-frontend-usage-heatmap-once'": "'--activate-frontend-dashboard-navigation-once'",
        "'portal-frontend-usage-heatmap-activation-20261102-r2-'": "'portal-frontend-dashboard-navigation-activation-20261104-r1-'",
        "and deployment_ready(before['frontend']), 'frontend_baseline_changed')":
            "and not before['frontend']['spec'].get('paused', False)\n            and deployment_ready(before['frontend']), 'frontend_baseline_changed')",
    }
    for old, new in replacements.items():
        assert source.count(old) == 1, ('literal_replacement_count', old)
        source = source.replace(old, new)
    ast.parse(source, feature_version=(3, 7))
    module = types.ModuleType('packaged_navigation_activation')
    exec(compile(source, '<packaged-navigation>', 'exec'), module.__dict__)
    module.validate_preloads(rows)
    module.read_prior(None)
    module.load_modules()
    assert not any(x in source for x in ('gateway_403_acceptance_required', 'read_gateway_private', 'CORE_PLAN'))
    return source.encode()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--field', type=Path, default=RELEASE / 'PRELOAD-FIELD.json')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    content = build(args.field)
    with args.output.open('xb') as stream:
        stream.write(content)
    print(json.dumps(dict(file=str(args.output), **pin(content), production_commands_executed=False)))
