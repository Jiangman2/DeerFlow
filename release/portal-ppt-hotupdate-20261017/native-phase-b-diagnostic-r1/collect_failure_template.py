#!/usr/bin/env python3
"""Read-only diagnostic for one fixed Gateway Phase B failure; never retries import."""
import argparse
import base64
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import types
import zlib

GUARD_SHA = '81c069d566023c68301498742906cfc5f78207e70a8e0d03ef0d0632ff7d2e2d'
GUARD_Z64 = '__GUARD__'
OP = 'abf96dde50174b1eb7caf70a4e6711b1'
EVIDENCE = Path('/var/lib/portal-ppt-native-phase-b-resume-b29ffd423beb44deb947f7725d74a253')
REPORT_SHA = 'ff707b34f008c642c44127b2b363241426649bb34b42a894d5d83a66b1307f82'
FAIL_SHA = '0d0952b80678eb07c26a5e82c4bb5bba95458f400bef3f81a654b21b50e5380d'
WIRE_SHA = 'b3f08cc0ca0f8cb75488ce37a26531071699f5eb5d9097687604851753d72aa9'
FILES = {'STARTED.json', 'PHASE-B-REQUESTED.json', 'frontend.PHASE-B-REQUESTED.json',
         'frontend.PHASE-B-PASS.json', 'gateway-forward.PHASE-B-REQUESTED.json', 'FAIL.json', 'REPORT.json'}
LIMIT = 2 * 1024 ** 2
ERRORS = {
    'NO_SPACE': r'no space left on device|\benospc\b',
    'QUOTA_EXCEEDED': r'disk quota exceeded|\bedquot\b',
    'IO_ERROR': r'input/output error|\beio\b',
    'OUT_OF_MEMORY': r'cannot allocate memory|out of memory|\benomem\b',
    'READ_ONLY_FILESYSTEM': r'read.only file system|\berofs\b',
    'PERMISSION_DENIED': r'permission denied|operation not permitted|\beacces\b|\beperm\b',
    'MISSING_OBJECT_OR_FILE': r'not found|no such file or directory|\benoent\b',
    'ALREADY_EXISTS': r'already exists|file exists|\beexist\b',
    'INVALID_ARGUMENT': r'invalid argument|\beinval\b',
    'UNSUPPORTED': r'not supported|unsupported|\benotsup\b',
    'DIGEST_OR_SIZE_MISMATCH': r'digest mismatch|unexpected digest|size mismatch|size validation',
    'UNPACK_OR_APPLY_FAILURE': r'failed to (?:unpack|extract|apply)',
    'SNAPSHOT_FAILURE': r'failed to (?:prepare|commit|mount|stat).*snapshot|snapshot.*(?:failed|error)',
    'CANCELLED_OR_TIMEOUT': r'context canceled|context cancelled|deadline exceeded|timed out'
}


def require(value, code):
    if not value: raise ValueError(code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def install_signals():
    def interrupted(signum, frame):
        raise ValueError('interrupted_no_retry')
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)


def load():
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(GUARD_Z64, validate=True), 65537)
    require(len(raw) <= 65536 and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail
            and sha(raw) == GUARD_SHA, 'guard_identity')
    guard = types.ModuleType('fixed_local_guard')
    exec(compile(raw, 'fixed_local_guard.py', 'exec'), guard.__dict__)
    host = guard.host_identity()
    module, profile = guard.load_target()
    module.B.DEADLINE = time.monotonic() + 90
    return guard, module, profile, host


def locked(module):
    path = Path('/var/lib/portal-ppt-native-preload-r1.lock')
    module.B.safe_dir(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        value = os.fstat(fd)
        require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1 and not value.st_mode & 0o022, 'common_lock_identity')
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd); raise


def checkpoint(module):
    module.B.safe_dir(EVIDENCE)
    require(set(os.listdir(EVIDENCE)) == FILES, 'failure_evidence_advanced_or_changed')
    values, observed = {}, {}
    for name in sorted(FILES):
        path = EVIDENCE / name
        before = path.lstat()
        raw = module.private_bytes(path, 65536)
        require(module.file_identity(before) == module.file_identity(path.lstat()), 'failure_receipt_changed')
        values[name] = module.B.parse(raw)
        observed[name] = dict(bytes=len(raw), sha256=sha(raw), mtime_ns=before.st_mtime_ns,
                              identity=module.file_identity(before))
    require((observed['REPORT.json']['bytes'], observed['REPORT.json']['sha256']) == (12001, REPORT_SHA)
            and (observed['FAIL.json']['bytes'], observed['FAIL.json']['sha256']) == (6578, FAIL_SHA), 'fixed_failure_changed')
    report, fail = values['REPORT.json'], values['FAIL.json']
    require(report['operation'] == OP and report['error_code'] == 'import_nonzero'
            and report['target_report'] == fail and fail['phase_a'] == 'PASS' and fail['phase_b'] == 'REQUESTED'
            and len(fail['image_rows']) == 1 and fail['image_rows'][0]['role'] == 'frontend', 'failure_state')
    require(values['gateway-forward.PHASE-B-REQUESTED.json'] == dict(role='gateway-forward', phase='PHASE-B', automatic_retry=False)
            and values['frontend.PHASE-B-PASS.json'].get('returncode') == 0, 'request_receipt_shape')
    return observed


