#!/usr/bin/env python3
"""Export the exact existing local MinerU image to a NEW private /opt directory.

No image pull/import/pin, Kubernetes mutation, provider call or service restart.
Uses ctr's local export to stdout, pacing writes with pipe backpressure. Private
partial files survive failure. CPU/I/O nice values are priorities, not quotas.
"""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import selectors
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

import inspect_build_input as inv
from verify_base_export import CheckFailure, CONFIG, COMPRESSED, GIB, MANIFEST, LAYERS, require, verify

STAGE = 'PREFLIGHT'
ROOT = None


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def safe_dir(path):
    require(path.is_absolute() and '..' not in path.parts, 'invalid_directory')
    for parent in reversed([path] + list(path.parents)):
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_output_ancestor')


def write_new(root, name, value):
    safe_dir(root)
    require(Path(name).name == name and name not in ('.', '..'), 'invalid_receipt_name')
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(str(root / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def check_space(path):
    safe_dir(path)
    require(path.stat().st_dev == Path('/opt').stat().st_dev != Path('/').stat().st_dev,
            'requires_separate_opt_filesystem')
    v = os.statvfs(str(path))
    reserve = max(20*GIB, (v.f_blocks * v.f_frsize + 4)//5)
    required = reserve + 3*GIB
    require(not v.f_flag & os.ST_RDONLY and v.f_bavail*v.f_frsize >= required and v.f_favail >= 10000,
            'export_space_reserve_insufficient')
    return {'available_GiB': round(v.f_bavail*v.f_frsize/GIB, 2),
            'required_free_GiB': round(required/GIB, 2),
            'reserve_GiB': round(reserve/GIB, 2), 'build_expansion_space': 'NOT_YET_ASSESSED'}


def current_identity():
    target = inv.target_from_list(inv.capture(inv.CTR + ['images', 'ls'], 'EXPORT_IDENTITY'))
    require(target == {'digest': MANIFEST, 'mediaType': inv.MANIFESTS[0]}, 'base_tag_identity_changed')
    row = inv.inspect_identity(target)
    require(row['config_digest'] == CONFIG and row['layers'] == LAYERS
            and row['declared_layer_bytes'] == COMPRESSED, 'base_metadata_changed')
    return row


def export_command():
    # --local prevents progress text from the transfer API entering tar stdout.
    return inv.CTR + ['images', 'export', '--local', '--platform', 'linux/arm64',
                      '--skip-manifest-json', '-', inv.IMAGE]


def stream_export(command, target, guard, deadline=900, maximum=3*GIB, rate=40*1024**2):
    require(not os.path.lexists(str(target)), 'export_target_already_exists')
    count, h, proc = 0, hashlib.sha256(), None
    start, last, next_guard = time.monotonic(), time.monotonic(), time.monotonic()
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as out, selectors.DefaultSelector() as sel:
            fd = None
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
            sel.register(proc.stdout, selectors.EVENT_READ)
            while True:
                now = time.monotonic()
                require(now-start <= deadline, 'export_deadline_exceeded')
                if now >= next_guard:
                    guard()
                    next_guard = now+15
                if now-last >= 15:
                    emit({'base_export': 'RUNNING', 'bytes_written': count, 'elapsed_seconds': round(now-start)})
                    last = now
                if not sel.select(timeout=1):
                    continue
                data = os.read(proc.stdout.fileno(), 1024**2)
                if not data:
                    break
                count += len(data)
                require(count <= maximum, 'export_archive_size_bound')
                out.write(data)
                h.update(data)
                if rate:
                    delay = count/rate - (time.monotonic()-start)
                    if delay > 0:
                        time.sleep(min(delay, 0.05))
            require(proc.wait(timeout=max(1, min(30, deadline-(time.monotonic()-start)))) == 0,
                    'ctr_export_failed_no_automatic_retry')
            require(count > 0, 'export_empty')
            out.flush()
            os.fsync(out.fileno())
        return {'archive_sha256': h.hexdigest(), 'bytes': count}
    finally:
        if fd is not None:
            os.close(fd)
        if proc is not None:
            if proc.poll() is None:
                # Stop this export client only; NEVER stop/restart containerd.
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if proc.stdout:
                proc.stdout.close()


def file_identity(path):
    s = path.lstat()
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
            and stat.S_IMODE(s.st_mode) == 0o600 and 0 < s.st_size <= 3*GIB, 'unsafe_export_file')
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def do_export():
    global STAGE, ROOT
    require(os.geteuid() == 0 and platform.system() == 'Linux'
            and platform.machine() in ('aarch64', 'arm64')
            and socket.gethostname().split('.')[0] == 'datax-01', 'requires_root_arm64_datax01')
    os.umask(0o077)
    os.nice(10)
    safe_dir(Path('/opt'))
    space = check_space(Path('/opt'))
    before = current_identity()
    # Refuse unsupported ctr instead of falling back to a different export mode.
    help_result = subprocess.run(inv.CTR + ['images', 'export', '--help'],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=20)
    require(help_result.returncode == 0 and len(help_result.stdout) < 65536
            and b'--local' in help_result.stdout and b'--skip-manifest-json' in help_result.stdout,
            'required_ctr_export_flags_unavailable')
    ROOT = Path(tempfile.mkdtemp(prefix='mineru-base-20261014-', dir='/opt'))
    safe_dir(ROOT)
    write_new(ROOT, 'STARTED.json', {'image': inv.IMAGE, 'manifest_digest': MANIFEST,
              'config_digest': CONFIG, 'space': space, 'production_workloads_mutated': False})
    emit({'base_export_directory': str(ROOT), 'space': space, 'automatic_retry': False})
    STAGE = 'EXPORT'
    partial = ROOT / 'mineru.base.oci.tar.partial'
    receipt = stream_export(export_command(), partial, lambda: check_space(ROOT))
    identity = file_identity(partial)
    STAGE = 'VERIFY_ALL_LAYERS_AND_DIFFIDS'
    checked = verify(partial, emit)
    require(identity == file_identity(partial), 'archive_changed_during_verification')
    require(current_identity() == before, 'base_identity_changed_during_export')
    check_space(ROOT)
    final = ROOT / 'mineru.base.oci.tar'
    require(not os.path.lexists(str(final)), 'final_archive_exists')
    partial.rename(final)
    result = dict(receipt, **checked)
    result.update(base_export='PASS', image=inv.IMAGE, archive=str(final),
                  utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  production_workloads_mutated=False, images_imported=False,
                  credentials_requested=False, image_env_printed=False,
                  live_baseline_recheck='REQUIRED_BEFORE_BUILD', build_started=False,
                  ready_for_build=False, ready_for_rollout=False)
    write_new(ROOT, 'BASE-EXPORT.json', result)
    report_sha = hashlib.sha256((ROOT/'BASE-EXPORT.json').read_bytes()).hexdigest()
    # Keep the detailed per-layer list in the root-private report, not the terminal.
    emit(dict((k, v) for k, v in result.items() if k != 'layers'))
    emit({'report': str(ROOT/'BASE-EXPORT.json'), 'report_sha256': report_sha})
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export-only', action='store_true', required=True)
    parser.parse_args()

    def interrupted(signum, frame):
        raise CheckFailure('export_interrupted_partial_files_retained')

    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(2400)
    lock = None
    try:
        safe_dir(Path('/run'))
        lock = os.open('/run/mineru-base-export-20261014.lock',
                       os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        s = os.fstat(lock)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022, 'unsafe_export_lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return do_export()
    except Exception as exc:
        result = {'base_export': 'FAIL', 'stage': STAGE, 'error_type': type(exc).__name__,
                  'failed_check': str(exc) if isinstance(exc, (CheckFailure, inv.CheckFailure)) else None,
                  'private_directory': str(ROOT) if ROOT else None,
                  'partial_files_retained': True, 'automatic_retry': False,
                  'production_workloads_mutated': False, 'images_imported': False,
                  'build_started': False, 'ready_for_rollout': False, 'raw_error_printed': False}
        if ROOT:
            try:
                write_new(ROOT, 'FAILED.json', result)
            except Exception:
                result['failure_receipt_saved'] = False
        emit(result)
        return 1
    finally:
        signal.alarm(0)
        if lock is not None:
            os.close(lock)


if __name__ == '__main__':
    sys.exit(main())
