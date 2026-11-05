"""Reclaim only pinned redundant archive / closed historical logs; no image prune."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tarfile
import tempfile
import time

# Filled by the package builder from the operator's metadata inventory.
PLAN = {}
CONTROL = '/var/lib/portal-datax01-verified-cleanup-20261022-r1'
DEADLINE = 0


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def budget():
    require(not DEADLINE or time.monotonic() < DEADLINE, 'TIME_BUDGET')


def parent_fd(path):
    parts = PurePosixPath(path).parts
    require(parts[0] == '/' and '..' not in parts, 'PATH_SHAPE')
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


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


def open_pinned(row):
    parent, name = parent_fd(row['path'])
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, 'NOT_SINGLE_REGULAR_FILE')
            observed = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_uid)
            require(observed == tuple(row[k] for k in ('device', 'inode', 'bytes', 'mtime_ns', 'uid')), 'FILE_CHANGED')
            return parent, name, fd, info
        except BaseException:
            os.close(fd)
            raise
    except BaseException:
        os.close(parent)
        raise


def require_closed(info):
    target = (info.st_dev, info.st_ino)
    started = time.monotonic()
    count = 0
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        try:
            with os.scandir('/proc/' + pid + '/fd') as entries:
                for entry in entries:
                    count += 1
                    require(count < 200000 and time.monotonic() - started < 30, 'OPEN_FILE_SCAN_LIMIT')
                    try:
                        value = entry.stat()
                    except FileNotFoundError:
                        continue
                    require((value.st_dev, value.st_ino) != target, 'FILE_IN_USE')
        except (FileNotFoundError, ProcessLookupError):
            continue
    budget()


def digest_stream(stream, limit):
    digest, total = hashlib.sha256(), 0
    while True:
        budget()
        block = stream.read(1024 * 1024)
        if not block:
            return digest.hexdigest(), total
        total += len(block)
        require(total <= limit, 'CONTENT_LIMIT')
        digest.update(block)


def unchanged(parent, name, fd, initial):
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    require(identity(current) == identity(initial) == identity(os.fstat(fd)), 'FILE_CHANGED_DURING_CHECK')


def compress_log(row, journal):
    parent, name, fd, info = open_pinned(row)
    temp = None
    try:
        require_closed(info)
        try:
            os.stat(name + '.gz', dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise Refused('COMPRESSED_DESTINATION_EXISTS')
        space = os.fstatvfs(parent)
        require(space.f_bavail * space.f_frsize > info.st_size + 2 * 1024**3, 'COMPRESSION_HEADROOM')
        output_fd, temp = tempfile.mkstemp(prefix='.portal-compress-', dir=str(Path(row['path']).parent))
        digest = hashlib.sha256()
        total = 0
        with os.fdopen(output_fd, 'wb') as out, os.fdopen(os.dup(fd), 'rb') as src:
            with gzip.GzipFile(filename='', fileobj=out, mode='wb', compresslevel=6, mtime=0) as gz:
                while True:
                    budget()
                    block = src.read(1024 * 1024)
                    if not block:
                        break
                    total += len(block)
                    require(total <= info.st_size, 'SOURCE_GREW')
                    digest.update(block)
                    gz.write(block)
            out.flush()
            os.fchown(out.fileno(), info.st_uid, info.st_gid)
            os.fchmod(out.fileno(), stat.S_IMODE(info.st_mode))
            os.fsync(out.fileno())
        with gzip.open(temp, 'rb') as check:
            actual, size = digest_stream(check, info.st_size)
        require(size == info.st_size and actual == digest.hexdigest(), 'COMPRESSED_COPY_MISMATCH')
        compressed = os.stat(temp)
        require(compressed.st_blocks * 512 + 1024 * 1024 < info.st_blocks * 512, 'INSUFFICIENT_COMPRESSION_SAVING')
        unchanged(parent, name, fd, info)
        require_closed(info)
        os.utime(temp, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.link(Path(temp).name, name + '.gz', src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.fsync(parent)
        journal({'phase': 'VERIFIED_COMPRESSED_COPY', 'source': row['path'], 'sha256': actual, 'bytes': size})
        unchanged(parent, name, fd, info)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
        return {'status': 'COMPRESSED_AND_VERIFIED', 'path': row['path'], 'sha256': actual,
                'compressed_bytes': compressed.st_size,
                'estimated_reclaimed_bytes': max(0, info.st_blocks * 512 - compressed.st_blocks * 512)}
    finally:
        if temp is not None:
            try:
                os.unlink(Path(temp).name, dir_fd=parent)
            except FileNotFoundError:
                pass
        os.close(fd)
        os.close(parent)


def verify_archive(fd, root, limit):
    count, total = 0, 0
    seen = set()
    copies = []
    with os.fdopen(os.dup(fd), 'rb') as stream, tarfile.open(fileobj=stream, mode='r:') as archive:
        for member in archive:
            budget()
            parts = PurePosixPath(member.name).parts
            require(not member.name.startswith('/') and '..' not in parts, 'UNSAFE_ARCHIVE_PATH')
            if member.isdir():
                continue
            require(member.isreg() and not member.issparse() and not member.linkname, 'UNSUPPORTED_ARCHIVE_MEMBER')
            require(parts and member.name not in seen, 'DUPLICATE_ARCHIVE_MEMBER')
            seen.add(member.name)
            # Both ordinary contents-only and one top-directory archive layouts.
            if parts[0] == Path(root).name:
                parts = parts[1:]
            require(parts, 'EMPTY_ARCHIVE_PATH')
            path = str(Path(root).joinpath(*parts))
            parent, name = parent_fd(path)
            target_fd = None
            try:
                target_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
                before = os.fstat(target_fd)
                require(stat.S_ISREG(before.st_mode) and before.st_size == member.size, 'EXTRACTED_COPY_SHAPE')
                with archive.extractfile(member) as packed, os.fdopen(os.dup(target_fd), 'rb') as copy:
                    packed_hash, packed_bytes = digest_stream(packed, limit)
                    copy_hash, copy_bytes = digest_stream(copy, limit)
                require((packed_hash, packed_bytes) == (copy_hash, copy_bytes), 'EXTRACTED_COPY_MISMATCH')
                unchanged(parent, name, target_fd, before)
                copies.append((path, identity(before)))
            finally:
                if target_fd is not None:
                    os.close(target_fd)
                os.close(parent)
            count += 1
            total += member.size
            require(count <= 4096 and total <= limit, 'ARCHIVE_EXPANSION_LIMIT')
    require(count > 0, 'EMPTY_ARCHIVE')
    for path, expected in copies:
        parent, name = parent_fd(path)
        try:
            require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == expected, 'EXTRACTED_COPY_CHANGED')
        finally:
            os.close(parent)
    return count, total


def remove_redundant_archive(row, root, journal):
    parent, name, fd, info = open_pinned(row)
    try:
        require_closed(info)
        count, total = verify_archive(fd, root, info.st_size)
        unchanged(parent, name, fd, info)
        require_closed(info)
        journal({'phase': 'COMPLETE_EXTRACTED_COPY_VERIFIED', 'source': row['path'], 'files': count, 'bytes': total})
        unchanged(parent, name, fd, info)
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
        return {'status': 'REDUNDANT_ARCHIVE_REMOVED', 'path': row['path'], 'verified_files': count,
                'estimated_reclaimed_bytes': info.st_blocks * 512}
    finally:
        os.close(fd)
        os.close(parent)


def main():
    global DEADLINE
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cleanup-verified-files-once', action='store_true', required=True)
    p.parse_args()
    require(os.uname().nodename.split('.')[0] == 'datax-01' and os.geteuid() == 0, 'HOST_OR_USER')
    require(PLAN and len(PLAN['logs']) <= 24 and len(PLAN.get('packages', [])) <= 10, 'PLAN_SHAPE')
    DEADLINE = time.monotonic() + 1800
    os.mkdir(CONTROL, 0o700)
    journal_path = Path(CONTROL) / 'JOURNAL.jsonl'
    def journal(row):
        with journal_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    before = os.statvfs('/')
    rows = []
    jobs = [('archive', PLAN['archive'])] + [('package', row) for row in PLAN.get('packages', [])] + [('log', row) for row in PLAN['logs']]
    for kind, row in jobs:
        print(json.dumps({'phase': 'VERIFY_AND_RECLAIM', 'checked': len(rows), 'total': len(jobs), 'path': row['path']}), flush=True)
        try:
            budget()
            result = remove_redundant_archive(row, PLAN['extracted_root'], journal) if kind == 'archive' else compress_log(row, journal)
        except Exception as exc:
            result = {'path': row['path'], 'status': 'SKIPPED_OR_INCOMPLETE',
                      'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__}
        rows.append(result)
        journal(result)
    after = os.statvfs('/')
    result = {'schema': 'portal-datax01-verified-cleanup.v1', 'status': 'CLEANUP_PASS_FINISHED_REVIEW_REQUIRED',
              'results': rows, 'before_available_bytes': before.f_bavail * before.f_frsize,
              'after_available_bytes': after.f_bavail * after.f_frsize,
              'estimated_reclaimed_bytes': sum(r.get('estimated_reclaimed_bytes', 0) for r in rows),
              'services_restarted': False, 'containerd_commands': 0, 'database_access': False,
              'automatic_retry': False, 'report_path': CONTROL + '/REPORT.json'}
    raw = json.dumps(result, sort_keys=True).encode() + b'\n'
    with (Path(CONTROL) / 'REPORT.json').open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    result['report_sha256'] = hashlib.sha256(raw).hexdigest()
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__, 'automatic_retry': False}))
        raise SystemExit(1)
