"""Recover the exact empty-map fixture panic; retain real GLM/Embedding gates."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import socket
import sys
import traceback

import recover_enrichment_gateway as previous
import diagnose_enrichment_startup as diagnostic
from fixture_config_fix import corrected_config
from gateway_enrichment_baseline import GatewayBaseline
from startup_safe_diagnostics import augment_runtime

DIAGNOSTIC_TOOL = Path('/var/lib/weknora-startup-diagnostic-tool-8HmOD37C/diagnose.py')
DIAGNOSTIC_TOOL_SHA = '10d1e2793e35a2da2956979fcd5d995eb35afc44d9da1f89c85933109a1f98b9'
DIAGNOSTIC_RESULT = Path('/var/lib/weknora-enrichment-startup-diagnostic-20261017-r1/RESULT.json')
DIAGNOSTIC_RESULT_SHA = '9646ad23aa79ae8f6305d7d30cc5a19dcad47041ec20b222e2ebe0d286826234'
FIXTURE = Path('/var/lib/weknora-enrichment-startup-fixture-20261017-r1')
FIXTURE_RESULT_SHA = '14c64299975083e2d2021a4502fd9a11400dd2059bd2ab1373a5c08b9e323efa'
STATE = Path('/var/lib/weknora-enrichment-config-recovery-20261017-r1')
NEW_STATE = Path('/var/lib/weknora-full-enrichment-20261017-r1-fix2')


def bind_previous(a):
    base = diagnostic.bind_previous(a)
    a.audit.read(DIAGNOSTIC_TOOL, DIAGNOSTIC_TOOL_SHA)
    r = json.loads(a.audit.read(DIAGNOSTIC_RESULT, DIAGNOSTIC_RESULT_SHA))
    f = json.loads(a.audit.read(FIXTURE/'RESULT.json', FIXTURE_RESULT_SHA))
    a.need(r.get('startup_diagnostic') == 'COMPLETE' and r.get('native_bootstrap') == 'FAILED_PANIC_CAPTURED' and
        r.get('native_bootstrap_exit_code') == 2 and r.get('provider_requests') == 0 and
        r.get('production_writes') == 0 and r.get('credentials_requested') is False and
        r.get('cleanup') == 'NAMESPACE_ABSENT_VERIFIED', 'diagnostic_receipt_not_expected')
    evidence = r.get('startup_evidence', {})
    a.need(evidence.get('classes', {}).get('nil_pointer') is True and
        evidence.get('classes', {}).get('config_loaded') is True and
        evidence.get('stack', [{}])[0] == dict(file='extract_entity.go', line=41,
            function='github.com/Tencent/WeKnora/internal/application/service/chat_pipeline.NewPluginExtractEntity'),
        'diagnostic_panic_not_exact_extract_manager')
    a.need(f.get('document_submission_attempted') is False and f.get('provider_requests') == 0 and
        f.get('production_baseline') == 'UNCHANGED' and f.get('cleanup') == 'NAMESPACE_ABSENT_VERIFIED' and
        f.get('failed_check') == 'isolated_bootstrap_exited_before_health', 'diagnostic_fixture_boundary_changed')
    for name in ('PROVIDER-REQUESTED.json', 'TEST-MODELS.json', 'UPLOAD.json', 'SEARCH.json'):
        a.need(not os.path.lexists(str(FIXTURE/name)), 'diagnostic_provider_boundary_requires_review')
    ack = json.loads(a.audit.read(FIXTURE/'NAMESPACE-ACK.json'))
    clean = json.loads(a.audit.read(FIXTURE/'CLEANUP-VERIFIED.json'))
    a.need(a.re.fullmatch('weknora-enrich-check-[a-f0-9]{16}', ack['name']) is not None and
        clean.get('namespace') == ack['name'] and clean.get('uid') == ack['uid'] and clean.get('absent') is True,
        'diagnostic_cleanup_binding')
    a.need(a.native.get('namespace', ack['name'], missing=True) is None, 'diagnostic_namespace_not_absent')
    return dict(base, diagnostic_result_sha256=DIAGNOSTIC_RESULT_SHA,
        diagnostic_fixture_result_sha256=FIXTURE_RESULT_SHA, diagnostic_namespace_absent=True,
        diagnostic_namespace_uid=ack['uid'], previous_provider_submissions='ZERO_VERIFIED_PRE_PROVIDER_BOUNDARY')


def install_fix(a):
    a.need(hashlib.sha256(a.RUNTIME_SOURCE.encode()).hexdigest() == diagnostic.RUNTIME_SHA, 'frozen_runtime_changed')
    original_config = a.clean_config
    before = original_config(); fixed = corrected_config(before)
    a.clean_config = lambda: corrected_config(original_config())
    a.RUNTIME_SOURCE = augment_runtime(a.RUNTIME_SOURCE)
    a.STATE = NEW_STATE
    a.gateway_baseline = GatewayBaseline(a)
    return dict(original_config_sha256=a.spec_hash(before), corrected_config_sha256=a.spec_hash(fixed),
        changed_paths=['extract.extract_graph.description', 'extract.extract_entity.description',
            'extract.fabri_text.with_tag', 'extract.fabri_text.with_no_tag', 'conversation.summary.max_input_chars'],
        candidate_image_changed=False, production_configuration_changed=False,
        real_glm_and_embedding_acceptance_preserved=True)


def save(a, name, value):
    a.audit.directory(STATE)
    fd = os.open(str(STATE/name), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write((json.dumps(value, sort_keys=True)+'\n').encode()); f.flush(); os.fsync(f.fileno())


def run(a):
    a.need(not os.path.lexists(str(STATE)) and not os.path.lexists(str(NEW_STATE)), 'previous_attempt_exists_USE_STATUS')
    binding = bind_previous(a)
    a.audit.directory(STATE.parent); STATE.mkdir(mode=0o700)
    save(a, 'STARTED.json', binding)
    report = dict(fixture_config_recovery='UNCONFIRMED', ready_for_rollout=False, production_writes=0,
        build_started=False, images_imported=False, model_changed=False, consumer_slots_changed=False,
        original_evidence_modified=False, automatic_resubmission=False, fresh_acceptance_state=str(NEW_STATE))
    try:
        fix = install_fix(a); save(a, 'FIX.json', fix)
        a.emit(dict(fix, fixture_config_recovery='START_FRESH_ISOLATED_REAL_ACCEPTANCE',
            provider_request_budget=a.REQUEST_BUDGET, production_writes=0))
        code = a.run()
        report['acceptance_exit_code'] = code
        receipt = json.loads(a.audit.read(NEW_STATE/'RESULT.json'))
        report.update(native_enrichment_acceptance=receipt.get('native_enrichment_acceptance'),
            cleanup=receipt.get('cleanup'), document_submission_attempted=receipt.get('document_submission_attempted'),
            failed_check=receipt.get('failed_check'), failed_stage=receipt.get('failed_stage'))
        a.need(bind_previous(a) == binding, 'original_evidence_changed')
        if code == 0:
            a.need(all(receipt.get(k) == 'PASS' for k in ('native_enrichment_acceptance', 'native_schema_migration',
                'real_asynq_glm_enrichment', 'real_embedding_1024d_index', 'vector_only_retrieval')) and
                receipt.get('document_submission_attempted') is True and
                receipt.get('cleanup') == 'NAMESPACE_ABSENT_VERIFIED' and receipt.get('production_baseline') == 'UNCHANGED',
                'full_native_acceptance_evidence_required')
            report['fixture_config_recovery'] = 'PASS'
    except BaseException as exc:
        report.update(error_type=type(exc).__name__, failed_check=str(exc) if isinstance(exc,a.native.AcceptanceFailure)
            else 'SAFE_DIAGNOSTIC_REQUIRED', raw_error_printed=False,
            code_frames=[dict(file=Path(x.filename).name, function=x.name, line=x.lineno)
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
    save(a, 'RESULT.json', report)
    a.emit(dict(report, report=str(STATE/'RESULT.json'),
        report_sha256=hashlib.sha256(a.audit.read(STATE/'RESULT.json')).hexdigest(),
        instruction='SEND_REPORT_USE_STATUS_NO_REPEAT_RECOVERY_OR_ACCEPTANCE'))
    return 0 if report['fixture_config_recovery'] == 'PASS' else 1


def status(a):
    a.STATE=NEW_STATE
    if STATE.exists():
        a.audit.directory(STATE)
        a.emit(dict(fixture_config_recovery_status='READ_ONLY', production_writes=0, provider_requests=0,
            records={n:json.loads(a.audit.read(STATE/n)) for n in ('STARTED.json','FIX.json','RESULT.json') if (STATE/n).exists()}))
    a.status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--accept-once', action='store_true'); group.add_argument('--status', action='store_true')
    group.add_argument('--cleanup-only', action='store_true'); args = parser.parse_args()
    previous.need(os.name == 'posix' and os.geteuid() == 0 and platform.machine() == 'aarch64' and
                  socket.gethostname().split('.')[0] == 'datax-01', 'root_arm64_datax01_required')
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0)); a=previous.load_original()
    def interrupt(signum,frame): raise a.native.AcceptanceFailure('interrupted_USE_STATUS')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupt)
    a.STATE=NEW_STATE
    if args.status:
        status(a); return 0
    if args.cleanup_only:
        ns=json.loads(a.audit.read(NEW_STATE/'STARTED.json'))['namespace']
        a.emit(dict(cleanup=a.cleanup(ns), production_writes=0, provider_requests=0)); return 0
    return run(a)


if __name__=='__main__': sys.exit(main())
