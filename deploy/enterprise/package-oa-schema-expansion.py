"""Package a receipt-bound, once-only OA additive migration for datax-01."""
import argparse
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def once(source, old, new):
    assert source.count(old) == 1, 'HOST_TEMPLATE_CHANGED'
    return source.replace(old, new, 1)


def build():
    spec = importlib.util.spec_from_file_location('compat_packager', HERE / 'package-oa-compat-activation.py')
    package = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(package)
    common_source = package.build()
    namespace = {'__name__': 'package_readonly_helpers'}
    exec(compile(common_source, '<compat-package>', 'exec'), namespace)
    field = json.loads((HERE / 'oa-compat-activation-field-20261022-r4.json').read_text(encoding='utf-8'))['observation']
    image = json.loads((HERE / 'oa-data-qa-installed-image-field-20261022-r1.json').read_text(encoding='utf-8'))['observation']
    assert field['status'] == 'COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED'
    assert field['runtime_accepted'] == {'gateway': 70, 'ppt-master-runner': 1}
    assert image['status'] == 'OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED'
    assert image['checks'][0]['observation']['status'] == 'INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED'
    assert image['checks'][0]['observation']['production_database_access'] is False
    expected = copy.deepcopy(namespace['EXPECTED'])
    migration_base = json.loads((HERE / 'oa-schema-expansion-migration-base-20261022.json').read_text())['files']
    for role, pin in expected.items():
        pin.update(field['after'][role])
        pin['old_image'] = pin['candidate_image']
        pin['database_endpoint_fingerprint'] = 'b0fdaf30f3160cd52abf01520da71a20570c23795938bf73165153083fae9d91'
        pin['migration_base_sources'] = migration_base

    functions = {'Refused', 'require', 'digest', 'binding', 'ready', 'command', 'nodes_gate',
                 'configuration_refs', 'configuration_metadata', 'pods_for', 'identities', 'endpoints'}
    assignments = {'BASE', 'REVIEWED_EXTRA_NODES', 'NODE_GUARD'}
    tree = ast.parse(common_source)
    chunks = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)) or (
                isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in functions) or (
                isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in assignments):
            chunks.append(ast.get_source_segment(common_source, node))
    common = '\n\n'.join(chunks) + '\n'
    assert 'def main(' not in common and 'def plan(' not in common

    receipts = {key: {label: receipt[source] for label, source in (
        ('path', 'report_path'), ('bytes', 'report_bytes'), ('sha256', 'report_sha256'))}
        for key, receipt in (('compatibility', field), ('feature_image', image))}
    migration = (ROOT / 'backend/packages/harness/deerflow/persistence/migrations/versions/0021_oa_result_sets.py').read_text(encoding='utf-8')
    assert len(migration.encode()) == 801 and hashlib.sha256(migration.encode()).hexdigest() == 'f0f4e5571251359f12ca023b864542a69fda97f990bf476dbf366d9039657347'
    child = (HERE / 'oa-schema-expansion-child.py').read_text(encoding='utf-8')
    host = (HERE / 'expand-oa-schema.py').read_text(encoding='utf-8')
    for key, placeholder, value in (
        ('EXPECTED', '{}', expected), ('RECEIPTS', '{}', receipts), ('FRONT', '{}', namespace['FRONT']),
        ('MIGRATION_SOURCE', "''", migration), ('CHILD', "''", child), ('COMMON', "''", common),
    ):
        # Embed COMMON last, so placeholders cannot accidentally match its code.
        host = once(host, key + ' = ' + placeholder, key + ' = ' + repr(value))
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
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'production_modified_locally': False}))