def window(receipts):
    request = receipts['gateway-forward.PHASE-B-REQUESTED.json']['mtime_ns']
    failed = receipts['FAIL.json']['mtime_ns']
    require(0 < request <= failed, 'receipt_time_order')
    start = request // 1000000000 - 5
    end = (failed + 999999999) // 1000000000 + 5
    require(0 < end - start <= 600, 'receipt_time_window_exceeds_10_minutes')
    return start, end


def project_journal(raw, start, end, image, tag):
    require(len(raw) <= LIMIT, 'journal_output_limit')
    lines = raw.splitlines()
    require(len(lines) <= 1000, 'journal_entry_limit')
    known = set(image['outer_blobs']) | {layer['chain_id'] for layer in image['layers']}
    matches, malformed = [], 0
    for line in lines:
        if len(line) > 65536:
            malformed += 1; continue
        try:
            row = json.loads(line)
            require(isinstance(row, dict), 'journal_object_required')
            stamp = row.get('__REALTIME_TIMESTAMP')
            require(isinstance(stamp, str) and re.fullmatch('[0-9]{1,20}', stamp), 'journal_timestamp')
            usec = int(stamp)
            require(start * 1000000 <= usec <= (end + 1) * 1000000, 'journal_timestamp_range')
            require(row.get('_SYSTEMD_UNIT') == 'containerd.service' and isinstance(row.get('MESSAGE'), str), 'journal_shape')
            message = row['MESSAGE']
            digests = sorted({('sha256:' + item) for item in re.findall(r'(?<![a-fA-F0-9])(?:sha256:)?([a-f0-9]{64})(?![a-fA-F0-9])', message)} & known)
            tag_match = bool(re.search(r'(?<![A-Za-z0-9._:/@-])' + re.escape(tag) + r'(?![A-Za-z0-9._:/@-])', message))
            if not digests and not tag_match:
                continue
            low = message.lower()
            categories = sorted(name for name, pattern in ERRORS.items() if re.search(pattern, low))
            priority = row.get('PRIORITY')
            error_like = bool(categories or re.search(r'\b(?:error|failed|failure|fatal)\b', low)
                              or isinstance(priority, str) and priority in ('0', '1', '2', '3'))
            if not error_like:
                continue
            grpc = re.findall(r'\bcode\s*=\s*(Cancelled|Unknown|InvalidArgument|DeadlineExceeded|NotFound|AlreadyExists|PermissionDenied|ResourceExhausted|FailedPrecondition|Aborted|OutOfRange|Unimplemented|Internal|Unavailable|DataLoss|Unauthenticated)\b', message)
            errnos = sorted({int(item) for item in re.findall(r'\b[Ee]rrno\s*[:=]?\s*([0-9]{1,3})\b', message)} & {1, 2, 5, 12, 13, 17, 20, 22, 24, 28, 30, 31, 95, 122})
            matches.append(dict(timestamp_utc=datetime.datetime.fromtimestamp(usec / 1000000, datetime.timezone.utc).isoformat(),
                                categories=categories or ['UNCLASSIFIED_TARGET_ERROR'], gateway_digests=digests,
                                fixed_gateway_tag_matched=tag_match, grpc_codes=sorted(set(grpc)), errno_values=errnos))
        except (ValueError, TypeError, KeyError, OverflowError):
            malformed += 1
    return dict(status='COLLECTED', matched_error_projection=matches[:100], matched_projection_omitted=max(0, len(matches)-100),
                returned_rows=len(lines), malformed_or_oversized_rows_omitted=malformed,
                tail_may_be_truncated=len(lines) == 1000, original_cli_stderr_recovered=False,
                raw_log_text_exported=False, interpretation='CORRELATED_DAEMON_CATEGORIES_ONLY_NOT_PROOF_OF_CLI_CAUSE',
                matched_digest_may_be_shared_base_layer=True,
                no_matches_mean='UNDETERMINED_NOT_NO_ERROR')


def collect_journal(module, receipts, image, tag):
    start, end = window(receipts)
    rc, raw = module.B.run(['journalctl', '--unit=containerd.service', '--since=@' + str(start), '--until=@' + str(end),
                           '--no-pager', '--output=json', '--lines=1000', '--all'],
                          'FIXED_FAILURE_JOURNAL', timeout=20, limit=LIMIT, allow_failure=True)
    if rc:
        return dict(status='NOT_COLLECTED', error_code='journal_command_nonzero', returncode=rc, raw_log_text_exported=False)
    result = project_journal(raw, start, end, image, tag)
    result['window'] = dict(since_unix_seconds=start, until_unix_seconds=end, bounded_seconds=end-start,
                            basis='GATEWAY_REQUEST_MINUS_5_SECONDS_TO_FAIL_PLUS_5_SECONDS')
    return result


