"""Build and qualify the sealed source snapshot; no push or production action."""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import frontend_slim as slim


def build(prepared):
    record_raw = (prepared / 'SOURCE-CHANGE.json').read_bytes()
    record = json.loads(record_raw)
    source = Path(record['source'])
    rows = record['source_inventory']
    for row in rows:
        raw = (source / row['path']).read_bytes()
        slim.require(len(raw) == row['bytes'] and slim.sha(raw) == row['sha256'], 'prepared_source_changed')
    out = prepared / 'build'
    out.mkdir()
    args = SimpleNamespace(
        node_image='node@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402',
        tag='deerflow.local/deer-flow-frontend:20261022-data-qa-token-r1',
        platform='linux/arm64', builder='desktop-linux', gateway_url='http://gateway:8001',
        app_version='', npm_registry='', enterprise_ca=None, enterprise_proxy=None)
    result = {'source_change_sha256': slim.sha(record_raw), 'source': str(source),
              'production_writes': 0, 'images_pushed': False, 'ready_for_rollout': False}
    try:
        base = slim.inspect_platform_base(args.node_image, args.platform)
        scope = 'deerflow-slim-v1-' + slim.sha((args.node_image + slim.sha((source / 'frontend/pnpm-lock.yaml').read_bytes())).encode())[:20]
        command = slim.build_command(args, source, out / 'METADATA.json', scope)
        result['build_seconds'] = slim.run_build(command, out)
        image = json.loads(slim.capture(['docker', 'image', 'inspect', args.tag]))[0]
        slim.require(image['Architecture'] == 'arm64' and image['Size'] < 1536 * 1024**2, 'runtime_image_scope')
        result['image'] = {'tag': args.tag, 'id': image['Id'], 'bytes': image['Size'], 'layers': len(image['RootFS']['Layers'])}
        result['fresh_runtime'] = slim.verify_fresh_layers(base, image)
        result['runtime'] = slim.qualify(image['Id'], args.platform, out)
        command = slim.build_command(args, source, out / 'EXPORT-METADATA.json', scope)
        command.remove('--load')
        command[-1:-1] = ['--output', 'type=oci,dest=' + str(out / 'frontend.oci.tar')]
        result['export_seconds'] = slim.run_build(command, out, 'EXPORT.log')
        result['export'] = slim.audit_oci(out / 'frontend.oci.tar', image)
        result['status'] = 'LOCAL_RUNTIME_AND_OCI_ACCEPTED_NOT_DEPLOYED'
    finally:
        slim.write_json(out / 'RESULT.json', result)
    print(json.dumps({'status': result['status'], 'result': str(out / 'RESULT.json'), 'production_writes': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared', type=Path, required=True)
    build(parser.parse_args().prepared)
