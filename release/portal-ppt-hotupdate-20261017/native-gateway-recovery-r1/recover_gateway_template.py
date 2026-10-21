#!/usr/bin/env python3
"""One reviewed Gateway unpack continuation with private bounded child output."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import types
import uuid
import zlib

GUARD_SHA = '6b20039722ebd7b17a03439deb717c8f587efc56667e72ba6eeb88816cb0513b'
GUARD_Z64 = '__GUARD__'
OP = 'abf96dde50174b1eb7caf70a4e6711b1'
DIAGNOSTIC_SHA = '5198cea4e71a0d925e1d79a96d60eb44196e3785c3f298f575861a57cccbf411'
PRIOR = Path('/var/lib/portal-ppt-native-phase-b-resume-b29ffd423beb44deb947f7725d74a253')
PINS = __PINS__
ONCE = Path('/var/lib/portal-ppt-gateway-recovery-r1-' + OP + '.STARTED.json')
ROOT = Path('/var/lib')
LIMIT = 2 * 1024 ** 2
ERROR_PHRASES = {
    'no space left on device': r'no space left on device|\benospc\b',
    'disk quota exceeded': r'disk quota exceeded|\bedquot\b',
    'input/output error': r'input/output error|\beio\b',
    'cannot allocate memory': r'cannot allocate memory|out of memory|\benomem\b',
    'permission denied': r'permission denied|operation not permitted|\beacces\b|\beperm\b',
    'read-only file system': r'read.only file system|\berofs\b',
    'not found or no such file': r'not found|no such file or directory|\benoent\b',
    'already exists': r'already exists|file exists|\beexist\b',
    'invalid argument': r'invalid argument|\beinval\b',
    'operation unsupported': r'unsupported|not supported|\benotsup\b',
    'invalid tar header': r'invalid tar header|invalid header|archive/tar',
    'unexpected end of file': r'unexpected eof|unexpected end of file',
    'digest or size mismatch': r'wrong diff id|digest mismatch|unexpected digest|size mismatch|size validation',
    'failed to extract layer': r'failed to extract layer',
    'failed to prepare extraction snapshot': r'failed to prepare extraction snapshot',
    'failed to commit snapshot': r'failed to commit snapshot',
    'context cancelled or deadline exceeded': r'context cancel(?:led|ed)|deadline exceeded|timed out'
}


def require(value, code):
    if not value: raise ValueError(code)


def sha(raw): return hashlib.sha256(raw).hexdigest()


def load():
    decoder = zlib.decompressobj()
    raw = decoder.decompress(base64.b64decode(GUARD_Z64, validate=True), 65537)
    require(len(raw) <= 65536 and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and sha(raw) == GUARD_SHA, 'guard_identity')
    guard = types.ModuleType('frozen_phase_b_guard')
    exec(compile(raw, 'frozen_phase_b_guard.py', 'exec'), guard.__dict__)
    host = guard.host_identity()
    module, profile = guard.load_target()
    return guard, module, profile, host


def old_boundary(module):
    module.B.safe_dir(PRIOR)
    require(len(PINS) == 7 and set(os.listdir(PRIOR)) == set(PINS), 'prior_phase_b_state_changed')
    values = {}
    for name, pin in sorted(PINS.items()):
        raw = module.private_bytes(PRIOR / name, 65536)
        require(len(raw) == pin['bytes'] and sha(raw) == pin['sha256'], 'prior_phase_b_receipt_changed')
        values[name] = module.B.parse(raw)
    report = values['REPORT.json']
    require(report['operation'] == OP and report['error_code'] == 'import_nonzero'
            and report['target_report'] == values['FAIL.json']
            and report['target_report']['phase_a'] == 'PASS' and report['target_report']['phase_b'] == 'REQUESTED', 'prior_failure_binding')
    require([row['role'] for row in report['target_report']['image_rows']] == ['frontend'], 'prior_completed_roles')
    return {name: pin for name, pin in sorted(PINS.items())}


def gateway_snapshot_gate(module, image):
    raw = module.B.capture(module.B.CTR + ['snapshots', '--snapshotter', 'overlayfs', 'ls'], 'GATEWAY_SNAPSHOT_GATE', timeout=30, limit=8*1024**2)
    chains = {layer['chain_id']: layer for layer in image['layers']}
    found = module.A.parse_snapshots(raw, chains)
    # Official extract keys contain a space before their target chain digest;
    # inspect full lines so an active extraction is not lost by whitespace splitting.
    for line in raw.decode('utf-8').splitlines()[1:]:
        tokens = line.split()
        if tokens and tokens[-1] in ('Active', 'View') and set(tokens) & set(chains):
            raise ValueError('gateway_active_or_view_snapshot_requires_review')
    prefix = 0
    for layer in image['layers']:
        if found.get(layer['chain_id'], {}).get('kind') != 'Committed': break
        prefix += 1
    require(all(item['kind'] == 'Committed' for item in found.values()) and prefix == len(found), 'gateway_snapshot_chain_not_closed')
    return dict(layers=len(image['layers']), committed_prefix=prefix, committed_total=len(found),
                active_or_view_conflict=False, already_committed=prefix == len(image['layers']))


def safe_error(exc, module):
    code = exc.code if module and isinstance(exc, module.B.Failure) else str(exc) if isinstance(exc, ValueError) else 'GATEWAY_RECOVERY_FAILED'
    return code if re.fullmatch('[A-Za-z0-9_]{1,120}', code) else 'GATEWAY_RECOVERY_FAILED'


def captured_import(module, row, fd, profile, plan, sealed_fd, result):
    require(fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY, 'import_fd_not_readonly')
    module.fd_regular(fd); os.lseek(fd, 0, os.SEEK_SET)
    argv = module.import_argv(row, fd, False)
    require(argv[-1] == '/proc/self/fd/' + str(fd), 'fixed_import_fd')
    logs, log_fds, counts, saved, workers = {}, [], [0, 0], [0, 0], []
    proc = None; failed = threading.Event(); exceeded = threading.Event()
    started = last = time.monotonic()
    def drain(stream, which):
        try:
            while True:
                block = os.read(stream.fileno(), 65536)
                if not block: break
                counts[which] += len(block)
                keep = block[:max(0, LIMIT-saved[which])]
                while keep:
                    count = os.write(log_fds[which], keep)
                    require(count > 0, 'capture_write_failed')
                    saved[which] += count; keep = keep[count:]
                if counts[which] > LIMIT: exceeded.set()
        except Exception: failed.set()
    error = None
    try:
        for name in ('stdout.private.log', 'stderr.private.log'):
            path = module.STATE / name
            log_fds.append(os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
            module.fd_regular(log_fds[-1], 0)
        module.write_receipt('GATEWAY-IMPORT-REQUESTED.json', dict(role=row['role'], phase='PHASE-B', automatic_retry=False))
        result['gateway_import_requested'] = True
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=module.B.SAFE_ENV, start_new_session=True, pass_fds=(fd,))
        result['import_process_started'] = True; result['image_import_may_be_partial'] = True
        for which, stream in enumerate((proc.stdout, proc.stderr)):
            worker = threading.Thread(target=drain, args=(stream, which), daemon=True); workers.append(worker); worker.start()
        module.B.progress('GATEWAY_SINGLE_IMPORT')
        while proc.poll() is None:
            module.B.deadline()
            require(time.monotonic()-started < 2400, 'import_timeout')
            require(not failed.is_set() and not exceeded.is_set(), 'import_output_limit_or_capture_failed')
            if time.monotonic()-last > 10:
                module.storage_check(profile, plan, 'MONITOR', sealed_fd)
                module.B.progress('GATEWAY_SINGLE_IMPORT', elapsed_seconds=int(time.monotonic()-started)); last = time.monotonic()
            time.sleep(.1)
        for worker in workers: worker.join(timeout=3)
        require(all(not worker.is_alive() for worker in workers) and not failed.is_set() and not exceeded.is_set(), 'import_pipe_failure')
        require(proc.returncode == 0, 'import_nonzero')
    except (Exception, KeyboardInterrupt) as exc:
        error = exc
    finally:
        module.B.stop(proc, True)
        result['import_returncode'] = proc.returncode if proc else None
        for worker in workers:
            if worker.ident is not None: worker.join(timeout=3)
        for file_fd in log_fds:
            os.fsync(file_fd); os.close(file_fd)
        if proc:
            for stream in (proc.stdout, proc.stderr):
                if stream: stream.close()
        for index, name in enumerate(('stdout.private.log', 'stderr.private.log')):
            if (module.STATE / name).exists():
                raw = module.private_bytes(module.STATE / name, LIMIT)
                logs[name] = dict(path=str(module.STATE / name), saved_bytes=len(raw), observed_bytes=counts[index],
                                  sha256=sha(raw), truncated=counts[index] > len(raw), root_private=True)
                if index == 1:
                    text = raw.decode('utf-8', 'replace').lower()
                    result['safe_error_phrases'] = sorted(phrase for phrase, pattern in ERROR_PHRASES.items() if re.search(pattern, text))
        receipt = dict(role=row['role'], returncode=proc.returncode if proc else None, process_started=proc is not None,
                       elapsed_seconds=round(time.monotonic()-started, 3), logs=logs,
                       capture_complete=len(workers) == 2 and all(worker.ident is not None and not worker.is_alive() for worker in workers) and not failed.is_set(),
                       output_limit_exceeded=exceeded.is_set(), automatic_retry=False)
        if error: receipt['error_code'] = safe_error(error, module)
        result['import_receipt'] = receipt
        result['import_returncode'] = receipt['returncode']
        module.write_receipt('GATEWAY-IMPORT-RESULT.json', receipt)
    if error: raise error


def run():
    module = lock = sealed_fd = metadata_fd = evidence = None
    result = dict(schema='portal-native-gateway-single-recovery.v1', operation=OP, status='INCOMPLETE',
                  diagnostic_sha256=DIAGNOSTIC_SHA, automatic_retry=False, ready_for_rollout=False,
                  native_runtime_passed=False, gateway_import_requested=None, import_process_started=None,
                  image_import_may_be_partial=None, import_returncode=None, safe_error_phrases=None,
                  frontend_reimported=False, other_four_roles_imported=False, phase_a_repeated=False,
                  old_evidence_modified=False, existing_images_deleted=False, workload_mutation=False,
                  database_access=False, model_access=False, ssh_scp=False,
                  raw_output_handoff=False, safe_error_projection='ALLOWLIST_PHRASES_ONLY_RAW_OUTPUT_RETAINED_ROOT_PRIVATE')
    rc = 1
    try:
        def interrupted(signum, frame): raise ValueError('interrupted_no_retry')
        for sig in (signal.SIGTERM, signal.SIGHUP): signal.signal(sig, interrupted)
        os.umask(0o077)
        guard, module, profile, host = load(); result['host_identity'] = host
        lock = module.common_lock()
        result['prior_receipts'] = old_boundary(module)
        plan, receipts = guard.inspect_checkpoint(module, profile)
        require(not os.path.lexists(ONCE), 'gateway_recovery_already_started')
        result.update(gateway_import_requested=False, import_process_started=False, image_import_may_be_partial=False)
        sealed_fd = guard.open_sealed(module)
        row = next(value for value in profile['images'] if value['role'] == 'gateway-forward')
        image = next(value for value in plan['images'] if value['role'] == 'gateway-forward')
        metadata_fd = guard.metadata_fd(module, sealed_fd, row, image, receipts[row['role'] + '.SEALED.json'], plan['allocation']['maximum_metadata_import_bytes'])
        evidence = module.B.fresh(ROOT / ('portal-ppt-gateway-single-recovery-' + uuid.uuid4().hex))
        result['evidence'] = str(evidence)
        module.B.write_new(ONCE, module.B.encoded(dict(operation=OP, evidence=str(evidence), automatic_retry=False)))
        module.STATE = evidence; module.REPORT = module.base_report(OP)
        module.write_receipt('STARTED.json', result)
        module.all_content_present(plan); module.verify_references(profile); module.B.collision_check(profile)
        frontend = next(value for value in profile['images'] if value['role'] == 'frontend')
        frontend_receipt, config = module.B.complete_row(frontend); del config
        result['frontend_read_only_verification'] = frontend_receipt
        result['gateway_before'] = gateway_snapshot_gate(module, image)
        module.storage_check(profile, plan, 'BEFORE_PHASE_B', sealed_fd)
        if result['gateway_before']['already_committed']:
            completion, config = module.B.complete_row(row); del config
            completion_status = 'GATEWAY_ALREADY_COMPLETE_REVIEW_REQUIRED'
            result['gateway_completion'] = completion
        else:
            captured_import(module, row, metadata_fd, profile, plan, sealed_fd, result)
            completion, config = module.B.complete_row(row); del config
            completion_status = 'GATEWAY_COMPLETE_REVIEW_REQUIRED'
            result['gateway_completion'] = completion
        result['gateway_after'] = gateway_snapshot_gate(module, image)
        result.update(status=completion_status, image_import_may_be_partial=False)
        rc = 0
    except (Exception, KeyboardInterrupt) as exc:
        result['error_code'] = safe_error(exc, module)
    finally:
        if module is not None and module.REPORT is not None:
            result['storage_checks'] = module.REPORT.get('storage_checks', [])
        raw = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode()
        if evidence is not None:
            try: module.B.write_new(evidence / 'REPORT.json', raw)
            except Exception: pass
        summary = dict(schema=result['schema'], status=result['status'], report_bytes=len(raw), report_sha256=sha(raw), exitcode=rc,
                       gateway_import_requested=result['gateway_import_requested'], import_returncode=result['import_returncode'],
                       safe_error_phrases=result['safe_error_phrases'], ready_for_rollout=False, automatic_retry=False)
        if result.get('error_code'): summary['error_code'] = result['error_code']
        try:
            require(module is not None, 'handoff_module_unavailable')
            summary['download_report'] = module.handoff_report(raw)
        except Exception:
            summary['download_report_status'] = 'UNAVAILABLE_SAFE_REPORT_PRINTED'
            sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
        print(json.dumps(summary, sort_keys=True), flush=True)
        for fd in (metadata_fd, sealed_fd, lock):
            if fd is not None: os.close(fd)
    return rc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--recover-gateway-once', action='store_true', required=True); parser.parse_args()
    raise SystemExit(run())
