"""Bind the combined notice and spacing rollout to two actual preload receipts and Frontend 228."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('mobile_notice', HERE / 'package-mobile-notice-removal-frontend-activation.py')
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
prior = previous.prior
replace, require = prior.replace, prior.require


def template(package):
    require(package.get('schema') == 'portal-chat-notice-layout-frontend-package.v1', 'spacing_package_required')
    source = previous.template(dict(package, schema='portal-mobile-notice-removal-frontend-package.v1'))
    accepted = json.loads((HERE / 'mobile-notice-removal-frontend-activation-field-20261024-r1.json').read_bytes())['observation']
    require(accepted['status'] == 'FRONTEND_MOBILE_NOTICE_REMOVAL_RUNTIME_ACCEPTED_BROWSER_PENDING'
            and accepted['stage_passed'] is True and accepted['after']['frontend']['generation'] == 228,
            'frontend_228_acceptance_required')
    source = source.replace('portal-frontend-mobile-notice-removal-', 'portal-frontend-chat-notice-layout-')
    source = source.replace('--activate-frontend-mobile-notice-removal-once', '--activate-frontend-chat-notice-layout-once')
    source = source.replace("'FRONTEND_NOTICE_RUNTIME_ACCEPTED_BROWSER_PENDING'",
                            "'FRONTEND_MOBILE_NOTICE_REMOVAL_RUNTIME_ACCEPTED_BROWSER_PENDING'")
    for key, value in {
        'PRIOR_RECEIPT': {'path': accepted['report_path'], 'bytes': accepted['report_bytes'], 'sha256': accepted['report_sha256']},
        'FRONT': accepted['after']['frontend'],
        'OLD': 'deerflow.local/deer-flow-frontend@' + accepted['cache_manifest'],
        'ACCEPTED': 'FRONTEND_CHAT_NOTICE_LAYOUT_RUNTIME_ACCEPTED_BROWSER_PENDING',
        'PRELOAD_PATHS': {host: root + '/portal-frontend-chat-notice-layout-preload-20261024-r1/REPORT.json'
                          for host, root in [('datax-02', '/var/lib'), ('ecs-zdz-pro-0015', '/mnt/data')]},
    }.items():
        source = replace(source, key, key + ' = ' + repr(value))
    ast.parse(source, feature_version=(3, 7))
    return source


def build(package_path, assets_path, preloads_path, prior_path):
    raw = [p.read_bytes() for p in (package_path, assets_path, preloads_path)]
    package, assets, field = [json.loads(value) for value in raw]
    require(field.get('evidence_origin') == 'OPERATOR_PASTED_SUMMARIES_NOT_DIRECT_NODE_READ'
            and field.get('schema') == 'portal-chat-notice-layout-frontend-preloads-field.v1',
            'actual_preload_summaries_required')
    receipts = field.get('receipts', [])
    require(len(receipts) == 2 and len({r['hostname'] for r in receipts}) == 2, 'two_nodes_required')
    source = template(package)
    prior.module_for(source).validate_preloads({r['hostname']: r for r in receipts})
    require(assets.get('image') == package['manifest']
            and assets.get('status') == 'COMPILED_FEATURES_PINNED_NOT_BROWSER_ACCEPTED', 'compiled_assets_required')
    require(set(assets['feature_markers']) == {'notice_text', 'notice_component', 'token_pending',
                                             'token_live', 'data_qa_progress', 'token_component',
                                             'mobile_chat_spacing', 'desktop_welcome_notice'}, 'features_missing')
    for key, value in {
        'FRONT_COMMON': prior.prior.prior_common(prior_path),
        'PRELOADS': {r['hostname']: r for r in receipts},
        'SOURCE_PINS': assets['source_pins'],
        'PROVENANCE': dict(zip(('qualified_package', 'compiled_assets', 'preload_summaries'), [prior.pin(r) for r in raw])),
    }.items():
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