def collect_snapshots(module, image):
    chains = {layer['chain_id']: layer for layer in image['layers']}
    raw = module.B.capture(module.B.CTR + ['snapshots', '--snapshotter', 'overlayfs', 'ls'],
                           'FIXED_GATEWAY_SNAPSHOT_PROJECTION', timeout=20, limit=LIMIT)
    found = module.A.parse_snapshots(raw, chains)
    prefix = 0
    for layer in image['layers']:
        if found.get(layer['chain_id'], {}).get('kind') != 'Committed': break
        prefix += 1
    return dict(scope='FIXED_GATEWAY_CHAINS_ONLY_CURRENT_SNAPSHOT_NOT_FAILURE_TIME', layers=len(image['layers']),
                committed_prefix=prefix, committed_total=sum(row['kind'] == 'Committed' for row in found.values()),
                chains=[dict(index=index+1, chain_id=layer['chain_id'], kind=found.get(layer['chain_id'], {}).get('kind', 'ABSENT'))
                        for index, layer in enumerate(image['layers'])])


def safe_error(exc, module):
    code = exc.code if module and isinstance(exc, module.B.Failure) else str(exc) if isinstance(exc, ValueError) else 'DIAGNOSTIC_FAILED'
    return code if re.fullmatch('[A-Za-z0-9_]{1,120}', code) else 'DIAGNOSTIC_FAILED'


def run():
    module = lock = None
    result = dict(schema='portal-native-phase-b-failure-diagnostic.v1', operation=OP, status='INCOMPLETE',
                  prior_report_sha256=REPORT_SHA, original_cli_stderr_available=False,
                  image_import=False, image_delete=False, image_export=False, image_prune=False,
                  workload_mutation=False, database_access=False, model_access=False, ssh_scp=False,
                  ready_for_rollout=False, automatic_retry=False, raw_environment_exported=False)
    rc = 1
    try:
        install_signals()
        guard, module, profile, host = load(); result['host_identity'] = host
        lock = locked(module)
        before = checkpoint(module); result['fixed_receipts'] = before
        wire = module.private_bytes(module.state_paths(OP)['control'] / 'PLAN-WIRE.json', 122307)
        require(len(wire) == 122307 and sha(wire) == WIRE_SHA, 'wire_identity')
        plan = module.unwire_plan(module.B.parse(wire)); module.validate_plan(profile, plan)
        image = next(row for row in plan['images'] if row['role'] == 'gateway-forward')
        row = next(row for row in profile['images'] if row['role'] == 'gateway-forward')
        for key, action in (('journal', lambda: collect_journal(module, before, image, row['tag'])),
                            ('gateway_snapshots', lambda: collect_snapshots(module, image))):
            try: result[key] = action()
            except Exception as exc:
                if isinstance(exc, ValueError) and str(exc) == 'interrupted_no_retry': raise
                result[key] = dict(status='NOT_COLLECTED', error_code=safe_error(exc, module))
        fd = module.B.open_anchored(Path('/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs'))
        try:
            value, space = os.fstat(fd), os.fstatvfs(fd)
            result['image_filesystem_now'] = dict(device=value.st_dev, available_bytes=space.f_bavail*space.f_frsize,
                                                  available_inodes=space.f_favail, capacity_bytes=space.f_blocks*space.f_frsize)
        finally: os.close(fd)
        require(before == checkpoint(module), 'failure_receipts_changed_during_read')
        result.update(status='COMPLETE_WITH_DECLARED_GAPS', checkpoint_stable=True)
        rc = 0
    except (Exception, KeyboardInterrupt) as exc:
        result['error_code'] = safe_error(exc, module)
    finally:
        if lock is not None: os.close(lock)
    raw = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode()
    journal = result.get('journal', {})
    categories = sorted({category for row in journal.get('matched_error_projection', []) for category in row['categories']}) if journal.get('status') == 'COLLECTED' else None
    summary = dict(schema=result['schema'], status=result['status'], report_bytes=len(raw), report_sha256=sha(raw), exitcode=rc,
                   journal_status=journal.get('status'), matched_error_categories=categories,
                   gateway_committed_prefix=result.get('gateway_snapshots', {}).get('committed_prefix'),
                   gateway_layers=result.get('gateway_snapshots', {}).get('layers'),
                   image_available_bytes_now=result.get('image_filesystem_now', {}).get('available_bytes'),
                   ready_for_rollout=False, automatic_retry=False)
    try:
        require(module is not None, 'handoff_module_unavailable')
        summary['download_report'] = module.handoff_report(raw)
    except Exception:
        summary['download_report_status'] = 'UNAVAILABLE_SAFE_REPORT_PRINTED'
        sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
    if result.get('error_code'): summary['error_code'] = result['error_code']
    print(json.dumps(summary, sort_keys=True), flush=True)
    return rc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--collect', action='store_true', required=True); parser.parse_args()
    raise SystemExit(run())
