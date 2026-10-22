#!/usr/bin/env bash
set -eu
[ "$#" -eq 0 ] || { echo "This fixed diagnostic accepts no arguments." >&2; exit 2; }
exec /usr/bin/python3 -I -B -u - <<'PORTAL_HOST_BASE_BOOTSTRAP_PY'
"""Fixed single-file operator bootstrap. Embedded payload is supplied by generator."""
import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid

VAR_LIB = Path('/var/lib')
OUTPUT_BASE = Path('/var/lib/portal-ppt-host-base-verify-r1')
HOME_BASE = Path('/home')
LOGIN = 'cnooc'
HOME = '/home/cnooc'
MAX_REPORT = 2 * 1024 * 1024
TOTAL_SECONDS = 1860
EXPECTED_FILES = {
    'verify_host_base.py': ('57fc8798ae3fac30dbf73862d66ad670319a9eea3d85436d4e68330ac57b5eee', 22621),
    'EXPECTATIONS.json': ('968f8bff22c6072b8c1075386198e6af5f23fff2c8e1e89651720084ee3ee199', 7154),
    'frozen_gateway_replay.py': ('ee7500ca36844d96308a83caef0663766569c42aa935bee2fd2c34f653b4751a', 21126),
    'frozen_export_verifier.py': ('23f61ad3164f37856c3c320c34a4dd8a0c81020e2bc9d14a783736f7a8a0be0f', 21084),
}
COLLECTOR_INPUTS = {name: row[0] for name, row in EXPECTED_FILES.items() if name != 'verify_host_base.py'}
PAYLOAD = {'verify_host_base.py': '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', 'EXPECTATIONS.json': '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', 'frozen_gateway_replay.py': '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', 'frozen_export_verifier.py': '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'}


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def directory_fd(path):
    """Walk absolute directories from / with no-follow FD anchoring."""
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'directory_path_invalid')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def private_root_directory(info):
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700,
            'directory_not_private_root')


def decode_payload(payload):
    require(set(payload) == set(EXPECTED_FILES), 'embedded_file_set_mismatch')
    decoded = {}
    for name, (expected, size) in EXPECTED_FILES.items():
        require(isinstance(payload[name], str) and len(payload[name]) <= 1024 * 1024, 'embedded_blob_limit')
        packed = base64.b64decode(payload[name].encode('ascii'), validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed), mode='rb') as source:
            raw = source.read(size + 1)
        require(len(raw) == size and sha(raw) == expected, 'embedded_file_hash_or_size')
        decoded[name] = raw
    return decoded


def stage_tool(files):
    parent_fd = directory_fd(VAR_LIB)
    try:
        parent = os.fstat(parent_fd)
        require(parent.st_uid == 0 and parent.st_mode & 0o022 == 0, 'var_lib_not_root_controlled')
        directory = Path(tempfile.mkdtemp(prefix='portal-ppt-host-base-tool-', dir=str(VAR_LIB)))
        control_fd = os.open(directory.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        try:
            private_root_directory(os.fstat(control_fd))
            for name, raw in files.items():
                require(name in EXPECTED_FILES and sha(raw) == EXPECTED_FILES[name][0], 'staged_file_not_pinned')
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=control_fd)
                with os.fdopen(fd, 'wb') as target:
                    target.write(raw)
                    target.flush()
                    os.fsync(target.fileno())
            os.fsync(control_fd)
        finally:
            os.close(control_fd)
        return directory
    finally:
        os.close(parent_fd)


def run_once(directory, output):
    # Exact argv only; inherit no arbitrary proxy/provider/runtime environment.
    process = subprocess.Popen([sys.executable, '-I', '-B', '-u', str(directory / 'verify_host_base.py'),
                                '--verify', '--out', str(output)], cwd=str(directory),
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'PYTHONIOENCODING': 'utf-8'},
                               stdin=subprocess.DEVNULL, start_new_session=True)
    timed_out = False
    try:
        # Reserve two five-second owned-process termination windows inside 1860s.
        returncode = process.wait(timeout=TOTAL_SECONDS - 10)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            returncode = process.wait(timeout=5)
    return {'collector_exit_code': returncode, 'outer_timeout': timed_out, 'attempts': 1}


def read_report(output):
    require(output.parent == OUTPUT_BASE and re.fullmatch(r'host-base-[a-f0-9]{16}', output.name), 'output_scope_changed')
    base_fd = directory_fd(OUTPUT_BASE)
    try:
        private_root_directory(os.fstat(base_fd))
        root_fd = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=base_fd)
        try:
            private_root_directory(os.fstat(root_fd))
            fd = os.open('REPORT.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root_fd)
            with os.fdopen(fd, 'rb') as stream:
                before = os.fstat(stream.fileno())
                require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == 0
                        and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= MAX_REPORT,
                        'report_type_permission_or_size')
                raw = stream.read(MAX_REPORT + 1)
                require(len(raw) == before.st_size and identity(before) == identity(os.fstat(stream.fileno()))
                        == identity(os.stat('REPORT.json', dir_fd=root_fd, follow_symlinks=False)), 'report_changed')
        finally:
            os.close(root_fd)
    finally:
        os.close(base_fd)
    doc = json.loads(raw.decode('utf-8'))
    require(doc['schema'] == 'portal-ppt-host-existing-oci-diagnostic.v1'
            and doc['status'] in ('INCOMPLETE', 'DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED')
            and doc['frozen_inputs'] == COLLECTOR_INPUTS
            and doc['image_env_or_runtime_config'] == 'NOT_PROJECTED', 'report_contract_mismatch')
    for key in ('ready_for_build', 'ready_for_rollout', 'image_execution', 'rootfs_extracted',
                'production_mutation', 'network_or_subprocess', 'automatic_retry'):
        require(doc[key] is False, 'report_boundary_mismatch')
    require(isinstance(doc['errors'], list) and len(doc['errors']) <= 4
            and all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_]{1,128}', x) for x in doc['errors']),
            'report_error_projection_mismatch')
    return raw, doc


