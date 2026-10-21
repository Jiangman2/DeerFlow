#!/usr/bin/env python3
"""Recover only the pinned pre-entry, zero-timeout copy pilot; Python 3.7+."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import types
import uuid

OLD = Path('/var/lib/weknora-rwx-copy-pilot-20261017-r1')
STATE = Path('/var/lib/weknora-rwx-copy-pilot-resume-20261017-r1')
OLD_UNIT = OLD.name + '.service'
UNIT = STATE.name + '.service'
ENGINE_SHA = 'c296371643e8911bd71ae8fb174075b435b8db60e16164d736b26a166b177881'
OLD_STARTED_SHA = 'ccc2e7a13f02749ffa5899d2c7a01dc795c8936a2ab91c64566d99faba8470ab'
OLD_TOKEN = '85409ba3783b45b8b5d8379f6576342f'
ENV = dict(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')
PROPS = ('LoadState', 'ActiveState', 'SubState', 'Type', 'Restart', 'TimeoutStartUSec',
         'RuntimeMaxUSec', 'KillMode', 'MainPID', 'ControlPID', 'Result',
         'ExecMainCode', 'ExecMainStatus', 'Transient', 'ControlGroup')
MARKERS = ('START-ACK.json', 'RUN-ENTERED.json', 'MOUNT-REQUESTED.json',
           'WORKER-REQUESTED.json', 'PROGRESS.json', 'RESULT.json')
LAST = {}
CAN_JOURNAL = False


class Failure(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise Failure(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def emit(value):
    print(json.dumps(value, sort_keys=True, separators=(',', ':')), flush=True)


def safe_dir(path):
    for p in (path,) + tuple(path.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_private_directory')


def read(path):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_private_file')
        data = f.read(1048577)
    need(len(data) <= 1048576, 'private_file_oversize')
    return data


def write(path, data):
    safe_dir(path.parent)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(path, value):
    write(path, (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode())


def present(path):
    # Broken links must not be mistaken for an absent operation marker.
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def executable(name):
    value = shutil.which(name, path=ENV['PATH'])
    need(value is not None, 'required_executable_missing')
    p = Path(value).resolve(strict=True)
    safe_dir(p.parent)
    s = p.stat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
         'unsafe_executable')
    return str(p)


def command(args, timeout=20):
    global LAST
    LAST = dict(program=Path(args[0]).name, exit_code=None)
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=ENV, close_fds=True)
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        LAST.update(timed_out=True, pid=p.pid)
        # Especially never kill or replay a service start or mount of uncertain outcome.
        raise Failure('command_timeout_outcome_uncertain_no_retry')
    LAST.update(exit_code=p.returncode, stdout_bytes=len(out), stderr_bytes=len(err),
                stderr_sha256=sha(err), stderr_classes={
                    'timeout': b'timed out' in err.lower() or b'timeout' in err.lower(),
                    'permission_denied': b'permission denied' in err.lower(),
                    'unsupported_property': bool(re.search(b'unknown (assignment|lvalue|property)', err.lower())),
                    'unit_exists': b'already exists' in err.lower(),
                }, raw_error_printed=False)
    need(len(out) <= 65536 and len(err) <= 65536, 'command_output_limit')
    need(p.returncode == 0, 'local_command_failed_no_retry')
    return out


def unit_state(unit, with_exec=False):
    args = [executable('systemctl'), 'show', unit, '--no-pager']
    for name in PROPS + (('ExecStart',) if with_exec else ()):
        args += ['-p', name]
    text = command(args).decode('utf-8', 'strict')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def validate_old_unit(value):
    expected = dict(LoadState='loaded', ActiveState='failed', SubState='failed',
                    Type='oneshot', Restart='no', TimeoutStartUSec='0',
                    MainPID='0', ControlPID='0', Result='timeout',
                    ExecMainCode='2', ExecMainStatus='15', Transient='yes', KillMode='control-group')
    need(all(value.get(k) == v for k, v in expected.items()), 'old_unit_failure_identity_changed')
    # Do not print/store ExecStart. Match the pinned Python entry and exact run token.
    need(' -I -B -u ' + str(OLD/'pilot.py') + ' --run ' + OLD_TOKEN + ' ;'
         in value.get('ExecStart', ''), 'old_unit_command_identity_changed')


def process_absence(proc=Path('/proc')):
    scanned = 0
    deadline = time.monotonic() + 15
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        need(time.monotonic() < deadline and scanned < 100000, 'process_scan_limit')
        try:
            with (entry/'cgroup').open('rb') as f:
                data = f.read(65537)
        except (FileNotFoundError, ProcessLookupError):
            continue  # Concurrent process exit is not an unreadable live process.
        need(len(data) <= 65536, 'process_cgroup_oversize')
        scanned += 1
        for line in data.decode('utf-8', 'strict').splitlines():
            parts = line.split(':', 2)
            need(len(parts) == 3, 'process_cgroup_shape_changed')
            need(OLD_UNIT not in parts[2].split('/'), 'old_unit_process_still_present')
    need(scanned > 0, 'process_scan_empty')
    return dict(scanned_processes=scanned, matching_old_unit_processes=0)


def mount_absence(engine):
    for path in ('/proc/self/mountinfo', '/proc/1/mountinfo'):
        text = Path(path).read_text()
        need(len(text) < 4*1024*1024, 'mount_inventory_oversize')
        for line in text.splitlines():
            fields = line.split()
            need(len(fields) >= 6, 'mount_inventory_shape_changed')
            point = fields[4]
            need(point != str(OLD) and not point.startswith(str(OLD)+'/'), 'old_state_mount_present')
    return dict(old_state_mounts_observed=0)


def load_engine(path):
    data = read(path)
    need(sha(data) == ENGINE_SHA, 'pinned_engine_hash_changed')
    module = types.ModuleType('receipt_bound_pilot_engine')
    module.__file__ = str(path)
    exec(compile(data, str(path), 'exec'), module.__dict__)
    return module, data


def old_snapshot(engine):
    safe_dir(OLD)
    need(set(p.name for p in OLD.iterdir()) == {'pilot.py', 'STARTED.json', 'START-REQUESTED.json'},
         'old_state_has_entry_or_unreviewed_files')
    data = read(OLD/'STARTED.json')
    need(sha(data) == OLD_STARTED_SHA, 'old_started_receipt_changed')
    record = json.loads(data)
    need(record.get('token') == OLD_TOKEN and record.get('script_sha256') == ENGINE_SHA,
         'old_started_identity_changed')
    need(sha(read(OLD/'pilot.py')) == ENGINE_SHA, 'old_engine_changed')
    request = json.loads(read(OLD/'START-REQUESTED.json'))
    need(request.get('unit') == OLD_UNIT, 'old_request_identity_changed')
    need(not any(present(OLD/x) for x in MARKERS), 'old_operation_entry_not_absent')
    state = unit_state(OLD_UNIT, with_exec=True)
    validate_old_unit(state)
    processes = process_absence()
    mounts = mount_absence(engine)
    return dict(old_started_sha256=OLD_STARTED_SHA, old_script_sha256=ENGINE_SHA,
                old_request_sha256=sha(read(OLD/'START-REQUESTED.json')),
                old_unit={k: state[k] for k in PROPS if k in state},
                operation_entry_markers_absent=True, processes=processes, mounts=mounts)


def launch_args(tool, token):
    return [executable('systemd-run'), '--unit='+UNIT,
            '--property=Type=exec', '--property=Restart=no',
            '--property=TimeoutStartSec=30s', '--property=RuntimeMaxSec=infinity',
            '--property=KillMode=control-group', '--property=UMask=0077',
            '--property=Nice=10', '--property=IOSchedulingClass=idle',
            executable('unshare'), '--mount', '--propagation', 'private',
            executable('python3'), '-I', '-B', '-u', str(tool), '--run', token]


def validate_new_unit(value, pid):
    expected = dict(Type='exec', Restart='no', TimeoutStartUSec='30s',
                    RuntimeMaxUSec='infinity', KillMode='control-group',
                    MainPID=str(pid), Transient='yes')
    need(all(value.get(k) == v for k, v in expected.items()), 'new_unit_effective_properties_changed')
    need(value.get('ActiveState') in ('activating', 'active'), 'new_unit_not_active')


def resume():
    global CAN_JOURNAL
    emit(dict(pilot_recovery='RUNNING', phase='READ_ONLY_PINNED_PRE_ENTRY_RECONCILIATION'))
    need(not present(STATE), 'recovery_attempt_exists_use_status_no_retry')
    engine, engine_bytes = load_engine(OLD/'pilot.py')
    snapshots = [old_snapshot(engine)]
    time.sleep(1)
    snapshots.append(old_snapshot(engine))
    need(snapshots[0]['old_unit'] == snapshots[1]['old_unit']
         and snapshots[0]['old_request_sha256'] == snapshots[1]['old_request_sha256'], 'old_failure_drift')
    evidence = engine.verify_audit()
    engine.resources()
    need(unit_state(UNIT).get('LoadState') == 'not-found', 'new_unit_name_already_present')
    for name in ('systemd-run', 'systemctl', 'unshare', 'mount', 'mount.nfs', 'python3'):
        executable(name)
    driver = read(Path(__file__).resolve())
    safe_dir(STATE.parent)
    STATE.mkdir(mode=0o700)  # Exclusive guard even if two operators launch concurrently.
    CAN_JOURNAL = True
    write(STATE/'resume.py', driver)
    write(STATE/'engine.py', engine_bytes)
    save(STATE/'RECOVERY.json', dict(snapshots=snapshots, previous_copy='ZERO_VERIFIED_PRE_ENTRY',
         diagnosis='TRANSIENT_ONESHOT_EFFECTIVE_START_TIMEOUT_ZERO', original_evidence_modified=False,
         business_limit_files=5000, business_limit_bytes=1024**3, combined_payload_rate=10*1024**2,
         production_mounts_changed=False, ready_to_migrate=False, ready_to_scale=False))
    token = uuid.uuid4().hex
    save(STATE/'STARTED.json', dict(token=token, script_sha256=ENGINE_SHA, driver_sha256=sha(driver),
         recovery_sha256=sha(read(STATE/'RECOVERY.json')), utc=utc(), evidence=evidence))
    # Check again after preparation, before the once-only mutation boundary.
    old_snapshot(engine)
    args = launch_args(STATE/'resume.py', token)
    save(STATE/'START-REQUESTED.json', dict(unit=UNIT, utc=utc(), service_type='exec', startup_timeout_seconds=30))
    emit(dict(pilot_recovery='RUNNING', phase='START_FRESH_UNIT_ONCE', unit=UNIT))
    command(args, timeout=45)
    save(STATE/'START-ACK.json', dict(unit=UNIT, utc=utc(), launch_only_not_copy_acceptance=True))
    emit(dict(pilot_recovery='BACKGROUND_LAUNCH_ACKNOWLEDGED', state=str(STATE), unit=UNIT,
              original_evidence_modified=False, production_mounts_changed=False,
              automatic_retry=False, copy_acceptance='PENDING_RESULT',
              status_command='sudo python3 -I -B '+str(STATE/'resume.py')+' --status'))


def run(token):
    global CAN_JOURNAL
    engine, _ = load_engine(STATE/'engine.py')
    record = json.loads(read(STATE/'STARTED.json'))
    need(record.get('token') == token and record.get('driver_sha256') == sha(read(Path(__file__)))
         and record.get('script_sha256') == ENGINE_SHA
         and record.get('recovery_sha256') == sha(read(STATE/'RECOVERY.json')), 'new_run_identity_changed')
    need(not present(STATE/'ENGINE-GATE.json') and not present(STATE/'RUN-ENTERED.json'),
         'new_run_already_entered_no_retry')
    CAN_JOURNAL = True
    old_snapshot(engine)
    effective = unit_state(UNIT)
    validate_new_unit(effective, os.getpid())
    engine.isolated_namespace()
    save(STATE/'ENGINE-GATE.json', dict(unit=effective, utc=utc(), business_io_started=False))
    # Fresh module instance; immutable old engine bytes and old state are never edited.
    engine.STATE = STATE
    engine.UNIT = UNIT
    def audited_command(args, timeout=30):
        try:
            return command(args, timeout)
        except Failure as exc:
            raise engine.Failure(str(exc))
        finally:
            engine.LAST = dict(LAST)
    engine.command = audited_command
    return engine.internal_run(token)


def status():
    value = dict(pilot_recovery_status='READ_ONLY', state=str(STATE), files_modified=False,
                 copy_started_by_status=False, mounts_created=False, automatic_retry=False)
    if present(STATE):
        safe_dir(STATE)
        value['operation_markers'] = {x: present(STATE/x) for x in
            ('STARTED.json', 'START-REQUESTED.json', 'START-ACK.json', 'ENGINE-GATE.json',
             'RUN-ENTERED.json', 'MOUNT-REQUESTED.json', 'WORKER-REQUESTED.json',
             'PROGRESS.json', 'RESULT.json', 'LAUNCH-FAILED.json', 'RUN-GATE-FAILED.json')}
        for name in ('PROGRESS.json', 'RESULT.json', 'LAUNCH-FAILED.json', 'RUN-GATE-FAILED.json'):
            if present(STATE/name):
                data = read(STATE/name)
                value[name] = dict(sha256=sha(data), fields=json.loads(data))
    else:
        value['state_present'] = False
    value['unit'] = unit_state(UNIT)
    value['old_unit'] = unit_state(OLD_UNIT)
    emit(value)


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--resume-once', action='store_true')
    group.add_argument('--status', action='store_true')
    group.add_argument('--run', help=argparse.SUPPRESS)
    args = p.parse_args()
    try:
        need(sys.platform == 'linux' and os.geteuid() == 0
             and socket.gethostname().split('.')[0] == 'datax-03', 'datax03_root_required')
        if args.status:
            status()
        elif args.run:
            return run(args.run)
        else:
            resume()
        return 0
    except BaseException as exc:
        result = dict(pilot_recovery='UNCONFIRMED', error_type=type(exc).__name__,
                      failed_check=str(exc) if isinstance(exc, Failure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                      errno=getattr(exc, 'errno', None), raw_error_printed=False, last_operation=LAST,
                      automatic_retry=False, original_evidence_modified=False,
                      instruction='USE_STATUS_NO_REPEAT_RESUME_NO_RESET_FAILED_NO_DELETE_STATE')
        # Never write when asked only for status or overwrite an existing failure receipt.
        if not args.status and CAN_JOURNAL and present(STATE):
            name = 'RUN-GATE-FAILED.json' if args.run else 'LAUNCH-FAILED.json'
            try:
                if not present(STATE/name):
                    save(STATE/name, result)
            except BaseException:
                pass
        emit(result)
        return 1


if __name__ == '__main__':
    sys.exit(main())
