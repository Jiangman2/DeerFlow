"""Append only OA v2/budget and Data QA progress to the current Gateway.

Reuses the previously qualified importer without changing its node operations.
This builds a preload-only candidate, never applies migrations or deploys it.
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
import pathlib
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
BASE = 'sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5'
IMPORTER_SHA = '4213b68bdf9b67a6c1c92c4adc24250121dcbb2910c7abed40ba3ab12d4477e1'
PATHS = (
    'app/gateway/oa_policy_flow_config.py', 'app/gateway/oa_policy_flow_service.py',
    'app/gateway/oa_policy_flow_results.py', 'app/gateway/routers/oa_policy_flow.py',
    'app/gateway/services.py',
    'packages/harness/deerflow/persistence/oa_policy_flow_jobs/model.py',
    'packages/harness/deerflow/persistence/migrations/versions/0021_oa_result_sets.py',
    'packages/harness/deerflow/community/appforge/tools.py',
    'packages/harness/deerflow/community/appforge/data_qa_stream.py',
    'packages/harness/deerflow/community/appforge/data_qa_progress.py',
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def build(raw):
    assert sha(raw) == IMPORTER_SHA, 'QUALIFIED_IMPORTER_CHANGED'
    spec = importlib.util.spec_from_file_location('oci_helpers', ROOT / 'deploy/enterprise/package-glm-stream-preload.py')
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    source = raw.decode().replace('\r\n', '\n')
    sources = helper.literal(source, 'SOURCES')
    profile = json.loads(sources['profile'])
    blobs = json.loads(sources['blobs'])
    old = profile['roles'][0]
    assert old['manifest_digest'] == BASE
    mr = base64.b64decode(blobs[BASE], validate=True)
    cr = base64.b64decode(blobs[old['config_digest']], validate=True)
    assert 'sha256:' + sha(mr) == BASE and 'sha256:' + sha(cr) == old['config_digest']
    manifest, config = json.loads(mr), json.loads(cr)
    assert config['architecture'] == 'arm64' and config['os'] == 'linux'
    assert len(manifest['layers']) == len(config['rootfs']['diff_ids']) == 67
    payload, files = io.BytesIO(), {}
    with tarfile.open(fileobj=payload, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for relative in sorted(PATHS):
            content = (ROOT / 'backend' / relative).read_text(encoding='utf-8').encode()
            compile(content, relative, 'exec')
            path = 'app/backend/' + relative
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(content), 0o644, 0
            archive.addfile(info, io.BytesIO(content))
            files[path] = {'bytes': len(content), 'sha256': sha(content)}
    expanded = payload.getvalue()
    packed = io.BytesIO()
    with gzip.GzipFile(fileobj=packed, mode='wb', filename='', mtime=0) as stream:
        stream.write(expanded)
    layer = packed.getvalue()
    ld, diff = 'sha256:' + sha(layer), 'sha256:' + sha(expanded)
    new_config = copy.deepcopy(config)
    new_config['rootfs']['diff_ids'].append(diff)
    new_config['history'].append({'created': '2026-10-21T10:20:00Z', 'created_by': 'OA opt-in result sets and independent budget; Data QA live progress'})
    ncr = canonical(new_config)
    cd = 'sha256:' + sha(ncr)
    new_manifest = copy.deepcopy(manifest)
    new_manifest['config'].update(digest=cd, size=len(ncr))
    new_manifest['layers'].append({'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': ld, 'size': len(layer)})
    nmr = canonical(new_manifest)
    md = 'sha256:' + sha(nmr)
    tag = 'deerflow.local/deer-flow-gateway:20261021-oa-data-qa-r1-' + md[7:19]
    chain = ''
    for item in config['rootfs']['diff_ids']:
        chain = item if not chain else 'sha256:' + sha((chain + ' ' + item).encode())
    profile['schema'] = 'portal-gateway-oa-data-qa-profile.v1'
    profile['bases']['gateway'] = {'config_bytes': len(cr), 'config_digest': old['config_digest'],
        'config_object_sha256': sha(canonical(config)), 'descriptor_prefix_sha256': sha(canonical(manifest['layers'])),
        'diff_prefix_sha256': sha(canonical(config['rootfs']['diff_ids'])), 'final_chain_id': chain,
        'layer_count': 67, 'manifest_bytes': len(mr), 'manifest_digest': BASE,
        'runtime_config_sha256': old['runtime_config_sha256'], 'tag': old['tag']}
    profile['preserve']['gateway'].update({old['tag']: BASE, 'deerflow.local/deer-flow-gateway@' + BASE: BASE})
    profile['roles'] = [{'base': 'gateway', 'role': 'gateway-forward', 'tag': tag, 'config_digest': cd,
        'manifest_digest': md, 'expected_layer_count': 68, 'runtime_config_sha256': old['runtime_config_sha256'],
        'tail_layers': [{'blob_digest': ld, 'compressed_bytes': len(layer), 'diff_id': diff, 'position': 68, 'uncompressed_bytes_upper_bound': len(expanded)}]}]
    profile['tail_files'] = {'gateway-forward': {'files': files, 'uncompressed_bytes': len(expanded)}}
    evidence = json.loads((ROOT / 'deploy/enterprise/oa-v2-field-observation-20261021-r2.json').read_text(encoding='utf-8'))
    qualification = {'oa_child_check': evidence, 'base_manifest': BASE, 'source_files': files,
        'production_deployed': False, 'preload_only': True, 'ready_for_rollout': False,
        'joint_image_runtime_acceptance_pending': True, 'production_schema_migration_pending': True,
        'oa_receiver_acceptance_pending': True}
    sources['profile'] = canonical(profile)
    sources['blobs'] = canonical({d: base64.b64encode(b).decode() for d, b in ((md, nmr), (cd, ncr), (ld, layer))})
    source = source.replace('portal-gateway-data-qa-glm', 'portal-gateway-oa-data-qa')
    source = source.replace('GATEWAY_DATA_QA_GLM', 'GATEWAY_OA_DATA_QA').replace('--preload-gateway-data-qa-glm-once', '--preload-gateway-oa-data-qa-once')
    for key, value in {'SOURCES': sources, 'SOURCE_PINS': {k: {'bytes': len(v), 'sha256': sha(v)} for k, v in sources.items()},
                       'ROUTE_PIN': files, 'PRODUCT_QUALIFICATION': qualification,
                       'PAYLOAD_LIMITS': {p: 262144 for p in files}, 'TAIL_LIMIT': 512 * 1024}.items():
        source = helper.replace_node(source, key, key + ' = ' + repr(value))
    source = helper.replace_node(source, 'accepted_nfs_gate',
        'def accepted_nfs_gate():\n    require(PRODUCT_QUALIFICATION == ' + repr(qualification) + ", 'candidate_evidence_changed')\n    require(ROUTE_PIN == " + repr(files) + ", 'candidate_source_changed')", function=True)
    ast.parse(source, feature_version=(3, 7))
    compile(source, '<oa-data-qa-preload>', 'exec')
    receipt = {'schema': 'portal-oa-data-qa-release-build.v1', 'profile': profile, 'qualification': qualification,
               'script_sha256': sha(source.encode()), 'script_bytes': len(source.encode()),
               'importer_source_sha256': IMPORTER_SHA, 'source_file_count': len(files),
               'base_layers_preserved': True, 'configuration_fields_preserved': True, 'ready_for_rollout': False}
    return source.encode(), receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified-importer', required=True, type=pathlib.Path)
    parser.add_argument('--output', required=True, type=pathlib.Path)
    parser.add_argument('--receipt', required=True, type=pathlib.Path)
    args = parser.parse_args()
    output, receipt = build(args.qualified_importer.read_bytes())
    with args.output.open('xb') as stream:
        stream.write(output)
    with args.receipt.open('xb') as stream:
        stream.write(canonical(receipt) + b'\n')
    print(json.dumps({'script_bytes': len(output), 'manifest': receipt['profile']['roles'][0]['manifest_digest'], 'ready_for_rollout': False}))
