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
PAYLOAD = {}  # Generator replaces this exact assignment with four locked blobs.


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
