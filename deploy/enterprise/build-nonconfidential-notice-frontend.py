"""Build and qualify the sealed source snapshot; no push or production action."""
import argparse
import json
from pathlib import Path
import sys
import subprocess
import time
import uuid
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import frontend_slim as slim


def qualify(image_id, platform, out):
    """Use the same runtime assertions with a bounded emulated-ARM startup budget."""
    layout = json.loads(slim.capture(['docker', 'run', '--rm', '--pull=never', '--platform', platform,
        '--network=none', '--entrypoint', 'node', image_id, '/opt/deerflow/verify-standalone.mjs',
        '/app/frontend', '--assets'], timeout=120))
    name = 'notice-frontend-qa-' + uuid.uuid4().hex[:12]
    cid = None
    try:
        cid = slim.capture(['docker', 'run', '-d', '--name', name, '--pull=never', '--platform', platform,
            '--network=none', '--cpus=2', '--memory=2g', '--pids-limit=256',
            '-e', 'WEKNORA_FRONTEND_URL=/platform/knowledge-bases', '-e', 'DEER_FLOW_SKILLHUB_ENABLED=true',
            '-e', 'DEER_FLOW_SSO_ONLY=true', '-e', 'DEER_FLOW_UNAUTHENTICATED_HOME_URL=https://sso.invalid/entry',
            image_id]).decode().strip()
        deadline = time.monotonic() + 180
        startup = []
        while time.monotonic() < deadline:
            try:
                result = subprocess.run(['docker', 'exec', cid, 'node', '-e',
                    "fetch('http://127.0.0.1:3000/api/runtime-config',{signal:AbortSignal.timeout(5000)}).then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=min(45, deadline-time.monotonic()))
                startup.append({'returncode': result.returncode})
                if result.returncode == 0:
                    break
            except subprocess.TimeoutExpired:
                startup.append({'status': 'LOCAL_STARTUP_TIMEOUT'})
            time.sleep(2)
        else:
            raise slim.CheckFailure('runtime_start_deadline')
        probe = json.loads(slim.capture(['docker', 'exec', cid, 'node', '-e', slim.HTTP_PROBE], timeout=90))
        value = {'layout': layout, 'http': probe, 'bounded_startup_observations': startup}
        slim.write_json(out / 'RUNTIME.json', value)
        return value
    finally:
        if cid:
            slim.capture(['docker', 'rm', '-f', cid])  # Only this local synthetic fixture.


def build(prepared, tag='deerflow.local/deer-flow-frontend:20261023-nonconfidential-notice-r2'):
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
        tag=tag,
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
        result['runtime'] = qualify(image['Id'], args.platform, out)
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
    parser.add_argument('--tag', default='deerflow.local/deer-flow-frontend:20261023-nonconfidential-notice-r2')
    args = parser.parse_args()
    build(args.prepared, args.tag)
