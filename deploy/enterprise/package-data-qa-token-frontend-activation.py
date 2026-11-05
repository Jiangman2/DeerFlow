"""Package the combined frontend only after its new preloads and Gateway acceptance."""
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
    require(package.get('schema') == 'portal-data-qa-token-frontend-package.v1'
            and package.get('archive_blob_and_diffid_validation') == 'PASS', 'qualified_combined_package_required')
    source = (HERE / 'activate-data-qa-progress-frontend.py').read_text(encoding='utf-8')
    for key in ('MANIFEST', 'CONFIG', 'TAG'):
        source = replace(source, key, key + ' = ' + repr(package[key.lower()]))
    source = source.replace('portal-frontend-data-qa-progress-activation', 'portal-frontend-data-qa-token-activation')
    source = source.replace('portal-frontend-data-qa-progress-preload-20261022-r2', 'portal-frontend-data-qa-token-preload-20261022-r1')
    source = source.replace('--activate-frontend-data-qa-progress-once', '--activate-frontend-data-qa-token-once')
    source = source.replace('FRONTEND_DATA_QA_PROGRESS_RUNTIME_ACCEPTED_BROWSER_PENDING', 'FRONTEND_DATA_QA_TOKEN_RUNTIME_ACCEPTED_BROWSER_PENDING')
    return source


def module_for(source):
    module = types.ModuleType('_combined_frontend_packaging')
    exec(compile(source, '<combined-frontend-template>', 'exec'), module.__dict__)
    return module


def assemble(package, assets, preloads, backend_pin, proven, backend_source, provenance):
    source = template(package)
    host = module_for(source)
    host.validate_preloads(preloads)
    host.validate_backend_pin(backend_pin)
    require(assets.get('image') == package['manifest'] and assets.get('status') == 'COMPILED_FEATURES_PINNED_NOT_BROWSER_ACCEPTED', 'compiled_assets_required')
    require(set(assets.get('feature_markers', {})) == {'token_pending', 'token_live', 'data_qa_progress', 'token_component'}, 'compiled_features_missing')
    for name, initial, value in (
        ('FRONT_COMMON', "''", proven), ('BACKEND_COMMON', "''", backend_source),
        ('PRELOADS', '{}', preloads), ('BACKEND', '{}', backend_pin),
        ('SOURCE_PINS', '{}', assets['source_pins']), ('PROVENANCE', '{}', provenance),
    ):
        marker = name + ' = ' + initial
        require(source.count(marker) == 1, 'combined_template_changed_' + name)
        source = source.replace(marker, name + ' = ' + repr(value), 1)
    ast.parse(source, feature_version=(3, 7))
    module_for(source).validate_preloads(preloads)
    return source


def build(package_path, assets_path, preloads_path, backend_path, prior_path):
    backend_pin, backend_record_pin = prior.backend_evidence(backend_path)
    require(preloads_path.is_file(), 'new_frontend_preload_receipts_required')
    package_raw, assets_raw, preloads_raw = [p.read_bytes() for p in (package_path, assets_path, preloads_path)]
    package, assets, observation = [json.loads(raw) for raw in (package_raw, assets_raw, preloads_raw)]
    require(observation.get('schema') == 'portal-data-qa-token-frontend-preloads-field.v1'
            and observation.get('evidence_origin') == 'OPERATOR_PASTED_SUMMARIES_NOT_DIRECT_NODE_READ', 'combined_preload_evidence_required')
    rows = observation.get('receipts', [])
    require(len(rows) == 2 and len({r.get('hostname') for r in rows}) == 2, 'two_combined_preloads_required')
    preloads = {r['hostname']: r for r in rows}
    proven = prior.prior_common(prior_path)
    backend = load('package-oa-feature-activation.py').build()
    provenance = {'combined_package': pin(package_raw), 'compiled_assets': pin(assets_raw),
                  'preload_summaries': pin(preloads_raw), 'backend_summary': backend_record_pin,
                  'prior_activator': {'bytes': 56689, 'sha256': prior.PRIOR_SHA},
                  'backend_checks': pin(backend.encode('utf-8'))}
    return assemble(package, assets, preloads, backend_pin, proven, backend, provenance)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('package', 'assets', 'preloads', 'backend', 'prior', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    raw = build(args.package, args.assets, args.preloads, args.backend, args.prior).encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(pin(raw), frontend_deployed=False, ready_for_rollout=False)))
