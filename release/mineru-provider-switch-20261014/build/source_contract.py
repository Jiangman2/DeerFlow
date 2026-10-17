"""Pinned legacy source evidence; no execution or chmod of production source."""
import hashlib
import os
from pathlib import Path
import stat

SOURCE_PATH = Path('/app/deerflow_mineru_adapter.py')
BASE_SHA = '1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39'
CANDIDATE_SHA = 'a509a7e145ba3d5d72ba86036437a9e5da2e46678054558522a6d61b66a22e74'
BASE_METADATA = {'uid': 0, 'gid': 0, 'links': 1, 'bytes': 119416, 'mode': '0666'}


class SourceCheck(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise SourceCheck(label)


def validate_plan(plan):
    require(plan.get('path') == str(SOURCE_PATH) and plan.get('before_sha256') == BASE_SHA
            and plan.get('after_sha256') == CANDIDATE_SHA and plan.get('before_metadata') == BASE_METADATA
            and plan.get('candidate_mode') == '0644', 'source_permission_contract_changed')


def read_verified(path, expected, mode=None, size=None):
    path = Path(path)
    require(path.is_absolute() and path.resolve() == path, 'noncanonical_source')
    for p in path.parents:
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_source_parent')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_gid == 0 and s.st_nlink == 1
                and 0 < s.st_size < 2*1024**2, 'unsafe_source_file')
        # A writable file is accepted ONLY for the single exact legacy identity.
        if mode == 0o666:
            require(path == SOURCE_PATH and expected == BASE_SHA and size == BASE_METADATA['bytes'],
                    'legacy_source_identity_not_pinned')
        else:
            require(mode is None or not mode & 0o022, 'unapproved_writable_mode')
        require((stat.S_IMODE(s.st_mode) == mode if mode is not None else not s.st_mode & 0o022)
                and (size is None or s.st_size == size), 'source_metadata_changed')
        chunks, remaining = [], s.st_size + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b''.join(chunks)
        identity = lambda x: (x.st_dev, x.st_ino, x.st_mode, x.st_uid, x.st_gid, x.st_nlink,
                              x.st_size, x.st_mtime_ns, x.st_ctime_ns)
        require(len(raw) == s.st_size and identity(s) == identity(os.fstat(fd))
                == identity(path.lstat()), 'source_changed_during_read')
        require(hashlib.sha256(raw).hexdigest() == expected, 'source_hash_mismatch')
        return s
    finally:
        os.close(fd)


def check_base(path, plan):
    validate_plan(plan)
    require(Path(path) == SOURCE_PATH, 'unexpected_legacy_source_path')
    return read_verified(path, BASE_SHA, mode=0o666, size=BASE_METADATA['bytes'])


def probe_base():
    try:
        read_verified(SOURCE_PATH, BASE_SHA, mode=0o666, size=BASE_METADATA['bytes'])
        return dict(source_probe='PASS', source_sha256=BASE_SHA, metadata=BASE_METADATA)
    except (SourceCheck, OSError) as exc:
        return dict(source_probe='FAIL', failed_check=str(exc) if isinstance(exc, SourceCheck)
                    else 'source_read_os_error', error_type=type(exc).__name__)
