"""Package only readonly verification functions, never the activation entrypoint."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import types

HERE = Path(__file__).resolve().parent


def build():
    spec = importlib.util.spec_from_file_location('_enable_packager', HERE / 'package-oa-v2-multi-only.py')
    packager = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(packager)
    prepared = types.ModuleType('_enable_package_no_entrypoint')
    exec(compile(packager.build(), '<enable-package-not-run>', 'exec'), prepared.__dict__)
    source = (HERE / 'enable-oa-v2-multi-only.py').read_text(encoding='utf-8')
    names = ('validate_config', 'current_configuration', 'validate_route', 'read_deployment',
             'other_services', 'current_pods', 'verify_pods')
    tree = ast.parse(source)
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in selected} == set(names)
    helpers = 'import copy, json\n\n' + '\n\n'.join(ast.get_source_segment(source, node) for node in selected)
    output = (HERE / 'verify-oa-v2-multi-after-timeout.py').read_text(encoding='utf-8')
    for name, value, empty in (('COMMON', prepared.COMMON, "''"), ('HELPERS', helpers, "''"),
                                ('CONFIG_KEYS', sorted(prepared.BASELINE_CONFIG), '[]')):
        output = packager.replace(output, name, value, empty)
    ast.parse(output, feature_version=(3, 7))
    assert 'dispatch_patch' not in output and 'def enable(' not in output
    assert "'patch', 'deployment'" not in output and "'rollout'" not in output
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = build().encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'workload_modified': False}))
