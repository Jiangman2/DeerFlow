"""Stage and replace one public Skill; no credential, provider or application access."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import zipfile

ROOT = Path('/mnt/skills/public/energy-market-daily')
PARENT = Path('/mnt/skills')
OLD_TREE = '3ecfce5688c0db0d0b8b703ad6fa614a24b8c8fefc22b5725657c7f507503a96'
NEW_TREE = 'f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044'
PACKAGE_SHA = '7964309dda73a26f7679ae48b98e2dcfb1aa1cefdc0bb9d2a8c09f2e06562610'
OLD_COUNT, NEW_COUNT = 163, 105

def require(ok, code):
    if not ok:
        raise ValueError(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')

def safe(path):
    path = Path(path)
    for p in (path, *path.parents):
        try:
            s = p.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(s.st_mode) and not (getattr(s, 'st_file_attributes', 0) & 0x400),
                'LINK_OR_REPARSE_REFUSED')
    return path

def identity(path):
    s = safe(path).stat()
    require(stat.S_ISDIR(s.st_mode), 'DIRECTORY_REQUIRED')
    return [s.st_dev, s.st_ino]

def read(path, limit=6 * 1024 * 1024):
    path = safe(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                'FILE_TYPE_LINKS_OR_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as f:
            raw = f.read(limit + 1)
        after = os.fstat(fd)
        require(len(raw) == before.st_size and len(raw) <= limit and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'FILE_CHANGED_DURING_READ')
        return raw
    finally:
        os.close(fd)

def write_new(path, raw, mode=0o600):
    path = safe(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
    finally:
        os.close(fd)
    os.chmod(path, mode)

def sync_directory(path):
    if os.name != 'nt':
        fd = os.open(str(safe(path)), os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

def save(path, value):
    path = safe(path)
    tmp = path.with_name('.journal-' + os.urandom(8).hex())
    write_new(tmp, canonical(value) + b'\n')
    os.replace(tmp, path)
    sync_directory(path.parent)

def inventory(root):
    identity(root)
    rows, total = [], 0
    for directory, folders, names in os.walk(root, followlinks=False):
        for name in folders:
            identity(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            raw = read(path)
            total += len(raw)
            rows.append(dict(path=path.relative_to(root).as_posix(), bytes=len(raw), sha256=sha(raw)))
            require(len(rows) <= 512 and total <= 32 * 1024 * 1024, 'TREE_BOUND')
    return sorted(rows, key=lambda row: row['path'])

def tree_hash(rows):
    return sha(canonical(rows))

def verify_tree(root, expected_hash, expected_count):
    rows = inventory(root)
    require(len(rows) == expected_count and tree_hash(rows) == expected_hash, 'SKILL_TREE_CHANGED')
    return rows


def release_path(operation):
    require(isinstance(operation, str) and re.fullmatch('[0-9a-f]{32}', operation), 'OPERATION_ID_REQUIRED')
    return safe(PARENT / ('.energy-portable-release-20261111-r1-' + operation))


def stage(operation, encoded):
    release = release_path(operation)
    root_identity = identity(ROOT)
    original = verify_tree(ROOT, OLD_TREE, OLD_COUNT)
    require(not release.exists(), 'RELEASE_EXISTS_REVIEW_REQUIRED')
    release.mkdir(mode=0o700)
    backup, candidate = release / 'backup', release / 'candidate'
    shutil.copytree(ROOT, backup)
    require(inventory(backup) == original and identity(ROOT) == root_identity, 'BACKUP_OR_LIVE_IDENTITY_CHANGED')
    verify_tree(ROOT, OLD_TREE, OLD_COUNT)
    raw = base64.b64decode(encoded, validate=True)
    require(sha(raw) == PACKAGE_SHA, 'PACKAGE_CHANGED')
    candidate.mkdir(mode=0o755)
    os.chmod(candidate, 0o755)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        require(len(archive.infolist()) == NEW_COUNT, 'PACKAGE_COUNT')
        for info in archive.infolist():
            require(info.filename.startswith('energy-market-daily/') and not info.is_dir() and info.file_size <= 1048576, 'PACKAGE_LAYOUT')
            relative = info.filename[len('energy-market-daily/'):]
            target = candidate / relative
            require(target.resolve().is_relative_to(candidate) and '.private' not in target.parts, 'PACKAGE_PATH')
            target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            for directory in (target.parent, *target.parent.parents):
                if directory == candidate or candidate in directory.parents:
                    os.chmod(directory, 0o755)
            write_new(target, archive.read(info), 0o644)
    verify_tree(candidate, NEW_TREE, NEW_COUNT)
    receipt = {'status': 'PORTABLE_SKILL_STAGED', 'operation': operation, 'live_before_identity': root_identity,
               'before_tree_sha256': OLD_TREE, 'after_tree_sha256': NEW_TREE, 'before_files': OLD_COUNT, 'after_files': NEW_COUNT,
               'backup_path': str(backup), 'release_path': str(release), 'skill_modified': False, 'credentials_included': False}
    save(release / 'STAGED.json', receipt)
    return receipt


def publish(operation):
    release = release_path(operation)
    receipt = json.loads(read(release / 'STAGED.json'))
    require(receipt['status'] == 'PORTABLE_SKILL_STAGED' and receipt['operation'] == operation, 'STAGE_RECEIPT_REQUIRED')
    require(identity(ROOT) == receipt['live_before_identity'], 'LIVE_INODE_CHANGED')
    verify_tree(ROOT, OLD_TREE, OLD_COUNT)
    verify_tree(release / 'backup', OLD_TREE, OLD_COUNT)
    verify_tree(release / 'candidate', NEW_TREE, NEW_COUNT)
    retired = release / 'retired-original'
    require(not retired.exists() and not retired.is_symlink() and not (release / 'SWITCH.json').exists(), 'SWITCH_ALREADY_REQUESTED')
    save(release / 'SWITCH.json', {'phase': 'BEFORE_FIRST_RENAME', 'operation': operation, 'automatic_rollback': False})
    os.rename(ROOT, retired)
    sync_directory(ROOT.parent)
    save(release / 'SWITCH.json', {'phase': 'OLD_RETIRED_NEW_NOT_INSTALLED', 'operation': operation, 'automatic_rollback': False})
    os.rename(release / 'candidate', ROOT)
    sync_directory(ROOT.parent)
    verify_tree(ROOT, NEW_TREE, NEW_COUNT)
    verify_tree(retired, OLD_TREE, OLD_COUNT)
    require(identity(retired) == receipt['live_before_identity'], 'RETIRED_INODE_CHANGED')
    result = {'status': 'PORTABLE_SKILL_INSTALLED', 'operation': operation, 'before_tree_sha256': OLD_TREE, 'after_tree_sha256': NEW_TREE,
              'backup_path': str(release / 'backup'), 'retired_path': str(retired), 'skill_modified': True,
              'old_skill_removed_from_discovery': True, 'old_files_retained_outside_public': True,
              'historical_reports_modified': False, 'automatic_rollback': False, 'switch_strategy': 'TWO_RENAMES_WITH_SHORT_NAME_GAP'}
    save(release / 'SWITCH.json', result)
    return result


def verify():
    verify_tree(ROOT, NEW_TREE, NEW_COUNT)
    return {'status': 'PORTABLE_SKILL_TREE_VERIFIED', 'tree_sha256': NEW_TREE, 'files': NEW_COUNT}


def main():
    out = {'status': 'INCOMPLETE', 'credentials_exported': False, 'provider_requests': 0, 'database_access': False}
    try:
        sys.dont_write_bytecode = True
        raw = sys.stdin.buffer.read(262145)
        require(len(raw) <= 262144, 'INPUT_BOUND')
        payload = json.loads(raw)
        # Publication code needs no network, subprocess, database or secrets.
        def audit(event, args):
            if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system', 'sqlite3.connect'):
                raise RuntimeError('EXTERNAL_OPERATION_FORBIDDEN')
        sys.addaudithook(audit)
        action = payload['action']
        require(action in ('stage', 'publish', 'verify'), 'FIXED_ACTION_REQUIRED')
        result = stage(payload['operation'], payload['package']) if action == 'stage' else publish(payload['operation']) if action == 'publish' else verify()
        out.update(result)
    except Exception as exc:
        allowed = {'OPERATION_ID_REQUIRED', 'RELEASE_EXISTS_REVIEW_REQUIRED', 'BACKUP_OR_LIVE_IDENTITY_CHANGED', 'PACKAGE_CHANGED', 'PACKAGE_COUNT',
                   'PACKAGE_LAYOUT', 'PACKAGE_PATH', 'STAGE_RECEIPT_REQUIRED', 'LIVE_INODE_CHANGED', 'SWITCH_ALREADY_REQUESTED', 'RETIRED_INODE_CHANGED',
                   'INPUT_BOUND', 'FIXED_ACTION_REQUIRED', 'SKILL_TREE_CHANGED', 'DIRECTORY_REQUIRED', 'TREE_BOUND', 'LINK_OR_REPARSE_REFUSED', 'FILE_TYPE_LINKS_OR_BOUND', 'FILE_CHANGED_DURING_READ'}
        out.update(error_class=type(exc).__name__, error_code=str(exc) if type(exc) is ValueError and str(exc) in allowed else 'PUBLICATION_FAILED_REDACTED')
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())

