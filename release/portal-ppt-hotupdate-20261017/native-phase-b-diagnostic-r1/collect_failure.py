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
GUARD_Z64 = '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'
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
