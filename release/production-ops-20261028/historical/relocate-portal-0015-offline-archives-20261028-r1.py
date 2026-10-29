#!/usr/bin/env python3
"""Move verified closed offline archives off root; preserve original paths as symlinks.

Python 3.7+, ecs-zdz-pro-0015 only. Exactly fourteen operator-observed old archives; no runtime image, database, log or service mutations. Current frontend preload tar remains in place.
The destination is retained permanently as recovery material, not a temp directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import shutil
import signal
import socket
import stat
import subprocess
import time

PLAN = [{'path': '/home/cnooc/deerflow-frontend-preload-f0598482e2de4d05/frontend-image.tar', 'bytes': 5934652928, 'allocated_bytes': 5934657536, 'device': 64771, 'inode': 1855795, 'mtime_ns': 1791434166003538578}, {'path': '/home/cnooc/deerflow-sandbox-preload0015-I4vyfkAY/sandbox-required-arm64.oci.tar', 'bytes': 5337633792, 'allocated_bytes': 5337640960, 'device': 64771, 'inode': 1970493, 'mtime_ns': 1791177604838712967}, {'path': '/home/cnooc/runner.candidate.oci.tar', 'bytes': 976393728, 'allocated_bytes': 976400384, 'device': 64771, 'inode': 1855807, 'mtime_ns': 1792276752267943083}, {'path': '/home/cnooc/frontend.chat-notice-layout.full.oci.tar', 'bytes': 127322112, 'allocated_bytes': 127332352, 'device': 64771, 'inode': 1855839, 'mtime_ns': 1792816979007487510}, {'path': '/home/cnooc/frontend.data-qa-progress.full.oci.tar', 'bytes': 127319040, 'allocated_bytes': 127324160, 'device': 64771, 'inode': 1855832, 'mtime_ns': 1792682905079276840}, {'path': '/home/cnooc/frontend.data-qa-token.full.oci.tar', 'bytes': 127319040, 'allocated_bytes': 127324160, 'device': 64771, 'inode': 1855819, 'mtime_ns': 1792708669269701606}, {'path': '/home/cnooc/frontend.nonconfidential-notice.full.oci.tar', 'bytes': 127313408, 'allocated_bytes': 127320064, 'device': 64771, 'inode': 1855835, 'mtime_ns': 1792728273958217754}, {'path': '/home/cnooc/frontend.mobile-notice-removal.full.oci.tar', 'bytes': 127313408, 'allocated_bytes': 127320064, 'device': 64771, 'inode': 1855834, 'mtime_ns': 1792810972044390055}, {'path': '/home/cnooc/frontend.oci.tar', 'bytes': 124756992, 'allocated_bytes': 124760064, 'device': 64771, 'inode': 1855805, 'mtime_ns': 1792275405183332338}, {'path': '/home/cnooc/k8s-bootstrap-images.tar', 'bytes': 65925632, 'allocated_bytes': 65929216, 'device': 64771, 'inode': 1855678, 'mtime_ns': 1789131051215776337}, {'path': '/home/cnooc/frontend.employee-roster.delta.oci.tar', 'bytes': 64276480, 'allocated_bytes': 64282624, 'device': 64771, 'inode': 1855825, 'mtime_ns': 1792488807825735459}, {'path': '/home/cnooc/frontend.ppt-ordinary.delta.oci.tar', 'bytes': 64276480, 'allocated_bytes': 64282624, 'device': 64771, 'inode': 1855820, 'mtime_ns': 1792415328869341493}, {'path': '/home/cnooc/frontend.haineng-brand.delta.oci.tar', 'bytes': 64276480, 'allocated_bytes': 64282624, 'device': 64771, 'inode': 1855823, 'mtime_ns': 1792461523388907567}, {'path': '/home/cnooc/frontend.portal-ui.public-skill.delta.oci.tar', 'bytes': 64266240, 'allocated_bytes': 64270336, 'device': 64771, 'inode': 1855818, 'mtime_ns': 1792403912188633977}]
EXTRA_ROOTS = []
DEST = '/mnt/data/portal-offline-archives-0015-20261028-r1'


SOURCE_DEVICE = 64771
DEST_DEVICE = 64785
DEADLINE = 0
COPY_RATE = 16 * 1024**2
CURRENT_FILE = None
PLAN_SHA256 = '93feeab3accdc3cd9a43d0c139c404b2459d5eeaecf0ac198a247413823e8e11'
BASE_RELOCATION_TOOL_SHA256 = 'f0c1610a8846dec2e7073d4ac3020736fdf58a3d51206f409d4abd5dd28f6f4a'
READ_RATE = 32 * 1024**2
MIN_AGE = 172800
MIN_BYTES = 33554432


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def budget():
    require(not DEADLINE or time.monotonic() < DEADLINE, 'TIME_LIMIT')


def parent_fd(path):
    parts = PurePosixPath(path).parts
    require(parts and parts[0] == '/' and '..' not in parts, 'INVALID_PATH')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink, s.st_uid, s.st_gid, s.st_mode)


def pin(path, s):
    return dict(path=path, device=s.st_dev, inode=s.st_ino, bytes=s.st_size, mtime_ns=s.st_mtime_ns, uid=s.st_uid)


def archive_name(path):
    return path.endswith(('.tar', '.tar.gz', '.tar(1).gz', '.zip'))


def allowed_source(path):
    return path in {r['path'] for r in PLAN} and path.startswith('/home/cnooc/') and archive_name(path)


def check_row(row, s):
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'NOT_SINGLE_REGULAR_FILE')
    require(tuple(row[k] for k in ('device', 'inode', 'bytes', 'mtime_ns')) ==
            (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns), 'SOURCE_CHANGED')
    require(s.st_dev == SOURCE_DEVICE and s.st_size >= MIN_BYTES, 'SOURCE_SIZE_OR_DEVICE')
    minimum_age = MIN_AGE
    require(time.time() - max(s.st_mtime, s.st_ctime) >= minimum_age, 'SOURCE_TOO_RECENT')
    require(not s.st_mode & (stat.S_ISUID | stat.S_ISGID), 'SPECIAL_FILE_MODE')


def unchanged(parent, name, fd, initial):
    require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(initial) ==
            identity(os.fstat(fd)), 'SOURCE_CHANGED_DURING_COPY')


def unescape_mount(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), value)


def object_identity(path):
    try:
        s = os.stat(path)
        return (s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode))
    except (FileNotFoundError, ProcessLookupError):
        return None


def check_mount(fields, info, path, pid, destination=None):
    root, point = unescape_mount(fields[3]), unescape_mount(fields[4])
    require(point != path and not point.startswith(path + '/'), 'SOURCE_MOUNTPOINT')
    if fields[2] != '{}:{}'.format(os.major(info.st_dev), os.minor(info.st_dev)) or root == '/':
        return
    require(path != root, 'SOURCE_DIRECT_FILE_MOUNT')
    if not path.startswith(root.rstrip('/') + '/'):
        return
    # Only the diagnosed system-service read-only /home mirror is accepted.
    # Other bind paths and container/chroot roots remain blocked.
    require(root == point == '/home' and 'ro' in fields[5].split(','), 'SOURCE_BIND_MOUNTED')
    process_root = '/proc/' + pid + '/root'
    require(object_identity(process_root) == object_identity('/'), 'MOUNT_PROCESS_ROOT_DIFFERS')
    for directory in ('/mnt/data', DEST):
        expected = object_identity(directory)
        require(expected is not None and object_identity(process_root + directory) == expected,
                'ARCHIVE_DESTINATION_NOT_VISIBLE_IN_NAMESPACE')
    expected_source = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
    require(object_identity(process_root + path) == expected_source, 'ARCHIVE_SOURCE_NAMESPACE_MISMATCH')
    if destination is not None:
        expected_copy = object_identity(destination)
        require(expected_copy is not None and object_identity(process_root + destination) == expected_copy,
                'ARCHIVE_COPY_NOT_VISIBLE_IN_NAMESPACE')


def require_unused(info, path, destination=None):
    """Reject open/mapped files; accept only the verified host read-only /home mirror."""
    key = (info.st_dev, info.st_ino)
    began = time.monotonic()
    seen_ns = set()
    checked = 0
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        budget()
        require(time.monotonic() - began < 45, 'PROCESS_SCAN_TIME_LIMIT')
        try:
            with os.scandir('/proc/' + pid + '/fd') as entries:
                for e in entries:
                    checked += 1
                    require(checked < 300000, 'PROCESS_SCAN_SIZE_LIMIT')
                    try:
                        s = e.stat()
                    except FileNotFoundError:
                        continue
                    require((s.st_dev, s.st_ino) != key, 'SOURCE_OPEN_BY_PROCESS')
            with open('/proc/' + pid + '/maps', 'r') as stream:
                for line in stream:
                    fields = line.split(None, 5)
                    if len(fields) >= 5 and int(fields[4]) == info.st_ino:
                        major, minor = (int(x, 16) for x in fields[3].split(':'))
                        require(os.makedev(major, minor) != info.st_dev, 'SOURCE_MEMORY_MAPPED')
            ns = (os.readlink('/proc/' + pid + '/ns/mnt'), object_identity('/proc/' + pid + '/root'))
            if ns not in seen_ns:
                seen_ns.add(ns)
                with open('/proc/' + pid + '/mountinfo', 'r') as stream:
                    content = stream.read(4194305)
                require(len(content) <= 4194304, 'MOUNT_SCAN_LIMIT')
                for line in content.splitlines():
                    fields = line.split()
                    require(len(fields) > 5, 'MOUNT_RECORD_INVALID')
                    check_mount(fields, info, path, pid, destination)
        except (FileNotFoundError, ProcessLookupError):
            continue


def transfer(src, out, rate, limit):
    digest, size = hashlib.sha256(), 0
    began = last_progress = time.monotonic()
    phase = 'COPY_ARCHIVE' if out is not None else 'VERIFY_RETAINED_COPY'
    print(json.dumps(dict(phase=phase, file=CURRENT_FILE, bytes_done=0, total_bytes=limit)), flush=True)
    while True:
        budget()
        block = os.read(src, 1024**2)
        if not block:
            break
        size += len(block)
        require(size <= limit, 'FILE_GREW')
        digest.update(block)
        if out is not None:
            view = memoryview(block)
            while view:
                written = os.write(out, view)
                require(written > 0, 'SHORT_WRITE')
                view = view[written:]
        if time.monotonic()-last_progress >= 10:
            print(json.dumps(dict(phase=phase, file=CURRENT_FILE, bytes_done=size, total_bytes=limit, percent=round(size*100.0/max(1,limit),1))), flush=True)
            last_progress = time.monotonic()
        if rate:
            delay = size / rate - (time.monotonic() - began)
            if delay > 0:
                time.sleep(min(delay, 1))
    print(json.dumps(dict(phase=phase, file=CURRENT_FILE, bytes_done=size, total_bytes=limit, complete=True)), flush=True)
    return digest.hexdigest(), size


def relocate(row, destfd, journal, check_unused=require_unused):
    global CURRENT_FILE
    path = row['path']
    CURRENT_FILE = os.path.basename(path)
    require(allowed_source(path), 'OUTSIDE_ARCHIVE_ALLOWLIST')
    parent, name = parent_fd(path)
    source = output = None
    temporary_link = None
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        initial = os.fstat(source)
        check_row(row, initial)
        check_unused(initial, path, None)
        require(os.fstat(destfd).st_dev == DEST_DEVICE != initial.st_dev, 'NOT_SEPARATE_DESTINATION')
        space = os.fstatvfs(destfd)
        require(space.f_bavail * space.f_frsize > initial.st_size + 20 * 1024**3, 'DESTINATION_HEADROOM')
        target = hashlib.sha256(path.encode()).hexdigest()[:24] + '-' + name
        # No overwrites. Incomplete copies remain for audit, original stays untouched.
        output = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=destfd)
        sha, size = transfer(source, output, COPY_RATE, initial.st_size)
        require(size == initial.st_size, 'COPY_LENGTH_MISMATCH')
        os.fchown(output, initial.st_uid, initial.st_gid)
        os.fchmod(output, stat.S_IMODE(initial.st_mode))
        for key in os.listxattr(source):
            os.setxattr(output, key, os.getxattr(source, key))
        os.utime(output, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        os.fsync(output)
        os.fsync(destfd)
        before_hash = identity(os.fstat(output))
        os.lseek(output, 0, os.SEEK_SET)
        verified = transfer(output, None, READ_RATE, initial.st_size)
        require(verified == (sha, size), 'DESTINATION_HASH_MISMATCH')
        copied = os.fstat(output)
        require(identity(copied) == before_hash, 'DESTINATION_CHANGED_DURING_HASH')
        require((copied.st_uid, copied.st_gid, stat.S_IMODE(copied.st_mode)) ==
                (initial.st_uid, initial.st_gid, stat.S_IMODE(initial.st_mode)), 'DESTINATION_METADATA_MISMATCH')
        require({k: os.getxattr(source, k) for k in os.listxattr(source)} ==
                {k: os.getxattr(output, k) for k in os.listxattr(output)}, 'DESTINATION_XATTR_MISMATCH')
        unchanged(parent, name, source, initial)
        destination = DEST + '/' + target
        check_unused(initial, path, destination)
        journal({'phase': 'VERIFIED_COPY_BEFORE_REPLACE', 'source': path, 'destination': destination,
                 'sha256': sha, 'bytes': size, 'original_mode': stat.S_IMODE(initial.st_mode),
                 'original_uid': initial.st_uid, 'original_gid': initial.st_gid})
        temporary_link = '.portal-archive-link-' + hashlib.sha256(path.encode()).hexdigest()[:24]
        os.symlink(destination, temporary_link, dir_fd=parent)
        os.chown(temporary_link, initial.st_uid, initial.st_gid, dir_fd=parent, follow_symlinks=False)
        unchanged(parent, name, source, initial)
        require(identity(os.stat(target, dir_fd=destfd, follow_symlinks=False)) == identity(os.fstat(output)) == identity(copied), 'DESTINATION_CHANGED')
        # One atomic replacement: readers see the original or the verified retained copy.
        os.replace(temporary_link, name, src_dir_fd=parent, dst_dir_fd=parent)
        temporary_link = None
        os.fsync(parent)
        require(os.readlink(name, dir_fd=parent) == destination, 'SOURCE_LINK_READBACK_FAILED')
        require(os.stat(name, dir_fd=parent).st_ino == copied.st_ino and
                os.stat(name, dir_fd=parent).st_dev == copied.st_dev, 'SOURCE_LINK_TARGET_FAILED')
        return {'status': 'RELOCATED_VERIFIED_ORIGINAL_PATH_PRESERVED', 'source': path,
                'destination': destination, 'sha256': sha, 'bytes': size,
                'estimated_root_reclaimed_bytes': initial.st_blocks * 512}
    finally:
        if temporary_link is not None:
            try:
                os.unlink(temporary_link, dir_fd=parent)
            except FileNotFoundError:
                pass
        for fd in (output, source, parent):
            if fd is not None:
                os.close(fd)


def discover():
    require(len(PLAN) == 14 and len({r['path'] for r in PLAN}) == 14, 'PLAN_COUNT')
    require(sum(r['bytes'] for r in PLAN) < 16*1024**3, 'PLAN_LIMIT')
    require(all(allowed_source(r['path']) for r in PLAN), 'PLAN_PATHS')
    return sorted(PLAN, key=lambda r: -r['bytes']), []


def available(path):
    s = os.statvfs(path)
    return s.f_bavail * s.f_frsize


def interrupted(signum, frame):
    raise InterruptedError('OPERATOR_INTERRUPTED')





def log_metadata():
    rows = []
    try:
        with os.scandir('/var/log') as entries:
            for i, entry in enumerate(entries):
                if i >= 4096:
                    return {'status': 'ENTRY_LIMIT', 'rows': []}
                st = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) and st.st_dev == SOURCE_DEVICE and st.st_size >= 1024**2:
                    rows.append(dict(pin(entry.path, st), allocated_bytes=st.st_blocks*512,
                                     not_yet_checked_for_active_use=True))
        rows.sort(key=lambda r: r['allocated_bytes'], reverse=True)
        return {'status': 'METADATA_ONLY_NOT_CLEANED', 'rows': rows[:30], 'omitted': max(0,len(rows)-30)}
    except OSError as exc:
        return {'status': 'INCOMPLETE', 'error_class': type(exc).__name__}


def main():
    global DEADLINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relocate-offline-archives-once', required=True, action='store_true')
    parser.parse_args()
    require(socket.gethostname().split('.')[0] == 'ecs-zdz-pro-0015' and os.geteuid() == 0, 'HOST_OR_USER')
    require(os.stat('/').st_dev == SOURCE_DEVICE, 'ROOT_DEVICE_CHANGED')
    pfd, name = parent_fd(DEST)
    destfd = None
    try:
        opt = os.fstat(pfd)
        require(opt.st_dev == DEST_DEVICE and opt.st_uid == 0 and not opt.st_mode & 0o022, 'DESTINATION_MOUNT_OR_PERMISSIONS')
        require(opt.st_mode & 0o001, 'DATA_NOT_TRAVERSABLE_BY_ORIGINAL_USERS')
        require('system.posix_acl_access' not in os.listxattr(pfd) and
                'system.posix_acl_default' not in os.listxattr(pfd), 'DATA_CUSTOM_ACL_REQUIRES_REVIEW')
        require(os.path.ismount('/mnt/data'), 'DATA_NOT_MOUNTPOINT')
        os.nice(19)
        ionice = shutil.which('ionice', path='/usr/sbin:/usr/bin:/sbin:/bin')
        require(ionice is not None, 'IONICE_REQUIRED')
        require(subprocess.run([ionice, '-c', '3', '-p', str(os.getpid())], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=10).returncode == 0, 'IONICE_NOT_ACCEPTED')
        DEADLINE = time.monotonic() + 5400
        rows, discovery_skips = discover()
        total = sum(r['bytes'] for r in rows)
        require(available('/mnt/data') > total + 20 * 1024**3, 'DESTINATION_TOTAL_HEADROOM')
        os.mkdir(name, 0o700, dir_fd=pfd)  # Exclusive once claim; never resume blindly.
        destfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        os.fchmod(destfd, 0o700)  # Retained offline material is administrator-only; no broader access than the original home.
        os.fsync(pfd)
        def journal(row):
            fd = os.open('JOURNAL.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600, dir_fd=destfd)
            with os.fdopen(fd, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, sort_keys=True) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(destfd)
        journal({'phase': 'PLAN', 'plan': rows, 'discovery_skips': discovery_skips})
        before = available('/')
        started = time.monotonic()
        results = []
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        for i, row in enumerate(rows):
            print(json.dumps({'phase': 'VERIFY_COPY_AND_RELOCATE', 'checked': i, 'total': len(rows),
                              'file': os.path.basename(row['path'])}), flush=True)
            stop = False
            try:
                budget()
                result = relocate(row, destfd, journal)
            except (Exception, KeyboardInterrupt) as exc:
                result = {'source': row['path'], 'status': 'SKIPPED_OR_INCOMPLETE_REVIEW_JOURNAL',
                          'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__}
                stop = isinstance(exc, (InterruptedError, KeyboardInterrupt)) or str(exc) == 'TIME_LIMIT'
            results.append(result)
            journal(result)
            if stop:
                break
        moved = [r for r in results if r['status'] == 'RELOCATED_VERIFIED_ORIGINAL_PATH_PRESERVED']
        report = {'schema': 'portal-0015-offline-archive-relocation.v1', 'source_plan_sha256': PLAN_SHA256,
                  'status': 'ARCHIVES_RELOCATED_REVIEW_REQUIRED' if moved else 'NO_ARCHIVES_RELOCATED',
                  'planned': len(rows), 'processed': len(results), 'relocated': len(moved),
                  'skipped_or_incomplete': len(results) - len(moved), 'discovery_skips': discovery_skips,
                  'error_counts': {code: sum(r.get('error_code') == code for r in results)
                                   for code in sorted(set(r['error_code'] for r in results if 'error_code' in r))},
                  'results': results, 'before_root_available_bytes': before, 'after_root_available_bytes': available('/'),
                  'estimated_root_reclaimed_bytes': sum(r['estimated_root_reclaimed_bytes'] for r in moved),
                  'destination': DEST, 'original_paths_preserved_as_symlinks': bool(moved), 'retained_archives_must_not_be_deleted': True, 'retained_directory_access': 'ROOT_ONLY_USE_SUDO_FOR_RECOVERY',
                  'service_restart_requests': 0, 'containerd_commands': 0, 'database_access': False,
                  'business_files_selected': False, 'log_files_selected': False, 'log_metadata': log_metadata(), 'automatic_retry': False,
                  'elapsed_seconds': round(time.monotonic() - started, 3), 'current_preload_package_preserved': True, 'report_path': DEST + '/REPORT.json'}
        raw = (json.dumps(report, sort_keys=True) + '\n').encode()
        fd = os.open('REPORT.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destfd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(destfd)
        report.pop('results')
        report['report_sha256'] = hashlib.sha256(raw).hexdigest()
        print(json.dumps(report, sort_keys=True), flush=True)
    finally:
        if destfd is not None:
            os.close(destfd)
        os.close(pfd)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                          'automatic_retry': False, 'review_journal_if_present': DEST + '/JOURNAL.jsonl'}), flush=True)
        raise SystemExit(1)
