"""Resume the exact pre-fixture Gateway replica-bound failure; no production switch."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import socket
import stat
import sys
import traceback

from gateway_enrichment_baseline import GatewayBaseline

OLD_TOOL = Path('/var/lib/weknora-full-enrichment-tool-dunIcgBN/accept.py')
OLD_TOOL_SHA = 'a880111601819c65f813c806cbb28fb1adab42a931132b1e004a6886e85bc2c3'
OLD_STATE = Path('/var/lib/weknora-full-enrichment-20261017-r1')
OLD_RESULT_SHA = '907f15094fe916f139bafb8a51f62f4e19c2818a6c940ea8fa276653189262ac'
STATE = Path('/var/lib/weknora-enrichment-gateway-recovery-20261017-r1')
NEW_STATE = Path('/var/lib/weknora-full-enrichment-20261017-r1-fix1')


class RecoveryFailure(RuntimeError): pass


def need(ok, label):
    if not ok: raise RecoveryFailure(label)


def load_original():
    for path in OLD_TOOL.parents:
        s = path.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_original_parent')
    fd = os.open(str(OLD_TOOL), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and
             not s.st_mode & 0o022 and 0 < s.st_size < 1048576, 'unsafe_original_tool')
        raw = f.read(1048576)
    need(hashlib.sha256(raw).hexdigest() == OLD_TOOL_SHA, 'original_tool_hash_changed')
    scope = {'__name__': 'frozen_full_enrichment', '__file__': str(OLD_TOOL)}
    exec(compile(raw, str(OLD_TOOL), 'exec'), scope)
    return sys.modules['accept_enrichment']


def bind_previous(a):
    a.audit.directory(OLD_STATE)
    raw = a.audit.read(OLD_STATE/'RESULT.json', OLD_RESULT_SHA)
    r = json.loads(raw)
    need(r.get('native_enrichment_acceptance') == 'UNCONFIRMED' and
         r.get('failed_check') == 'gateway_rollout_not_stable' and
         r.get('failed_stage') == 'FRESH_RESOURCE_AND_COMPLETED_GATEWAY_ROLLOUT_REVIEW' and
         r.get('document_submission_attempted') is False and r.get('production_writes') == 0 and
         r.get('production_workloads_mutated') is False and r.get('cleanup') == 'NOT_REQUESTED',
         'previous_failure_not_exact_pre_fixture_boundary')
    need({p.name for p in OLD_STATE.iterdir()} == {'RESULT.json'},
         'previous_resource_or_provider_boundary_requires_review')
    return dict(previous_report_sha256=hashlib.sha256(raw).hexdigest(), previous_tool_sha256=OLD_TOOL_SHA,
                previous_provider_submissions='ZERO_VERIFIED_PRE_FIXTURE_BOUNDARY',
                fresh_acceptance_state=str(NEW_STATE), original_evidence_modified=False)


def save(a, name, value):
    a.audit.directory(STATE)
    fd = os.open(str(STATE/name), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write((json.dumps(value, sort_keys=True)+'\n').encode()); f.flush(); os.fsync(f.fileno())


def recover(a):
    need(not os.path.lexists(str(STATE)) and not os.path.lexists(str(NEW_STATE)), 'previous_attempt_exists_USE_STATUS')
    binding = bind_previous(a)
    a.audit.directory(STATE.parent); STATE.mkdir(mode=0o700)
    save(a, 'STARTED.json', binding)
    summary = dict(gateway_enrichment_recovery='UNCONFIRMED', production_writes=0, build_started=False,
        images_imported=False, original_evidence_modified=False, ready_for_rollout=False,
        automatic_resubmission=False, fresh_acceptance_state=str(NEW_STATE))
    try:
        a.STATE = NEW_STATE
        a.gateway_baseline = GatewayBaseline(a)
        a.emit(dict(binding, gateway_enrichment_recovery='START_NEW_ISOLATED_ATTEMPT',
                    production_writes=0, request_budget=a.REQUEST_BUDGET))
        code = a.run()  # Frozen full-App flow, payload, budgets and cleanup unchanged.
        summary['acceptance_exit_code'] = code
        need(bind_previous(a) == binding, 'original_evidence_changed')
        if code == 0: summary['gateway_enrichment_recovery'] = 'PASS'
    except BaseException as exc:
        summary.update(error_type=type(exc).__name__, failed_check=str(exc) if isinstance(exc, RecoveryFailure)
            else 'SAFE_DIAGNOSTIC_REQUIRED', raw_error_printed=False,
            code_frames=[dict(file=Path(x.filename).name, function=x.name, line=x.lineno)
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
    save(a, 'RESULT.json', summary)
    a.emit(dict(summary, report=str(STATE/'RESULT.json'),
                report_sha256=hashlib.sha256(a.audit.read(STATE/'RESULT.json')).hexdigest(),
                instruction='USE_STATUS_NO_REPEAT_RECOVERY_OR_OLD_ACCEPT'))
    return 0 if summary['gateway_enrichment_recovery'] == 'PASS' else 1


def status(a):
    row = dict(gateway_enrichment_recovery_status='READ_ONLY', production_writes=0, provider_requests=0,
               files_modified=False, pod_exec=False, state=str(STATE))
    if STATE.exists():
        a.audit.directory(STATE)
        row['records'] = {name: json.loads(a.audit.read(STATE/name)) for name in ('STARTED.json', 'RESULT.json')
                          if (STATE/name).exists()}
    a.emit(row); a.STATE = NEW_STATE; a.status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--recover-once', action='store_true')
    group.add_argument('--status', action='store_true')
    group.add_argument('--cleanup-only', action='store_true')
    args = parser.parse_args()
    need(os.name == 'posix' and os.geteuid() == 0 and platform.machine() == 'aarch64' and
         socket.gethostname().split('.')[0] == 'datax-01', 'root_arm64_datax01_required')
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    a = load_original()
    def interrupt(signum, frame): raise RecoveryFailure('interrupted_USE_STATUS')
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM): signal.signal(sig, interrupt)
    if args.status: status(a); return 0
    if args.cleanup_only:
        a.STATE = NEW_STATE
        ns = json.loads(a.audit.read(NEW_STATE/'STARTED.json'))['namespace']
        a.emit(dict(cleanup=a.cleanup(ns), production_writes=0, provider_requests=0, original_result_modified=False))
        return 0
    return recover(a)


if __name__ == '__main__': sys.exit(main())
