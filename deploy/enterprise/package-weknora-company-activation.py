"""Seal a company-knowledge rollout only after the operator supplies bulk completion."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
BULK_FIELD = HERE / 'weknora-company-enrollment-field-20261024-r1.json'


def read(name):
    return json.loads((HERE / name).read_text(encoding='utf-8'))


def extract(path, names):
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names), 'QUALIFIED_HELPER_SHAPE_CHANGED'
    return '\n\n'.join(ast.get_source_segment(source, n) for n in nodes) + '\n'


def render(bulk=None):
    release = read('weknora-company-preload-build-20261024-r1.json')
    profile = release['profile']
    role = profile['roles'][0]
    expected = read('chat-notice-layout-frontend-activation-field-20261024-r1.json')['observation']['after']
    assert expected['gateway']['generation'] == 386 and expected['frontend']['generation'] == 229
    expected['gateway'].update(nodes={n: v['expected_node_uid'] for n, v in profile['nodes'].items()},
        candidate_image='deerflow.local/deer-flow-gateway@' + role['manifest_digest'], config_digest=role['config_digest'])
    expected['weknora-app'] = {'uid': '44aca5b9-8fc1-44d6-af61-37c6d62723d5', 'generation': 82,
        'spec_sha256': 'e7f481fc2b552098b8a8249beb2eb064212218cc48d5bbade2807446dc7ee83c'}
    # WeKnora UI belongs to colleagues: preserve a fresh snapshot at execution,
    # rather than asserting that its historical build is still the newest one.
    field = read('weknora-company-preloads-field-20261024-r1.json')
    assert field['accepted_nodes'] == 5 and {r['hostname'] for r in field['observations']} == set(expected['gateway']['nodes'])
    for row in field['observations']:
        assert row['node_preload_passed'] and row['old_references_preserved'] and not row['image_import_may_be_partial']
        assert row['expected_node_uid'] == expected['gateway']['nodes'][row['hostname']]
        assert len(row['roles']) == 1 and row['roles'][0]['manifest_digest'] == role['manifest_digest']
        assert row['roles'][0]['config_digest'] == role['config_digest']
    image = read('weknora-company-image-check-field-20261024-r1.json')['observation']
    pins = {'image': {k: image['report_' + k] for k in ('path', 'bytes', 'sha256')}}
    if bulk is not None:
        assert bulk['status'] == 'BULK_VIEWER_ENROLLMENT_ACCEPTED_AUTO_ENROLLMENT_NOT_DEPLOYED', 'BULK_NOT_COMPLETE'
        assert bulk['observation']['remaining_workspace_memberships'] == 0, 'REMAINING_MEMBERSHIPS'
        pins['bulk'] = {k: bulk['report_' + k] for k in ('path', 'bytes', 'sha256')}
    common = extract(HERE / 'activate-oa-compat.py', ('Refused', 'require', 'digest', 'binding', 'ready',
        'configuration_refs', 'configuration_metadata', 'pods_for', 'identities', 'endpoints'))
    task = 'import asyncio,contextlib,io,json\n' + extract(HERE / 'oa-compat-live-check.py', ('database',))
    task += '''
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        result = asyncio.run(asyncio.wait_for(database(), timeout=55))
except Exception as exc:
    result = {'status':'INCOMPLETE', 'error_class':type(exc).__name__, 'raw_errors_exported':False}
print(json.dumps(result,sort_keys=True))
'''
    sources = {k.removeprefix('app/backend/'): v['sha256'] for k, v in release['qualification']['source_files'].items()}
    values = {
        'COMMON': common, 'NODE_GUARD': (HERE / 'oa-compat-node-gate.py').read_text(encoding='utf-8'),
        'RUNTIME_CHILD': (HERE / 'weknora-company-live-check.py').read_text(encoding='utf-8'), 'TASK_CHILD': task,
        'EXPECTED': expected, 'RECEIPTS': pins, 'SOURCES': sources,
        'OLD_IMAGE': 'deerflow.local/deer-flow-gateway@' + profile['bases']['gateway']['manifest_digest'],
        'OVERRIDES': [{'name': 'WEKNORA_COMPANY_KB_ENABLED', 'value': 'true'},
                      {'name': 'WEKNORA_COMPANY_KB_ORGANIZATION_IDS', 'value': '["b9cfbf7f-7281-4f62-a72b-6a22379efe60"]'}],
    }
    host = (HERE / 'activate-weknora-company.py').read_text(encoding='utf-8')
    for key, value in values.items():
        empty = "''" if isinstance(value, str) else ('{}' if isinstance(value, dict) else '[]')
        old = key + ' = ' + empty
        assert host.count(old) == 1, 'HOST_TEMPLATE_CHANGED'
        host = host.replace(old, key + ' = ' + repr(value), 1)
    ast.parse(host, feature_version=(3, 7))
    for text in (task, values['RUNTIME_CHILD']):
        ast.parse(text, feature_version=(3, 12))
    if bulk is not None:
        namespace = {'__name__': 'package_verification'}
        exec(compile(host, '<packaged-activation>', 'exec'), namespace)
        namespace['validate_evidence']({'image': image, 'bulk': bulk})
    return host


def build():
    if not BULK_FIELD.is_file():
        raise RuntimeError('FINAL_BULK_ENROLLMENT_RECEIPT_REQUIRED_NO_PACKAGE_CREATED')
    field = json.loads(BULK_FIELD.read_text(encoding='utf-8'))
    return render(field['observation'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = build().encode()
    with Path(args.output).open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'production_workload_modified': False}))
