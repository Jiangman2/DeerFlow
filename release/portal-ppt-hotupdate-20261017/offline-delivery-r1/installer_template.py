"""Operator-owned offline extraction; shell calls the existing preload only on success."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import socket
import stat
import sys
import tarfile
import time

EXPECTED = __EXPECTED_FILES__
BUNDLE_BYTES = __BUNDLE_BYTES__
BUNDLE_SHA256 = '__BUNDLE_SHA256__'
ARCHIVE = '/home/cnooc/portal-ppt-native-offline-20261017-r1.tar'
DESTINATION = '/home/cnooc/portal-ppt-native-preload-20261017-r1'


class InvalidBundle(Exception):
    pass


def check(ok, message):
    if not ok:
        raise InvalidBundle(message)


def identity(s):
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def extract_bundle(archive_path, destination, expected, bundle_bytes, bundle_sha256):
    """No extractall, links, overwrites, root writes, or executable archive metadata."""
    destination = Path(destination)
    check(not os.path.lexists(str(destination)), 'destination_exists_preserve_for_review')
    check(len(expected) == 8 and len({r['name'] for r in expected}) == 8, 'invalid_fixed_file_list')
    rows = {r['name']: r for r in expected}
    check(all(Path(n).name == n and n not in ('.', '..') and '/' not in n and '\\' not in n for n in rows), 'invalid_fixed_basename')
    fs = os.statvfs(str(destination.parent))
    check(fs.f_bavail * fs.f_frsize >= sum(r['bytes'] for r in expected) + 2 * 1024 ** 3, 'insufficient_extract_space')
    source_fd = os.open(str(archive_path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    directory_fd = None
    try:
        before = os.fstat(source_fd)
        check(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == bundle_bytes, 'bundle_size_or_type_mismatch')
        with os.fdopen(source_fd, 'rb', closefd=False) as stream:
            whole = hashlib.sha256()
            total, last = 0, time.monotonic()
            while True:
                data = stream.read(4 * 1024 ** 2)
                if not data:
                    break
                whole.update(data)
                total += len(data)
                check(total <= bundle_bytes, 'bundle_grew')
                if time.monotonic() - last >= 20:
                    print(json.dumps({'offline_progress': 'verify_bundle', 'bytes': total}), flush=True)
                    last = time.monotonic()
            check(total == bundle_bytes and whole.hexdigest() == bundle_sha256, 'bundle_sha256_mismatch')
            check(identity(before) == identity(os.fstat(source_fd)), 'bundle_changed')
            stream.seek(0)
            with tarfile.open(fileobj=stream, mode='r:') as tar:
                entries = {}
                for member in tar:
                    check(len(entries) < 8, 'extra_tar_member')
                    check(member.name in rows and member.name not in entries, 'unexpected_or_duplicate_tar_member')
                    check(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not member.linkname and not member.pax_headers, 'nonregular_tar_member')
                    check(member.size == rows[member.name]['bytes'], 'member_size_mismatch')
                    entries[member.name] = member
                check(set(entries) == set(rows) and not tar.pax_headers, 'incomplete_tar_members')
                os.mkdir(str(destination), 0o700)
                directory_fd = os.open(str(destination), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                ds = os.fstat(directory_fd)
                check(ds.st_uid == os.geteuid() and stat.S_IMODE(ds.st_mode) == 0o700, 'invalid_new_directory')
                for row in expected:
                    print(json.dumps({'offline_progress': 'extract_verified_file', 'file': row['name']}), flush=True)
                    target_fd = os.open(row['name'], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
                    h, count = hashlib.sha256(), 0
                    with os.fdopen(target_fd, 'wb') as target, tar.extractfile(entries[row['name']]) as source:
                        while True:
                            data = source.read(4 * 1024 ** 2)
                            if not data:
                                break
                            count += len(data)
                            check(count <= row['bytes'], 'member_grew')
                            h.update(data)
                            target.write(data)
                        target.flush()
                        os.fsync(target.fileno())
                    check(count == row['bytes'] and h.hexdigest() == row['sha256'], 'extracted_file_sha256_mismatch')
                check(identity(before) == identity(os.fstat(source_fd)), 'bundle_changed_during_extraction')
                check((ds.st_dev, ds.st_ino) == (destination.lstat().st_dev, destination.lstat().st_ino), 'destination_replaced')
                os.fsync(directory_fd)
        return {'offline_install': 'VERIFIED', 'directory': str(destination), 'files': 8, 'bundle_sha256': bundle_sha256}
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        os.close(source_fd)


def main():
    check(os.geteuid() != 0 and os.geteuid() == pwd.getpwnam('cnooc').pw_uid,
          'run_with_bash_as_cnooc_not_sudo_bash')
    check(socket.gethostname().split('.')[0] == 'datax-01', 'wrong_host')
    os.umask(0o077)
    print(json.dumps(extract_bundle(ARCHIVE, DESTINATION, EXPECTED, BUNDLE_BYTES, BUNDLE_SHA256)), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, InvalidBundle) else type(exc).__name__
        print(json.dumps({'offline_install': 'INCOMPLETE', 'error': code, 'automatic_retry': False,
                          'instruction': 'Preserve existing files and return this output for review.'}), file=sys.stderr, flush=True)
        raise SystemExit(1)
