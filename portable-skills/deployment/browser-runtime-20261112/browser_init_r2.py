"""Copy one image-owned browser directory into a read-only nonroot handoff."""
import hashlib
import json
import os
from pathlib import Path
import stat

SOURCE = Path('/root/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux')
DESTINATION = Path('/stage')
MAX_FILES = 256
MAX_BYTES = 512 * 1024 * 1024
IMAGE_ID = 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
STEP = 'start'
PROGRESS = {'files': 0, 'bytes': 0}


def metadata(path):
    try:
        state = path.lstat()
        return {'uid': state.st_uid, 'gid': state.st_gid, 'mode': stat.S_IMODE(state.st_mode), 'links': state.st_nlink,
                'bytes': state.st_size, 'symlink': stat.S_ISLNK(state.st_mode), 'directory': stat.S_ISDIR(state.st_mode),
                'regular': stat.S_ISREG(state.st_mode), 'readable': os.access(str(path), os.R_OK), 'searchable': os.access(str(path), os.X_OK)}
    except OSError as exc:
        return {'error_class': type(exc).__name__, 'errno': exc.errno}


def diagnostic():
    paths = {'writable': Path('/writable'), 'destination': DESTINATION, 'source': SOURCE, 'fixed_executable': SOURCE / 'headless_shell'}
    paths.update({'source_parent_%d' % index: path for index, path in enumerate(SOURCE.parents)})
    return {'stage': STEP, 'identity': {'uid': os.geteuid(), 'gid': os.getegid(), 'groups': os.getgroups()},
            'paths': {name: metadata(path) for name, path in paths.items()}, 'progress': PROGRESS,
            'source_contents_exported': False, 'credentials_read': False}


def need(ok, code):
    if not ok:
        raise ValueError(code)


def no_links(path):
    for part in (path,) + tuple(path.parents):
        need(not part.is_symlink(), 'SOURCE_LINK_REFUSED')


def stamp(state):
    return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)


def copy_browser(source=SOURCE, destination=DESTINATION):
    global STEP
    STEP = 'source_directory_guard'
    no_links(source)
    need(source.is_dir() and destination.is_dir(), 'BROWSER_DIRECTORY_REQUIRED')
    target = destination / 'chrome-linux'
    target.mkdir(mode=0o755)
    rows, total = [], 0
    for directory, folders, names in os.walk(str(source), followlinks=False):
        current = Path(directory)
        no_links(current)
        relative = current.relative_to(source)
        (target / relative).mkdir(mode=0o755, exist_ok=True)
        for name in folders:
            no_links(current / name)
            need((current / name).is_dir(), 'BROWSER_DIRECTORY_TYPE')
        for name in sorted(names):
            path = current / name
            STEP = 'source_file_guard'
            PROGRESS['current_relative_sha256'] = hashlib.sha256(path.relative_to(source).as_posix().encode()).hexdigest()
            PROGRESS['current_file_metadata'] = metadata(path)
            no_links(path)
            relative_file = path.relative_to(source)
            need('..' not in relative_file.parts and len(str(relative_file)) <= 512, 'BROWSER_PATH_BOUND')
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                before = os.fstat(fd)
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and 0 <= before.st_size <= 256 * 1024 * 1024, 'BROWSER_FILE_TYPE_OR_BOUND')
                total += before.st_size
                need(len(rows) < MAX_FILES and total <= MAX_BYTES, 'BROWSER_TREE_BOUND')
                output = target / relative_file
                mode = 0o755 if before.st_mode & 0o111 else 0o644
                out_fd = os.open(str(output), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
                digest, copied = hashlib.sha256(), 0
                try:
                    while True:
                        raw = os.read(fd, 1024 * 1024)
                        if not raw:
                            break
                        copied += len(raw)
                        need(copied <= before.st_size, 'BROWSER_FILE_CHANGED')
                        digest.update(raw)
                        view = memoryview(raw)
                        while view:
                            view = view[os.write(out_fd, view):]
                    os.fsync(out_fd)
                    os.fchmod(out_fd, mode)
                finally:
                    os.close(out_fd)
                need(copied == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), 'BROWSER_FILE_CHANGED')
                rows.append({'path': 'chrome-linux/' + relative_file.as_posix(), 'bytes': copied, 'sha256': digest.hexdigest(), 'executable': bool(before.st_mode & 0o111)})
                PROGRESS.update(files=len(rows), bytes=total)
            finally:
                os.close(fd)
    rows.sort(key=lambda row: row['path'])
    STEP = 'copied_executable_guard'
    executable = target / 'headless_shell'
    need(executable.is_file() and os.access(str(executable), os.X_OK), 'FIXED_HEADLESS_SHELL_REQUIRED')
    inventory = {'schema': 'energy-browser-runtime.v1', 'architecture': 'linux/arm64', 'source_image_id': IMAGE_ID,
                 'executable': 'chrome-linux/headless_shell', 'files': rows,
                 'licenses': [row['path'] for row in rows if any(word in Path(row['path']).name.lower() for word in ('license', 'notice', 'copying'))]}
    raw = json.dumps(inventory, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\n'
    STEP = 'write_runtime_manifest'
    fd = os.open(str(destination / 'RUNTIME.json'), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
    return {'status': 'BROWSER_COPIED_FOR_NONROOT', 'files': len(rows), 'bytes': total, 'inventory_sha256': hashlib.sha256(raw).hexdigest()}


def prepare_nonroot_project_root():
    global STEP
    STEP = 'temporary_root_group_guard'
    # fsGroup=1000 on the Pod gives this emptyDir group 1000 and setgid.
    # Only these isolated temporary directories become group writable.
    current = Path('/writable')
    need(current.stat().st_gid == 1000, 'TEMPORARY_FSGROUP_REQUIRED')
    for name in ('energy-browser-qualification', 'skills', 'public'):
        current = current / name
        STEP = 'prepare_' + name
        PROGRESS['temporary_directory_before'] = metadata(current)
        current.mkdir(mode=0o2775)
        os.chmod(str(current), 0o2775)
        PROGRESS['temporary_directory_after'] = metadata(current)
        need(current.stat().st_gid == 1000, 'TEMPORARY_FSGROUP_REQUIRED')


if __name__ == '__main__':
    try:
        need(os.geteuid() == 0, 'IMAGE_READER_ROOT_REQUIRED')
        prepare_nonroot_project_root()
        print(json.dumps(copy_browser(), sort_keys=True))
    except Exception as exc:
        allowed = {'SOURCE_LINK_REFUSED', 'BROWSER_DIRECTORY_REQUIRED', 'BROWSER_DIRECTORY_TYPE', 'BROWSER_PATH_BOUND',
                   'BROWSER_FILE_TYPE_OR_BOUND', 'BROWSER_TREE_BOUND', 'BROWSER_FILE_CHANGED', 'FIXED_HEADLESS_SHELL_REQUIRED',
                   'TEMPORARY_FSGROUP_REQUIRED', 'IMAGE_READER_ROOT_REQUIRED'}
        print(json.dumps(dict(diagnostic(), status='INCOMPLETE', error_class=type(exc).__name__,
                              error_code=str(exc) if isinstance(exc, ValueError) and str(exc) in allowed else 'INIT_FAILURE_REDACTED'), sort_keys=True))
        raise SystemExit(1)
