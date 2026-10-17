#!/usr/bin/env python3
"""Export only existing PG/Redis image content; never connect to production stores.

Root Linux/ARM64: datax-02 PostgreSQL; datax-03 Redis. Python3.7+.
Dependencies are frozen inside the single-file operator delivery.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import stat
import sys

import export_weknora_base as io
import verify_weknora_base as verify

require = verify.require
GIB = 1024**3
MAX_ARCHIVE = 16*GIB
ROLE = None
ROOT = None
PHASE = 'PREFLIGHT'
TARGETS = {
    'datax-02': {
        'role': 'postgres',
        'image': 'deerflow.local/weknora-postgres:pg16-search-arm64-20260722-1',
        'config_digest': 'sha256:5656e1d666225b9ee09c83684b3f1363d6b216bab498d11e0a186a21e175b85b',
        'root_digest': None,
    },
    'datax-03': {
        'role': 'redis',
        'image': 'm.daocloud.io/docker.io/library/redis@sha256:c9d92d840fd011c908f040592857c724ae6d877f2aba5c40ad963276507386b2',
        'config_digest': None,
        'root_digest': 'sha256:c9d92d840fd011c908f040592857c724ae6d877f2aba5c40ad963276507386b2',
    },
}


def configure(node):
    global ROLE, ROOT
    require(node in TARGETS, 'only_datax02_postgres_or_datax03_redis')
    ROLE = dict(TARGETS[node], node=node)
    ROOT = Path('/var/lib/weknora-enrichment-image-export-'+ROLE['role']+'-20261017-r1')
    io.ROOT = ROOT


def emit(value): io.emit(value)


def capture(args):
    allowed = (args == io.CTR+['images', 'ls']
               or args == io.CTR+['images', 'export', '--help']
               or args == io.CRI+['inspecti', ROLE['image']]
               or (args[:len(io.CTR)+2] == io.CTR+['content', 'get']
                   and len(args) == len(io.CTR)+3 and verify.DIGEST.fullmatch(args[-1])))
    require(allowed, 'non_image_read_command_rejected')
    chunks = []
    io.pipe_command(args, chunks.append, 8*1024**2, 30)
    return b''.join(chunks)


def read_json(desc):
    raw = capture(io.CTR+['content', 'get', desc['digest']])
    require('sha256:'+hashlib.sha256(raw).hexdigest() == desc['digest']
            and ('size' not in desc or len(raw) == desc['size']), 'image_metadata_hash_mismatch')
    obj = json.loads(raw, object_pairs_hook=verify.unique)
    require(isinstance(obj, dict), 'image_metadata_not_object')
    return obj


def identity():
    rows = [line.split() for line in capture(io.CTR+['images', 'ls']).decode('utf-8').splitlines()]
    rows = [r for r in rows if r and r[0] == ROLE['image']]
    require(len(rows) == 1 and len(rows[0]) >= 3, 'exact_reviewed_image_missing')
    kind, root = rows[0][1:3]
    require(kind in verify.MANIFESTS+verify.INDEXES and verify.DIGEST.fullmatch(root), 'unsupported_image_target')
    require(ROLE['root_digest'] is None or root == ROLE['root_digest'], 'reviewed_root_digest_changed')
    desc = {'mediaType': kind, 'digest': root}
    obj = read_json(desc)
    if kind in verify.INDEXES:
        require(obj.get('schemaVersion') == 2 and isinstance(obj.get('manifests'), list)
                and 0 < len(obj['manifests']) <= 64, 'invalid_image_index')
        choices = [d for d in obj['manifests'] if d.get('platform', {}).get('os') == 'linux'
                   and d.get('platform', {}).get('architecture') == 'arm64'
                   and d.get('platform', {}).get('variant', '') in ('', 'v8')]
        require(len(choices) == 1, 'arm64_manifest_not_unique')
        desc = io.descriptor(choices[0]); obj = read_json(desc)
    require(desc.get('mediaType') in verify.MANIFESTS and obj.get('schemaVersion') == 2, 'unsupported_manifest')
    config_desc = io.descriptor(obj.get('config'))
    require(ROLE['config_digest'] is None or config_desc['digest'] == ROLE['config_digest'], 'reviewed_config_changed')
    cfg = read_json(config_desc)
    require(cfg.get('os') == 'linux' and cfg.get('architecture') == 'arm64', 'image_not_linux_arm64')
    layers = obj.get('layers'); diffids = cfg.get('rootfs', {}).get('diff_ids')
    require(isinstance(layers, list) and isinstance(diffids, list)
            and 0 < len(layers) == len(diffids) <= 2048
            and cfg.get('rootfs', {}).get('type') == 'layers', 'image_layer_shape')
    for layer, diffid in zip(layers, diffids):
        io.descriptor(layer)
        require(layer.get('mediaType') in ('application/vnd.oci.image.layer.v1.tar+gzip',
                'application/vnd.oci.image.layer.v1.tar', 'application/vnd.docker.image.rootfs.diff.tar.gzip')
                and isinstance(diffid, str) and verify.DIGEST.fullmatch(diffid), 'unsupported_layer')
    compressed = sum(x['size'] for x in layers)
    require(0 < compressed <= MAX_ARCHIVE-64*1024**2, 'archive_budget_exceeded')
    cri = json.loads(capture(io.CRI+['inspecti', ROLE['image']]), object_pairs_hook=verify.unique)['status']
    require(cri.get('id') == config_desc['digest'], 'cri_config_identity_mismatch')
    return {'role': ROLE['role'], 'source_node': ROLE['node'], 'image': ROLE['image'],
            'root_digest': root, 'root_media_type': kind, 'manifest_digest': desc['digest'],
            'config_digest': config_desc['digest'], 'layers': len(layers),
            'compressed_layer_bytes': compressed, 'platform': 'linux/arm64',
            'cri_identity': 'PASS', 'image_config_or_env_printed': False}


def export_command():
    return io.CTR+['images', 'export', '--local', '--platform', 'linux/arm64',
                   '--skip-manifest-json', '-', ROLE['image']]


def status():
    emit({'store_image_export_status': 'READ_ONLY', 'source_node': ROLE['node'], 'role': ROLE['role'],
          'evidence': str(ROOT), 'state_exists': os.path.lexists(str(ROOT)),
          'production_writes': 0, 'provider_requests': 0})
    if not os.path.lexists(str(ROOT)): return 0
    io.safe_dir(ROOT)
    for name in ('STARTED.json', 'RESULT.json', 'FAILED.json'):
        if os.path.lexists(str(ROOT/name)):
            raw, row = io.read_receipt(name)
            emit({'record': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                  'status': row.get('store_image_export'), 'phase': row.get('phase'),
                  'failed_check': row.get('failed_check')})
    for name in ('store.oci.tar.partial', 'store.oci.tar'):
        if os.path.lexists(str(ROOT/name)):
            emit({'file': name, 'bytes': io.private_meta(ROOT/name)[2], 'archive_rehashed': False})
    return 0


def export_once():
    global PHASE
    require(not os.path.lexists(str(ROOT)), 'operation_exists_USE_STATUS_NO_REPEAT')
    before = identity()
    maximum = before['compressed_layer_bytes']+64*1024**2
    space = io.check_space(ROOT.parent, maximum)
    help_text = capture(io.CTR+['images', 'export', '--help'])
    require(b'--local' in help_text and b'--skip-manifest-json' in help_text, 'local_export_flags_unavailable')
    ROOT.mkdir(mode=0o700)
    io.write_new('STARTED.json', {'store_image_export': 'STARTED', 'identity': before, 'space': space})
    emit({'store_image_export': 'START', 'identity': before, 'evidence': str(ROOT),
          'rate_limit_MiB_s': 16, 'daemon_global_io_limit': False, 'production_writes': 0})
    try:
        PHASE = 'EXPORT_EXISTING_IMAGE_CONTENT_ONLY'
        h = hashlib.sha256(); partial = ROOT/'store.oci.tar.partial'
        fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        def progress(count, elapsed):
            io.check_space(ROOT, max(0, maximum-count))
            emit({'store_image_export': 'RUNNING', 'phase': PHASE, 'bytes_written': count,
                  'elapsed_seconds': round(elapsed), 'role': ROLE['role']})
        with os.fdopen(fd, 'wb') as f:
            os.fchmod(f.fileno(), 0o600)
            def consume(block): f.write(block); h.update(block)
            count = io.pipe_command(export_command(), consume, maximum, 2400, progress, 16*1024**2)
            f.flush(); os.fsync(f.fileno())
        require(count > 0, 'empty_export')
        snapshot = io.private_meta(partial)
        PHASE = 'VERIFY_ARCHIVE_LAYERS_AND_DIFFIDS'
        def verification_progress(row):
            io.check_space(ROOT, 0)
            emit(dict(row, role=ROLE['role']))
        checked = verify.verify(partial, verification_progress, before['manifest_digest'],
                                before['config_digest'], before['layers'], before['compressed_layer_bytes'],
                                before['root_digest'])
        require(io.private_meta(partial) == snapshot, 'export_changed_during_verification')
        PHASE = 'RECHECK_SOURCE_IMAGE_IDENTITY'
        require(identity() == before, 'source_image_changed_during_export')
        io.check_space(ROOT, 0)
        final = ROOT/'store.oci.tar'
        require(not os.path.lexists(str(final)), 'destination_exists')
        partial.rename(final)
        result = dict(checked, store_image_export='PASS', identity=before, archive=str(final),
            archive_bytes=count, archive_sha256=h.hexdigest(), utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            phase=PHASE, production_writes=0, provider_requests=0, runtime_volumes_exported=False,
            production_database_connections=0, production_redis_connections=0, credentials_requested=False,
            image_config_or_env_printed=False, archive_must_remain_private=True, images_imported_or_pulled=False,
            build_started=False, pods_created=0, ready_for_rollout=False)
        io.write_new('RESULT.json', result)
        raw, _ = io.read_receipt('RESULT.json')
        emit({k: v for k, v in result.items() if k != 'layers'})
        emit({'report': str(ROOT/'RESULT.json'), 'report_sha256': hashlib.sha256(raw).hexdigest()})
        return 0
    except BaseException as exc:
        fail = failure(exc)
        try: io.write_new('FAILED.json', fail)
        except Exception: fail['failure_receipt_saved'] = False
        emit(fail)
        return 1


def failure(exc):
    detail = str(exc)
    if not isinstance(exc, verify.CheckFailure) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', detail):
        detail = 'SAFE_DIAGNOSTIC_REQUIRED'
    return {'store_image_export': 'UNCONFIRMED', 'phase': PHASE, 'failed_check': detail,
            'error_type': type(exc).__name__, 'raw_error_printed': False, 'production_writes': 0,
            'provider_requests': 0, 'images_imported_or_pulled': False, 'build_started': False,
            'automatic_retry': False, 'instruction': 'USE_STATUS_NO_REPEAT_EXPORT_KEEP_ARCHIVE_PRIVATE'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--export-once', action='store_true'); g.add_argument('--status', action='store_true')
    g.add_argument('--inspect', action='store_true')
    args = p.parse_args(argv)
    try:
        require(platform.system() == 'Linux' and platform.machine() in ('arm64', 'aarch64')
                and os.geteuid() == 0, 'root_linux_arm64_required')
        configure(socket.gethostname().split('.')[0]); os.umask(0o077)
        if args.status: return status()
        require(stat.S_ISSOCK(Path(io.SOCKET).lstat().st_mode), 'containerd_socket_missing')
        if args.inspect:
            emit({'store_image_inventory': identity(), 'files_modified': False, 'production_writes': 0})
            return 0
        import fcntl
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        def interrupted(signum, frame): raise verify.CheckFailure('interrupted_USE_STATUS')
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM): signal.signal(sig, interrupted)
        signal.alarm(3600); os.nice(10)
        io.safe_dir(ROOT.parent)
        lockpath = ROOT.parent/(ROOT.name+'.lock')
        fd = os.open(str(lockpath), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'rb') as lock:
            s = os.fstat(lock.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                    and not s.st_mode & 0o022, 'unsafe_operation_lock')
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return export_once()
    except Exception as exc:
        emit(failure(exc)); return 1
    finally:
        if platform.system() == 'Linux': signal.alarm(0)


if __name__ == '__main__': sys.exit(main())
