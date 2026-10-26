#!/usr/bin/env python3
"""One-shot, local-only export of the reviewed live WeKnora base on datax-03.

Python 3.7+. No image pull/import/tag, workload exec or Kubernetes write.
The archive is root-private and may contain configuration; never publish it.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import queue
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

import verify_weknora_base as verify

CheckFailure, require = verify.CheckFailure, verify.require
IMAGE = 'deerflow.local/weknora-app:0.8.0-enterprise-proxy1-anydoc1-arm64-20260912'
CONFIG = 'sha256:93d6900e88edcd1b5f389c5756f849b34f5051d832a72b0bc17b0af86c709194'
POD = 'weknora-app-f6fb87c6d-gqtf5'
POD_UID = 'd64b460f-b27e-4a6f-afde-c5583b7d0455'
CONTAINER = '8cfe3a38b2bf36f76091ea9534be15108a631c48ac38068f59b406920c89a810'
SOCKET = '/run/containerd/containerd.sock'
CTR = ['ctr', '--address', SOCKET, '--namespace', 'k8s.io']
CRI = ['crictl', '--runtime-endpoint', 'unix://' + SOCKET,
       '--image-endpoint', 'unix://' + SOCKET, '--timeout', '20s']
ROOT = Path('/var/lib/weknora-glm-base-export-20261016-r1')
GIB = 1024**3
STAGE = 'PREFLIGHT'
MAX_ARCHIVE = 16*GIB


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def safe_dir(path):
    require(path.is_absolute() and '..' not in path.parts, 'invalid_private_path')
    for parent in reversed([path] + list(path.parents)):
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_ancestor')


def private_meta(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
            and stat.S_IMODE(s.st_mode) == 0o600, 'unsafe_private_file')
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def write_new(name, value):
    safe_dir(ROOT)
    require(Path(name).name == name and name not in ('.', '..'), 'unsafe_receipt_name')
    raw = (json.dumps(value, sort_keys=True, indent=2)+'\n').encode()
    fd = os.open(str(ROOT/name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(raw); f.flush(); os.fsync(f.fileno())


def read_receipt(name):
    safe_dir(ROOT)
    path = ROOT/name
    before = private_meta(path)
    require(before[2] <= 2*1024**2, 'receipt_oversized')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        raw = f.read(2*1024**2+1)
    require(before == private_meta(path) and len(raw) == before[2], 'receipt_changed')
    return raw, json.loads(raw, object_pairs_hook=verify.unique)


def pipe_command(argv, consume, maximum, timeout, progress=None, rate=0):
    """Bound memory/time on both Windows tests and Linux; kill only our client.

    stdout backpressure limits the export stream, not containerd's total I/O.
    stderr is discarded: image config or raw process diagnostics are not logs.
    """
    messages, stopped = queue.Queue(maxsize=4), threading.Event()
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, bufsize=0)
    def reader():
        try:
            while not stopped.is_set():
                data = p.stdout.read(65536)
                while not stopped.is_set():
                    try:
                        messages.put(('data', data), timeout=.1)
                        break
                    except queue.Full:
                        pass
                if not data:
                    return
        except Exception:
            if not stopped.is_set():
                try: messages.put(('error', b''), timeout=.1)
                except queue.Full: pass
    worker = threading.Thread(target=reader, daemon=True)
    worker.start()
    start, last, count = time.monotonic(), time.monotonic(), 0
    try:
        while True:
            now = time.monotonic()
            require(now-start < timeout, 'local_command_deadline')
            if progress and now-last >= 15:
                progress(count, now-start)
                last = now
            try: kind, data = messages.get(timeout=.1)
            except queue.Empty: continue
            require(kind == 'data', 'local_pipe_read_failed')
            if not data:
                break
            count += len(data)
            require(count <= maximum, 'local_output_size_bound')
            consume(data)
            if rate:
                delay = count/rate - (time.monotonic()-start)
                if delay > 0: time.sleep(min(delay, 1))
        require(p.wait(timeout=max(.01, min(10, timeout-(time.monotonic()-start)))) == 0,
                'local_command_failed_no_retry')
        return count
    finally:
        stopped.set()
        if p.poll() is None:
            p.terminate()
            try: p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill(); p.wait(timeout=3)
        worker.join(timeout=2)
        p.stdout.close()


def capture(args):
    allowed = (args == CTR+['images', 'ls'] or args == CTR+['images', 'export', '--help']
               or args == CRI+['inspect', CONTAINER] or args == CRI+['inspecti', IMAGE]
               or (args[:len(CTR)+2] == CTR+['content', 'get'] and len(args) == len(CTR)+3
                   and isinstance(args[-1], str) and verify.DIGEST.fullmatch(args[-1])))
    require(allowed, 'non_readonly_command_rejected')
    chunks = []
    pipe_command(args, chunks.append, 8*1024**2, 30)
    return b''.join(chunks)


def descriptor(d):
    require(isinstance(d, dict) and isinstance(d.get('digest'), str)
            and verify.DIGEST.fullmatch(d['digest']) and type(d.get('size')) is int
            and 0 < d['size'] <= 8*GIB and not d.get('urls') and 'data' not in d,
            'invalid_local_descriptor')
    return d


def read_json(d):
    raw = capture(CTR+['content', 'get', d['digest']])
    require('sha256:'+hashlib.sha256(raw).hexdigest() == d['digest']
            and ('size' not in d or len(raw) == d['size']), 'local_metadata_hash_mismatch')
    obj = json.loads(raw, object_pairs_hook=verify.unique)
    require(isinstance(obj, dict), 'local_metadata_not_object')
    return obj


def bind_container(status, identity):
    labels = status.get('labels', {})
    require(status.get('id') == CONTAINER and status.get('state') == 'CONTAINER_RUNNING'
            and status.get('metadata', {}).get('name') == 'app'
            and status.get('metadata', {}).get('attempt', 0) == 0
            and labels.get('io.kubernetes.pod.uid') == POD_UID
            and labels.get('io.kubernetes.pod.name') == POD
            and labels.get('io.kubernetes.pod.namespace') == 'deer-flow', 'live_container_identity_changed')
    image_ref = status.get('imageRef', '')
    # CRI versions use the config ID or a repository-qualified manifest ID.
    acceptable = (CONFIG, identity['manifest_digest'], identity['root_digest'])
    require(image_ref in acceptable or image_ref in
            tuple('deerflow.local/weknora-app@'+d for d in acceptable), 'live_container_image_changed')


def current_identity():
    rows = [r.split() for r in capture(CTR+['images', 'ls']).decode('utf-8').splitlines()]
    rows = [r for r in rows if r and r[0] == IMAGE]
    require(len(rows) == 1 and len(rows[0]) >= 3, 'exact_base_image_not_found')
    kind, root = rows[0][1:3]
    require(kind in verify.MANIFESTS+verify.INDEXES and verify.DIGEST.fullmatch(root), 'invalid_tag_target')
    desc = {'mediaType': kind, 'digest': root}
    obj = read_json(desc)
    if kind in verify.INDEXES:
        require(obj.get('schemaVersion') == 2 and isinstance(obj.get('manifests'), list)
                and len(obj['manifests']) <= 64, 'invalid_local_index')
        matches = [d for d in obj['manifests'] if d.get('platform', {}).get('os') == 'linux'
                   and d.get('platform', {}).get('architecture') == 'arm64'
                   and d.get('platform', {}).get('variant', '') in ('', 'v8')]
        require(len(matches) == 1, 'arm64_manifest_not_unique')
        desc = descriptor(matches[0]); obj = read_json(desc)
    require(desc['mediaType'] in verify.MANIFESTS and obj.get('schemaVersion') == 2,
            'invalid_local_manifest')
    cfg_desc = descriptor(obj.get('config'))
    require(cfg_desc['digest'] == CONFIG, 'base_config_changed')
    cfg = read_json(cfg_desc)
    require(cfg.get('os') == 'linux' and cfg.get('architecture') == 'arm64', 'base_platform_changed')
    layers, ids = obj.get('layers'), cfg.get('rootfs', {}).get('diff_ids')
    require(isinstance(layers, list) and isinstance(ids, list) and 0 < len(layers) == len(ids) <= 2048
            and cfg.get('rootfs', {}).get('type') == 'layers', 'base_layer_shape_changed')
    for layer, diff in zip(layers, ids):
        descriptor(layer)
        require(layer.get('mediaType') in ('application/vnd.oci.image.layer.v1.tar+gzip',
                'application/vnd.oci.image.layer.v1.tar', 'application/vnd.docker.image.rootfs.diff.tar.gzip')
                and isinstance(diff, str) and verify.DIGEST.fullmatch(diff), 'unsupported_layer')
    size = sum(d['size'] for d in layers)
    require(0 < size <= MAX_ARCHIVE-64*1024**2, 'base_export_too_large')
    image = json.loads(capture(CRI+['inspecti', IMAGE]))['status']
    require(image.get('id') == CONFIG and IMAGE in image.get('repoTags', []), 'cri_image_mismatch')
    result = {'image': IMAGE, 'config_digest': CONFIG, 'manifest_digest': desc['digest'],
              'root_digest': root, 'root_media_type': kind, 'layers': len(layers),
              'compressed_layer_bytes': size, 'pod_uid': POD_UID, 'container_id': CONTAINER}
    bind_container(json.loads(capture(CRI+['inspect', CONTAINER]))['status'], result)
    return result


def space_budget(v, remaining):
    capacity, available = v.f_blocks*v.f_frsize, v.f_bavail*v.f_frsize
    reserve = max(20*GIB, (capacity+4)//5)
    require(not v.f_flag & getattr(os, 'ST_RDONLY', 1) and available >= reserve+remaining
            and v.f_favail >= 10000, 'export_space_reserve_insufficient')
    return {'available_bytes': available, 'reserve_bytes': reserve,
            'remaining_budget_bytes': remaining, 'free_inodes': v.f_favail}


def check_space(path, remaining):
    safe_dir(path)
    return space_budget(os.statvfs(str(path)), remaining)


def export_command():
    return CTR+['images', 'export', '--local', '--platform', 'linux/arm64',
                '--skip-manifest-json', '-', IMAGE]


def status():
    safe_dir(ROOT)
    records = []
    for name in ('STARTED.json', 'BASE-EXPORT.json', 'FAILED.json'):
        if os.path.lexists(str(ROOT/name)):
            raw, obj = read_receipt(name)
            records.append({'name': name, 'sha256': hashlib.sha256(raw).hexdigest(),
                            'status': obj.get('weknora_base_export'), 'stage': obj.get('stage'),
                            'failed_check': obj.get('failed_check')})
    files = []
    for name in ('weknora.base.oci.tar.partial', 'weknora.base.oci.tar'):
        if os.path.lexists(str(ROOT/name)):
            files.append({'name': name, 'bytes': private_meta(ROOT/name)[2]})
    emit({'weknora_base_export_status': 'READ_ONLY', 'evidence': str(ROOT), 'records': records,
          'files': files, 'files_modified': False, 'production_workloads_mutated': False,
          'archive_rehashed': False, 'automatic_retry': False})
    return 0


def do_export():
    global STAGE
    # Exact directory doubles as an exclusive one-shot journal. No repeat after
    # success, partial output, interruption, or ambiguous client failure.
    require(not os.path.lexists(str(ROOT)), 'operation_exists_use_status_no_repeat')
    before = current_identity()
    maximum = before['compressed_layer_bytes']+64*1024**2
    space = check_space(ROOT.parent, maximum)
    help_text = capture(CTR+['images', 'export', '--help'])
    require(b'--local' in help_text and b'--skip-manifest-json' in help_text,
            'required_local_export_flags_missing')
    ROOT.mkdir(mode=0o700)
    safe_dir(ROOT)
    write_new('STARTED.json', {'weknora_base_export': 'STARTED', 'identity': before, 'space': space})
    emit({'weknora_base_export': 'START', 'evidence': str(ROOT), 'space': space,
          'base_identity': before, 'stream_MiB_per_second_limit': 16, 'workloads_restarted': False})
    partial = ROOT/'weknora.base.oci.tar.partial'
    STAGE = 'LOCAL_EXPORT'
    fd = os.open(str(partial), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    h = hashlib.sha256()
    def progress(count, elapsed):
        check_space(ROOT, max(0, maximum-count))
        emit({'weknora_base_export': 'RUNNING', 'stage': STAGE, 'bytes_written': count,
              'elapsed_seconds': round(elapsed)})
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), 0o600)
        def consume(block):
            f.write(block); h.update(block)
        size = pipe_command(export_command(), consume, maximum, 2400, progress, 16*1024**2)
        f.flush(); os.fsync(f.fileno())
    require(size > 0, 'empty_archive')
    file_before = private_meta(partial)
    STAGE = 'VERIFY_LAYER_HASHES_AND_DIFFIDS'
    def verification_progress(row):
        check_space(ROOT, 0)
        emit(row)
    checked = verify.verify(partial, verification_progress, before['manifest_digest'], CONFIG,
                            before['layers'], before['compressed_layer_bytes'], before['root_digest'])
    require(private_meta(partial) == file_before, 'archive_changed_during_verification')
    STAGE = 'RECHECK_LOCAL_BASELINE'
    require(current_identity() == before, 'live_base_changed_during_export')
    check_space(ROOT, 0)
    final = ROOT/'weknora.base.oci.tar'
    require(not os.path.lexists(str(final)), 'final_archive_exists')
    partial.rename(final)
    result = dict(checked, weknora_base_export='PASS', image=IMAGE, archive=str(final), bytes=size,
                  archive_sha256=h.hexdigest(), base_identity=before,
                  utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  production_workloads_mutated=False, images_imported_or_pulled=False,
                  provider_requests=0, credentials_requested=False, image_env_printed=False,
                  build_started=False, ready_for_build=False, ready_for_rollout=False,
                  next_gate='PRIVATE_TRANSFER_AND_PIN_ISOLATED_GO_RUST_CGO_INPUTS')
    write_new('BASE-EXPORT.json', result)
    raw, _ = read_receipt('BASE-EXPORT.json')
    emit({k: v for k, v in result.items() if k != 'layers'})
    emit({'report': str(ROOT/'BASE-EXPORT.json'), 'report_sha256': hashlib.sha256(raw).hexdigest()})
    return 0


def main(argv=None):
    global STAGE
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--export-once', action='store_true')
    g.add_argument('--status', action='store_true')
    args = p.parse_args(argv)
    created_here = False
    try:
        require(platform.system() == 'Linux' and platform.machine() in ('arm64', 'aarch64')
                and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-03',
                'requires_root_linux_arm64_datax03')
        os.umask(0o077)
        if args.status: return status()
        require(stat.S_ISSOCK(Path(SOCKET).lstat().st_mode), 'containerd_socket_missing')
        # Mark ownership only after this process creates ROOT, not after a
        # competing invocation's mkdir. See the exclusive journal in do_export.
        def interrupted(signum, frame):
            raise CheckFailure('export_interrupted_use_status')
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
            signal.signal(sig, interrupted)
        signal.alarm(3600)
        os.nice(10)
        # OS advisory lock serializes preflight through final receipt; imported
        # here so Windows offline verification does not need an fcntl shim.
        import fcntl
        safe_dir(ROOT.parent)
        fd = os.open('/var/lib/weknora-glm-base-export-20261016-r1.lock',
                     os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'rb') as lock:
            s = os.fstat(lock.fileno())
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                    and not s.st_mode & 0o022, 'unsafe_operation_lock')
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(not os.path.lexists(str(ROOT)), 'operation_exists_use_status_no_repeat')
            created_here = True
            return do_export()
    except Exception as exc:
        result = {'weknora_base_export': 'UNCONFIRMED', 'stage': STAGE,
                  'failed_check': str(exc) if type(exc) is CheckFailure else 'SAFE_DIAGNOSTIC_REQUIRED',
                  'error_type': type(exc).__name__, 'raw_error_printed': False,
                  'evidence': str(ROOT) if os.path.lexists(str(ROOT)) else None,
                  'production_workloads_mutated': False, 'provider_requests': 0,
                  'images_imported_or_pulled': False, 'build_started': False,
                  'ready_for_rollout': False, 'automatic_retry': False,
                  'instruction': 'USE_STATUS_NO_REPEAT_EXPORT; KEEP_PRIVATE_ARTIFACTS'}
        if created_here and ROOT.is_dir():
            try: write_new('FAILED.json', result)
            except Exception: result['failure_receipt_saved'] = False
        emit(result)
        return 1
    finally:
        if platform.system() == 'Linux': signal.alarm(0)


if __name__ == '__main__':
    sys.exit(main())
