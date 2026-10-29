"""Package only the accepted combined images, current model config and updated key receipt."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RELEASE = ROOT / 'release/combined-features-20261028-r1'


def selected(path, names):
    source = path.read_text(encoding='utf-8')
    parts, found = [], set()
    for n in ast.parse(source).body:
        name = n.name if isinstance(n, (ast.FunctionDef, ast.ClassDef)) else n.targets[0].id if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) else None
        if name in names:
            parts.append(ast.get_source_segment(source, n)); found.add(name)
    assert found == set(names)
    return '\n\n'.join(parts)


def build():
    read = lambda p: json.loads((RELEASE / p).read_bytes())
    source = read('SOURCE.json'); gw = read('GATEWAY-BUILD.json'); fe = read('FRONTEND-BUILD.json')
    baseline = read('FIELD-BASELINE.json')['observation']
    scope = read('RELEASE-SCOPE-UPDATE.json'); field = read('FIELD-VALIDATION.json')
    assert scope['v41_enablement'] == 'DEFERRED_BY_OPERATOR' and scope['active_task_interruption_authorized'] is True
    assert source['work_update_included'] is False and scope['database_migration_requested'] is False
    gateway_preloads = read('GATEWAY-PRELOADS-FIELD.json')['observations']
    frontend_preloads = read('FRONTEND-PRELOADS-FIELD.json')['observations']
    assert len(gateway_preloads) == 5 and len(frontend_preloads) == 2
    target = gw['profile']['roles'][0]
    targets = dict(gateway=dict(image='deerflow.local/deer-flow-gateway@' + target['manifest_digest'],
                    config=target['config_digest'], old_image=source['gateway_base_image'],
                    nodes={n: v['expected_node_uid'] for n, v in gw['profile']['nodes'].items()}),
                   frontend=dict(image='deerflow.local/deer-flow-frontend@' + fe['image']['id'],
                    config='sha256:3d74a9749c57799195ffd104f5b0bd51ef8256e7d6b73a1c47661f905a29d78c',
                    old_image=source['frontend_image'], nodes={r['hostname']: r['expected_node_uid'] for r in frontend_preloads}))
    for role, observations in [('gateway', gateway_preloads), ('frontend', frontend_preloads)]:
        assert {r['hostname'] for r in observations} == set(targets[role]['nodes'])
        for r in observations:
            o = r['roles'][0] if role == 'gateway' else r
            assert r['old_references_preserved'] is True and r['image_import_may_be_partial'] is False
            assert r['expected_node_uid'] == targets[role]['nodes'][r['hostname']]
            assert o['manifest_digest'] == targets[role]['image'].split('@')[1] and o['config_digest'] == targets[role]['config']
            assert all(o[k] == 'PASS' for k in ('phase_a', 'phase_b', 'content_and_unpack', 'cri_visibility'))
    helper = selected(HERE / 'inspect-combined-features-production.py',
        {'BASE', 'Refused', 'require', 'digest', 'binding', 'Reader', 'owned_pods', 'identity'})
    helper = helper.replace('self.commands < 60', 'self.commands < 6000').replace('remaining = 300 -', 'remaining = 10800 -')
    helper = helper.replace('p = subprocess.Popen(args,', "args = [('--request-timeout=120s' if a.startswith('--request-timeout=') else a) for a in args] if len(args) > len(BASE) and args[len(BASE)] == 'exec' else args\n        p = subprocess.Popen(args,")
    source_old = json.loads((HERE/'production-source-lock-20261026.json').read_bytes())['source_sets']['gateway']
    assert len(source_old) == 583
    source_new = {r['path']: r['sha256'] for r in source['source_inventories']['gateway']}
    assert len(source_new) == 588
    assets = read('COMPILED-ASSETS.json')
    assert assets['manifest'] == fe['image']['id'] and len(assets['source_pins']) == 13
    values = dict(BASELINES={n: {k: d[k] for k in ('uid', 'generation', 'spec_sha256')} for n, d in baseline['deployments'].items()},
                  TARGETS=targets, RECEIPTS={'installed': field['installed_images']['report'], 'storage': field['shared_storage']['report']},
                  OLD_SOURCES=source_old, NEW_SOURCES=source_new, FRONTEND_PINS=assets['source_pins'],
                  FRONTEND_HELPER=(HERE/'combined-frontend-activation-checks.py').read_text(encoding='utf-8'),
                  NODE_GUARD=(HERE/'oa-compat-node-gate.py').read_text(encoding='utf-8'),
                  RUNTIME_CHILD=(HERE/'verify-combined-live-child.py').read_text(encoding='utf-8'))
    host = (HERE/'activate-combined-features.py').read_text(encoding='utf-8')
    host = host.replace('HELPERS = None', helper)
    host = host.replace('CONFIG_REFS_HELPER = None', selected(HERE/'activate-oa-compat.py', {'configuration_refs'}))
    for name, value in values.items():
        old = name + ' = None'; assert host.count(old) == 1
        host = host.replace(old, name + ' = ' + repr(value), 1)
    ast.parse(host, feature_version=(3, 7))
    ast.parse(values['FRONTEND_HELPER'], feature_version=(3, 7))
    ast.parse(values['RUNTIME_CHILD'], feature_version=(3, 12))
    return host.encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); raw = build()
    with args.output.open('xb') as f: f.write(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), production_modified=False,
                         v41_enabled=False, model_configuration_changed=False, active_task_interruption_authorized=True)))