def export_report(raw):
    require(0 < len(raw) <= MAX_REPORT, 'export_report_limit')
    user = pwd.getpwnam(LOGIN)
    require(user.pw_name == LOGIN and user.pw_uid > 0 and user.pw_gid >= 0 and user.pw_dir == HOME,
            'download_user_identity_mismatch')
    parent_fd = directory_fd(HOME_BASE)
    home_fd = None
    try:
        parent = os.fstat(parent_fd)
        require(parent.st_uid == 0 and parent.st_mode & 0o022 == 0, 'home_parent_not_root_controlled')
        home_fd = os.open(LOGIN, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
        home = os.fstat(home_fd)
        require(home.st_uid == user.pw_uid, 'home_owner_mismatch')
        name = 'portal-ppt-host-base-20261017-r1-' + uuid.uuid4().hex[:16]
        os.mkdir(name, 0o700, dir_fd=home_fd)
        download_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=home_fd)
        try:
            initial_directory = os.fstat(download_fd)
            private_root_directory(initial_directory)
            fd = os.open('REPORT.json', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=download_fd)
            with os.fdopen(fd, 'w+b') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
                stream.seek(0)
                require(sha(stream.read(MAX_REPORT + 1)) == sha(raw), 'download_copy_digest_mismatch')
                file_info = os.fstat(stream.fileno())
                require(stat.S_ISREG(file_info.st_mode) and file_info.st_nlink == 1
                        and file_info.st_uid == 0 and stat.S_IMODE(file_info.st_mode) == 0o600, 'download_file_identity')
                os.fchown(stream.fileno(), user.pw_uid, user.pw_gid)
                os.fsync(stream.fileno())
            current = os.stat(name, dir_fd=home_fd, follow_symlinks=False)
            require((current.st_dev, current.st_ino) == (initial_directory.st_dev, initial_directory.st_ino),
                    'download_directory_replaced')
            # Only after writing/checking the sole safe report is the directory handed over.
            os.fsync(download_fd)
            os.fchown(download_fd, user.pw_uid, user.pw_gid)
            require(os.fstat(download_fd).st_uid == user.pw_uid, 'download_directory_handoff_failed')
            os.fsync(home_fd)
        finally:
            os.close(download_fd)
        return Path(HOME) / name / 'REPORT.json'
    finally:
        if home_fd is not None:
            os.close(home_fd)
        os.close(parent_fd)


def main():
    control, output = None, None
    summary = {'wrapper_status': 'INCOMPLETE', 'attempts': 0, 'automatic_retry': False,
               'ready_for_build': False, 'ready_for_rollout': False}
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename == 'datax-01', 'root_datax_gate')
        os.umask(0o077)
        files = decode_payload(PAYLOAD)
        control = stage_tool(files)
        output = OUTPUT_BASE / ('host-base-' + uuid.uuid4().hex[:16])
        print('control_directory=' + str(control), flush=True)
        print('private_report_directory=' + str(output), flush=True)
        summary.update(run_once(control, output))
        require(not summary['outer_timeout'], 'outer_process_deadline_exceeded')
        require(summary['collector_exit_code'] in (0, 2), 'collector_unexpected_exit')
        raw, doc = read_report(output)
        destination = export_report(raw)
        summary.update({'wrapper_status': 'REPORT_EXPORTED_REVIEW_REQUIRED', 'report_status': doc['status'],
                        'report_errors': doc['errors'], 'report_sha256': sha(raw), 'download_report': str(destination),
                        'private_report': str(output / 'REPORT.json'), 'control_directory': str(control)})
        print(json.dumps(summary, sort_keys=True), flush=True)
        print('Return REPORT.json and this terminal summary. Do not rerun before review.', flush=True)
        return 0 if doc['status'] == 'DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED' and not doc['errors'] else 2
    except Exception as exc:
        summary['error'] = str(exc) if isinstance(exc, Refused) else 'unexpected_wrapper_failure_details_withheld'
        summary['control_directory'] = str(control) if control else None
        summary['private_report_directory'] = str(output) if output else None
        print(json.dumps(summary, sort_keys=True), flush=True)
        print('Stopped; existing evidence retained. Do not rerun before review.', flush=True)
        return 2


if __name__ == '__main__':
    sys.exit(main())

PORTAL_HOST_BASE_BOOTSTRAP_PY
