"""Package the bounded callback transport diagnostic against the unchanged release."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    spec = importlib.util.spec_from_file_location('oa_common_package', HERE / 'package-oa-v2-integration-preflight.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    common = module.build()
    namespace = {'__name__': '_oa_common'}
    exec(compile(common, '<oa-common>', 'exec'), namespace)
    child = namespace['CHILD']
    probe = (HERE / 'oa-callback-transport-probe.py').read_text(encoding='utf-8')
    assert child.count('def inspect(pins):') == 1
    child = child.replace('def inspect(pins):', probe + '\n\ndef inspect(pins):')
    marker = "result['local_route'] = local_probe(config)"
    assert child.count(marker) == 1
    child = child.replace(marker, "result['local_route'] = {'status': 'NOT_REQUESTED', 'requests': 0}\n    result['callback_diagnosis'] = diagnose(config)")
    marker = '\n\ndef require(ok, code):'
    assert common.count(marker) == 1
    common = common.replace(marker, '\n\nCHILD = ' + repr(child) + marker)
    source = (HERE / 'diagnose-oa-callback-transport.py').read_text(encoding='utf-8')
    assert source.count("COMMON = ''") == 1
    source = source.replace("COMMON = ''", 'COMMON = ' + repr(common))
    for value in (probe, child, common, source):
        ast.parse(value, feature_version=(3, 7))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = build().encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(), 'production_modified': False}))
