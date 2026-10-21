#!/usr/bin/env python3
"""Recover the exact acknowledged PG import; import only the untouched local Redis archive."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import stat
import sys
import time

import diagnose_enrichment_preload as diagnostic

OLD = Path('/opt/weknora-enrichment-preload-20261017-r1')
STATE = Path('/opt/weknora-enrichment-preload-resume-20261017-r1')
RECEIPTS = {
    'STARTED.json': '0b440ed8bcda765d06262e715533eae45f893fb4ddc5f9453eddaab5c8efa6be',
    'FAILED.json': '469696342b6304d3145f5340b78e6bdcadcaeb22358cfd28781b6d03e87ee362',
    'postgres/EXPORT.json': '27ac4b9ff6096b47a4aa1cc9ac07a0a6a0b444934ade337fb9bcdc27fcd3b854',
    'postgres/TRANSFER-PASS.json': 'fb6b20eb7c533e285f421aeb3121c78bd1bd48254687fae6a2cb96a70e54748f',
    'postgres/IMPORT-REQUESTED.json': 'f08ab50823aa11c2a722afaf3008a024c6e1e8b4963b7661b3abcbc249d72780',
    'postgres/IMPORT-ACK.json': '20ebd65b325b7589575bf83db73cfac6f98f88f6cc4027f48c4cef7e651bcdc2',
    'redis/EXPORT.json': '5c3b8b501e20f4ef6bcf3f4fe2045dd2a2f550318afea29d746d9fbece10454d',
    'redis/TRANSFER-PASS.json': '0da1aa76f7cafc365fbdb57c5521a13f4f41da2670a2376ca78830339c67cb07',
}
PHASE = 'PREFLIGHT'
IMPORT_ATTEMPTED = False
LAST_READ = {}


def emit(value): diagnostic.emit(value)


def common():
    return dict(state=str(STATE), previous_state=str(OLD), original_evidence_modified=False,
                postgres_reimported=False, source_transfer_repeated=False, credentials_requested=False,
                production_writes=0, production_workloads_mutated=False, production_database_connections=0,
                production_redis_connections=0, pods_created=0, provider_requests=0, model_changed=False,
                consumer_slots_changed=False, build_started=False, ready_for_rollout=False,
                native_enrichment_acceptance='NOT_TESTED', automatic_mutation_retry=False)


def phase(value):
    global PHASE
    PHASE = value
    emit(dict(enrichment_preload_recovery='RUNNING', phase=value))


def read_record(p, path, sha=None):
    p.io.safe_dir(path.parent)
    before = p.io.private_meta(path)
    p.require(0 < before[2] <= 2*1024**2, 'receipt_size')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f: raw = f.read(2*1024**2+1)
    p.require(p.io.private_meta(path) == before and len(raw) == before[2], 'receipt_changed')
    p.require(sha is None or hashlib.sha256(raw).hexdigest() == sha, 'historical_receipt_hash_changed')
    return json.loads(raw, object_pairs_hook=p.verify.unique), hashlib.sha256(raw).hexdigest()


def bind_history(p):
    p.io.safe_dir(OLD)
    records = {name: read_record(p, OLD/name, sha)[0] for name, sha in RECEIPTS.items()}
    fail = records['FAILED.json']
    p.require(fail.get('enrichment_store_preload') == 'UNCONFIRMED'
              and fail.get('failed_stage') == 'VERIFY_LOCAL_CONTENT_UNPACK_CRI_PIN'
              and fail.get('failed_check') == 'local_command_failed_no_retry'
              and fail.get('imports_attempted') == ['postgres'] and fail.get('production_writes') == 0,
              'historical_failure_boundary_changed')
    p.require(records['postgres/IMPORT-ACK.json'] == {'client_exit_code': 0}, 'postgres_import_not_acknowledged')
    for path in (OLD/'RESULT.json', OLD/'postgres/PRELOAD-PASS.json', OLD/'postgres/PIN-REQUESTED.json',
                 OLD/'redis/IMPORT-REQUESTED.json', OLD/'redis/IMPORT-ACK.json', OLD/'redis/PIN-REQUESTED.json',
                 OLD/'redis/PRELOAD-PASS.json'):
        p.require(not os.path.lexists(str(path)), 'previous_operation_advanced_REVIEW_STATUS')
    for item in p.ITEMS:
        r = records[item['role']+'/TRANSFER-PASS.json']
        p.require(r.get('private_transfer') == 'PASS' and r.get('archive') == str(OLD/item['role']/'store.oci.tar')
                  and all(r.get(k) == v for k, v in item.items()), 'transfer_binding_changed')
        path = OLD/item['role']/'store.oci.tar'
        p.require(p.io.private_meta(path)[2] == item['archive_bytes'], 'retained_archive_size_changed')
    return dict(RECEIPTS)


def install_read_checks(p):
    """At most 3 read-only CLI attempts, never rerun import/label on any failure."""
    original = p.cli.command
    def read(argv, **options):
        if argv == ['ps', '-eo', 'comm=,args=']:
            detail = {'operation': 'READ_PROCESS_METADATA'}
        elif argv == p.cli.CRI+['imagefsinfo', '--output', 'json']:
            detail = {'operation': 'READ_IMAGEFS'}
        else:
            detail = diagnostic.operation(p, argv)  # Explicitly excludes imports and labels.
        p.require(not options.get('monitor'), 'readonly_monitor_not_allowed')
        for attempt in range(1, 4):
            LAST_READ.clear(); LAST_READ.update(detail, attempt=attempt)
            p.cli.LAST.clear()
            try:
                raw = original(argv, **options)
                LAST_READ.update(p.cli.LAST, status='PASS'); return raw
            except p.cli.PreloadFailure as exc:
                LAST_READ.update(p.cli.LAST, status='FAIL')
                if str(exc) not in ('command_failed_STATUS_ONLY', 'command_timeout_STATUS_ONLY') or attempt == 3:
                    raise
                emit(dict(readonly_check_retry=detail, attempt=attempt, maximum_attempts=3,
                          last_operation=dict(p.cli.LAST), import_retried=False, raw_error_printed=False))
                time.sleep(2)
    p.cli.command = read
    p.inventory.capture = lambda args: read(args, timeout=30, limit=8*1024**2)
    return original  # Mutation caller must use this ONCE, not the read-only wrapper.


def redis_archive(p, item):
    archive = OLD/'redis/store.oci.tar'
    p.io.safe_dir(archive.parent); before = p.io.private_meta(archive)
    fd = os.open(str(archive), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        sha, size = p.verify.hashed(f, item['archive_bytes'], emit, 'redis_archive')
    p.require(size == item['archive_bytes'] and sha == 'sha256:'+item['archive_sha256'], 'redis_archive_hash_changed')
    checked = p.verify.verify(archive, emit, item['manifest_digest'], item['config_digest'], item['layers'],
                              item['compressed_layer_bytes'], item['root_digest'])
    p.require(checked['uncompressed_layer_bytes'] == item['uncompressed_layer_bytes'], 'redis_expansion_changed')
    p.archive_reference(item, archive)
    p.require(p.io.private_meta(archive) == before, 'redis_archive_changed_during_verification')
    return archive, before


def recover(p, mutate):
    global IMPORT_ATTEMPTED
    pg, redis = p.ITEMS
    p.require(not os.path.lexists(str(STATE)), 'recovery_already_started_USE_STATUS')
    p.io.safe_dir(STATE.parent)
    phase('BIND_EXACT_FAILURE_AND_TRANSFER_RECEIPTS')
    hashes = bind_history(p)
    phase('VERIFY_COMPLETED_POSTGRES_WITHOUT_REIMPORT')
    accepted_pg = p.image_check(pg, pinned=True)
    p.require(not p.reference_present(redis), 'redis_reference_already_present_REVIEW_STATUS')
    phase('VERIFY_LOCAL_REDIS_ARCHIVE_AND_CAPACITY')
    archive, metadata = redis_archive(p, redis)
    mem = re.search(r'(?m)^MemAvailable:\s+(\d+)\s+kB$', Path('/proc/meminfo').read_text())
    p.require(mem is not None and int(mem.group(1))*1024 >= 4*p.io.GIB, 'host_memory_reserve')
    paths = p.cli.image_fs_paths(p.cli.cri('imagefsinfo', '--output', 'json'))
    budget = p.disk_budget(paths, [redis])
    p.io.check_space(STATE.parent, 4*1024**2); p.processes_clear()
    STATE.mkdir(mode=0o700)
    p.record(STATE/'STARTED.json', dict(common(), previous_receipts=hashes, postgres=accepted_pg,
                                      imagefs=budget, redis_archive=str(archive), redis_archive_sha256=redis['archive_sha256']))
    try:
        phase('IMPORT_REDIS_ONCE')
        p.disk_budget(paths, [redis]); p.processes_clear()
        p.require(p.io.private_meta(archive) == metadata, 'redis_archive_changed_before_import')
        bind_history(p)
        p.require(not p.reference_present(redis), 'redis_reference_appeared_before_import')
        p.record(STATE/'IMPORT-REQUESTED.json', dict(role='redis', image=redis['image'], root_digest=redis['root_digest']))
        IMPORT_ATTEMPTED = True
        p.cli.PHASE = 'ENRICHMENT_RECOVERY_IMPORT_REDIS'
        mutate(p.cli.import_args(archive), timeout=1200, limit=1024**2,
               monitor=lambda: p.disk_budget(paths, []))
        p.record(STATE/'IMPORT-ACK.json', {'client_exit_code': 0})
        phase('VERIFY_BOTH_IMAGES_CONTENT_UNPACK_CRI_PIN')
        accepted_redis = p.image_check(redis)
        if not accepted_redis['pinned']:
            p.record(STATE/'PIN-REQUESTED.json', {'image': redis['image']})
            mutate(p.io.CTR+['images', 'label', redis['image'], p.cli.PIN], limit=65536)
        for _ in range(15):
            accepted_redis = p.image_check(redis)
            if accepted_redis['pinned']: break
            time.sleep(1)
        p.require(accepted_redis['pinned'], 'redis_pin_unconfirmed_USE_STATUS')
        accepted_pg = p.image_check(pg, pinned=True)
        p.disk_budget(paths, []); bind_history(p)
        result = dict(common(), enrichment_store_preload_recovery='PASS',
                      images=[accepted_pg, accepted_redis], redis_import_acknowledged=True,
                      historical_read_failure_cause='NOT_RECONSTRUCTABLE_FROM_DISCARDED_STDERR',
                      next_gate='ISOLATED_FULL_APP_ENRICHMENT_FIXTURE')
        p.record(STATE/'RESULT.json', result); emit(result)
        emit(dict(report=str(STATE/'RESULT.json'), report_sha256=read_record(p, STATE/'RESULT.json')[1]))
        return 0
    except BaseException as exc:
        result = failure(p, exc)
        try: p.record(STATE/'FAILED.json', result)
        except Exception: result['failure_receipt_saved'] = False
        emit(result); return 1


def failure(p, exc):
    return dict(common(), enrichment_store_preload_recovery='UNCONFIRMED', phase=PHASE,
                error_type=type(exc).__name__, failed_check=p.cli.safe_detail(exc) if p else 'SAFE_DIAGNOSTIC_REQUIRED',
                last_read=dict(LAST_READ), last_operation=dict(p.cli.LAST) if p else {},
                redis_import_attempted=IMPORT_ATTEMPTED, raw_error_printed=False,
                instruction='USE_STATUS_NO_REPEAT_RECOVER_NO_DELETE_STATE')


def status(p):
    records = []
    if os.path.lexists(str(STATE)):
        p.io.safe_dir(STATE)
        for name in ('STARTED.json', 'IMPORT-REQUESTED.json', 'IMPORT-ACK.json', 'PIN-REQUESTED.json', 'RESULT.json', 'FAILED.json'):
            if os.path.lexists(str(STATE/name)):
                row, sha = read_record(p, STATE/name)
                records.append(dict(file=name, sha256=sha, fields={k: row.get(k) for k in (
                    'enrichment_store_preload_recovery', 'phase', 'failed_check', 'redis_import_acknowledged')}))
    images = []
    for item in p.ITEMS:
        try: images.append(p.image_check(item) if p.reference_present(item) else dict(role=item['role'], present=False))
        except Exception as exc: images.append(dict(role=item['role'], status='NOT_VERIFIED', failed_check=p.cli.safe_detail(exc)))
    emit(dict(common(), enrichment_recovery_status='READ_ONLY', records=records, images=images,
              files_modified=False, imports_attempted_by_status=0, image_labels_changed=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument('--recover-once', action='store_true'); g.add_argument('--status', action='store_true')
    args = parser.parse_args(argv); p = None
    try:
        diagnostic.require(sys.platform == 'linux' and platform.machine() in ('aarch64', 'arm64')
                           and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_arm64_datax01_required')
        os.umask(0o077)
        p = diagnostic.load(); mutate = install_read_checks(p)
        if args.status: return status(p)
        import fcntl
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        def interrupted(signum, frame): raise p.verify.CheckFailure('interrupted_USE_STATUS')
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGALRM): signal.signal(sig, interrupted)
        signal.alarm(1800); os.nice(10)
        # Reuse the old operation's lock so the old tool cannot race this recovery.
        p.io.safe_dir(p.LOCK.parent)
        fd = os.open(str(p.LOCK), os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as lock:
            s = os.fstat(lock.fileno())
            p.require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                      and stat.S_IMODE(s.st_mode) == 0o600, 'unsafe_existing_lock')
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return recover(p, mutate)
    except (Exception, KeyboardInterrupt) as exc:
        emit(failure(p, exc)); return 1
    finally:
        if sys.platform == 'linux': signal.alarm(0)


if __name__ == '__main__': sys.exit(main())
