"""Prepare a frontend-only activator after both exact-image preload receipts arrive."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import types

HERE = Path(__file__).resolve().parent


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior = load('package-data-qa-progress-frontend-activation.py')
replace = load('package-data-qa-progress-frontend.py').replace
require, pin = prior.require, prior.pin


def template(package):
    require(package.get('schema') == 'portal-nonconfidential-notice-frontend-package.v1'
            and package.get('archive_blob_and_diffid_validation') == 'PASS', 'qualified_notice_package_required')
    source = (HERE / 'activate-nonconfidential-notice-frontend.py').read_text(encoding='utf-8')
    for key in ('MANIFEST', 'CONFIG', 'TAG'):
        source = replace(source, key, key + ' = ' + repr(package[key.lower()]))
    return source


def module_for(source):
    module = types.ModuleType('_notice_frontend_packaging')
    exec(compile(source, '<notice-frontend-template>', 'exec'), module.__dict__)
    return module


def assemble(package, assets, preloads, proven, provenance):
    source = template(package)
    module_for(source).validate_preloads(preloads)
    require(assets.get('image') == package['manifest']
            and assets.get('status') == 'COMPILED_FEATURES_PINNED_NOT_BROWSER_ACCEPTED', 'compiled_assets_required')
    require(set(assets.get('feature_markers', {})) == {'token_pending', 'token_live', 'data_qa_progress',
            'token_component', 'notice_text', 'notice_component'}, 'compiled_features_missing')
    for key, value in (('FRONT_COMMON', proven), ('PRELOADS', preloads),
                       ('SOURCE_PINS', assets['source_pins']), ('PROVENANCE', provenance)):
        source = replace(source, key, key + ' = ' + repr(value))
    ast.parse(source, feature_version=(3, 7))
    module_for(source).load_modules()
    return source


def build(package_path, assets_path, preloads_path, prior_path):
    raw = [p.read_bytes() for p in (package_path, assets_path, preloads_path)]
    package, assets, observation = [json.loads(r) for r in raw]
    require(observation.get('schema') == 'portal-nonconfidential-notice-frontend-preloads-field.v1'
            and observation.get('evidence_origin') == 'OPERATOR_PASTED_SUMMARIES_NOT_DIRECT_NODE_READ',
            'actual_notice_preloads_required')
    rows = observation.get('receipts', [])
    require(len(rows) == 2 and len({r.get('hostname') for r in rows}) == 2, 'two_preloads_required')
    provenance = dict(zip(('qualified_package', 'compiled_assets', 'preload_summaries'), [pin(r) for r in raw]))
    provenance['proven_activator'] = {'bytes': 56689, 'sha256': prior.PRIOR_SHA}
    return assemble(package, assets, {r['hostname']: r for r in rows}, prior.prior_common(prior_path), provenance)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'assets', 'preloads', 'prior', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    content = build(args.package, args.assets, args.preloads, args.prior).encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(content)
    print(json.dumps(dict(pin(content), frontend_deployed=False)))
