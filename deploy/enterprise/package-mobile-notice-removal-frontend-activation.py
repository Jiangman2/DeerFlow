"""Bind the mobile-only rollout to real preload receipts and the last accepted frontend."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior = load('package-nonconfidential-notice-frontend-activation.py')
replace = prior.replace
require = prior.require
RECEIPT_FILE = HERE / 'nonconfidential-notice-frontend-activation-field-20261023-r3.json'


def template(package):
    require(package.get('schema') == 'portal-mobile-notice-removal-frontend-package.v1'
            and package.get('archive_blob_and_diffid_validation') == 'PASS', 'qualified_package_required')
    previous = json.loads(RECEIPT_FILE.read_bytes())['observation']
    require(previous['stage_passed'] is True and previous['after']['frontend']['generation'] == 227,
            'previous_frontend_acceptance_required')
    source = (HERE / 'activate-nonconfidential-notice-frontend.py').read_text(encoding='utf-8')
    # The historical cache-list failure belongs to the prior rollout, not this one.
    lines = source.splitlines(keepends=True)
    for node in reversed(ast.parse(source).body):
        if isinstance(node, ast.FunctionDef) and node.name in ('read_failed_r2', 'validate_failed_r2'):
            del lines[node.lineno - 1:node.end_lineno]
    source = ''.join(lines)
    require(source.count("        r['prior_noop_failure'] = read_failed_r2(helper)\n") == 1, 'historical_call_changed')
    source = source.replace("        r['prior_noop_failure'] = read_failed_r2(helper)\n", '')
    source = source.replace("'FRONTEND_DATA_QA_TOKEN_RUNTIME_ACCEPTED_BROWSER_PENDING'",
                            "'FRONTEND_NOTICE_RUNTIME_ACCEPTED_BROWSER_PENDING'")
    source = source.replace('portal-frontend-nonconfidential-notice-activation.v2',
                            'portal-frontend-mobile-notice-removal-activation.v1')
    source = source.replace('portal-frontend-nonconfidential-notice-activation-',
                            'portal-frontend-mobile-notice-removal-activation-')
    source = source.replace('--resume-frontend-nonconfidential-notice-once',
                            '--activate-frontend-mobile-notice-removal-once')
    values = {
        'PRIOR_RECEIPT': {'path': previous['report_path'], 'bytes': previous['report_bytes'], 'sha256': previous['report_sha256']},
        'FRONT': previous['after']['frontend'],
        'OLD': 'deerflow.local/deer-flow-frontend@' + previous['cache_manifest'],
        'MANIFEST': package['manifest'], 'CONFIG': package['config'], 'TAG': package['tag'],
        'ONCE': 'portal-frontend-mobile-notice-removal-activation-20261024-r1.once',
        'ACCEPTED': 'FRONTEND_MOBILE_NOTICE_REMOVAL_RUNTIME_ACCEPTED_BROWSER_PENDING',
        'FAILED_R2': None,
        'PRELOAD_PATHS': {host: root + '/portal-frontend-mobile-notice-removal-preload-20261024-r1/REPORT.json'
                          for host, root in [('datax-02', '/var/lib'), ('ecs-zdz-pro-0015', '/mnt/data')]},
    }
    for key, value in values.items():
        source = replace(source, key, key + ' = ' + repr(value))
    ast.parse(source, feature_version=(3, 7))
    return source


def build(package_path, assets_path, preloads_path, prior_path):
    raw = [p.read_bytes() for p in (package_path, assets_path, preloads_path)]
    package, assets, field = [json.loads(value) for value in raw]
    require(field.get('evidence_origin') == 'OPERATOR_PASTED_SUMMARIES_NOT_DIRECT_NODE_READ'
            and field.get('schema') == 'portal-mobile-notice-removal-frontend-preloads-field.v1',
            'actual_preload_summaries_required')
    receipts = field.get('receipts', [])
    require(len(receipts) == 2 and len({r['hostname'] for r in receipts}) == 2, 'two_nodes_required')
    source = template(package)
    prior.module_for(source).validate_preloads({r['hostname']: r for r in receipts})
    require(assets.get('image') == package['manifest']
            and assets.get('status') == 'COMPILED_FEATURES_PINNED_NOT_BROWSER_ACCEPTED', 'compiled_assets_required')
    require(set(assets['feature_markers']) == {'notice_text', 'notice_component', 'token_pending',
                                             'token_live', 'data_qa_progress', 'token_component'}, 'features_missing')
    values = {'FRONT_COMMON': prior.prior.prior_common(prior_path),
              'PRELOADS': {r['hostname']: r for r in receipts}, 'SOURCE_PINS': assets['source_pins'],
              'PROVENANCE': dict(zip(('qualified_package', 'compiled_assets', 'preload_summaries'),
                                     [prior.pin(r) for r in raw]))}
    for key, value in values.items():
        source = replace(source, key, key + ' = ' + repr(value))
    ast.parse(source, feature_version=(3, 7))
    prior.module_for(source).load_modules()
    return source


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'assets', 'preloads', 'prior', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    content = build(args.package, args.assets, args.preloads, args.prior).encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(content)
    print(json.dumps(dict(prior.pin(content), frontend_deployed=False)))
