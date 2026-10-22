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
GUARD_Z64 = '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'
OP = 'abf96dde50174b1eb7caf70a4e6711b1'
DIAGNOSTIC_SHA = '5198cea4e71a0d925e1d79a96d60eb44196e3785c3f298f575861a57cccbf411'
PRIOR = Path('/var/lib/portal-ppt-native-phase-b-resume-b29ffd423beb44deb947f7725d74a253')
PINS = {'FAIL.json': {'bytes': 6578, 'sha256': '0d0952b80678eb07c26a5e82c4bb5bba95458f400bef3f81a654b21b50e5380d'}, 'PHASE-B-REQUESTED.json': {'bytes': 89, 'sha256': '4ba0e4d946e960ed7569a93652aaac2256b33c5fd9df7c8a51368c199ec6ca7b'}, 'REPORT.json': {'bytes': 12001, 'sha256': 'ff707b34f008c642c44127b2b363241426649bb34b42a894d5d83a66b1307f82'}, 'STARTED.json': {'bytes': 5029, 'sha256': 'a94b79226bd03e38f31626091c06808dd51b14f0978f865c3304f113e94ea804'}, 'frontend.PHASE-B-PASS.json': {'bytes': 88, 'sha256': '96829d52f52aa1754924cb2ce1a9fb64236bc24e632343334911697ecdf31de3'}, 'frontend.PHASE-B-REQUESTED.json': {'bytes': 75, 'sha256': '18ce87540a0cad4fcde4e2fd2a37fedad96db0256a2b3eb4e38f33fb1ae7d3a4'}, 'gateway-forward.PHASE-B-REQUESTED.json': {'bytes': 82, 'sha256': '352946706f50f379fdd43c30b8e22dfd732f9bfbaf527fc87f76f216012af441'}}
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
