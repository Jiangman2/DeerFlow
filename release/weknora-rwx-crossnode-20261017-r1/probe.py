"""Injected into a nonroot, isolated probe Pod. No network client or app imports."""
import hashlib
import json
import os
import re
import stat
import sys
import time

MOUNTS = ('/probe/files', '/probe/docreader')
SERVER = '192.168.111.189'
PREFIX = '/mnt/data/weknora-rwx-r2/'
SIZE = 65536


def need(ok, message):
    if not ok:
        raise RuntimeError(message)


def payload(token, role, letter):
    return hashlib.sha256((token + role + letter).encode()).digest() * (SIZE // 32)


def mount_check(path, text):
    rows = []
    for line in text.splitlines():
        left, sep, right = line.partition(' - ')
        a, b = left.split(), right.split()
        if sep and len(a) >= 6 and len(b) >= 3 and a[4] == path:
            rows.append((a, b))
    need(len(rows) == 1, 'exact_mount_missing_or_ambiguous')
    a, b = rows[0]
    opts = set(a[5].split(',') + b[2].split(','))
    need(b[0] in ('nfs4', 'nfs') and b[1] == SERVER + ':' + PREFIX + path.rsplit('/', 1)[1],
         'unexpected_nfs_source')
    need({'rw', 'hard', 'vers=4.1'} <= opts and 'soft' not in opts, 'unexpected_nfs_options')
    return dict(protocol='NFSv4.1', hard=True, read_write=True)


def directory_fd(path, uid, mode):
    # Reject symlinks in each ancestor, including the mount root itself.
    current = '/'
    for part in path.strip('/').split('/'):
        current = os.path.join(current, part)
        need(stat.S_ISDIR(os.lstat(current).st_mode), 'not_real_directory')
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    s = os.fstat(fd)
    if not (s.st_uid == uid and s.st_gid == uid and stat.S_IMODE(s.st_mode) == mode):
        os.close(fd)
        raise RuntimeError('directory_identity_or_mode')
    return fd


def verify_file(fd, name, expected):
    handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(handle, 'rb') as stream:
        s = os.fstat(stream.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 1000 and s.st_gid == 1000
             and s.st_nlink == 1 and s.st_size == SIZE and stat.S_IMODE(s.st_mode) == 0o600,
             'synthetic_file_identity')
        need(stream.read(SIZE + 1) == expected, 'synthetic_checksum_mismatch')


def publish(fd, letter, data):
    part, target = letter + '.part', letter + '.bin'
    need(not {part, target} & set(os.listdir(fd)), 'synthetic_name_exists_no_retry')
    handle = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
    with os.fdopen(handle, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    # Only the two controlled clients can reach the unique directory in this run.
    need(target not in os.listdir(fd), 'rename_target_exists')
    os.rename(part, target, src_dir_fd=fd, dst_dir_fd=fd)
    os.fsync(fd)
    verify_file(fd, target, data)


def run(phase, token):
    need(phase in ('write-a', 'read-a-write-b', 'read-b', 'cleanup'), 'phase_invalid')
    need(re.fullmatch('[0-9a-f]{32}', token), 'token_invalid')
    need(os.geteuid() == 1000 and os.getegid() == 1000, 'not_uid_gid_1000')
    os.umask(0o077)
    with open('/proc/self/mountinfo', 'r') as stream:
        mountinfo = stream.read(1048577)
    need(len(mountinfo) <= 1048576, 'mountinfo_oversize')
    # Verify BOTH mounts before doing anything to either one.
    mounts = [mount_check(path, mountinfo) for path in MOUNTS]
    rows = []
    for path, mounted in zip(MOUNTS, mounts):
        start = time.monotonic()
        role = path.rsplit('/', 1)[1]
        root = directory_fd(path, 1000, 0o770)
        name = '.weknora-rwx-probe-' + token
        fd = None
        try:
            v = os.fstatvfs(root)
            need(v.f_bavail * v.f_frsize >= 1024**3 and v.f_favail >= 1000, 'nfs_headroom_low')
            if phase == 'write-a':
                os.mkdir(name, mode=0o700, dir_fd=root)
                os.fsync(root)
            fd = directory_fd(path + '/' + name, 1000, 0o700)
            a, b = payload(token, role, 'a'), payload(token, role, 'b')
            if phase == 'write-a':
                need(not os.listdir(fd), 'probe_directory_not_empty')
                publish(fd, 'a', a)
            elif phase == 'read-a-write-b':
                need(set(os.listdir(fd)) == {'a.bin'}, 'unexpected_probe_members')
                verify_file(fd, 'a.bin', a)
                publish(fd, 'b', b)
            else:
                need(set(os.listdir(fd)) == {'a.bin', 'b.bin'}, 'unexpected_probe_members')
                verify_file(fd, 'a.bin', a)
                verify_file(fd, 'b.bin', b)
                if phase == 'cleanup':
                    # No recursive deletion, and only after both hashes match.
                    for item in ('a.bin', 'b.bin'):
                        os.unlink(item, dir_fd=fd)
                    os.fsync(fd)
                    os.close(fd)
                    fd = None
                    os.rmdir(name, dir_fd=root)
                    os.fsync(root)
            rows.append(dict(role=role, result='PASS', mount=mounted,
                             elapsed_ms=round((time.monotonic() - start) * 1000, 2),
                             available_bytes=v.f_bavail * v.f_frsize,
                             synthetic_file_bytes=SIZE))
        finally:
            if fd is not None:
                os.close(fd)
            os.close(root)
    return dict(probe='PASS', phase=phase, token=token, uid=1000, gid=1000, volumes=rows,
                provider_requests=0, business_data_accessed=False)


if __name__ == '__main__':
    try:
        need(len(sys.argv) == 3, 'argument_count')
        print(json.dumps(run(sys.argv[1], sys.argv[2]), sort_keys=True), flush=True)
    except Exception as exc:
        print(json.dumps(dict(probe='UNCONFIRMED', error_type=type(exc).__name__,
                              check=str(exc) if type(exc) is RuntimeError else 'OS_IO_ERROR',
                              raw_error_printed=False)), flush=True)
        sys.exit(1)
