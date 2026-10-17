#!/usr/bin/env python3
"""Receipt-bound Python3.7 O_PATH recovery; bounded NFS pilot; Python 3.7+."""
import argparse
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import select
import socket
import stat
import sys
import time
import traceback
import types
import uuid

PREVIOUS = Path('/var/lib/weknora-rwx-copy-pilot-resume-20261017-r1')
FAILED_EXEC = Path('/var/lib/weknora-rwx-copy-exec-recovery-20261017-r1')
FAILED_EXEC_SHA = 'e57196941d7382efc2a5e854dc695179bab78b3874187acb97b17c980d291656'
FAILED_RESULT_SHA = 'f22c5ab2afa77f9dd6da674a9237353da13aa25682a3d957302c0d374158db13'
STATE = Path('/var/lib/weknora-rwx-copy-exec-recovery-20261017-r2')
UNIT = STATE.name + '.service'
HELPER_SHA = 'db1ecbec03347d839fab8afee1cbaa63a6c9a4f594693aa6d5c455343dd91d63'
ENGINE_SHA = 'c296371643e8911bd71ae8fb174075b435b8db60e16164d736b26a166b177881'
RESULT_SHA = '4343feaba4f016cad3175a83f46ffd1d2c62d65262a40a91a40a1f4ca548310b'
PROGRESS_SHA = '8ecaf06e60140d127e07f36bf6112572b55ca9220ad0393659b478bf7d85fec0'
ENV = dict(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')
H = None
OWNED = False
PHASE = 'PREFLIGHT'


class Failure(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Failure(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_dir(path):
    for p in (path,) + tuple(path.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_private_directory')


def fd_bytes(fd, digest):
    s = os.fstat(fd)
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
         and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_code_descriptor')
    data = os.pread(fd, 1048577, 0)
    need(len(data) == s.st_size and sha(data) == digest, 'code_hash_changed')
    return data


def code_fd(path, digest):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        fd_bytes(fd, digest)
        return fd
    except BaseException:
        os.close(fd)
        raise


def module_bytes(data, name, filename):
    module = types.ModuleType(name)
    module.__file__ = filename
    exec(compile(data, filename, 'exec'), module.__dict__)
    return module


def helpers():
    fd = code_fd(PREVIOUS/'resume.py', HELPER_SHA)
    try:
        return module_bytes(fd_bytes(fd, HELPER_SHA), 'pinned_helpers', str(PREVIOUS/'resume.py'))
    finally:
        os.close(fd)


def diagnostic(exc, engine=None):
    allowed = (Failure,) + ((engine.Failure,) if engine is not None else ())
    return dict(error_type=type(exc).__name__, errno=getattr(exc, 'errno', None),
                failed_check=str(exc) if isinstance(exc, allowed) else 'SAFE_DIAGNOSTIC_REQUIRED',
                code_frames=[dict(file=Path(x.filename).name, function=x.name, line=x.lineno)
                             for x in traceback.extract_tb(exc.__traceback__)[-12:]],
                raw_error_printed=False)


def emit(value):
    print(json.dumps(value, sort_keys=True, separators=(',', ':')), flush=True)


def phase(name):
    global PHASE
    PHASE = name
    emit(dict(pilot_exec_recovery='RUNNING', phase=name))


def validate_previous_unit(value, token):
    expected = dict(LoadState='loaded', ActiveState='failed', SubState='failed', Type='exec',
                    TimeoutStartUSec='30s', Restart='no', MainPID='0', ControlPID='0',
                    Result='exit-code', ExecMainCode='1', ExecMainStatus='1',
                    Transient='yes', KillMode='control-group', RuntimeMaxUSec='infinity')
    need(all(value.get(k) == v for k, v in expected.items()), 'previous_unit_changed')
    need(' -I -B -u ' + str(PREVIOUS/'resume.py') + ' --run ' + token + ' ;'
         in value.get('ExecStart', ''), 'previous_unit_command_changed')


def previous_absence(engine, proc=Path('/proc')):
    deadline = time.monotonic() + 15
    scanned = 0
    for p in proc.iterdir():
        if not p.name.isdigit():
            continue
        need(time.monotonic() < deadline and scanned < 100000, 'process_scan_limit')
        try:
            with (p/'cgroup').open('rb') as f:
                data = f.read(65537)
        except (FileNotFoundError, ProcessLookupError):
            continue
        need(len(data) <= 65536, 'process_cgroup_oversize')
        scanned += 1
        for line in data.decode('utf-8', 'strict').splitlines():
            parts = line.split(':', 2)
            need(len(parts) == 3, 'process_cgroup_shape')
            need(not any(p.name+'.service' in parts[2].split('/') for p in (PREVIOUS, FAILED_EXEC)),
                 'previous_worker_still_present')
    need(scanned > 0, 'process_scan_empty')
    for name in ('/proc/self/mountinfo', '/proc/1/mountinfo'):
        for row in engine.mount_rows(Path(name).read_text()):
            need(not any(row['point'] == str(p) or row['point'].startswith(str(p)+'/')
                         for p in (PREVIOUS, FAILED_EXEC)), 'previous_mount_still_present')
    return dict(scanned_processes=scanned, matching_previous_processes=0, previous_mounts_observed=0)


def permission_snapshot(engine):
    original = H.old_snapshot(engine)
    H.safe_dir(PREVIOUS)
    required = {'resume.py', 'engine.py', 'RECOVERY.json', 'STARTED.json', 'START-REQUESTED.json',
                'START-ACK.json', 'ENGINE-GATE.json', 'RUN-ENTERED.json', 'MOUNT-REQUESTED.json',
                'WORKER-REQUESTED.json', 'PROGRESS.json', 'RESULT.json', 'source-ro', 'nfs'}
    need(set(p.name for p in PREVIOUS.iterdir()) == required, 'previous_state_shape_changed')
    hashes = {}
    for name in sorted(required - {'source-ro', 'nfs'}):
        hashes[name] = sha(H.read(PREVIOUS/name))
    need(hashes['resume.py'] == HELPER_SHA and hashes['engine.py'] == ENGINE_SHA,
         'previous_code_changed')
    need(hashes['RESULT.json'] == RESULT_SHA and hashes['PROGRESS.json'] == PROGRESS_SHA,
         'previous_failure_or_progress_changed')
    r = json.loads(H.read(PREVIOUS/'RESULT.json'))
    need(r.get('pilot') == 'UNCONFIRMED' and r.get('errno') == 13
         and r.get('error_type') == 'PermissionError' and not r.get('source_files_modified')
         and r.get('source_contents_read') == 'NOT_CONFIRMED', 'previous_failure_boundary_changed')
    progress = json.loads(H.read(PREVIOUS/'PROGRESS.json'))['last_progress']
    need(progress.get('phase') == 'SAMPLE_METADATA' and progress.get('uid') == progress.get('gid') == 1000,
         'previous_progress_boundary_changed')
    started = json.loads(H.read(PREVIOUS/'STARTED.json'))
    token = started.get('token', '')
    need(re.fullmatch('[0-9a-f]{32}', token) is not None
         and started.get('driver_sha256') == HELPER_SHA
         and started.get('script_sha256') == ENGINE_SHA
         and started.get('recovery_sha256') == hashes['RECOVERY.json'], 'previous_started_binding_changed')
    for name in ('START-REQUESTED.json', 'START-ACK.json'):
        need(json.loads(H.read(PREVIOUS/name)).get('unit') == PREVIOUS.name+'.service', 'previous_unit_receipt_changed')
    need(json.loads(H.read(PREVIOUS/'WORKER-REQUESTED.json')).get('pilot_directory')
         == '.weknora-copy-pilot-'+token, 'previous_worker_token_changed')
    value = H.unit_state(PREVIOUS.name+'.service', with_exec=True)
    validate_previous_unit(value, token)
    for name in ('source-ro', 'nfs'):
        H.safe_dir(PREVIOUS/name)
        need(not any((PREVIOUS/name).iterdir()), 'previous_local_mountpoint_not_empty')
    return dict(original=original, hashes=hashes,
                unit={k: value[k] for k in H.PROPS if k in value}, absence=previous_absence(engine),
                target_emptiness='MUST_RECHECK_AS_UID1000_BEFORE_COPY')


def validate_failed_exec_unit(value, token):
    expected = dict(LoadState='loaded', ActiveState='failed', SubState='failed', Type='exec',
                    TimeoutStartUSec='30s', Restart='no', MainPID='0', ControlPID='0',
                    Result='exit-code', ExecMainCode='1', ExecMainStatus='1',
                    Transient='yes', KillMode='control-group', RuntimeMaxUSec='infinity')
    need(all(value.get(k) == v for k, v in expected.items()), 'failed_exec_unit_changed')
    need(' -I -B -u ' + str(FAILED_EXEC/'recover.py') + ' --run ' + token + ' ;'
         in value.get('ExecStart', ''), 'failed_exec_command_changed')


def previous_snapshot(engine):
    prior = permission_snapshot(engine)
    H.safe_dir(FAILED_EXEC)
    required = {'recover.py', 'engine.py', 'RECOVERY.json', 'STARTED.json',
                'START-REQUESTED.json', 'START-ACK.json', 'ENGINE-GATE.json', 'RUN-ENTERED.json',
                'MOUNT-REQUESTED.json', 'WORKER-REQUESTED.json', 'RESULT.json', 'source-ro', 'nfs'}
    need(set(p.name for p in FAILED_EXEC.iterdir()) == required, 'failed_exec_state_shape_changed')
    hashes = {name: sha(H.read(FAILED_EXEC/name)) for name in sorted(required-{'source-ro', 'nfs'})}
    need(hashes['recover.py'] == FAILED_EXEC_SHA and hashes['engine.py'] == ENGINE_SHA
         and hashes['RESULT.json'] == FAILED_RESULT_SHA, 'failed_exec_pinned_bytes_changed')
    result = json.loads(H.read(FAILED_EXEC/'RESULT.json'))
    need(result.get('pilot') == 'UNCONFIRMED' and result.get('errno') == 9
         and result.get('failed_step') == 'DROP_EXEC'
         and result.get('source_contents_read') == 'NOT_CONFIRMED'
         and not result.get('source_files_modified'), 'failed_exec_boundary_changed')
    need(result.get('code_frames') == [dict(file='recover.py', function='launch_worker', line=291)],
         'failed_exec_frame_changed')
    started = json.loads(H.read(FAILED_EXEC/'STARTED.json'))
    token = started.get('token', '')
    need(re.fullmatch('[0-9a-f]{32}', token) is not None
         and started.get('driver_sha256') == FAILED_EXEC_SHA
         and started.get('script_sha256') == ENGINE_SHA
         and started.get('recovery_sha256') == hashes['RECOVERY.json'], 'failed_exec_start_binding_changed')
    recovery = json.loads(H.read(FAILED_EXEC/'RECOVERY.json'))
    equal_snapshot(prior, recovery['previous'])
    need(recovery['selftest'].get('identity_selftest') == 'PASS', 'failed_exec_selftest_changed')
    for name in ('START-REQUESTED.json', 'START-ACK.json'):
        need(json.loads(H.read(FAILED_EXEC/name)).get('unit') == FAILED_EXEC.name+'.service',
             'failed_exec_request_changed')
    need(json.loads(H.read(FAILED_EXEC/'WORKER-REQUESTED.json')).get('pilot_directory')
         == '.weknora-copy-pilot-'+token, 'failed_exec_worker_binding_changed')
    unit = H.unit_state(FAILED_EXEC.name+'.service', with_exec=True)
    validate_failed_exec_unit(unit, token)
    for name in ('source-ro', 'nfs'):
        H.safe_dir(FAILED_EXEC/name)
        need(not any((FAILED_EXEC/name).iterdir()), 'failed_exec_local_mountpoint_not_empty')
    prior['exec_failure'] = dict(hashes=hashes, unit={k: unit[k] for k in H.PROPS if k in unit})
    return prior


def inherit_fd(fd):
    # CPython3.7's ioctl fast path returns EBADF on a valid O_PATH FD.
    # F_GETFD/F_SETFD are explicitly supported for O_PATH on Linux.
    os.fstat(fd)
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    fcntl.fcntl(fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
    need(fcntl.fcntl(fd, fcntl.F_GETFD) == flags & ~fcntl.FD_CLOEXEC,
         'inherit_fd_flags_not_preserved')


def opath_legacy_probe():
    # Only a synthetic reference to "/" is used; no business path or mount.
    fd = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.set_inheritable(fd, True)
            result = dict(legacy_os_set_inheritable='PASS')
        except OSError as exc:
            result = dict(legacy_os_set_inheritable='FAIL', errno=exc.errno,
                          error_type=type(exc).__name__)
        flags = fcntl.fcntl(fd, fcntl.F_GETFD)
        fcntl.fcntl(fd, fcntl.F_SETFD, flags | fcntl.FD_CLOEXEC)
        inherit_fd(fd)
        result['fcntl_opath_inheritance'] = 'PASS'
        return result
    finally:
        os.close(fd)


def opath_after_exec(src, dst):
    need(stat.S_ISDIR(os.fstat(src).st_mode) and stat.S_ISDIR(os.fstat(dst).st_mode),
         'selftest_directory_fds_missing')
    need(fcntl.fcntl(dst, fcntl.F_GETFL) & os.O_PATH, 'selftest_opath_fd_required')
    for fd in (src, dst):
        need(not fcntl.fcntl(fd, fcntl.F_GETFD) & fcntl.FD_CLOEXEC,
             'selftest_fd_not_inheritable')
        text = Path('/proc/self/fdinfo/'+str(fd)).read_text()
        need(re.search(r'(?m)^mnt_id:\s*\d+\s*$', text), 'selftest_directory_fdinfo_failed')
    readable = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dst)
    try:
        before, after = os.fstat(dst), os.fstat(readable)
        need((before.st_dev, before.st_ino) == (after.st_dev, after.st_ino),
             'selftest_opath_reopen_identity_changed')
    finally:
        os.close(readable)
    return dict(opath_exec_inheritance='PASS', opath_reopen_as_uid1000='PASS',
                descriptor_flag_method='FCNTL_F_GETFD_F_SETFD')


def equal_snapshot(a, b):
    need(a.get('exec_failure') == b.get('exec_failure'), 'previous_exec_failure_drift')
    need(a['hashes'] == b['hashes'] and a['unit'] == b['unit']
         and a['original']['old_unit'] == b['original']['old_unit']
         and a['original']['old_request_sha256'] == b['original']['old_request_sha256'],
         'previous_evidence_drift')


def send(fd, value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()
    need(len(data) <= 65536, 'worker_message_oversize')
    while data:
        n = os.write(fd, data)
        need(n > 0, 'worker_pipe_short_write')
        data = data[n:]


def identity_probe():
    lib = ctypes.CDLL(None, use_errno=True)
    need(os.getresuid() == os.getresgid() == (1000, 1000, 1000) and os.getgroups() == [],
         'exec_worker_identity_changed')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    for name in ('CapInh', 'CapPrm', 'CapEff', 'CapAmb'):
        need(name in status and int(status[name].strip(), 16) == 0, 'exec_worker_has_capabilities')
    need(status.get('NoNewPrivs', '').strip() == '1', 'no_new_privs_missing')
    dumpable = lib.prctl(3, 0, 0, 0, 0)
    need(dumpable == 1, 'ordinary_exec_dumpable_unavailable')
    fd = os.open('/dev/null', os.O_RDONLY | os.O_NOFOLLOW)
    try:
        text = Path('/proc/self/fdinfo/'+str(fd)).read_text()
        need(re.search(r'(?m)^mnt_id:\s*\d+\s*$', text), 'exec_fdinfo_unavailable')
    finally:
        os.close(fd)
    need(resource.getrlimit(resource.RLIMIT_CORE) == (0, 0), 'core_limit_changed')
    return dict(uid=1000, gid=1000, supplementary_groups=[], capabilities_zero=True,
                no_new_privs=True, dumpable=dumpable, fdinfo_read='PASS',
                mount_id_present=True, kernel=os.uname().release, identity_selftest='PASS')


def exec_child(enginefd, output, src, dst, token, mode):
    step, engine = 'EXEC_IDENTITY_SELFTEST', None
    try:
        need(mode in ('selftest', 'copy'), 'unknown_worker_mode')
        identity = identity_probe()
        if mode == 'selftest':
            opath = opath_after_exec(src, dst)
            result = dict(pilot='PASS', business_files_read=False, mounts_created=False, **identity, **opath)
        else:
            step = 'LOAD_PINNED_ENGINE'
            engine = module_bytes(fd_bytes(enginefd, ENGINE_SHA), 'pinned_exec_engine', 'engine.py')
            os.close(enginefd)
            step = 'VERIFY_INHERITED_DIRECTORY_FDS'
            readable = os.open('.', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME, dir_fd=dst)
            os.close(dst); dst = readable
            engine.fd_mount_id(src); engine.fd_mount_id(dst)
            send(output, dict(progress=dict(phase='EXEC_IDENTITY_SELFTEST', **identity)))
            def report(value):
                nonlocal step
                step = value.get('phase', 'ENGINE_WORKER')
                send(output, dict(progress=value))
            result = engine.worker(src, dst, token, report)
            result['exec_identity'] = identity
        result['worker_launch'] = 'FORK_DROP_EXEC_PINNED_PYTHON'
    except BaseException as exc:
        result = dict(pilot='UNCONFIRMED', failed_step=step, **diagnostic(exc, engine))
    send(output, dict(result=result))
    return 0 if result['pilot'] == 'PASS' else 1


# Code comes from a verified, already-open read-only descriptor, not a UID1000
# path reopen through a root-private state directory. No credentials in argv/env.
BOOTSTRAP = '''import hashlib,os,sys,types
try:
 fd=int(sys.argv[1]); raw=os.pread(fd,1048577,0)
 if hashlib.sha256(raw).hexdigest()!=sys.argv[2]: raise RuntimeError('bootstrap_hash')
 os.close(fd)
 m=types.ModuleType('exec_recovery_driver'); m.__file__='exec_recovery_driver.py'
 exec(compile(raw,m.__file__,'exec'),m.__dict__)
 rc=m.exec_child(int(sys.argv[3]),int(sys.argv[4]),int(sys.argv[5]),int(sys.argv[6]),sys.argv[7],sys.argv[8])
except BaseException:
 os.write(int(sys.argv[4]),b'{"result":{"pilot":"UNCONFIRMED","failed_check":"EXEC_BOOTSTRAP_FAILED","raw_error_printed":false}}\\n')
 rc=1
os._exit(rc)
'''


def launch_worker(engine, src, dst, token, mode, driver_path=None):
    driver_path = Path(__file__).resolve() if driver_path is None else driver_path
    driver = H.read(driver_path)
    dfd = code_fd(driver_path, sha(driver))
    efd = readfd = writefd = None
    pid = None
    synthetic = []
    try:
        if mode == 'selftest':
            src = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            synthetic.append(src)
            dst = os.open('/', os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
            synthetic.append(dst)
        efd = code_fd(Path(engine.__file__), ENGINE_SHA)
        python = H.executable('python3')
        need(not os.stat(python).st_mode & (stat.S_ISUID | stat.S_ISGID), 'privileged_python_rejected')
        readfd, writefd = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                keep = {dfd, efd, writefd, src, dst}
                need(all(fd >= 3 for fd in keep) and len(keep) == 5, 'worker_fd_set_shape')
                for name in os.listdir('/proc/self/fd'):
                    fd = int(name)
                    if fd > 2 and fd not in keep:
                        try: os.close(fd)
                        except OSError: pass
                null = os.open('/dev/null', os.O_RDWR)
                for fd in (0, 1, 2): os.dup2(null, fd)
                if null > 2: os.close(null)
                for role, fd in (('driver', dfd), ('engine', efd), ('ipc', writefd),
                                 ('source', src), ('target_opath', dst)):
                    step = 'PRESERVE_'+role.upper()+'_FD'
                    inherit_fd(fd)
                step = 'DROP_EXEC'
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                lib = ctypes.CDLL(None, use_errno=True)
                need(lib.prctl(38, 1, 0, 0, 0) == 0, 'set_no_new_privs_failed')
                os.setgroups([]); os.setgid(1000); os.setuid(1000)
                os.chdir('/')
                os.execve(python, [python, '-I', '-B', '-u', '-c', BOOTSTRAP,
                          str(dfd), sha(driver), str(efd), str(writefd), str(src), str(dst), token, mode], ENV)
            except BaseException as exc:
                send(writefd, dict(result=dict(pilot='UNCONFIRMED', failed_step=locals().get('step', 'PREPARE_EXEC_FDS'), **diagnostic(exc))))
                os._exit(1)
        os.close(writefd); writefd = None
        os.close(dfd); dfd = None
        os.close(efd); efd = None
        return collect_worker(engine, readfd, pid, mode)
    finally:
        for fd in (dfd, efd, readfd, writefd) + tuple(synthetic):
            if fd is not None: os.close(fd)


def collect_worker(engine, readfd, pid, mode):
    data, result = b'', None
    progress = dict(phase='EXEC_WORKER_STARTING')
    start = time.monotonic()
    while True:
        ready, _, _ = select.select([readfd], [], [], 15)
        if not ready:
            need(mode != 'selftest' or time.monotonic()-start < 20, 'identity_selftest_timeout_no_copy_started')
            if mode == 'copy':
                engine.save(STATE/'PROGRESS.json', dict(utc=H.utc(), worker_pid=pid,
                    last_progress=progress, heartbeat=True, hard_nfs_io_may_wait=True), replace=True)
            continue
        chunk = os.read(readfd, 16384)
        if not chunk: break
        data += chunk
        need(len(data) <= 131072, 'worker_message_limit')
        while b'\n' in data:
            line, data = data.split(b'\n', 1)
            value = json.loads(line)
            need(isinstance(value, dict) and len(value) == 1, 'worker_message_shape')
            if 'progress' in value:
                need(mode == 'copy' and result is None, 'unexpected_worker_progress')
                progress = value['progress']
                engine.save(STATE/'PROGRESS.json', dict(utc=H.utc(), worker_pid=pid, last_progress=progress), replace=True)
                emit(progress)
            elif 'result' in value:
                need(result is None, 'duplicate_worker_result')
                result = value['result']
            else:
                raise Failure('worker_message_shape')
    _, status = os.waitpid(pid, 0)
    need(result is not None and not data and os.WIFEXITED(status), 'worker_result_missing')
    need(result.get('pilot') in ('PASS', 'UNCONFIRMED'), 'worker_result_shape')
    need(os.WEXITSTATUS(status) == (0 if result['pilot'] == 'PASS' else 1), 'worker_exit_mismatch')
    return result


def launch_args(token):
    return [H.executable('systemd-run'), '--unit='+UNIT, '--property=Type=exec',
            '--property=Restart=no', '--property=TimeoutStartSec=30s', '--property=RuntimeMaxSec=infinity',
            '--property=KillMode=control-group', '--property=UMask=0077', '--property=LimitCORE=0',
            '--property=Nice=10', '--property=IOSchedulingClass=idle', H.executable('unshare'),
            '--mount', '--propagation', 'private', H.executable('python3'), '-I', '-B', '-u',
            str(STATE/'recover.py'), '--run', token]


def recover():
    global OWNED
    phase('VERIFY_PINNED_FAILURE_AND_NO_PREVIOUS_PROCESSES')
    need(not H.present(STATE), 'attempt_exists_use_status_no_retry')
    engine, engine_bytes = H.load_engine(PREVIOUS/'engine.py')
    before = previous_snapshot(engine)
    need(H.unit_state(UNIT).get('LoadState') == 'not-found', 'fresh_unit_required')
    evidence = engine.verify_audit()
    engine.resources()
    phase('NO_BUSINESS_OPATH_FORK_DROP_EXEC_SELFTEST')
    legacy_probe = opath_legacy_probe()
    test = launch_worker(engine, -1, -1, '', 'selftest')
    test['legacy_opath_api_probe'] = legacy_probe
    emit(dict(exec_selftest=test))
    need(test.get('pilot') == 'PASS' and test.get('identity_selftest') == 'PASS'
         and test.get('opath_exec_inheritance') == 'PASS'
         and test.get('opath_reopen_as_uid1000') == 'PASS', 'exec_identity_selftest_failed_no_copy')
    equal_snapshot(before, previous_snapshot(engine))
    for name in ('systemd-run', 'systemctl', 'unshare', 'mount', 'mount.nfs', 'python3'):
        H.executable(name)
    driver = H.read(Path(__file__).resolve())
    H.safe_dir(STATE.parent)
    STATE.mkdir(mode=0o700)
    OWNED = True
    H.write(STATE/'recover.py', driver)
    H.write(STATE/'engine.py', engine_bytes)
    H.save(STATE/'RECOVERY.json', dict(previous=before, selftest=test,
        diagnosis='PYTHON37_SET_INHERITABLE_IOCTL_EBADF_ON_OPATH',
        method='FCNTL_PRESERVE_OPATH_THEN_FORK_DROP_EXEC', original_evidence_modified=False,
        target_emptiness='REQUIRED_BEFORE_WRITES', full_migration=False))
    token = uuid.uuid4().hex
    H.save(STATE/'STARTED.json', dict(token=token, script_sha256=ENGINE_SHA, driver_sha256=sha(driver),
        recovery_sha256=sha(H.read(STATE/'RECOVERY.json')), utc=H.utc(), evidence=evidence))
    equal_snapshot(before, previous_snapshot(engine))
    args = launch_args(token)
    H.save(STATE/'START-REQUESTED.json', dict(unit=UNIT, utc=H.utc()))
    phase('START_FRESH_BOUNDED_PILOT_ONCE')
    H.command(args, timeout=45)
    H.save(STATE/'START-ACK.json', dict(unit=UNIT, utc=H.utc(), copy_acceptance='PENDING_RESULT'))
    emit(dict(pilot_exec_recovery='BACKGROUND_LAUNCH_ACKNOWLEDGED', copy_acceptance='PENDING_RESULT',
        automatic_retry=False, original_evidence_modified=False, production_mounts_changed=False,
        business_copy_limit_files=5000, business_copy_limit_bytes=1073741824,
        max_combined_copy_readback_payload_bytes_per_second=10485760,
        ready_to_migrate=False, ready_to_scale=False, state=str(STATE),
        status_command='sudo python3 -I -B '+str(STATE/'recover.py')+' --status'))


def run(token):
    global OWNED
    engine, _ = H.load_engine(STATE/'engine.py')
    record = json.loads(H.read(STATE/'STARTED.json'))
    need(record.get('token') == token and record.get('driver_sha256') == sha(H.read(Path(__file__)))
         and record.get('script_sha256') == ENGINE_SHA
         and record.get('recovery_sha256') == sha(H.read(STATE/'RECOVERY.json')), 'new_run_identity_changed')
    need(not H.present(STATE/'ENGINE-GATE.json') and not H.present(STATE/'RUN-ENTERED.json'), 'already_entered_no_retry')
    OWNED = True
    previous = json.loads(H.read(STATE/'RECOVERY.json'))['previous']
    equal_snapshot(previous, previous_snapshot(engine))
    effective = H.unit_state(UNIT)
    H.validate_new_unit(effective, os.getpid())
    engine.isolated_namespace()
    H.save(STATE/'ENGINE-GATE.json', dict(unit=effective, utc=H.utc(), business_io_started=False))
    engine.STATE, engine.UNIT = STATE, UNIT
    def command(args, timeout=30):
        try:
            return H.command(args, timeout)
        except H.Failure as exc:
            raise engine.Failure(str(exc))
        finally:
            engine.LAST = dict(H.LAST)
    engine.command = command
    engine.failure = lambda exc: diagnostic(exc, engine)
    engine.run_worker = lambda src, dst, value: launch_worker(engine, src, dst, value, 'copy')
    return engine.internal_run(token)


def status():
    value = dict(pilot_exec_status='READ_ONLY', state=str(STATE), automatic_retry=False,
                 files_modified=False, mounts_created=False, copy_started_by_status=False)
    if H.present(STATE):
        H.safe_dir(STATE)
        names = ('STARTED.json', 'START-REQUESTED.json', 'START-ACK.json', 'ENGINE-GATE.json',
                 'RUN-ENTERED.json', 'MOUNT-REQUESTED.json', 'WORKER-REQUESTED.json', 'PROGRESS.json',
                 'RESULT.json', 'LAUNCH-FAILED.json', 'RUN-GATE-FAILED.json')
        value['operation_markers'] = {name: H.present(STATE/name) for name in names}
        for name in ('PROGRESS.json', 'RESULT.json', 'LAUNCH-FAILED.json', 'RUN-GATE-FAILED.json'):
            if H.present(STATE/name):
                data = H.read(STATE/name)
                value[name] = dict(sha256=sha(data), fields=json.loads(data))
    value['unit'] = H.unit_state(UNIT)
    value['previous_unit'] = H.unit_state(PREVIOUS.name+'.service')
    emit(value)


def main():
    global H
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--recover-once', action='store_true')
    g.add_argument('--status', action='store_true')
    g.add_argument('--run', help=argparse.SUPPRESS)
    args = p.parse_args()
    try:
        need(sys.platform == 'linux' and os.geteuid() == 0
             and socket.gethostname().split('.')[0] == 'datax-03', 'datax03_root_required')
        H = helpers()
        if args.status: status()
        elif args.run: return run(args.run)
        else: recover()
        return 0
    except BaseException as exc:
        result = dict(pilot_exec_recovery='UNCONFIRMED', phase=PHASE, automatic_retry=False,
                      original_evidence_modified=False, production_mounts_changed=False,
                      instruction='USE_STATUS_NO_REPEAT_RECOVERY_NO_CHMOD_NO_DELETE_STATE', **diagnostic(exc))
        if H is not None:
            result['last_operation'] = H.LAST
            if isinstance(exc, H.Failure): result['failed_check'] = str(exc)
            if OWNED and not args.status:
                name = 'RUN-GATE-FAILED.json' if args.run else 'LAUNCH-FAILED.json'
                if not H.present(STATE/name): H.save(STATE/name, result)
        emit(result)
        return 1


if __name__ == '__main__':
    sys.exit(main())

