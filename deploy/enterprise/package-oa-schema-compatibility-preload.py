"""Build two narrow old-consumer compatibility overlays; never deploy or migrate.

Each overlay contains only the exact field-tested bootstrap variant and helper.
The existing image configuration, migration tree and all prior layers are kept.
"""
import argparse
import ast
import base64
import copy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[2]
PERSIST = 'app/backend/packages/harness/deerflow/persistence/'
INPUTS = {
    'gateway': {
        'file': 'preload-portal-gateway-data-qa-glm-20261021-r1.py',
        'sha256': '4213b68bdf9b67a6c1c92c4adc24250121dcbb2910c7abed40ba3ab12d4477e1',
        'base': 'sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5',
        'prefix': 'portal-gateway-data-qa-glm', 'upper': 'GATEWAY_DATA_QA_GLM',
        'flag': '--preload-gateway-data-qa-glm-once', 'layers': 67,
        'baseline': 'b852ea3bd215124e605e8c5a276c1cc99343a67d4165136121d885ea3def9449',
        'candidate': '611dff501f59b3b94bccd4b3f9b31309955eb5e38356ed257882ead79d8e1fb1',
    },
    'runner': {
        'file': 'prepare-portal-ppt-runner-host-capability-20261018-r1.py',
        'sha256': '53f541432ddcd80770e50076b515adcabb758c24833306229571fedcf9a9e29a',
        'base': 'sha256:8a853325c46a215c06d0ab77720aa3a8e48b06c98c60f2837c663867e704b0f0',
        'prefix': 'portal-ppt-runner-host-capability', 'upper': 'RUNNER_HOST_CAPABILITY',
        'flag': '--prepare-runner-host-capability-once', 'layers': 384,
        'baseline': '990e6381692083a00a856a61c19f072e81f57f4ec41b232db4d8e8ebecf3ab86',
        'candidate': '7b704c7f4434556d354f9bbbd326dfdb15f935dbb5b1d8b1fd7908866f02e612',
    },
}
HELPER_SHA = '2dd04bfafc29856c0f646926043aed7b713d79d69744a9053ff99fdf6af24c8b'
CHECKER_SHA = 'c4a42a6499416614d078ff75ff35db588e1366373a586efd2aeb028001b1a04c'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def helpers():
    spec = importlib.util.spec_from_file_location('oci_helpers', ROOT / 'deploy/enterprise/package-glm-stream-preload.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build(kind, importer, checker):
    pin = INPUTS[kind]
    assert sha(importer) == pin['sha256'], 'IMPORTER_CHANGED'
    assert sha(checker) == CHECKER_SHA, 'CHECKER_CHANGED'
    h = helpers()
    # Read the very bytes used by the successful isolated field check.
    child = h.literal(checker.decode(), 'CHILD')
    variants = h.literal(child, 'BOOT_VARIANTS')
    bootstrap = base64.b64decode(variants[pin['baseline']]['candidate'], validate=True)
    helper = base64.b64decode(h.literal(child, 'HELPER_CODE'), validate=True)
    assert sha(bootstrap) == pin['candidate'] and sha(helper) == HELPER_SHA
    source = importer.decode().replace('\r\n', '\n')
    sources = h.literal(source, 'SOURCES')
    profile, encoded = json.loads(sources['profile']), json.loads(sources['blobs'])
    old = profile['roles'][0]
    assert old['manifest_digest'] == pin['base'] and old['base'] == kind
    mr = base64.b64decode(encoded[pin['base']], validate=True)
    cr = base64.b64decode(encoded[old['config_digest']], validate=True)
    assert 'sha256:' + sha(mr) == pin['base'] and 'sha256:' + sha(cr) == old['config_digest']
    manifest, config = json.loads(mr), json.loads(cr)
    assert config['architecture'] == 'arm64' and config['os'] == 'linux'
    assert len(manifest['layers']) == len(config['rootfs']['diff_ids']) == pin['layers']
    data, files = io.BytesIO(), {}
    with tarfile.open(fileobj=data, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, raw in [('bootstrap.py', bootstrap), ('oa_schema_compatibility.py', helper)]:
            compile(raw, name, 'exec')
            info = tarfile.TarInfo(PERSIST + name)
            info.size, info.mode, info.mtime = len(raw), 0o644, 0
            tar.addfile(info, io.BytesIO(raw))
            files[info.name] = {'bytes': len(raw), 'sha256': sha(raw)}
    expanded = data.getvalue()
    output = io.BytesIO()
    with gzip.GzipFile(fileobj=output, mode='wb', filename='', mtime=0) as stream:
        stream.write(expanded)
    layer = output.getvalue()
    ld, diff = 'sha256:' + sha(layer), 'sha256:' + sha(expanded)
    nc = copy.deepcopy(config)
    nc['rootfs']['diff_ids'].append(diff)
    nc['history'].append({'created': '2026-10-22T01:00:00Z', 'created_by': 'Known OA additive schema startup compatibility; local migration head unchanged'})
    ncr = canonical(nc)
    cd = 'sha256:' + sha(ncr)
    nm = copy.deepcopy(manifest)
    nm['config'].update(digest=cd, size=len(ncr))
    nm['layers'].append({'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': ld, 'size': len(layer)})
    nmr = canonical(nm)
    md = 'sha256:' + sha(nmr)
    repo = old['tag'].rsplit(':', 1)[0]
    tag = repo + ':20261022-oa-schema-compat-r1-' + md[7:19]
    chain = ''
    for item in config['rootfs']['diff_ids']:
        chain = item if not chain else 'sha256:' + sha((chain + ' ' + item).encode())
    profile['bases'][kind] = {
        'config_bytes': len(cr), 'config_digest': old['config_digest'],
        'config_object_sha256': sha(canonical(config)),
        'descriptor_prefix_sha256': sha(canonical(manifest['layers'])),
        'diff_prefix_sha256': sha(canonical(config['rootfs']['diff_ids'])),
        'final_chain_id': chain, 'layer_count': pin['layers'],
        'manifest_bytes': len(mr), 'manifest_digest': pin['base'],
        'runtime_config_sha256': old['runtime_config_sha256'], 'tag': old['tag'],
    }
    profile['preserve'][kind].update({old['tag']: pin['base'], repo + '@' + pin['base']: pin['base']})
    new_role = dict(old, tag=tag, manifest_digest=md, config_digest=cd,
                    expected_layer_count=pin['layers'] + 1,
                    tail_layers=[{'blob_digest': ld, 'compressed_bytes': len(layer), 'diff_id': diff,
                                  'position': pin['layers'] + 1, 'uncompressed_bytes_upper_bound': len(expanded)}])
    profile['roles'] = [new_role]
    profile['tail_files'] = {old['role']: {'files': files, 'uncompressed_bytes': len(expanded)}}
    prefix = 'portal-' + kind + '-oa-schema-compatibility'
    profile['schema'] = prefix + '-profile.v1'
    sources['profile'] = canonical(profile)
    sources['blobs'] = canonical({d: base64.b64encode(v).decode() for d, v in [(md, nmr), (cd, ncr), (ld, layer)]})
    evidence = {'evidence_origin': 'OPERATOR_PASTED_20261022_R2_RESULT', 'consumer': kind,
                'status': 'SYNTHETIC_SCHEMA_COMPATIBILITY_ACCEPTED', 'checks_passed': 6,
                'baseline_bootstrap_sha256': pin['baseline'], 'candidate_bootstrap_sha256': pin['candidate'],
                'candidate_helper_sha256': HELPER_SHA, 'field_checker_sha256': CHECKER_SHA,
                'production_database_access': False, 'image_runtime_acceptance_pending': True}
    source = source.replace(pin['prefix'], prefix).replace(pin['upper'], kind.upper() + '_OA_SCHEMA_COMPATIBILITY')
    source = source.replace(pin['flag'], '--preload-' + kind + '-oa-schema-compatibility-once')
    for key, value in {
        'SOURCES': sources, 'SOURCE_PINS': {k: {'bytes': len(v), 'sha256': sha(v)} for k, v in sources.items()},
        'CONTROL': '/var/lib/' + prefix + '-node-20261022-r1',
        'PAYLOAD_LIMITS': {p: 65536 for p in files}, 'TAIL_LIMIT': 131072,
    }.items():
        source = h.replace_node(source, key, key + ' = ' + repr(value))
    if kind == 'gateway':
        source = h.replace_node(source, 'ROUTE_PIN', 'ROUTE_PIN = ' + repr(files))
        source = h.replace_node(source, 'PRODUCT_QUALIFICATION', 'PRODUCT_QUALIFICATION = ' + repr(evidence))
        source = h.replace_node(source, 'accepted_nfs_gate',
            'def accepted_nfs_gate():\n    require(PRODUCT_QUALIFICATION == ' + repr(evidence) +
            ", 'candidate_evidence_changed')\n    require(ROUTE_PIN == " + repr(files) + ", 'candidate_source_changed')", function=True)
    ast.parse(source, feature_version=(3, 7))
    compile(source, '<compatibility-preload>', 'exec')
    receipt = {'schema': 'portal-oa-schema-compatibility-build.v1', 'consumer': kind,
               'profile': profile, 'qualification': evidence, 'script_bytes': len(source.encode()),
               'script_sha256': sha(source.encode()), 'importer_sha256': pin['sha256'],
               'migration_tree_preserved': True, 'base_layers_preserved': True,
               'runtime_configuration_preserved': True, 'ready_for_rollout': False}
    return source.encode(), receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-directory', type=Path, required=True)
    parser.add_argument('--output-directory', type=Path, required=True)
    args = parser.parse_args()
    args.output_directory.mkdir(exist_ok=False)
    checker = (args.input_directory / 'verify-portal-oa-schema-compatibility-20261021-r2.py').read_bytes()
    for kind, pin in INPUTS.items():
        script, receipt = build(kind, (args.input_directory / pin['file']).read_bytes(), checker)
        name = 'preload-portal-' + kind + '-oa-schema-compatibility-20261022-r1.py'
        (args.output_directory / name).write_bytes(script)
        (args.output_directory / (kind + '-BUILD.json')).write_bytes(canonical(receipt) + b'\n')
        print(json.dumps({'consumer': kind, 'manifest': receipt['profile']['roles'][0]['manifest_digest'],
                          'script_bytes': len(script), 'ready_for_rollout': False}))
