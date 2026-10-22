import argparse
import ast
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def build():
    evidence = json.loads((HERE / 'oa-schema-compatibility-field-observation-20261022-r2.json').read_text())
    consumers = evidence.get('consumers')
    if consumers is None:
        raise ValueError('CONSUMER_EVIDENCE_SHAPE')
    expected = {}
    for name, kind, node in [('gateway', 'gateway', 'ecs-zdz-pro-0018'), ('ppt-master-runner', 'runner', 'datax-01')]:
        base = next(r for r in consumers if r['deployment'] == name)
        build = json.loads((HERE / ('oa-' + kind + '-schema-compatibility-build-20261022.json')).read_text())
        profile = build['profile']
        role = profile['roles'][0]
        expected[name] = {'uid': base['uid'], 'generation': base['generation'], 'spec_sha256': base['spec_sha256'],
                          'node': node, 'node_uid': profile['nodes'][node]['expected_node_uid'],
                          'candidate_image': 'deerflow.local/deer-flow-' + ('gateway' if kind == 'gateway' else 'ppt-master-runner') + '@' + role['manifest_digest'],
                          'config_digest': role['config_digest'],
                          'sources': {'bootstrap': base['observation']['candidate_bootstrap_sha256'],
                                      'helper': base['observation']['candidate_helper_sha256']}}
    child = (HERE / 'oa-compat-image-check.py').read_text(encoding='utf-8')
    source = (HERE / 'verify-oa-compat-images.py').read_text(encoding='utf-8')
    source = source.replace('EXPECTED = {}', 'EXPECTED = ' + repr(expected), 1).replace("CHILD = ''", 'CHILD = ' + repr(child), 1)
    ast.parse(source, feature_version=(3, 7))
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    Path(args.output).write_text(build(), encoding='utf-8', newline='\n')
