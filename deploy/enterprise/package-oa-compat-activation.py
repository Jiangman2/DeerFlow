import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    spec = importlib.util.spec_from_file_location('image_builder', HERE / 'package-oa-compat-image-check.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    n = {'__name__': 'packaging'}
    exec(module.build(), n)
    expected = n['EXPECTED']
    old = json.loads((HERE / 'oa-schema-compatibility-preloads-field-20261022-r1.json').read_text())['receipts']
    continued = json.loads((HERE / 'oa-schema-compatibility-preload-continuation-20261022-r2.json').read_text())['receipt']
    receipts = [r for r in old if r['hostname'] != 'datax-01'] + [continued]
    for name, kind in [('gateway', 'gateway'), ('ppt-master-runner', 'runner')]:
        profile = json.loads((HERE / ('oa-' + kind + '-schema-compatibility-build-20261022.json')).read_text())['profile']
        expected[name]['old_image'] = expected[name]['candidate_image'].split('@')[0] + '@' + profile['bases'][kind]['manifest_digest']
        expected[name]['nodes'] = {host: row['expected_node_uid'] for host, row in profile['nodes'].items()}
        expected[name]['port'] = 8001 if kind == 'gateway' else 8003
    child = (HERE / 'oa-compat-live-check.py').read_text(encoding='utf-8')
    source = (HERE / 'activate-oa-compat.py').read_text(encoding='utf-8')
    source = source.replace('EXPECTED = {}', 'EXPECTED = ' + repr(expected), 1)
    source = source.replace('PRELOADS = []', 'PRELOADS = ' + repr(receipts), 1)
    source = source.replace("CHILD = ''", 'CHILD = ' + repr(child), 1)
    ast.parse(source, feature_version=(3, 7))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    raw = build().encode()
    Path(args.output).write_bytes(raw)
    print(json.dumps({'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}))
