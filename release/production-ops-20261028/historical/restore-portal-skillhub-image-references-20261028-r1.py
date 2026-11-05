#!/usr/bin/env python3
"""Restore two lost local SkillHub image names after validating historical OCI content.

Python 3.7+. No pull/import, Deployment patch, Pod deletion, Nginx reload or
business request. Existing pending SkillHub Pod may start automatically.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import socket
import subprocess
import threading
import time

IMAGES = [
    {'role': 'server', 'root': 'sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2', 'target': 'deerflow.local/skillhub-server:20260908-enterprise-arm64-rc15-skillhubsession2'},
    {'role': 'wait-for-dependencies', 'root': 'sha256:1805d663453e3ece129a60ca48d2c7d1054778c0abf4882527cc64071295180c', 'target': 'deerflow.local/skillhub-busybox:20260905-enterprise-arm64-rc15-roleexpertskillhub1'},
]
for image in IMAGES:
    image['source'] = 'import-2026-10-09@' + image['root']
UID = '009babb5-b084-4397-a5ee-56a4b4b91032'
EXPECTED_SPEC = 'dc58fef71aaf1f8dc3b1dcc5b12ab6d376ed25208cf58fd3c511dcd7f4685b6f'
POD_UID = '04e4804a-f04e-49e2-b181-bbdfb276245f'
SERVICE_UID = '9c0ec531-1b1d-4c4d-8616-e016cf3b3725'
NODE_UID = 'ec5698b0-8d82-4eed-bae0-3711c7b7f36c'
REPORT_DIR = pathlib.Path('/var/lib/portal-skillhub-reference-recovery-20261028-r1')
K = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=12s', '-n', 'deer-flow']
C = ['ctr', '-n', 'k8s.io']


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def command(args, limit=4194304):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    require(p.returncode == 0, 'COMMAND_FAILED')
    require(len(p.stdout) <= limit, 'OUTPUT_LIMIT')
    return p.stdout


def get(kind, name):
    return json.loads(command(K + ['get', kind, name, '-o', 'json']))


def fingerprint(d):
    return {'uid': d['metadata']['uid'], 'generation': d['metadata'].get('generation'),
            'spec_sha256': hashlib.sha256(json.dumps(d['spec'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def refs():
    result = {}
    for line in command(C + ['images', 'ls'], 16777216).decode().splitlines():
        v = line.split()
        if len(v) >= 3 and re.fullmatch(r'sha256:[0-9a-f]{64}', v[2]):
            result[v[0]] = v[2]
    return result


def descriptor(d):
    require(isinstance(d, dict) and re.fullmatch(r'sha256:[0-9a-f]{64}', str(d.get('digest', ''))) is not None, 'INVALID_DESCRIPTOR')
    require(type(d.get('size')) is int and 0 < d['size'] <= 8589934592, 'INVALID_BLOB_SIZE')
    return d['digest'], d['size']


def metadata(digest, size=None):
    raw = command(C + ['content', 'get', digest], 2097152)
    require('sha256:' + hashlib.sha256(raw).hexdigest() == digest, 'METADATA_DIGEST_MISMATCH')
    require(size is None or len(raw) == size, 'METADATA_SIZE_MISMATCH')
    return json.loads(raw)


def verify_blob(d):
    digest, size = descriptor(d)
    p = subprocess.Popen(C + ['content', 'get', digest], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(180, p.kill)
    timer.daemon = True
    timer.start()
    h = hashlib.sha256()
    count = 0
    try:
        while True:
            chunk = p.stdout.read(1048576)
            if not chunk:
                break
            count += len(chunk)
            require(count <= size, 'LAYER_SIZE_EXCEEDED')
            h.update(chunk)
        require(p.wait(timeout=5) == 0, 'LAYER_READ_FAILED_OR_TIMEOUT')
        require(count == size and 'sha256:' + h.hexdigest() == digest, 'LAYER_CONTENT_MISMATCH')
    finally:
        timer.cancel()
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()
    return count


def verify_content(image):
    root = image['root']
    index = metadata(root)
    matches = [d for d in index.get('manifests', []) if d.get('platform', {}).get('os') == 'linux' and d.get('platform', {}).get('architecture') == 'arm64']
    require(len(matches) == 1, 'ARM64_MANIFEST_AMBIGUOUS')
    digest, size = descriptor(matches[0])
    manifest = metadata(digest, size)
    config_digest, config_size = descriptor(manifest['config'])
    config = metadata(config_digest, config_size)
    require(config.get('architecture') == 'arm64' and config.get('os') == 'linux', 'CONFIG_PLATFORM_MISMATCH')
    layers = manifest['layers']
    require(isinstance(layers, list) and 0 < len(layers) <= 128, 'LAYER_COUNT_INVALID')
    require(sum(descriptor(d)[1] for d in layers) <= 21474836480, 'TOTAL_LAYER_LIMIT')
    total = 0
    for n, d in enumerate(layers, 1):
        total += verify_blob(d)
        print(json.dumps({'phase': 'VERIFY_HISTORICAL_IMAGE_LAYERS', 'role': image['role'], 'checked': n, 'total': len(layers)}), flush=True)
    return {'index_digest': root, 'manifest_digest': digest, 'config_digest': config_digest,
            'verified_layers': len(layers), 'verified_compressed_bytes': total, 'status': 'PASS'}


def validate_deployment(d):
    require(d['metadata']['uid'] == UID and d['metadata']['generation'] == 14, 'SKILLHUB_BASELINE_CHANGED')
    require(fingerprint(d)['spec_sha256'] == EXPECTED_SPEC, 'SKILLHUB_SPEC_CHANGED')
    require(d['spec']['replicas'] == 1 and not d['spec'].get('paused'), 'SKILLHUB_REPLICAS_CHANGED')
    spec = d['spec']['template']['spec']
    cs = spec.get('containers', []) + spec.get('initContainers', [])
    require(len(cs) == 2, 'CONTAINER_LAYOUT_CHANGED')
    for image in IMAGES:
        found = [c for c in cs if c['name'] == image['role']]
        require(len(found) == 1 and found[0]['image'] == image['target'] and found[0].get('imagePullPolicy') == 'Never', 'SKILLHUB_IMAGE_CHANGED')


def active_pods(d):
    selector = ','.join(k + '=' + v for k, v in sorted(d['spec']['selector']['matchLabels'].items()))
    items = json.loads(command(K + ['get', 'pods', '-l', selector, '-o', 'json']))['items']
    active = [p for p in items if p.get('status', {}).get('phase') not in ('Failed', 'Succeeded')]
    require(len(active) == 1 and all(p['spec'].get('nodeName') == 'datax-01' and not p['metadata'].get('deletionTimestamp') for p in active), 'ACTIVE_POD_LAYOUT_CHANGED')
    # Confirm controller chain, rather than trusting labels alone.
    owners = {}
    for p in active:
        refs_ = [o for o in p['metadata'].get('ownerReferences', []) if o.get('controller')]
        require(len(refs_) == 1 and refs_[0]['kind'] == 'ReplicaSet', 'POD_OWNER_CHANGED')
        o = refs_[0]
        if o['name'] not in owners:
            owners[o['name']] = get('replicaset', o['name'])
        rs = owners[o['name']]
        require(rs['metadata']['uid'] == o['uid'] and any(x.get('controller') and x.get('kind') == 'Deployment' and x.get('uid') == UID for x in rs['metadata'].get('ownerReferences', [])), 'CONTROLLER_CHAIN_CHANGED')
    require(active[0]['metadata']['uid'] == POD_UID, 'PENDING_POD_REPLACED')
    return active


def observe(before, content):
    deadline = time.monotonic() + 360
    while True:
        d = get('deployment', 'skillhub-server')
        require(fingerprint(d) == before, 'SKILLHUB_SPEC_CHANGED')
        p = active_pods(d)[0]
        accepted = False
        cs = p.get('status', {}).get('containerStatuses', [])
        init = p.get('status', {}).get('initContainerStatuses', [])
        if len(cs) == 1 and len(init) == 1 and cs[0].get('ready') and cs[0].get('state', {}).get('running') and init[0].get('state', {}).get('terminated', {}).get('exitCode') == 0:
            for c in cs + init:
                v = content[c['name']]
                iid = c.get('imageID', '')
                require(any(iid == value or iid.endswith('@' + value) or iid.endswith('://' + value) for value in (v['index_digest'], v['manifest_digest'], v['config_digest'])), 'RUNNING_IMAGE_ID_MISMATCH')
            accepted = True
        service = get('service', 'skillhub-server')
        require(service['metadata']['uid'] == SERVICE_UID, 'SERVICE_IDENTITY_CHANGED')
        slices = json.loads(command(K + ['get', 'endpointslices', '-l', 'kubernetes.io/service-name=skillhub-server', '-o', 'json']))['items']
        ready = {e.get('targetRef', {}).get('uid') for s in slices for e in s.get('endpoints', []) if e.get('conditions', {}).get('ready') is True and not e.get('conditions', {}).get('terminating')}
        print(json.dumps({'phase': 'OBSERVE_EXISTING_SKILLHUB_POD', 'accepted_pods': int(accepted), 'ready_endpoints': len(ready)}), flush=True)
        if accepted and ready == {POD_UID} and d.get('status', {}).get('readyReplicas') == 1:
            return {'pod': p['metadata']['name'], 'uid': POD_UID, 'containers': [{'name': c['name'], 'image_id': c['imageID']} for c in cs + init]}
        require(time.monotonic() < deadline, 'OBSERVATION_TIMEOUT_DO_NOT_RETAG_OR_DELETE_PODS')
        time.sleep(15)


def main(out):
    require(socket.gethostname().split('.')[0] == 'datax-01', 'WRONG_HOST')
    require(os.geteuid() == 0, 'ROOT_REQUIRED')
    node = get('node', 'datax-01')
    require(node['metadata']['uid'] == NODE_UID and node['status']['nodeInfo']['architecture'] == 'arm64', 'NODE_IDENTITY_CHANGED')
    require(not REPORT_DIR.exists(), 'PRIOR_REPORT_PRESENT_DO_NOT_REPEAT')
    d = get('deployment', 'skillhub-server')
    validate_deployment(d)
    active_pods(d)
    service = get('service', 'skillhub-server')
    require(service['metadata']['uid'] == SERVICE_UID, 'SERVICE_IDENTITY_CHANGED')
    out['before'] = fingerprint(d)
    protected = {n: fingerprint(get('deployment', n)) for n in ('gateway', 'gateway-run-dispatcher', 'frontend', 'nginx-security-heai-20261012-r1', 'skillhub-web', 'skillhub-scanner')}
    images = refs()
    for image in IMAGES:
        require(images.get(image['source']) == image['root'], 'HISTORICAL_REFERENCE_CHANGED')
        require(images.get(image['target']) in (None, image['root']), 'TARGET_REFERENCE_CONFLICT')
    # Verify BOTH images completely before restoring either name.
    out['content_verification'] = {image['role']: verify_content(image) for image in IMAGES}
    require(fingerprint(get('deployment', 'skillhub-server')) == out['before'], 'BASELINE_CHANGED_DURING_CHECK')
    active_pods(d)
    images = refs()
    for image in IMAGES:
        require(images.get(image['source']) == image['root'] and images.get(image['target']) in (None, image['root']), 'IMAGE_REFERENCES_CHANGED_DURING_CHECK')
    REPORT_DIR.mkdir(mode=0o700)
    out['once_claimed'] = True
    out['tags'] = []
    # Main image first; releasing the missing init image may start the service.
    for image in IMAGES:
        row = {'role': image['role'], 'target': image['target'], 'digest': image['root'], 'tag_request_may_have_applied': False}
        out['tags'].append(row)
        if image['target'] not in images:
            row['tag_request_may_have_applied'] = True
            out['tag_request_may_have_applied'] = True
            command(C + ['images', 'tag', image['source'], image['target']])
            row['tag_command_accepted'] = True
        require(refs().get(image['target']) == image['root'], 'TAG_READBACK_MISMATCH')
        row['target_reference_verified'] = True
    out['accepted_pod'] = observe(out['before'], out['content_verification'])
    require(all(fingerprint(get('deployment', n)) == v for n, v in protected.items()), 'OTHER_DEPLOYMENT_CHANGED')
    out['other_deployment_specs_preserved'] = True
    out['status'] = 'SKILLHUB_REFERENCES_RESTORED_RUNTIME_READY_BROWSER_PENDING'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-skillhub-references-once', action='store_true', required=True)
    parser.parse_args()
    result = {'schema': 'portal-skillhub-reference-recovery.v1', 'status': 'INCOMPLETE',
              'deployment_patch_requests': 0, 'pod_delete_requests': 0, 'nginx_reload_requests': 0,
              'image_pull_or_import_requests': 0, 'business_requests_by_tool': 0, 'database_queries_by_tool': 0,
              'database_access_via_ordinary_service_startup_possible': True,
              'automatic_retry': False, 'automatic_rollback': False, 'once_claimed': False,
              'tag_request_may_have_applied': False, 'browser_acceptance_pending': True,
              'existing_pending_pods_may_start': True}
    try:
        main(result)
    except Exception as exc:
        result['error_class'] = type(exc).__name__
        result['error_code'] = str(exc) if isinstance(exc, Refused) else 'EXECUTION_NOT_ACCEPTED'
    if result['once_claimed']:
        path = REPORT_DIR / 'REPORT.json'
        with path.open('x', encoding='utf-8') as f:
            json.dump(result, f, sort_keys=True)
        result['report_path'] = str(path)
        result['report_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    print(json.dumps(result, sort_keys=True), flush=True)
    raise SystemExit(0 if result['status'] != 'INCOMPLETE' else 1)
