#!/usr/bin/env python3
"""Resume one prepared, never-entered target operation locally on datax-02."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import types
import uuid

OP = 'abf96dde50174b1eb7caf70a4e6711b1'
PRIOR = 'e8104bf11adc4959ad0b6078e7c97990'
SOURCE_REPORT_SHA = '8ac6f5adf848272d2057bdac0c07d37e454997417bd57d0ae38f019bffa300d8'
PREPARED_SHA = '94ae14afd5f72e83bd5f0fb9add65f499c32179e473bd3e48b6abc3156036fc5'
PREPARED_BYTES = 2871
TARGET_CODE_SHA = '2e71b02dd3d26b7d8b6d00824b95170a8e50c7eed0ede99222161ae54aeeafc5'
TARGET_CODE_BYTES = 46761
ALLOCATION_SHA = '0dd4db700f5c3d5a0a14b23b1d356d7473967df139ce69b1739a3a5448b0484d'
PROFILE_SHA = 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2'
SSH_FINGERPRINT = 'SHA256:VIGU1xEuM2WSdXfS2dCu0qqZjSgbl1UeIXDjZEOPWMI'
CONTROL = Path('/var/lib/portal-ppt-native-preload-r3-control-' + OP)
TARGET = Path('/var/lib/portal-ppt-native-preload-r3-target-' + OP)
ONCE = Path('/var/lib/portal-ppt-native-r3-local-resume-' + OP + '.STARTED.json')
PUBLIC_KEY = Path('/etc/ssh/ssh_host_ecdsa_key.pub')
ROOT = Path('/var/lib')


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def private_read(path, limit):
    for parent in (path.parent,) + tuple(path.parent.parents):
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'private_parent_required')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= limit, 'private_file_required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'private_file_changed')
        return raw
    finally:
        os.close(fd)


def host_identity():
    require(sys.platform.startswith('linux') and os.geteuid() == 0, 'linux_root_required')
    require(socket.gethostname().split('.')[0] == 'datax-02' and os.uname().machine in ('aarch64', 'arm64'), 'target_host_platform')
    fields = private_read(PUBLIC_KEY, 4096).split()
    require(len(fields) >= 2 and fields[0] == b'ecdsa-sha2-nistp256', 'host_public_key_type')
    key = base64.b64decode(fields[1], validate=True)
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
    require(fingerprint == SSH_FINGERPRINT, 'target_host_key_changed')
    return dict(hostname='datax-02', platform='linux/arm64', ssh_public_key_fingerprint=fingerprint,
                kubernetes_node_uid_freshly_checked=False, runtime_preflight_still_required=True)


def load_target():
    path = CONTROL / 'preload_native_r3.py'
    raw = private_read(path, 128 * 1024)
    require(len(raw) == TARGET_CODE_BYTES and sha(raw) == TARGET_CODE_SHA, 'frozen_target_code_changed')
    module = types.ModuleType('fixed_r3_target_local_resume')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    require(module.ALLOCATION_SHA == ALLOCATION_SHA and module.PROFILE_SHA == PROFILE_SHA, 'frozen_target_constants')
    profile = module.load_modules(True)
    return module, profile


def uploaded_files(module, profile, prepared):
    path = Path(prepared['upload'])
    require(path == module.state_paths(OP)['upload'], 'upload_path_changed')
    fd = module.B.open_anchored(path)
    try:
        value = os.fstat(fd)
        require(stat.S_ISDIR(value.st_mode) and (value.st_dev, value.st_ino, value.st_uid) ==
                (prepared['upload_device'], prepared['upload_inode'], prepared['upload_uid'])
                and stat.S_IMODE(value.st_mode) == 0o700, 'upload_directory_changed')
        expected = {row['basename']: row for row in profile['images']}
        require(len(expected) == 6 and set(os.listdir(fd)) == set(expected), 'upload_file_set_changed')
        rows = []
        for name, row in sorted(expected.items()):
            file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            try:
                observed = module.fd_regular(file_fd, row['archive_bytes'], private=False)
                require(observed.st_uid == prepared['upload_uid'] and stat.S_IMODE(observed.st_mode) == 0o600, 'upload_file_owner_mode')
                rows.append(dict(role=row['role'], basename=name, bytes=observed.st_size, device=observed.st_dev,
                                 inode=observed.st_ino, expected_archive_sha256=row['archive_sha256'],
                                 sha_verification='DEFERRED_TO_FROZEN_TARGET_COPY_AND_FULL_HASH'))
            finally:
                os.close(file_fd)
        require(identity(value) == identity(os.fstat(fd)), 'upload_directory_changed_during_check')
        return rows
    finally:
        os.close(fd)


def inspect_prepared(module, profile):
    module.B.safe_dir(TARGET)
    require(set(os.listdir(str(TARGET))) == {'STARTED.json', 'PREPARED.json'}, 'target_attempt_already_entered_or_changed')
    started_raw = private_read(TARGET / 'STARTED.json', 65536)
    prepared_raw = private_read(TARGET / 'PREPARED.json', 65536)
    require(len(prepared_raw) == PREPARED_BYTES and sha(prepared_raw) == PREPARED_SHA, 'prepared_receipt_changed')
    started, prepared = module.B.parse(started_raw), module.B.parse(prepared_raw)
    for value in (started, prepared):
        require(value['operation'] == OP and value['prior_operation'] == PRIOR and value['target'] == 'datax-02'
                and value['target_import_requested'] is False and value['image_import_may_be_partial'] is False
                and value['phase_a'] == value['phase_b'] == 'NOT_REQUESTED', 'prepared_state_invalid')
    require(started['status'] == 'INCOMPLETE' and prepared['status'] == 'PREPARED_NO_IMPORT', 'prepared_status')
    once_raw = private_read(module.ONCE, 65536)
    require(module.B.parse(once_raw) == dict(prior_operation=PRIOR, operation=OP, automatic_retry=False), 'original_once_binding')
    sealed = Path('/opt') / module.state_paths(OP)['sealed_name']
    require(not os.path.lexists(str(sealed)), 'target_seal_already_exists')
    wire_raw = private_read(CONTROL / 'PLAN-WIRE.json', 8 * 1024 ** 2)
    plan = module.unwire_plan(module.B.parse(wire_raw))
    module.validate_plan(profile, plan)
    rows = uploaded_files(module, profile, prepared)
    return plan, dict(started_sha256=sha(started_raw), prepared_sha256=sha(prepared_raw), original_once_sha256=sha(once_raw),
                      plan_wire_sha256=sha(wire_raw), plan_wire_bytes=len(wire_raw),
                      plan_provenance='PREVIOUS_FIXED_SOURCE_BOOTSTRAP_ROOT_PRIVATE_CONTROL_NOT_ORIGINAL_PLAN_BYTES',
                      uploaded_files=rows, original_target_unentered=True)


def claim(module, evidence):
    try:
        module.B.write_new(ONCE, encoded(dict(operation=OP, evidence=str(evidence), automatic_retry=False)))
    except FileExistsError:
        raise ValueError('local_resume_already_started')


def safe_error(exc, module):
    code = exc.code if module and isinstance(exc, module.B.Failure) else str(exc) if isinstance(exc, ValueError) else 'LOCAL_RESUME_FAILED'
    return code if re.fullmatch('[A-Za-z0-9_]{1,120}', code) else 'LOCAL_RESUME_FAILED'


def run():
    module = profile = lock = evidence = None
    entered = False
    result = dict(schema='portal-ppt-native-r3-local-resume.v1', operation=OP, status='INCOMPLETE',
                  source_report_sha256=SOURCE_REPORT_SHA, source_failure_preserved=True,
                  source_import_state='UNKNOWN_NO_REMOTE_RESULT_PRESERVED', target_code_sha256=TARGET_CODE_SHA,
                  prepared_sha256=PREPARED_SHA, automatic_retry=False, ready_for_rollout=False,
                  native_runtime_passed=False, new_ssh_or_scp=False, source_archives_copied=False,
                  workload_mutation=False, database_access=False, model_access=False, existing_images_deleted=False,
                  image_import_may_be_partial=None, target_import_requested=None,
                  target_entry_evidence='UNKNOWN_UNTIL_LOCKED_PREPARED_CHECK', frozen_target_entered=False)
    rc = 1
    try:
        def interrupted(signum, frame):
            raise ValueError('interrupted_no_retry')
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        result['host_identity'] = host_identity()
        os.umask(0o077)
        module, profile = load_target()
        lock = module.common_lock()
        # Checks are made while holding the same lock as the original target.
        plan, observations = inspect_prepared(module, profile)
        result['guard_checks'] = observations
        result.update(target_import_requested=False, image_import_may_be_partial=False,
                      target_entry_evidence='LOCKED_PREPARED_CHECK_CONFIRMED_NOT_ENTERED')
        require(not os.path.lexists(str(ONCE)), 'local_resume_already_started')
        evidence = module.B.fresh(ROOT / ('portal-ppt-native-r3-local-resume-' + uuid.uuid4().hex))
        result['evidence'] = str(evidence)
        claim(module, evidence)
        module.B.write_new(evidence / 'GUARD-STARTED.json', encoded(result))
        module.B.progress('LOCAL_TARGET_RESUME', operation=OP, original_target_unentered=True)
        entered = True
        result['frozen_target_entered'] = True
        target = module.import_target(profile, plan, OP)
        require(target.get('status') == 'COMPLETE_PRELOAD' and target.get('phase_a') == target.get('phase_b') == 'PASS'
                and len(target.get('image_rows', [])) == 6, 'target_completion_not_verified')
        result.update(status='COMPLETE_PRELOAD', target_report=target, target_import_requested=True,
                      image_import_may_be_partial=False)
        rc = 0
    except (Exception, KeyboardInterrupt) as exc:
        result['error_code'] = safe_error(exc, module)
        if entered and module is not None:
            requested = bool(module.IMPORT_REQUESTED)
            result.update(target_import_requested=requested, image_import_may_be_partial=requested)
            if module.REPORT is not None:
                target = dict(module.REPORT)
                target.update(status='INCOMPLETE', error_code=result['error_code'], automatic_retry=False,
                              target_import_requested=requested, image_import_may_be_partial=requested)
                result['target_report'] = target
                # Only this invocation's normal target entry may receive FAIL.
                if (TARGET / 'RUN-REQUESTED.json').is_file():
                    try:
                        module.write_receipt('FAIL.json', target)
                    except Exception:
                        result['target_failure_write_status'] = 'UNAVAILABLE_OR_ALREADY_EXISTS'
    finally:
        try:
            raw = encoded(result)
            if evidence is not None:
                try:
                    module.B.write_new(evidence / 'REPORT.json', raw)
                except Exception:
                    pass
            summary = dict(status=result['status'], operation=OP, report_sha256=sha(raw), report_bytes=len(raw), exitcode=rc,
                           target_import_requested=result['target_import_requested'],
                           image_import_may_be_partial=result['image_import_may_be_partial'], automatic_retry=False)
            if result.get('error_code'):
                summary['error_code'] = result['error_code']
            try:
                require(module is not None, 'report_handoff_module_unavailable')
                summary['download_report'] = module.handoff_report(raw)
            except Exception:
                summary['download_report_status'] = 'HANDOFF_UNAVAILABLE_SAFE_REPORT_PRINTED'
                sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
            print(json.dumps(summary, sort_keys=True), flush=True)
        finally:
            if lock is not None:
                os.close(lock)
    return rc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-on-target', action='store_true', required=True)
    parser.parse_args()
    raise SystemExit(run())
