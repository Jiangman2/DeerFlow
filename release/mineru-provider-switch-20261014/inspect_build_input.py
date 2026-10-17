#!/usr/bin/env python3
"""Python 3.7+: local read-only MinerU image identity and disk inventory.

No Kubernetes access, image import/pull/export/pin, provider call or file write.
Image JSON may contain credentials; only the explicit safe summary is printed.
This is NOT a layer-content audit or permission to build/import/roll out.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import sys

IMAGE = 'deerflow.local/deer-flow-mineru-adapter:20260921-enterprise-arm64-rc15-conversation-runtime4'
CONFIG = 'sha256:bfbdd4d3309110be003549b2959d5080204b27bcd6440e33d4c67d80ee3775e9'
SOCKET = '/run/containerd/containerd.sock'
CTR = ['ctr', '--address', SOCKET, '--namespace', 'k8s.io']
CRI = ['crictl', '--runtime-endpoint', 'unix://' + SOCKET,
       '--image-endpoint', 'unix://' + SOCKET, '--timeout', '20s']
MANIFESTS = ('application/vnd.oci.image.manifest.v1+json',
             'application/vnd.docker.distribution.manifest.v2+json')
INDEXES = ('application/vnd.oci.image.index.v1+json',
           'application/vnd.docker.distribution.manifest.list.v2+json')
STAGE = 'START'


class CheckFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise CheckFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def capture(args, label, limit=8 * 1024 * 1024):
    global STAGE
    STAGE = label
    allowed = (args[:len(CTR)] == CTR and args[len(CTR):len(CTR)+2] in
               (['images', 'ls'], ['content', 'get'])) or (
               args[:len(CRI)] == CRI and args[len(CRI)] in ('inspecti', 'imagefsinfo'))
    require(allowed, 'non_readonly_command_rejected')
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=30, stdin=subprocess.DEVNULL)
    require(result.returncode == 0, 'readonly_command_failed')
    require(len(result.stdout) <= limit, 'readonly_output_too_large')
    return result.stdout


def digest(value):
    require(isinstance(value, str) and re.fullmatch(r'sha256:[0-9a-f]{64}', value),
            'invalid_digest')
    return value


def target_from_list(raw):
    rows = [line.split() for line in raw.decode('utf-8').splitlines()]
    rows = [row for row in rows if row and row[0] == IMAGE]
    require(len(rows) <= 1, 'duplicate_image_records')
    if not rows:
        return None
    row = rows[0]
    require(len(row) >= 3 and row[1] in MANIFESTS + INDEXES, 'unsupported_image_target')
    return {'mediaType': row[1], 'digest': digest(row[2])}


def descriptor(value):
    require(isinstance(value, dict), 'invalid_descriptor')
    digest(value.get('digest'))
    require(type(value.get('size')) is int and value['size'] > 0, 'invalid_descriptor_size')
    require(not value.get('urls'), 'external_descriptor_not_accepted')
    return value


def read_json(desc):
    raw = capture(CTR + ['content', 'get', digest(desc['digest'])], 'READ_IMAGE_METADATA')
    require('sha256:' + hashlib.sha256(raw).hexdigest() == desc['digest'], 'metadata_hash_mismatch')
    require('size' not in desc or len(raw) == desc['size'], 'metadata_size_mismatch')
    result = json.loads(raw)
    require(isinstance(result, dict), 'metadata_not_object')
    return result


def inspect_identity(target, read=read_json):
    obj = read(target)
    manifest_digest = target['digest']
    if target['mediaType'] in INDEXES:
        require(obj.get('schemaVersion') == 2 and isinstance(obj.get('manifests'), list)
                and len(obj['manifests']) <= 64, 'invalid_image_index')
        matches = [d for d in obj['manifests'] if d.get('platform', {}).get('os') == 'linux'
                   and d.get('platform', {}).get('architecture') == 'arm64'
                   and d.get('platform', {}).get('variant', '') in ('', 'v8')]
        require(len(matches) == 1, 'arm64_manifest_not_unique')
        selected = descriptor(matches[0])
        require(selected.get('mediaType') in MANIFESTS, 'nested_or_unsupported_manifest')
        manifest_digest = selected['digest']
        obj = read(selected)
    require(obj.get('schemaVersion') == 2 and obj.get('mediaType') in MANIFESTS,
            'invalid_manifest')
    config_desc = descriptor(obj.get('config'))
    require(config_desc['digest'] == CONFIG, 'production_config_identity_changed')
    cfg = read(config_desc)
    require(cfg.get('architecture') == 'arm64' and cfg.get('os') == 'linux',
            'production_platform_mismatch')
    layers = obj.get('layers')
    require(isinstance(layers, list) and 0 < len(layers) <= 2048, 'invalid_layers')
    for layer in layers:
        descriptor(layer)
    diffids = cfg.get('rootfs', {}).get('diff_ids', [])
    require(cfg.get('rootfs', {}).get('type') == 'layers' and len(diffids) == len(layers),
            'diffid_count_mismatch')
    for item in diffids:
        digest(item)
    return {'image': IMAGE, 'config_digest': CONFIG, 'target_digest': target['digest'],
            'target_media_type': target['mediaType'], 'manifest_digest': manifest_digest,
            'platform': 'linux/arm64', 'layers': len(layers),
            'declared_layer_bytes': sum(layer['size'] for layer in layers),
            'metadata_hashes': 'VERIFIED_BY_READ_JSON', 'layer_bytes_rehashed': False,
            'uncompressed_layer_bytes': 'NOT_YET_MEASURED',
            'image_env_or_credentials_printed': False}


def filesystem(path):
    p = Path(path)
    require(p.is_absolute() and '..' not in p.parts and not any(ord(c) < 32 for c in path),
            'invalid_storage_path')
    if not p.exists():
        return {'path': path, 'exists': False}
    unsafe = []
    for parent in reversed([p] + list(p.parents)):
        s = parent.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            unsafe.append(str(parent))
    s, v = p.lstat(), os.statvfs(str(p))
    return {'path': path, 'exists': True, 'uid': s.st_uid, 'gid': s.st_gid,
            'mode': oct(stat.S_IMODE(s.st_mode)), 'device': s.st_dev,
            'available_GiB': round(v.f_bavail * v.f_frsize / 1024**3, 2),
            'capacity_GiB': round(v.f_blocks * v.f_frsize / 1024**3, 2),
            'available_inodes': v.f_favail, 'read_only': bool(v.f_flag & os.ST_RDONLY),
            'private_output_ancestors_safe': not unsafe, 'unsafe_ancestors': unsafe,
            'permissions_modified': False}


def inventory():
    node = socket.gethostname().split('.')[0]
    require(node in ('datax-01', 'datax-02', 'datax-03'), 'unexpected_node')
    require(os.geteuid() == 0 and platform.system() == 'Linux', 'requires_linux_root')
    require(stat.S_ISSOCK(Path(SOCKET).lstat().st_mode), 'containerd_socket_missing')
    result = {'node': node, 'storage': [filesystem(p) for p in ('/', '/opt', '/var/lib')],
              'production_workloads_mutated': False, 'files_modified': False,
              'images_imported_or_pulled': False, 'provider_requests': 0,
              'build_started': False, 'ready_for_build': False, 'ready_for_rollout': False}
    emit({'base_input_inventory': 'RUNNING', 'node': node, 'scope': 'LOCAL_READ_ONLY'})
    target = target_from_list(capture(CTR + ['images', 'ls'], 'IMAGE_TAG_LOOKUP'))
    if target is None:
        result.update(base_input_inventory='COMPLETE', local_base_image='NOT_PRESENT',
                      next_action='INSPECT_DATAx02_OR_DATAx03_NO_IMAGE_PULL')
        return result
    cri = json.loads(capture(CRI + ['inspecti', IMAGE], 'CRI_IMAGE_IDENTITY'))['status']
    require(cri.get('id') == CONFIG and IMAGE in cri.get('repoTags', []), 'cri_identity_mismatch')
    result['base'] = inspect_identity(target)
    result['base']['cri_identity'] = 'PASS'
    result['base']['cri_pinned'] = cri.get('pinned')
    result['base']['cri_declared_bytes'] = cri.get('size')
    imagefs = json.loads(capture(CRI + ['imagefsinfo', '--output', 'json'], 'CRI_IMAGEFS'))
    imagefs = imagefs.get('status', imagefs)
    paths = sorted({row.get('fsId', {}).get('mountpoint', '')
                    for key in ('imageFilesystems', 'containerFilesystems')
                    for row in imagefs.get(key, [])})
    require(0 < len(paths) <= 8, 'image_filesystem_not_reported')
    result['imagefs'] = [filesystem(p) for p in paths]
    current = target_from_list(capture(CTR + ['images', 'ls'], 'IMAGE_TAG_RECHECK'))
    require(current == target, 'image_target_changed_during_audit')
    result.update(base_input_inventory='COMPLETE', local_base_image='METADATA_PASS',
                  next_action='REVIEW_SPACE_THEN_EXPORT_AND_VERIFY_OCI_BASE')
    return result


def main():
    try:
        emit(inventory())
        return 0
    except Exception as exc:
        emit({'base_input_inventory': 'INCOMPLETE', 'stage': STAGE,
              'failed_check': str(exc) if type(exc) is CheckFailure else None,
              'error_type': type(exc).__name__, 'raw_error_printed': False,
              'production_workloads_mutated': False, 'files_modified': False,
              'images_imported_or_pulled': False, 'build_started': False,
              'ready_for_rollout': False, 'automatic_retry': False})
        return 1


if __name__ == '__main__':
    sys.exit(main())
