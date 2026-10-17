#!/usr/bin/env python3
"""Repair one pinned prepare snapshot, then stage a NEW pool operation once.

Python 3.7+. No recursive chmod, evidence deletion, public switch or queue drain.
Previously writable files are not trusted merely because permissions are fixed:
their deployment specs and route are reconciled to the pinned prepare receipt,
and the frozen staging tool regenerates its manifests from the live baseline.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import types

HERE = Path(__file__).absolute().parent
PREPARED = Path('/var/lib/mineru-bluegreen16-prepare-1kye80k4')
OLD_STAGE = Path('/var/lib/mineru-bluegreen16-stage-20261015-r1')
NEW_STAGE = Path('/var/lib/mineru-bluegreen16-stage-20261015-r1-fix1')
RECOVERY = Path('/var/lib/mineru-bluegreen16-permission-recovery-20261015-r1')
OLD_SHA = '231c5ae2fb88a39d619a2551c4848b841656d89dae9737201f264108d50531dc'
PREPARE_SHA = 'bafea93f168585cfb5ea7ed7ad3eaf6e60ee7b44bcb1117132a559d47593a7d6'
STAGE_SHA = '7818d32892d0bb51dd249f6c430dd27a32304972f798d40104ef383c6c2f83af'
LIMIT = 2 * 1024**2
PHASE = 'BOOTSTRAP'
DETAIL = None


class RecoveryFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise RecoveryFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate_json_key')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs)


def identity(s):
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_gid, s.st_nlink,
            s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def safe_dir(path):
    require(path.is_absolute(), 'absolute_directory_required')
    for part in reversed([path] + list(path.parents)):
        s = part.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_control_ancestor')


def file_metadata(s, private=False):
    require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_gid == 0
            and s.st_nlink == 1 and 0 < s.st_size < LIMIT, 'unsafe_evidence_file')
    require(not s.st_mode & 0o7000, 'special_file_permissions')
    if private:
        require(not s.st_mode & 0o022, 'unsafe_private_file')


def read_fd(fd, parent_fd, name, private=False):
    before = os.fstat(fd)
    file_metadata(before, private)
    os.lseek(fd, 0, os.SEEK_SET)
    chunks, total = [], 0
    while True:
        chunk = os.read(fd, min(65536, LIMIT - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        require(total < LIMIT, 'evidence_file_too_large')
    after = os.fstat(fd)
    current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    require(identity(before) == identity(after) == identity(current)
            and total == before.st_size, 'evidence_changed_during_read')
    return b''.join(chunks), before


def locked(path, expected):
    safe_dir(path.parent)
    directory = os.open(str(path.parent), os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            raw, _ = read_fd(fd, directory, path.name, private=True)
            require(sha(raw) == expected, 'locked_evidence_hash_mismatch')
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(directory)


def load_stage():
    raw = locked(HERE/'stage_bluegreen16.py', STAGE_SHA)
    module = types.ModuleType('frozen_mineru_stage_permission_recovery')
    # Same package directory; emitted --status must point to this wrapper,
    # whose CLI selects NEW_STAGE, never the previous failed operation.
    module.__file__ = str(Path(__file__).absolute())
    exec(compile(raw, str(HERE/'stage_bluegreen16.py'), 'exec'), module.__dict__)
    require(module.ROOT == OLD_STAGE and module.PREPARED == PREPARED
            and module.REPORT_SHA == PREPARE_SHA, 'frozen_stage_binding_changed')
    module.ROOT = NEW_STAGE
    module.bootstrap()  # All helper hashes/permissions checked before repairs.
    return module


def failed_attempt(report):
    require(report.get('failed_stage') == 'BIND_PREPARE_AND_LIVE_BASELINE'
            and report.get('failed_check') == 'unsafe_private_directory'
            and report.get('cluster_writes_attempted') is False
            and report.get('create_requested') == [] and report.get('created') == []
            and 'last_stage_operation' in report and report['last_stage_operation'] is None
            and report.get('existing_workloads_modified') is False
            and report.get('public_route_changed') is False,
            'previous_attempt_not_verified_before_first_cluster_write')


def expected_members(stage):
    names = {'RESULT.json', 'OBJECTS.proposed.private.json',
             'PUBLIC-SERVICE.before.private.json', 'PUBLIC-SERVICE.forward.dry-run.private.json',
             'PUBLIC-SERVICE.forward.admitted.private.json'}
    names.update(n+'.before.private.json' for n in stage.core.inv.BASELINES)
    names.update(n+suffix for n in stage.ALLOWED
                 for suffix in ('.proposed.private.json', '.admitted.private.json'))
    require(len(names) == 27, 'unexpected_prepared_member_count')
    return names


def object_identity(obj, kind, name):
    m = obj.get('metadata', {})
    require(obj.get('kind') == kind and m.get('name') == name
            and m.get('namespace') == 'deer-flow', 'snapshot_object_identity_changed')


def validate_snapshots(stage, raw_files):
    require(set(raw_files) == expected_members(stage), 'unexpected_prepared_members')
    require(sha(raw_files['RESULT.json']) == PREPARE_SHA, 'prepare_report_hash_mismatch')
    data = {name: decode(raw) for name, raw in raw_files.items()}
    report = data['RESULT.json']
    stage.report_contract(report)
    digest = stage.core.inv.digest
    require(set(report['baselines']) == set(stage.core.inv.BASELINES), 'baseline_set_changed')
    for name, lock in stage.core.inv.BASELINES.items():
        obj, baseline = data[name+'.before.private.json'], report['baselines'][name]
        object_identity(obj, 'Deployment', name)
        require((obj['metadata']['uid'], digest(obj['spec'])) == tuple(lock)
                == (baseline['uid'], baseline['spec_sha256'])
                and obj['metadata']['generation'] == baseline['generation'], 'baseline_snapshot_changed')
    listed = data['OBJECTS.proposed.private.json']
    require(isinstance(listed, list) and len(listed) == 9, 'object_bundle_incomplete')
    objects = {o['metadata']['name']: o for o in listed}
    require(set(objects) == set(stage.ALLOWED), 'object_bundle_names_changed')
    for review in report['dry_runs']:
        name, kind = review['name'], review['kind']
        obj = data[name+'.proposed.private.json']
        admitted = data[name+'.admitted.private.json']
        object_identity(obj, kind, name)
        object_identity(admitted, kind, name)
        require(objects[name] == obj and digest(obj['spec']) == review['proposed_spec_sha256']
                and digest(admitted['spec']) == review['admitted_spec_sha256'], 'prepared_spec_hash_mismatch')
        stage.validate_target(obj)
        stage.prep.validate_admission(obj, admitted)
    old, answer = data['PUBLIC-SERVICE.before.private.json'], data['PUBLIC-SERVICE.forward.admitted.private.json']
    route = report['route_plan']
    for obj in (old, answer):
        object_identity(obj, 'Service', stage.prep.WORKER)
        require(obj['metadata']['uid'] == route['uid'], 'public_service_uid_changed')
    require(digest(old['spec']) == route['original_spec_sha256']
            and digest(answer['spec']) == route['proposed_spec_sha256'], 'public_service_spec_changed')
    expected = copy.deepcopy(old['spec'])
    expected['selector'] = stage.prep.labels('dispatcher')
    require(answer['spec'] == expected and data['PUBLIC-SERVICE.forward.dry-run.private.json'] ==
            stage.prep.route_patch(old, stage.prep.labels('dispatcher')), 'route_changed_more_than_selector')
    return report


def repair_snapshot(stage, audit):
    global PHASE, DETAIL
    safe_dir(PREPARED.parent)
    before = PREPARED.lstat()
    require(stat.S_ISDIR(before.st_mode) and before.st_uid == before.st_gid == 0
            and stat.S_IMODE(before.st_mode) in (0o777, 0o700), 'unexpected_prepare_directory_metadata')
    directory = os.open(str(PREPARED), os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    files = {}
    try:
        require(identity(os.fstat(directory)) == identity(before) == identity(PREPARED.lstat()),
                'prepare_directory_replaced')
        # Close the writable directory first; no chmod on /var/lib or siblings.
        os.fchmod(directory, 0o700)
        os.fsync(directory)
        audit['prepare_directory_mode'] = '0700'
        require(set(os.listdir(directory)) == expected_members(stage), 'unexpected_prepared_members')
        PHASE = 'VERIFY_27_PREPARE_FILES'
        raw_files, old_stats = {}, {}
        for name in sorted(expected_members(stage)):
            DETAIL = name
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            files[name] = fd
            raw_files[name], old_stats[name] = read_fd(fd, directory, name)
        report = validate_snapshots(stage, raw_files)
        PHASE = 'RESTRICT_VERIFIED_FILE_PERMISSIONS'
        for name, fd in sorted(files.items()):
            DETAIL = name
            raw, current = read_fd(fd, directory, name)
            require(raw == raw_files[name] and identity(current) == identity(old_stats[name]),
                    'file_changed_before_permission_repair')
            os.fchmod(fd, 0o600)
            os.fsync(fd)
            after, metadata = read_fd(fd, directory, name, private=True)
            require(after == raw and stat.S_IMODE(metadata.st_mode) == 0o600,
                    'file_changed_after_permission_repair')
        require(set(os.listdir(directory)) == expected_members(stage), 'prepared_members_changed')
        current = PREPARED.lstat()
        require(identity(current) == identity(os.fstat(directory)) and current.st_uid == 0
                and stat.S_IMODE(current.st_mode) == 0o700, 'prepare_directory_changed_after_repair')
        # Recheck every open inode once more; permissions don't revoke old FDs.
        for name, fd in sorted(files.items()):
            require(read_fd(fd, directory, name, private=True)[0] == raw_files[name], 'file_changed_at_final_recheck')
        audit.update(permission_repair='PASS', directories=1, files=27, file_mode='0600',
            file_bytes_unchanged_during_this_operation=True, prepared_report_sha256=PREPARE_SHA,
            full_private_file_historical_integrity='NOT_ASSERTED',
            pinned_report_and_all_proposed_admitted_specs='VERIFIED',
            snapshots_used_as_executable_manifests=False,
            files_sha256={n: sha(raw) for n, raw in sorted(raw_files.items())})
        return report
    finally:
        for fd in files.values():
            os.close(fd)
        os.close(directory)


def live_readonly_reconcile(stage, reviewed):
    # No exec, dry-run, provider request, create or adoption here. The frozen
    # stage repeats full baseline/resource/admission checks before its writes.
    require(not stage.get('pods', selector=stage.prep.LABEL+'='+stage.prep.RELEASE)['items'],
            'new_pool_pods_already_exist_use_status')
    for name, kind in sorted(stage.ALLOWED.items()):
        require(stage.get('deployments' if kind == 'Deployment' else 'services', name) is None,
                'new_pool_name_already_exists_use_status')
    for name, baseline in reviewed['baselines'].items():
        actual = stage.get('deployments', name)
        require(actual and actual['metadata']['uid'] == baseline['uid']
                and stage.core.inv.digest(actual['spec']) == baseline['spec_sha256'], 'live_baseline_changed')
    service = stage.get('services', stage.prep.WORKER)
    route = reviewed['route_plan']
    require(service and service['metadata']['uid'] == route['uid']
            and stage.core.inv.digest(service['spec']) == route['original_spec_sha256'], 'live_public_route_changed')


def stage_once(stage, status=False):
    original = sys.argv
    try:
        sys.argv = [str(Path(__file__).absolute()), '--status' if status else '--stage-only']
        return stage.main()
    finally:
        sys.argv = original


def ensure_new(path):
    safe_dir(path.parent)
    require(not path.exists() and not path.is_symlink(), 'operation_exists_use_status_do_not_repeat')


def main():
    global PHASE, DETAIL
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--recover-and-stage', action='store_true')
    modes.add_argument('--status', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    stage = load_stage()
    if args.status:
        emit(dict(permission_recovery_evidence=str(RECOVERY), new_stage_evidence=str(NEW_STAGE),
                  status_only=True, files_modified=False))
        if (RECOVERY/'PERMISSION-RESULT.json').exists():
            # Do not print full private inventories or arbitrary exception text.
            row = decode(stage.core.pf.read_private(RECOVERY/'PERMISSION-RESULT.json'))
            emit({k: row.get(k) for k in ('permission_repair', 'failed_phase', 'failed_check', 'stage_invocation_permitted')})
        return stage_once(stage, status=True)
    ensure_new(NEW_STAGE)
    ensure_new(RECOVERY)
    PHASE = 'PIN_PREVIOUS_NO_WRITE_FAILURE'
    failed_attempt(decode(locked(OLD_STAGE/'RESULT.json', OLD_SHA)))
    require({p.name for p in OLD_STAGE.iterdir()} == {'RESULT.json'}, 'previous_stage_has_unreviewed_records')
    RECOVERY.mkdir(mode=0o700)
    audit = dict(permission_repair='UNCONFIRMED', stage_invocation_permitted=False,
        old_stage_result_sha256=OLD_SHA, old_failure_evidence_modified=False,
        new_stage_evidence=str(NEW_STAGE), public_route_changed=False,
        production_workloads_mutated=False, provider_requests=0, automatic_retry=False)
    emit(dict(permission_recovery_evidence=str(RECOVERY), new_stage_evidence=str(NEW_STAGE),
              scope='ONE_EXACT_PREPARE_DIRECTORY_AND_27_FILES', public_route_changed=False))
    try:
        reviewed = repair_snapshot(stage, audit)
        PHASE, DETAIL = 'LIVE_READ_ONLY_RECONCILIATION', None
        live_readonly_reconcile(stage, reviewed)
        audit.update(live_baseline='UNCHANGED', new_pool_names='ABSENT', stage_invocation_permitted=True)
    except BaseException as exc:
        audit.update(failed_phase=PHASE, error_type=type(exc).__name__, file=DETAIL,
                     failed_check=str(exc) if isinstance(exc, RecoveryFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                     stage_invocation_permitted=False)
    stage.core.save(RECOVERY, 'PERMISSION-RESULT.json', audit)
    emit({k: audit.get(k) for k in ('permission_repair', 'failed_phase', 'failed_check', 'files',
        'directories', 'live_baseline', 'new_pool_names', 'stage_invocation_permitted')})
    if not audit['stage_invocation_permitted']:
        emit(dict(instruction='SEND_REPORT_USE_STATUS_DO_NOT_REPEAT', report=str(RECOVERY/'PERMISSION-RESULT.json')))
        return 1
    PHASE = 'RUN_FROZEN_STAGE_IN_NEW_OPERATION'
    stage.core.save(RECOVERY, 'STAGE-REQUESTED.json', dict(new_stage_evidence=str(NEW_STAGE),
        frozen_stage_source_sha256=STAGE_SHA, production_route_switch=False, old_pool_modified=False))
    return stage_once(stage)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(permission_recovery='UNCONFIRMED', failed_phase=PHASE,
            error_type=type(exc).__name__, raw_error_printed=False, automatic_retry=False,
            failed_check=str(exc) if isinstance(exc, RecoveryFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            instruction='USE_STATUS_DO_NOT_REPEAT', ready_for_rollout=False))
        sys.exit(1)
