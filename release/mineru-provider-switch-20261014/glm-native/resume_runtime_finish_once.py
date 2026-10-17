"""Resume one verified pre-preparation interruption; never erase previous evidence.

Python 3.7+, root on datax-01. Reuses the already delivered, hash-pinned tool.
No production writes, image import, compiler invocation or provider requests.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time

OLD_ROOT = Path('/opt/weknora-glm-runtime-fix-P4S71dPB')
OLD_STATE = Path('/var/lib/weknora-glm-runtime-finish-20261016-r1')
STATE = Path('/var/lib/weknora-glm-runtime-resume-20261016-r1')
MANIFEST_SHA = '24d4a6fd223a347b378dd798759e84785068c665caf55ffe16c2c3e7357cddb1'
PHASE = 'READ_ONLY_INTERRUPTION_RECONCILIATION'


class RecoveryFailure(RuntimeError):
    pass


def require(ok, check):
    if not ok:
        raise RecoveryFailure(check)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def exists(path):
    return os.path.lexists(str(path))


def directory(path):
    for p in (path,) + tuple(path.parents):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_directory')


def read(path, maximum=65536):
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and 0 < s.st_size <= maximum, 'unsafe_private_file')
        value = f.read(maximum + 1)
        require(len(value) == s.st_size, 'private_file_size_changed')
        return value


def write(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def original_boundary():
    directory(OLD_ROOT)
    directory(OLD_STATE)
    require({p.name for p in OLD_STATE.iterdir()} == {'STARTED.json'},
            'previous_state_not_started_only_USE_STATUS')
    raw = read(OLD_STATE/'STARTED.json')
    require(json.loads(raw) == {'automatic_retry': False, 'build_root': str(OLD_ROOT/'rebuild')},
            'previous_started_binding_changed')
    require(not exists(OLD_ROOT/'rebuild'), 'previous_output_exists_REVIEW_REQUIRED')
    require(not exists(OLD_ROOT/'build.log'), 'previous_build_log_exists_REVIEW_REQUIRED')
    return hashlib.sha256(raw).hexdigest()


def verify_patch():
    root = OLD_ROOT/'patch'
    raw = read(root/'PACKAGE.SHA256')
    require(hashlib.sha256(raw).hexdigest() == MANIFEST_SHA, 'delivered_patch_manifest_changed')
    rows = {}
    for line in raw.decode('ascii').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9_.+-]+)', line)
        require(m is not None and m[2] not in rows and m[2] != 'PACKAGE.SHA256', 'patch_manifest_format')
        rows[m[2]] = m[1]
    require(len(rows) == 8 and {p.name for p in root.iterdir()} == set(rows) | {'PACKAGE.SHA256'},
            'delivered_patch_members_changed')
    for name, expected in rows.items():
        require(hashlib.sha256(read(root/name, 4*1024**2)).hexdigest() == expected,
                'delivered_patch_hash_changed:' + name)
    return root


def process_hits(raw, current_pid):
    hits = []
    for line in raw.decode('utf-8', 'replace').splitlines():
        p = line.split(None, 3)
        if len(p) != 4:
            continue
        pid, ppid, comm, args = p
        if int(pid) == current_pid:
            continue
        image = comm in ('buildctl', 'nerdctl') or (comm == 'ctr' and
                re.search(r'\bimages\s+(import|export)\b', args))
        prepare = comm.startswith('python') and any(x in args for x in (
            'prepare_runtime_finish.py', 'finish_runtime_build.py', 'resume_runtime_finish_once.py'))
        daemon = comm == 'buildkitd' and ('weknora-glm-runtime-fix-' in args or
                 '/opt/weknora-glm-dwarf-fix-qYPHEQjp/' in args)
        if image or prepare or daemon:
            hits.append({'pid': int(pid), 'ppid': int(ppid), 'program': comm})
    return hits


def processes_clear():
    p = subprocess.run(['ps', '-eo', 'pid=,ppid=,comm=,args='], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=15, check=True)
    require(len(p.stdout) < 8*1024**2, 'process_inventory_too_large')
    hits = process_hits(p.stdout, os.getpid())
    if hits:
        emit({'blocking_processes': hits, 'processes_stopped': False})
    require(not hits, 'other_image_or_recovery_operation_active')


def progress_digest(path):
    """The same SHA256, with bounded progress; never print file contents."""
    h = hashlib.sha256()
    count = 0
    started = last = time.monotonic()
    with path.open('rb') as f:
        before = os.fstat(f.fileno())
        large = before.st_size >= 64*1024**2
        if large:
            emit({'runtime_resume': 'HASH_START', 'file': path.name, 'bytes': before.st_size})
        for block in iter(lambda: f.read(1024**2), b''):
            h.update(block)
            count += len(block)
            now = time.monotonic()
            if large and now - last >= 10:
                emit({'runtime_resume': 'HASH_RUNNING', 'file': path.name, 'bytes_read': count,
                      'total_bytes': before.st_size, 'elapsed_seconds': int(now-started)})
                last = now
        after = os.fstat(f.fileno())
    fields = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    require(fields(before) == fields(after) and count == before.st_size, 'hashed_file_changed')
    if large:
        emit({'runtime_resume': 'HASH_READ_COMPLETE', 'file': path.name, 'bytes_read': count,
              'elapsed_seconds': int(time.monotonic()-started)})
    return h.hexdigest()


def load_verified(root):
    # Only called after every delivered file and the pinned manifest are verified.
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('runtime_resume_original', str(root/'prepare_runtime_finish.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    require(Path(module.safe.__file__).resolve() == root/'repair_sqlite_build.py', 'helper_import_location')
    return module


def prepare_fresh(module, output):
    original_state, digest, argv = module.STATE, module.safe.digest, sys.argv
    try:
        module.STATE = STATE/'preparation'
        module.safe.digest = progress_digest
        sys.argv = [str(OLD_ROOT/'patch/prepare_runtime_finish.py'), '--output', str(output)]
        module.main()  # All original package, cache, space and compile-receipt gates stay intact.
    finally:
        module.STATE, module.safe.digest, sys.argv = original_state, digest, argv


def child(command, cwd):
    p = subprocess.Popen(command, cwd=str(cwd))
    started = time.monotonic()
    try:
        while True:
            try:
                code = p.wait(timeout=20)
                break
            except subprocess.TimeoutExpired:
                emit({'runtime_resume': 'RUNNING', 'phase': PHASE,
                      'elapsed_seconds': int(time.monotonic()-started), 'compile_reused': True})
        require(code == 0, 'runtime_build_child_failed_USE_STATUS')
    except BaseException:
        # Foreground Ctrl+C also reaches the child. Let its existing handler stop
        # only its own BuildKit units; never terminate another operator's process.
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            emit({'child_pid': p.pid, 'child_exit': 'NOT_CONFIRMED', 'instruction': 'USE_STATUS_NO_REPEAT'})
        raise


def status():
    result = {'runtime_resume_status': 'READ_ONLY', 'files_modified': False, 'production_writes': 0,
              'provider_requests': 0, 'automatic_retry': False, 'state': str(STATE)}
    if not exists(STATE):
        result['state_exists'] = False
        emit(result)
        return
    directory(STATE)
    records = {}
    for name in ('STARTED.json', 'RESULT.json', 'preparation/STARTED.json',
                 'preparation/PREPARED.json', 'preparation/FAILED.json'):
        p = STATE/name
        if exists(p):
            raw = read(p)
            records[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'fields': json.loads(raw)}
    prepared = records.get('preparation/PREPARED.json', {}).get('fields', {})
    if prepared:
        output = Path(prepared['build_root'])
        require(output.name == 'rebuild' and output.parent.parent == Path('/opt') and
                output.parent.name.startswith('weknora-glm-runtime-fix-'), 'status_output_shape')
        for name in ('BUILD-RESULT.json', 'BUILD-FAILED.json'):
            p = output/'build1'/name
            if exists(p):
                raw = read(p, 4*1024**2)
                value = json.loads(raw)
                records[name] = {'path': str(p), 'sha256': hashlib.sha256(raw).hexdigest(),
                    'fields': {k: value.get(k) for k in ('weknora_glm_candidate_build', 'stage', 'failed_check',
                               'compile_reused', 'images_imported', 'ready_for_rollout')}}
    result['records'] = records
    emit(result)


def resume():
    global PHASE
    require(not exists(STATE), 'resume_already_attempted_USE_STATUS')
    directory(STATE.parent)
    binding = original_boundary()
    patch_root = verify_patch()
    processes_clear()
    module = load_verified(patch_root)
    # mkdir is the atomic once-only guard. No old receipt or output is removed.
    STATE.mkdir(mode=0o700)
    write(STATE/'STARTED.json', {'old_state': str(OLD_STATE), 'old_started_sha256': binding,
          'old_patch_manifest_sha256': MANIFEST_SHA, 'automatic_retry': False, 'production_writes': 0})
    try:
        require(original_boundary() == binding, 'previous_state_changed')
        root = Path(tempfile.mkdtemp(prefix='weknora-glm-runtime-fix-', dir='/opt'))
        output = root/'rebuild'
        emit({'runtime_resume': 'START', 'new_build_root': str(output), 'old_records_preserved': True,
              'compile_reused': True, 'production_writes': 0, 'provider_requests': 0})
        PHASE = 'REVALIDATE_AND_REUSE_COMPILED_OUTPUTS'
        prepare_fresh(module, output)
        require(original_boundary() == binding, 'original_interruption_record_changed')
        command = [sys.executable, '-E', '-s', '-B', '-u', str(output/'package/finish_runtime_build.py'),
                   '--output', str(output/'build1')]
        PHASE = 'READ_ONLY_BUILD_PREFLIGHT'
        child(command + ['--check-only'], output/'package')
        PHASE = 'RUNTIME_ONLY_BUILD_NO_RECOMPILE'
        child(command + ['--build-only-ack', 'YES'], output/'package')
        result_raw = read(output/'build1/BUILD-RESULT.json', 4*1024**2)
        result = json.loads(result_raw)
        require(result.get('weknora_glm_candidate_build') == 'PASS' and result.get('compile_reused') is True
                and result.get('images_imported') is False and result.get('ready_for_rollout') is False,
                'candidate_success_receipt_required')
        value = {'runtime_resume': 'PASS', 'build_result': str(output/'build1/BUILD-RESULT.json'),
                 'build_result_sha256': hashlib.sha256(result_raw).hexdigest(), 'compile_reused': True}
    except BaseException as exc:
        value = {'runtime_resume': 'UNCONFIRMED', 'phase': PHASE, 'error_type': type(exc).__name__,
                 'failed_check': str(exc) if isinstance(exc, RecoveryFailure) else 'USE_STATUS_FOR_SAFE_DETAIL'}
        value.update(production_writes=0, provider_requests=0, images_imported=False, ready_for_rollout=False,
                     automatic_retry=False, original_evidence_modified=False)
        write(STATE/'RESULT.json', value)
        emit(value)
        raise
    value.update(production_writes=0, provider_requests=0, images_imported=False, ready_for_rollout=False,
                 automatic_retry=False, original_evidence_modified=False)
    write(STATE/'RESULT.json', value)
    emit(value)


def main():
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--status', action='store_true')
    mode.add_argument('--resume-once', action='store_true')
    a = p.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'requires_root_datax01')
    os.umask(0o077)
    status() if a.status else resume()


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        if isinstance(exc, SystemExit):
            raise
        emit({'runtime_resume': 'STOPPED', 'error_type': type(exc).__name__,
              'failed_check': str(exc) if isinstance(exc, RecoveryFailure) else 'USE_STATUS_FOR_SAFE_DETAIL',
              'instruction': 'USE_STATUS_DO_NOT_REPEAT', 'production_writes': 0, 'provider_requests': 0})
        sys.exit(1)
