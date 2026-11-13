"""Read only the small 2.0.1 Skill tree; never open browser dependency files."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys

COUNT = 107
MAX_TOTAL = 32 * 1024 * 1024
RUNTIME_DIRECTORY = '../../.energy-browser-runtime-20261112-r1/linux-arm64'


def need(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def no_links(path):
    for parent in (path,) + tuple(path.parents):
        state = parent.lstat()
        need(not stat.S_ISLNK(state.st_mode), 'SKILL_LINK_REFUSED')


def stamp(state):
    value = state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns
    return value if os.name == 'nt' else value + (state.st_ctime_ns,)


def read(path):
    no_links(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= 1048576, 'SKILL_FILE_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as incoming:
            raw = incoming.read(1048577)
        need(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), 'SKILL_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def inventory(root):
    no_links(root)
    need(root.is_dir(), 'SKILL_DIRECTORY_REQUIRED')
    identity = root.stat().st_dev, root.stat().st_ino
    rows, total, folders_seen = [], 0, 0
    def failed(_):
        raise ValueError('SKILL_DIRECTORY_UNREADABLE')
    for directory, folders, names in os.walk(root, followlinks=False, onerror=failed):
        folders_seen += len(folders)
        need(folders_seen <= 128, 'SKILL_DIRECTORY_BOUND')
        for folder in folders:
            path = Path(directory) / folder
            no_links(path)
            need(path.is_dir() and folder != '.private', 'SKILL_DIRECTORY_INVALID')
        for name in names:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            need(len(relative) <= 512 and not set(PurePosixPath(relative).parts) & {'.private', '..'} and '\\' not in relative, 'SKILL_FILE_PATH')
            raw = read(path)
            total += len(raw)
            rows.append({'path': relative, 'bytes':len(raw), 'sha256':sha(raw)})
            need(len(rows) <= COUNT and total <= MAX_TOTAL, 'SKILL_TREE_BOUND')
    need((root.stat().st_dev, root.stat().st_ino) == identity, 'SKILL_DIRECTORY_CHANGED')
    return sorted(rows, key=lambda row: row['path'])


def inspect(root, expected_tree, runtime_manifest):
    need(root.is_absolute() and '..' not in root.parts and re.fullmatch('[0-9a-f]{64}', expected_tree)
         and re.fullmatch('[0-9a-f]{64}', runtime_manifest), 'SKILL_READER_ARGUMENTS')
    first, second = inventory(root), inventory(root)
    need(first == second and len(second) == COUNT and sha(canonical(second)) == expected_tree, 'SKILL_TREE_CHANGED')
    files = {row['path']:{'bytes':row['bytes'],'sha256':row['sha256']} for row in second if row['path'] != 'MANIFEST.json'}
    manifest_raw = read(root / 'MANIFEST.json')
    manifest = json.loads(manifest_raw)
    need(manifest.get('schema') == 'energy-daily-portable-files.v1' and manifest.get('version') == '2.0.1-portable'
         and manifest.get('files') == files, 'SKILL_MANIFEST_CHANGED')
    binding = json.loads(read(root / 'assets/browser-runtime.json'))
    need(binding == {'schema':'energy-browser-runtime-binding.v1','directory':RUNTIME_DIRECTORY,'manifest_sha256':runtime_manifest}, 'BROWSER_BINDING_CHANGED')
    # The runtime dependency is deliberately outside root. Its existence,
    # binary hashes and real PNG were accepted by the installer qualification.
    # This Gateway reader reconciles only the small binding file.
    need(inventory(root) == second, 'SKILL_TREE_CHANGED')
    return {'status':'ENERGY_BROWSER_FIX_SKILL_TREE_VERIFIED','tree_sha256':expected_tree,'files':COUNT,
            'skill_manifest_sha256':sha(manifest_raw),'runtime_manifest_sha256':runtime_manifest,
            'read_only':True,'runtime_dependency_files_read':0,'browser_launched':False,'credentials_read':False}


def main():
    out = {'status':'INCOMPLETE','read_only':True,'runtime_dependency_files_read':0,'browser_launched':False,'credentials_read':False}
    try:
        need(len(sys.argv) == 4, 'SKILL_READER_ARGUMENTS')
        if os.name == 'posix':
            def expired(*_):
                raise ValueError('SKILL_READER_TIME_BOUND')
            signal.signal(signal.SIGALRM, expired)
            signal.alarm(25)
        def audit(event, args):
            if event.startswith('socket.') or event in ('subprocess.Popen','os.system','sqlite3.connect'):
                raise ValueError('SKILL_READER_EXTERNAL_OPERATION')
            if event == 'open' and len(args) > 2 and isinstance(args[2], int):
                need(not args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND), 'SKILL_READER_WRITE_REFUSED')
        sys.addaudithook(audit)
        out.update(inspect(Path(sys.argv[1]), sys.argv[2], sys.argv[3]))
    except Exception as exc:
        out.update(error_class=type(exc).__name__,error_code=str(exc) if re.fullmatch('[A-Z0-9_]{1,100}',str(exc)) else 'SKILL_READER_FAILED_REDACTED')
    finally:
        if os.name == 'posix':
            signal.alarm(0)
    print(json.dumps(out,sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())
