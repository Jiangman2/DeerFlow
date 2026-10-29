#!/usr/bin/env python3
"""Restore the verified healthy-peer Alloy image on datax-01; no workload patches."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import tarfile
import time

MANIFEST = 'sha256:87a8fcab1a8bb39047ee25ccd749b5b89de1eeb41f8f85dac7b906894d1a11b8'
CONFIG = 'sha256:8b089d2834ca96f22974b0ea75528dbd3806c1bf38d67faa4483c72a2e5cab1e'
TARGET = 'docker.io/grafana/alloy:v1.11.0'
POD_UID = '6305d4cf-b74e-4b41-9e0b-74cae1b8b2a3'
NODE_UID = 'ec5698b0-8d82-4eed-bae0-3711c7b7f36c'
ARCHIVE_BYTES = 141445120
ARCHIVE_SHA256 = 'ead5e8e157b563aeb6fe11efc008621676a14b2d6fe6b1c565bf9a5a9679f839'
ARCHIVE = 'alloy.arm64.oci.tar'
CONTROL = Path('/var/lib/portal-alloy-image-recovery-20261028-r1')
K = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=12s']
C = ['ctr', '-n', 'k8s.io']


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def command(args, timeout=25, cap=16777216, pass_fds=()):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, pass_fds=pass_fds)
    require(p.returncode == 0, 'COMMAND_FAILED')
    require(len(p.stdout) <= cap, 'OUTPUT_LIMIT')
    return p.stdout


def get(kind, name=None, namespace='observability'):
    scope = [] if namespace is None else ['-n', namespace]
    return json.loads(command(K + scope + ['get', kind] + ([name] if name else []) + ['-o', 'json']))


def fingerprint(obj):
    m = obj['metadata']
    return {'uid': m['uid'], 'generation': m.get('generation'),
            'spec_sha256': hashlib.sha256(json.dumps(obj['spec'], sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def refs():
    result = {}
    for line in command(C + ['images', 'list']).decode().splitlines():
        fields = line.split()
        if len(fields) > 2 and re.fullmatch(r'sha256:[0-9a-f]{64}', fields[2]):
            result[fields[0]] = fields[2]
    return result


def hash_stream(stream, limit):
    h, size = hashlib.sha256(), 0
    while True:
        block = stream.read(1024**2)
        if not block:
            break
        size += len(block)
        require(size <= limit, 'BLOB_SIZE_LIMIT')
        h.update(block)
    return 'sha256:' + h.hexdigest(), size


def archive_metadata(tar, digest, expected_size=None):
    name = 'blobs/sha256/' + digest[7:]
    entry = tar.getmember(name)
    require(entry.isreg() and entry.size <= 2097152, 'METADATA_SHAPE')
    with tar.extractfile(entry) as stream:
        raw = stream.read()
    require('sha256:' + hashlib.sha256(raw).hexdigest() == digest, 'METADATA_HASH')
    require(expected_size is None or len(raw) == expected_size, 'METADATA_SIZE')
    return json.loads(raw)


def verify_archive(stream):
    stream.seek(0)
    digest, size = hash_stream(stream, 512 * 1024**2)
    require(size == ARCHIVE_BYTES and digest == 'sha256:' + ARCHIVE_SHA256, 'TRANSPORT_HASH_OR_SIZE')
    stream.seek(0)
    unpacked = 0
    with tarfile.open(fileobj=stream, mode='r:') as tar:
        entries = tar.getmembers()
        require(len(entries) <= 20 and len({x.name for x in entries}) == len(entries), 'ARCHIVE_ENTRY_COUNT')
        require(all(x.isreg() and (x.name in ('index.json', 'oci-layout') or re.fullmatch(r'blobs/sha256/[0-9a-f]{64}', x.name)) for x in entries), 'ARCHIVE_ENTRY_SCOPE')
        index = json.load(tar.extractfile('index.json'))
        require(len(index['manifests']) == 1, 'INDEX_SHAPE')
        descriptor = index['manifests'][0]
        require(descriptor['digest'] == MANIFEST and descriptor.get('annotations', {}).get('org.opencontainers.image.ref.name') == TARGET, 'INDEX_IDENTITY')
        manifest = archive_metadata(tar, MANIFEST, descriptor['size'])
        require(manifest['config']['digest'] == CONFIG, 'CONFIG_IDENTITY')
        config = archive_metadata(tar, CONFIG, manifest['config']['size'])
        require(config.get('os') == 'linux' and config.get('architecture') == 'arm64', 'PLATFORM_MISMATCH')
        layers, diffs = manifest['layers'], config['rootfs']['diff_ids']
        require(len(layers) == len(diffs) == 5, 'LAYER_COUNT')
        expected_names = {'index.json', 'oci-layout', 'blobs/sha256/' + MANIFEST[7:], 'blobs/sha256/' + CONFIG[7:]}
        for i, (layer, diff) in enumerate(zip(layers, diffs), 1):
            require(re.fullmatch(r'sha256:[0-9a-f]{64}', layer['digest']) is not None, 'LAYER_DIGEST')
            require(layer['mediaType'].endswith('tar.gzip'), 'LAYER_MEDIA_TYPE')
            name = 'blobs/sha256/' + layer['digest'][7:]
            expected_names.add(name)
            with tar.extractfile(name) as packed:
                observed, length = hash_stream(packed, layer['size'])
            require(observed == layer['digest'] and length == layer['size'], 'LAYER_HASH_OR_SIZE')
            with tar.extractfile(name) as packed, gzip.GzipFile(fileobj=packed, mode='rb') as raw:
                observed, length = hash_stream(raw, 2 * 1024**3)
            require(observed == diff, 'UNCOMPRESSED_LAYER_HASH')
            unpacked += length
            require(unpacked <= 2 * 1024**3, 'UNPACKED_TOTAL_LIMIT')
            print(json.dumps({'phase': 'VERIFY_ALLOY_OFFLINE_LAYER', 'checked': i, 'total': 5}), flush=True)
        require({x.name for x in entries} == expected_names, 'EXTRA_ARCHIVE_CONTENT')
    stream.seek(0)
    return {'manifest_digest': MANIFEST, 'config_digest': CONFIG, 'layers': 5,
            'compressed_archive_bytes': size, 'uncompressed_layer_bytes': unpacked, 'status': 'PASS'}


def accepted_image_id(value):
    return value in (CONFIG, MANIFEST, 'docker.io/grafana/alloy@' + MANIFEST, 'grafana/alloy@' + MANIFEST)


def pod_ready(pod):
    cs = pod.get('status', {}).get('containerStatuses') or []
    return (not pod['metadata'].get('deletionTimestamp') and pod.get('status', {}).get('phase') == 'Running' and
            any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in pod.get('status', {}).get('conditions', [])) and
            len(cs) == 1 and cs[0].get('ready') is True and accepted_image_id(cs[0].get('imageID')))


def preflight():
    n = get('node', 'datax-01', None)
    require(n['metadata']['uid'] == NODE_UID and n['status']['nodeInfo']['architecture'] == 'arm64', 'NODE_IDENTITY')
    require(any(c['type'] == 'Ready' and c['status'] == 'True' for c in n['status']['conditions']), 'NODE_NOT_READY')
    require(not any(c['type'].endswith('Pressure') and c['status'] == 'True' for c in n['status']['conditions']), 'NODE_PRESSURE')
    ds = get('daemonset', 'alloy')
    spec = ds['spec']['template']['spec']
    require(not spec.get('initContainers') and len(spec.get('containers', [])) == 1, 'ALLOY_LAYOUT_CHANGED')
    c = spec['containers'][0]
    require(c['name'] == 'alloy' and c['image'] in ('grafana/alloy:v1.11.0', TARGET), 'ALLOY_VERSION_CHANGED')
    require(c.get('imagePullPolicy') in ('IfNotPresent', 'Never'), 'ALLOY_PULL_POLICY_REQUIRES_REVIEW')
    pods = get('pods')['items']
    owned = [p for p in pods if any(o.get('controller') and o.get('kind') == 'DaemonSet' and o.get('uid') == ds['metadata']['uid'] for o in p['metadata'].get('ownerReferences', [])) and p.get('status', {}).get('phase') not in ('Failed', 'Succeeded')]
    target = [p for p in owned if p['spec'].get('nodeName') == 'datax-01' and not p['metadata'].get('deletionTimestamp')]
    require(len(target) == 1 and target[0]['metadata']['uid'] == POD_UID, 'ALLOY_TARGET_POD_CHANGED')
    peers = [p for p in owned if p['spec'].get('nodeName') != 'datax-01' and pod_ready(p)]
    require(peers, 'NO_MATCHING_HEALTHY_ALLOY_PEER')
    p = target[0]
    if not pod_ready(p):
        cs = p.get('status', {}).get('containerStatuses', [])
        require(len(cs) == 1 and cs[0].get('state', {}).get('waiting', {}).get('reason') in ('ImagePullBackOff', 'ErrImagePull', 'ErrImageNeverPull'), 'ALLOY_FAULT_CHANGED')
    return ds, p, [{'node': p['spec']['nodeName'], 'uid': p['metadata']['uid']} for p in peers]


def protect():
    return {name: fingerprint(get('deployment', name, 'deer-flow')) for name in ('gateway', 'frontend', 'ppt-master-runner', 'skillhub-server')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore-alloy-image-once', required=True, action='store_true')
    parser.parse_args()
    require(socket.gethostname().split('.')[0] == 'datax-01' and os.geteuid() == 0, 'HOST_OR_USER')
    os.nice(19)
    ionice = shutil.which('ionice', path='/usr/sbin:/usr/bin:/sbin:/bin')
    require(ionice is not None, 'IONICE_MISSING')
    command([ionice, '-c', '3', '-p', str(os.getpid())])
    ds, pod, peers = preflight()
    if pod_ready(pod):
        print(json.dumps({'status': 'ALLOY_ALREADY_READY_VERIFIED_NO_IMPORT', 'image_import_requests': 0, 'workload_patch_requests': 0}))
        return
    before, old_refs = fingerprint(ds), refs()
    protected = protect()
    require(TARGET not in old_refs or old_refs[TARGET] == MANIFEST, 'EXISTING_IMAGE_REFERENCE_CONFLICT')
    path = Path(__file__).resolve().parent / ARCHIVE
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        initial = os.fstat(fd)
        require(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1, 'ARCHIVE_NOT_SINGLE_REGULAR')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            verified = verify_archive(stream)
        require((os.fstat(fd).st_size, os.fstat(fd).st_mtime_ns, os.fstat(fd).st_ctime_ns) == (initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns), 'ARCHIVE_CHANGED')
        v = os.statvfs('/var/lib/containerd')
        require(v.f_bavail * v.f_frsize > 3 * 1024**3 + 2 * verified['uncompressed_layer_bytes'], 'IMAGEFS_HEADROOM')
        fresh_ds, fresh_pod, unused = preflight()
        require(fingerprint(fresh_ds) == before and fresh_pod['metadata']['uid'] == pod['metadata']['uid'], 'BASELINE_CHANGED')
        require(refs() == old_refs and protect() == protected, 'CONCURRENT_RUNTIME_OR_DEPLOYMENT_CHANGE')
        CONTROL.mkdir(mode=0o700)
        report = {'schema': 'portal-alloy-offline-recovery.v1', 'status': 'INCOMPLETE',
                  'before_daemonset': before, 'healthy_peers': peers, 'content_verification': verified,
                  'image_import_requests': 0, 'import_may_have_applied': False,
                  'workload_patch_requests': 0, 'pod_delete_requests': 0, 'service_restart_requests': 0,
                  'database_access': False, 'business_requests_by_tool': 0,
                  'ordinary_collector_may_start': True, 'automatic_retry': False}
        def save():
            raw = (json.dumps(report, sort_keys=True) + '\n').encode()
            with (CONTROL / 'REPORT.tmp').open('wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(CONTROL / 'REPORT.tmp'), str(CONTROL / 'REPORT.json'))
            return hashlib.sha256(raw).hexdigest()
        try:
            report['image_import_requests'] = 1
            report['import_may_have_applied'] = True
            save()
            print(json.dumps({'phase': 'IMPORT_MATCHING_ALLOY_IMAGE'}), flush=True)
            require((os.fstat(fd).st_size, os.fstat(fd).st_mtime_ns, os.fstat(fd).st_ctime_ns) == (initial.st_size, initial.st_mtime_ns, initial.st_ctime_ns), 'ARCHIVE_CHANGED_BEFORE_IMPORT')
            os.lseek(fd, 0, os.SEEK_SET)
            command(C + ['images', 'import', '--platform', 'linux/arm64', '/proc/self/fd/' + str(fd)], timeout=240, pass_fds=(fd,))
            after_refs = refs()
            require(after_refs.get(TARGET) == MANIFEST, 'IMPORTED_REFERENCE_NOT_ACCEPTED')
            require(all(after_refs.get(k) == v for k, v in old_refs.items()), 'PRIOR_REFERENCES_CHANGED')
            report['old_image_references_preserved'] = True
            report['image_import_accepted'] = True
            until = time.monotonic() + 360
            while True:
                current = get('pod', pod['metadata']['name'])
                require(current['metadata']['uid'] == POD_UID, 'POD_REPLACED_DURING_OBSERVATION')
                require(fingerprint(get('daemonset', 'alloy')) == before and protect() == protected, 'WORKLOAD_SPEC_CHANGED_DURING_OBSERVATION')
                if pod_ready(current):
                    report.update(status='ALLOY_EXISTING_POD_READY_IMAGE_RECOVERED', accepted_pod={'name': current['metadata']['name'], 'uid': POD_UID, 'image_id': current['status']['containerStatuses'][0]['imageID']}, runtime_accepted=True)
                    break
                require(time.monotonic() < until, 'ALLOY_READINESS_OBSERVATION_TIMEOUT')
                print(json.dumps({'phase': 'OBSERVE_ORIGINAL_ALLOY_POD', 'ready': False}), flush=True)
                time.sleep(10)
            report['protected_deployment_specs_preserved'] = protect() == protected
            report['daemonset_spec_preserved'] = fingerprint(get('daemonset', 'alloy')) == before
        except Exception as exc:
            report['error_code'] = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        digest = save()
        print(json.dumps(dict(report, report_path=str(CONTROL / 'REPORT.json'), report_sha256=digest), sort_keys=True), flush=True)
        if report['status'] == 'INCOMPLETE':
            raise SystemExit(1)
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__, 'automatic_retry': False, 'workload_patch_requests': 0}))
        raise SystemExit(1)
