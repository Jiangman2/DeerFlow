"""Build and qualify the sealed frontend presentation snapshot locally, without push/deploy."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
import frontend_slim as slim

NODE = 'node@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402'
TAG = 'deerflow.local/deer-flow-frontend:20261028-frontend-polish-r1'


def verify_source(record):
    root = Path(record['source'])
    for row in record['source_inventory']:
        file = root / row['path']
        slim.require(not file.is_symlink(), 'snapshot_link')
        raw = file.read_bytes()
        slim.require(len(raw) == row['bytes'] and slim.sha(raw) == row['sha256'], 'snapshot_changed:' + row['path'])
    return root


def qualify_emulated(image_id, out):
    """Keep HTTP assertions intact; allow bounded Docker/ARM emulation startup time."""
    layout = json.loads(slim.capture(['docker', 'run', '--rm', '--pull=never', '--platform', 'linux/arm64',
        '--network=none', '--entrypoint', 'node', image_id, '/opt/deerflow/verify-standalone.mjs',
        '/app/frontend', '--assets'], timeout=120))
    name = 'deerflow-polish-qa-' + uuid.uuid4().hex[:12]
    cid = None
    try:
        cid = slim.capture(['docker', 'run', '-d', '--name', name, '--pull=never', '--platform', 'linux/arm64',
            '--network=none', '--cpus=2', '--memory=2g', '--pids-limit=256',
            '-e', 'WEKNORA_FRONTEND_URL=/platform/knowledge-bases', '-e', 'DEER_FLOW_SKILLHUB_ENABLED=true',
            '-e', 'DEER_FLOW_SSO_ONLY=true', '-e', 'DEER_FLOW_UNAUTHENTICATED_HOME_URL=https://sso.invalid/entry',
            image_id], timeout=60).decode().strip()
        deadline = time.monotonic() + 240
        attempts = 0
        while time.monotonic() < deadline:
            attempts += 1
            probe = "fetch('http://127.0.0.1:3000/api/runtime-config',{signal:AbortSignal.timeout(10000)}).then(r=>process.exit(r.status===200?0:1)).catch(()=>process.exit(1))"
            try:
                r = subprocess.run(['docker', 'exec', cid, 'node', '-e', probe],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=min(60, max(1, deadline-time.monotonic())))
            except subprocess.TimeoutExpired:
                raise slim.CheckFailure('emulated_probe_process_timeout')
            if r.returncode == 0:
                break
            time.sleep(2)
        else:
            raise slim.CheckFailure('emulated_runtime_start_deadline')
        http = json.loads(slim.capture(['docker', 'exec', cid, 'node', '-e', slim.HTTP_PROBE], timeout=120))
        result = dict(layout=layout, http=http, startup_attempts=attempts,
                      docker_exec_timeout_seconds=60, individual_http_timeout_seconds=10,
                      platform='linux/arm64', network='none')
        slim.write_json(out / 'RUNTIME.json', result)
        return result
    finally:
        if cid:
            slim.capture(['docker', 'rm', '-f', cid], timeout=60)


def resume_qualification(snapshot, previous, output):
    """Recheck the exact built image after a local qualification timeout; no rebuild."""
    sealed_raw = (snapshot / 'SOURCE-CHANGE.json').read_bytes()
    sealed = json.loads(sealed_raw)
    root = verify_source(sealed)
    prior_raw = (previous / 'RESULT.json').read_bytes()
    prior = json.loads(prior_raw)
    slim.require(prior.get('status') == 'INCOMPLETE' and prior.get('error_class') == 'TimeoutExpired'
                 and prior.get('source_change_sha256') == slim.sha(sealed_raw), 'unexpected_prior_qualification')
    slim.require(not output.exists(), 'fresh_qualification_directory_required')
    image = json.loads(slim.capture(['docker', 'image', 'inspect', TAG], timeout=60))[0]
    slim.require(image['Id'] == prior['image']['id'] and image['Architecture'] == 'arm64'
                 and len(image['RootFS']['Layers']) == 11, 'built_image_changed')
    metadata = json.loads((previous / 'METADATA.json').read_bytes())
    slim.require(metadata['containerimage.digest'] == image['Id'], 'build_metadata_mismatch')
    output.mkdir()
    result = dict(source_change_sha256=slim.sha(sealed_raw), source=str(root),
                  prior_build_receipt=dict(path=str(previous / 'RESULT.json'), sha256=slim.sha(prior_raw)),
                  build_seconds=prior['build_seconds'], image=prior['image'], production_writes=0,
                  images_pushed=False, ready_for_rollout=False, status='INCOMPLETE')
    try:
        result['fresh_runtime'] = slim.verify_fresh_layers(slim.inspect_platform_base(NODE, 'linux/arm64'), image)
        result['runtime'] = qualify_emulated(image['Id'], output)
        args = SimpleNamespace(builder='desktop-linux', platform='linux/arm64', node_image=NODE,
            gateway_url='http://gateway:8001', app_version='', tag=TAG, npm_registry='', enterprise_ca=None, enterprise_proxy=None)
        scope = 'deerflow-slim-v1-' + slim.sha((NODE + slim.sha((root / 'frontend/pnpm-lock.yaml').read_bytes())).encode())[:20]
        command = slim.build_command(args, root, output / 'EXPORT-METADATA.json', scope)
        command.remove('--load')
        command[-1:-1] = ['--output', 'type=oci,dest=' + str(output / 'frontend.oci.tar')]
        result['export_seconds'] = slim.run_build(command, output, 'EXPORT.log')
        result['export'] = slim.audit_oci(output / 'frontend.oci.tar', image)
        verify_source(sealed)
        result['status'] = 'LOCAL_RUNTIME_AND_OCI_ACCEPTED_NOT_DEPLOYED'
    except Exception as exc:
        result['error_class'] = type(exc).__name__
        if isinstance(exc, slim.CheckFailure):
            result['error_code'] = str(exc)
        raise
    finally:
        slim.write_json(output / 'RESULT.json', result)
        print(json.dumps(dict(status=result['status'], result=str(output / 'RESULT.json'), production_writes=0)), flush=True)


def build(snapshot, output=None):
    record_raw = (snapshot / 'SOURCE-CHANGE.json').read_bytes()
    record = json.loads(record_raw)
    slim.require(record['base_generation'] == 229 and record['backend_included'] is False
                 and record['work_candidate_included'] is False and record['local_preview_fixtures_included'] is False,
                 'unexpected_scope')
    root = verify_source(record)
    out = output or snapshot / 'build'
    slim.require(not out.exists(), 'fresh_build_directory_required')
    exists = subprocess.run(['docker', 'image', 'inspect', TAG], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    slim.require(exists.returncode != 0, 'tag_already_exists')
    out.mkdir()
    args = SimpleNamespace(builder='desktop-linux', platform='linux/arm64', node_image=NODE,
                           gateway_url='http://gateway:8001', app_version='',
                           tag=TAG, npm_registry='', enterprise_ca=None, enterprise_proxy=None)
    result = dict(source_change_sha256=slim.sha(record_raw), source=str(root), production_writes=0,
                  images_pushed=False, ready_for_rollout=False, status='INCOMPLETE')
    try:
        scope = 'deerflow-slim-v1-' + slim.sha((NODE + slim.sha((root / 'frontend/pnpm-lock.yaml').read_bytes())).encode())[:20]
        command = slim.build_command(args, root, out / 'METADATA.json', scope)
        result['build_seconds'] = slim.run_build(command, out)
        image = json.loads(slim.capture(['docker', 'image', 'inspect', TAG]))[0]
        slim.require(image['Architecture'] == 'arm64' and len(image['RootFS']['Layers']) == 11, 'runtime_architecture_or_layers')
        result['image'] = dict(tag=TAG, id=image['Id'], bytes=image['Size'], layers=len(image['RootFS']['Layers']))
        result['fresh_runtime'] = slim.verify_fresh_layers(slim.inspect_platform_base(NODE, args.platform), image)
        result['runtime'] = slim.qualify(image['Id'], args.platform, out)
        command = slim.build_command(args, root, out / 'EXPORT-METADATA.json', scope)
        command.remove('--load')
        command[-1:-1] = ['--output', 'type=oci,dest=' + str(out / 'frontend.oci.tar')]
        result['export_seconds'] = slim.run_build(command, out, 'EXPORT.log')
        result['export'] = slim.audit_oci(out / 'frontend.oci.tar', image)
        verify_source(record)
        result['status'] = 'LOCAL_RUNTIME_AND_OCI_ACCEPTED_NOT_DEPLOYED'
    except Exception as exc:
        result['error_class'] = type(exc).__name__
        if isinstance(exc, slim.CheckFailure):
            result['error_code'] = str(exc)
        raise
    finally:
        slim.write_json(out / 'RESULT.json', result)
        print(json.dumps(dict(status=result['status'], result=str(out / 'RESULT.json'), production_writes=0)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--resume-qualification', type=Path)
    args = parser.parse_args()
    if args.resume_qualification:
        if args.out is None:
            parser.error('--out is required for qualification')
        resume_qualification(args.snapshot, args.resume_qualification, args.out)
    else:
        build(args.snapshot, args.out)
