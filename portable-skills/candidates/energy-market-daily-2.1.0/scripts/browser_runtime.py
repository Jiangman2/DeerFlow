"""Optional operator-installed browser dependency, copied to private local temp.

The portable public code package does not require this enterprise binding.
No browser, credential, network or business-data operation occurs on import.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import tempfile

from common import Failure

SKILL = Path(__file__).resolve().parents[1]
# The production ARM64 headless_shell is 298,683,784 bytes. Keep a finite
# per-file bound above that measured executable and a separate total bound.
MAX_FILE = 384 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


def need(ok, code):
    if not ok:
        raise Failure(code)


def no_links(path):
    for parent in (path, *path.parents):
        state = parent.lstat()
        need(not stat.S_ISLNK(state.st_mode), 'BROWSER_RUNTIME_LINK_REFUSED')


def stamp(state):
    # Windows stat() exposes creation time as ctime on some supported Python
    # versions while fstat() exposes a different timestamp. Hash/mtime/identity
    # remain authoritative there; Linux additionally checks change time.
    base = state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns
    return base if os.name == 'nt' else base + (state.st_ctime_ns,)


def read(path, limit):
    no_links(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, 'BROWSER_RUNTIME_FILE_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), 'BROWSER_RUNTIME_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def inventory(manifest):
    need(isinstance(manifest, dict) and manifest.get('schema') == 'energy-browser-runtime.v1'
         and manifest.get('architecture') == 'linux/arm64'
         and re.fullmatch(r'sha256:[0-9a-f]{64}', manifest.get('source_image_id', '')), 'BROWSER_RUNTIME_MANIFEST_INVALID')
    rows = manifest.get('files')
    need(isinstance(rows, list) and 0 < len(rows) <= 256, 'BROWSER_RUNTIME_INVENTORY_BOUND')
    found, total = {}, 0
    for row in rows:
        need(isinstance(row, dict) and set(row) == {'path', 'bytes', 'sha256', 'executable'}, 'BROWSER_RUNTIME_INVENTORY_INVALID')
        name = row['path']
        need(isinstance(name, str) and 0 < len(name) <= 256 and '\\' not in name and ':' not in name, 'BROWSER_RUNTIME_PATH_INVALID')
        relative = PurePosixPath(name)
        need(not relative.is_absolute() and '..' not in relative.parts and str(relative) == name and name not in found and name != 'RUNTIME.json', 'BROWSER_RUNTIME_PATH_INVALID')
        need(type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_FILE and type(row['executable']) is bool
             and isinstance(row['sha256'], str) and re.fullmatch('[0-9a-f]{64}', row['sha256']), 'BROWSER_RUNTIME_INVENTORY_INVALID')
        found[name] = row
        total += row['bytes']
    need(total <= MAX_TOTAL, 'BROWSER_RUNTIME_TOTAL_BOUND')
    entry = manifest.get('executable')
    need(entry in found and found[entry]['executable'], 'BROWSER_RUNTIME_EXECUTABLE_INVALID')
    return found


def copy_file(source, target, row):
    no_links(source)
    fd = os.open(source, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == row['bytes']
             and not before.st_mode & (stat.S_ISUID | stat.S_ISGID), 'BROWSER_RUNTIME_SOURCE_INVALID')
        sha, length = hashlib.sha256(), 0
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(fd, 'rb', closefd=False) as incoming, target.open('xb') as outgoing:
            while True:
                chunk = incoming.read(1024 * 1024)
                if not chunk:
                    break
                length += len(chunk)
                need(length <= row['bytes'], 'BROWSER_RUNTIME_FILE_CHANGED')
                sha.update(chunk)
                outgoing.write(chunk)
        need(length == row['bytes'] and sha.hexdigest() == row['sha256']
             and stamp(before) == stamp(os.fstat(fd)) == stamp(source.stat()), 'BROWSER_RUNTIME_FILE_CHANGED')
        target.chmod(0o500 if row['executable'] else 0o400)
    finally:
        os.close(fd)


def binding_present():
    try:
        (SKILL / 'assets/browser-runtime.json').lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        # An unreadable operator binding must fail visibly, not silently switch
        # to a different browser installation.
        return True


def installed_runtime():
    binding = json.loads(read(SKILL / 'assets/browser-runtime.json', 32768))
    need(isinstance(binding, dict) and set(binding) == {'schema', 'directory', 'manifest_sha256'}
         and binding.get('schema') == 'energy-browser-runtime-binding.v1'
         and isinstance(binding.get('directory'), str)
         and re.fullmatch(r'(?:\.\./){1,2}\.energy-browser-runtime-[a-z0-9-]{1,64}/linux-arm64', binding['directory'])
         and isinstance(binding.get('manifest_sha256'), str) and re.fullmatch('[0-9a-f]{64}', binding['manifest_sha256']), 'BROWSER_RUNTIME_BINDING_INVALID')
    parts = PurePosixPath(binding['directory']).parts
    count = 2 if parts[:2] == ('..', '..') else 1
    # Ordinary sandboxes mount only the public category. New bindings stay in
    # that category; the old two-parent form remains readable on full mounts.
    base = SKILL.parent if count == 1 else SKILL.parents[1]
    root = base / Path(*parts[count:])
    try:
        root.lstat()
    except FileNotFoundError:
        raise Failure('BROWSER_RUNTIME_ROOT_NOT_MOUNTED', runtime_root=str(root)) from None
    no_links(root)
    try:
        raw = read(root / 'RUNTIME.json', 262144)
    except FileNotFoundError:
        raise Failure('BROWSER_RUNTIME_MANIFEST_MISSING', runtime_root=str(root)) from None
    need(hashlib.sha256(raw).hexdigest() == binding['manifest_sha256'], 'BROWSER_RUNTIME_MANIFEST_CHANGED')
    manifest = json.loads(raw)
    rows = inventory(manifest)
    for name in rows:
        path = root / name
        try:
            no_links(path)
            state = path.stat()
        except FileNotFoundError:
            raise Failure('BROWSER_RUNTIME_FILE_MISSING', runtime_component=name) from None
        need(stat.S_ISREG(state.st_mode) and state.st_nlink == 1 and state.st_size == rows[name]['bytes']
             and not state.st_mode & (stat.S_ISUID | stat.S_ISGID), 'BROWSER_RUNTIME_SOURCE_INVALID')
        need(os.access(path, os.R_OK), 'BROWSER_RUNTIME_UNREADABLE')
    return root, manifest, rows


def observe_runtime():
    """Read-only prerequisite observation; no launch, copy or provider request."""
    present = binding_present()
    applicable = platform.system() == 'Linux' and platform.machine().lower() in ('aarch64', 'arm64')
    out = {'operator_binding_present': present, 'operator_binding_applicable': applicable,
           'operator_runtime_ready': None, 'error_code': None, 'recovery_scope': None,
           'runtime_payload_hashes_checked': False, 'browser_launch_verified': False}
    if not present or not applicable:
        return out
    try:
        installed_runtime()
        out['operator_runtime_ready'] = True
    except Failure as exc:
        out.update(operator_runtime_ready=False, error_code=exc.code, recovery_scope='operator')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        out.update(operator_runtime_ready=False, error_code='BROWSER_RUNTIME_UNAVAILABLE', recovery_scope='operator', error_class=type(exc).__name__)
    return out


@contextmanager
def runtime_browser():
    if platform.system() != 'Linux' or platform.machine().lower() not in ('aarch64', 'arm64') or not binding_present():
        yield None
        return
    try:
        root, manifest, rows = installed_runtime()
        names = set()
        for directory, folders, files in os.walk(root, followlinks=False):
            need(len(folders) + len(files) <= 512, 'BROWSER_RUNTIME_INVENTORY_BOUND')
            for folder in folders:
                no_links(Path(directory) / folder)
            for name in files:
                relative = (Path(directory) / name).relative_to(root).as_posix()
                need(relative == 'RUNTIME.json' or relative in rows, 'BROWSER_RUNTIME_EXTRA_FILE')
                names.add(relative)
        need(names == set(rows) | {'RUNTIME.json'}, 'BROWSER_RUNTIME_FILE_SET_CHANGED')
        # A fresh 0700 directory avoids predictable shared /tmp caches. Copying
        # also permits execution when the read-only NFS source has noexec set.
        with tempfile.TemporaryDirectory(prefix='energy-browser-') as temporary:
            local = Path(temporary)
            local.chmod(0o700)
            for name, row in rows.items():
                copy_file(root / name, local / name, row)
            executable = local / manifest['executable']
            need(os.access(executable, os.R_OK | os.X_OK), 'BROWSER_LOCAL_TEMP_NOEXEC')
            yield str(executable)
    except Failure as exc:
        if exc.code.startswith('BROWSER_'):
            exc.details.update(recovery_scope='operator', retry_allowed=False, dependency_installation_can_repair=False,
                               prepared_sources_preserved=True)
        raise
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise Failure('BROWSER_RUNTIME_UNAVAILABLE', error_class=type(exc).__name__, prepared_sources_preserved=True,
                      recovery_scope='operator', retry_allowed=False, dependency_installation_can_repair=False) from None
