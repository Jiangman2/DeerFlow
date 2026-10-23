"""Package the single-image Gateway rollout, bound to accepted 0021 expansion."""
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build():
    expansion_package = load(HERE / 'package-oa-schema-expansion.py', 'expansion_package')
    namespace = {'__name__': 'feature_packaging_only'}
    exec(compile(expansion_package.build(), '<expansion-package>', 'exec'), namespace)
    expected = copy.deepcopy(namespace['EXPECTED'])
    record = json.loads((HERE / 'oa-schema-expansion-field-20261022-r1.json').read_text(encoding='utf-8'))['observation']
    assert record['status'] == 'OA_SCHEMA_EXPANSION_ACCEPTED_FEATURE_NOT_DEPLOYED'
    assert record['schema_revision'] == '0021_oa_result_sets' and record['verified'] == 6
    expansion = {k: record['report_' + k] for k in ('path', 'bytes', 'sha256')}
    installed = json.loads((HERE / 'oa-data-qa-installed-image-field-20261022-r1.json').read_text(encoding='utf-8'))['observation']
    acceptance = {k: installed['report_' + k] for k in ('path', 'bytes', 'sha256')}
    pins = json.loads((HERE / 'oa-data-qa-installed-image-pins-20261022.json').read_text(encoding='utf-8'))
    feature = {k: pins[k] for k in ('candidate_image', 'config_digest', 'sources')}
    base = json.loads((HERE / 'data-qa-glm-release-20261021-r1.json').read_text(encoding='utf-8'))
    files = base['profile']['tail_files']['gateway-forward']['files']
    preserved = {path: value for path, value in files.items() if path not in feature['sources']}
    assert len(preserved) == 13 and any(path.endswith('/models/glm_stream_guard.py') for path in preserved)
    feature['preserved_sources'] = preserved
    feature['baseline_bootstrap_sha256'] = 'b852ea3bd215124e605e8c5a276c1cc99343a67d4165136121d885ea3def9449'
    preloads = json.loads((HERE / 'oa-data-qa-preload-field-observation-20261021.json').read_text(encoding='utf-8'))['preloaded_nodes']
    assert len(preloads) == 5
    child = (HERE / 'oa-feature-live-check.py').read_text(encoding='utf-8')
    host = (HERE / 'activate-oa-data-qa-feature.py').read_text(encoding='utf-8')
    for key, placeholder, value in (
        ('EXPECTED', '{}', expected), ('FRONT', '{}', namespace['FRONT']), ('FEATURE', '{}', feature),
        ('PRELOADS', '[]', preloads), ('EXPANSION', '{}', expansion), ('ACCEPTANCE', '{}', acceptance),
        ('SCHEMA_CHILD', "''", namespace['CHILD']), ('FEATURE_CHILD', "''", child),
        ('COMMON', "''", namespace['COMMON']),
    ):
        old = key + ' = ' + placeholder
        assert host.count(old) == 1, 'HOST_TEMPLATE_CHANGED'
        host = host.replace(old, key + ' = ' + repr(value), 1)
    ast.parse(host, feature_version=(3, 7))
    ast.parse(child, feature_version=(3, 12))
    return host


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = build().encode()
    with Path(args.output).open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'production_workload_modified': False}))
