"""Bind actual frontend preloads and accepted Gateway receipt; never mutate a cluster."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import types

HERE = Path(__file__).resolve().parent
# The user added GLM Token-wait summary work after this candidate was preloaded.
# Rebind a qualified replacement image and new preloads before enabling packaging.
CANDIDATE_SUPERSEDED = True
PRIOR_SHA = 'da20b5053912c13c60aaf7152eb040f7f6d69e8843222a891f413a7eb4cab141'
PRIOR_NAMES = ('canonical', 'pin', 'binding', 'prepare_patch', 'node_gate', 'controller', 'ready', 'normal_inventory', 'Execution')
SOURCE_PINS = {
    '/app/frontend/.next/BUILD_ID': {'bytes': 21, 'sha256': '9f21e0c81acf60563f8d7fd8bed37d175c532c45418ef597c99b8d03c5baef36'},
    '/app/frontend/.next/routes-manifest.json': {'bytes': 10193, 'sha256': '9b19f00490aaa2b8ffb5d056901e9ba959518b38b1365a3ea1290c0f689647f9'},
    '/app/frontend/.next/static/chunks/17wn03xf_7czf.js': {'bytes': 525105, 'sha256': 'a6ca24e986284450bb80d47a6e970ed64b4225943386f28ea79befe047b583ab'},
    '/app/frontend/.next/static/chunks/1-04c_eaxy17o.js': {'bytes': 312909, 'sha256': '1ab91f7abee25309c5aa58d71d3fbde10b3c8b90105b715c73cf6915f1b32139'},
}


def require(ok, code):
    if not ok:
        raise ValueError(code)


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def host_module():
    return load(HERE / 'activate-data-qa-progress-frontend.py', '_frontend_activation_template')


def prior_common(path):
    raw = path.read_bytes()
    require(pin(raw) == {'bytes': 56689, 'sha256': PRIOR_SHA}, 'proven_frontend_source_changed')
    source = raw.decode('utf-8')
    nodes = ast.parse(source).body
    statements = []
    for name in ('HELPER', 'HELPER_PIN'):
        found = [n for n in nodes if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
        require(len(found) == 1, 'proven_helper_shape')
        statements.append(ast.get_source_segment(source, found[0]))
    for name in PRIOR_NAMES:
        found = [n for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == name]
        require(len(found) == 1, 'proven_function_shape_' + name)
        statements.append(ast.get_source_segment(source, found[0]))
    return 'import copy,hashlib,json,os,re,time,types\n' + '\n\n'.join(statements) + '\n'


def preload_evidence(path):
    raw = path.read_bytes()
    record = json.loads(raw)
    require(record.get('schema') == 'portal-data-qa-progress-frontend-preloads-field.v1'
            and record.get('evidence_origin') == 'OPERATOR_PASTED_SUMMARIES_NOT_DIRECT_NODE_READ', 'operator_preloads_required')
    rows = record.get('receipts', [])
    require(len(rows) == 2 and len({r.get('hostname') for r in rows}) == 2, 'two_preload_receipts_required')
    result = {r['hostname']: r for r in rows}
    host_module().validate_preloads(result)
    return result, pin(raw)


def backend_evidence(path):
    require(path is not None and path.is_file(), 'accepted_gateway_receipt_not_yet_available')
    raw = path.read_bytes()
    record = json.loads(raw)
    record = record.get('observation', record)
    host = host_module()
    require(record.get('status') == host.BACKEND_ACCEPTED and record.get('accepted_pod_count') == 70
            and record.get('runtime_accepted') is True and record.get('feature_image_deployed') is True
            and record.get('v2_enabled') is False and record.get('patch_request_count') == 1
            and all(record.get(k) is True for k in ('frontend_preserved', 'runner_preserved',
                'configuration_metadata_preserved', 'gateway_other_spec_preserved', 'patch_acknowledged')),
            'accepted_gateway_receipt_required')
    require(record.get('schema_revision') == host.TARGET and record.get('schema_migration_requested') is False
            and record.get('provider_requests') == record.get('oa_callback_requests') == 0
            and record.get('gateway_after', {}).get('uid') == 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
            and record.get('gateway_after', {}).get('generation') == 384, 'accepted_gateway_scope')
    result = {k: record.get('report_' + k) for k in ('path', 'bytes', 'sha256')}
    host.validate_backend_pin(result)
    return result, pin(raw)


def assemble(preloads, backend_pin, proven_source, backend_source, provenance):
    host = host_module()
    host.validate_preloads(preloads)
    host.validate_backend_pin(backend_pin)
    require(proven_source and backend_source and provenance, 'package_dependencies_required')
    source = (HERE / 'activate-data-qa-progress-frontend.py').read_text(encoding='utf-8')
    for name, initial, value in (
        ('FRONT_COMMON', "''", proven_source), ('BACKEND_COMMON', "''", backend_source),
        ('PRELOADS', '{}', preloads), ('BACKEND', '{}', backend_pin), ('SOURCE_PINS', '{}', SOURCE_PINS),
        ('PROVENANCE', '{}', provenance),
    ):
        token = name + ' = ' + initial
        require(source.count(token) == 1, 'host_template_changed_' + name)
        source = source.replace(token, name + ' = ' + repr(value), 1)
    ast.parse(source, feature_version=(3, 7))
    # Compile/import only; the entrypoint is never called during packaging.
    module = types.ModuleType('_frontend_activation_packaging_check')
    exec(compile(source, '<frontend-activation-package>', 'exec'), module.__dict__)
    module.validate_preloads(module.PRELOADS)
    return source


def build(preload_path, backend_path, prior_path):
    # Refuse absent/failed backend evidence before assembling any operator script.
    backend_pin, backend_receipt_pin = backend_evidence(backend_path)
    require(not CANDIDATE_SUPERSEDED, 'candidate_superseded_requires_token_summary_rebuild')
    preloads, preload_pin = preload_evidence(preload_path)
    proven = prior_common(prior_path)
    feature_package = load(HERE / 'package-oa-feature-activation.py', '_frontend_backend_checks_package')
    backend = feature_package.build()
    provenance = {'proven_frontend_activator': {'bytes': 56689, 'sha256': PRIOR_SHA},
                  'frontend_preload_summaries': preload_pin, 'backend_operator_summary': backend_receipt_pin,
                  'backend_checks': pin(backend.encode('utf-8')),
                  'frontend_build_qualification': pin((HERE / 'data-qa-progress-frontend-build-20261021.json').read_bytes())}
    return assemble(preloads, backend_pin, proven, backend, provenance)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preloads', type=Path, default=HERE / 'data-qa-progress-frontend-preloads-field-20261022-r2.json')
    parser.add_argument('--backend-acceptance', type=Path, required=True)
    parser.add_argument('--prior-activator', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = build(args.preloads, args.backend_acceptance, args.prior_activator).encode('utf-8')
    with args.output.open('xb') as stream:
        stream.write(raw)
    print(json.dumps(dict(pin(raw), frontend_deployed=False, ready_for_rollout=False)))
