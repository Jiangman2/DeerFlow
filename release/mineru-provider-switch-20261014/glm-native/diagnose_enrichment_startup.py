"""One disposable, model-free native startup diagnostic after the pinned panic."""
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
from startup_safe_diagnostics import augment_runtime

OLD_STATE = Path('/var/lib/weknora-full-enrichment-20261017-r1-fix1')
OLD_RESULT_SHA = '9014b43f59ebb2a5ac1dec3ead0b6acea4d4efa5cb3b84d84d5182dcd91d472b'
RECOVERY_TOOL = Path('/var/lib/weknora-enrichment-gateway-tool-JoReaKKS/recover.py')
RECOVERY_TOOL_SHA = '6ccdca8ba2d21207c72be7bf934ad768ae605d8f618adc0c11cd15900be054fb'
STATE = Path('/var/lib/weknora-enrichment-startup-diagnostic-20261017-r1')
NEW_STATE = Path('/var/lib/weknora-enrichment-startup-fixture-20261017-r1')
RUNTIME_SHA = '840459bb2292c638239c19f18beb8018f0f910d9ffa949c2612b0b094b5f6f81'
STOP_HEALTHY = 'diagnostic_bootstrap_healthy_stop_before_models'


def bind_previous(a):
    a.audit.read(RECOVERY_TOOL, RECOVERY_TOOL_SHA)
    r = json.loads(a.audit.read(OLD_STATE/'RESULT.json', OLD_RESULT_SHA))
    a.need(r.get('failed_check') == 'isolated_bootstrap_exited_before_health' and
           r.get('failed_stage') == 'NATIVE_FRESH_SCHEMA_MIGRATION' and
           r.get('document_submission_attempted') is False and r.get('production_writes') == 0 and
           r.get('cleanup') == 'NAMESPACE_ABSENT_VERIFIED' and r.get('production_baseline') == 'UNCHANGED',
           'previous_failure_not_expected_pre_provider_panic')
    for name in ('PROVIDER-REQUESTED.json', 'TEST-MODELS.json', 'UPLOAD.json', 'SEARCH.json'):
        a.need(not os.path.lexists(str(OLD_STATE/name)), 'previous_provider_boundary_requires_review')
    diag = json.loads(a.audit.read(OLD_STATE/'FAILURE-DIAGNOSTICS.json'))
    stopped = diag.get('runtime', {}).get('STOPPED-bootstrap.json', {})
    a.need(stopped.get('exit_code') == 2 and stopped.get('classes', {}).get('panic') == 1 and
           stopped.get('logs_truncated') is False and diag.get('postgres_startup', {}).get('database_ready') is True,
           'previous_startup_diagnostic_changed')
    ack = json.loads(a.audit.read(OLD_STATE/'NAMESPACE-ACK.json'))
    clean = json.loads(a.audit.read(OLD_STATE/'CLEANUP-VERIFIED.json'))
    a.need(ack.get('name') == 'weknora-enrich-check-ba5d3a44a7984ea0' and
           ack.get('uid') == '29417cd4-36fa-42a7-980b-c7a677d4d7bf', 'previous_namespace_identity')
    a.need(clean.get('namespace') == ack['name'] and clean.get('uid') == ack['uid'] and clean.get('absent') is True,
           'previous_cleanup_binding')
    a.need(a.native.get('namespace', ack['name'], missing=True) is None, 'old_test_namespace_not_absent')
    return dict(previous_failure_sha256=OLD_RESULT_SHA, old_namespace=ack['name'], old_namespace_absent=True,
                previous_provider_submissions='ZERO_VERIFIED_PRE_PROVIDER_BOUNDARY', original_evidence_modified=False,
                previous_diagnostics_sha256=hashlib.sha256(a.audit.read(OLD_STATE/'FAILURE-DIAGNOSTICS.json')).hexdigest())


def install_diagnostic(a):
    a.need(hashlib.sha256(a.RUNTIME_SOURCE.encode()).hexdigest() == RUNTIME_SHA, 'frozen_runtime_hash_changed')
    a.RUNTIME_SOURCE = augment_runtime(a.RUNTIME_SOURCE)
    a.STATE = NEW_STATE
    # No full Gateway review is necessary because this diagnostic has no relay,
    # model configuration, upload or search path. Never claim it revalidates Gateway.
    a.gateway_baseline = lambda: dict(service_ip='127.0.0.1', pods=[], diagnostic_only=True)
    original_manifests, original_remote, original_schema, original_record = a.manifests, a.remote, a.schema_receipt, a.record
    def manifests(*args, **kwargs):
        policy, cm, sec, pod = original_manifests(*args, **kwargs)
        policy['spec']['egress'] = []
        pod['spec']['hostAliases'] = []
        # Kubernetes may omit an empty list in returned specs. Omit it ourselves.
        del pod['spec']['hostAliases']
        pod['spec']['activeDeadlineSeconds'] = 600
        return policy, cm, sec, pod
    def remote(ns, mode, *args, **kwargs):
        a.need(mode in ('status', 'bootstrap', 'health', 'stop-bootstrap', 'stop-all'),
               'diagnostic_forbids_app_model_upload_search')
        return original_remote(ns, mode, *args, **kwargs)
    def schema(ns):
        result = original_schema(ns)
        a.record('DIAGNOSTIC-SCHEMA.json', result)
        raise a.native.AcceptanceFailure(STOP_HEALTHY)
    def blocked(*args, **kwargs):
        raise a.native.AcceptanceFailure('diagnostic_forbids_provider_boundary')
    def project(value):
        return dict(value, native_enrichment_acceptance='NOT_TESTED',
            diagnostic_only=True, gateway_revalidated=False, provider_requests=0,
            model_credentials_scope='NOT_REQUESTED_STARTUP_ONLY',
            production_baseline_scope='WEKNORA_ONLY_NO_GATEWAY_OR_MODEL_REQUESTS')
    def record(name, value):
        if name == 'RESULT.json': value = project(value)
        return original_record(name, value)
    a.manifests, a.remote, a.schema_receipt, a.record = manifests, remote, schema, record
    a.grants = blocked
    a.getpass.getpass = blocked
    a.REQUEST_BUDGET = dict(documents=0, vector_searches=0, glm=0, embedding=0,
                           scope='STARTUP_ONLY_NO_PROVIDER_CREDENTIALS_OR_REQUESTS')
    original_emit, original_phase = a.emit, a.phase
    def emit(value):
        return original_emit(project(value) if 'native_enrichment_acceptance' in value else value)
    def phase(label):
        if label == 'FRESH_RESOURCE_AND_COMPLETED_GATEWAY_ROLLOUT_REVIEW':
            label = 'FRESH_RESOURCE_REVIEW_STARTUP_ONLY_NO_GATEWAY_REQUESTS'
        return original_phase(label)
    a.emit, a.phase = emit, phase
    # Empty egress is semantically equivalent to omission, like empty ingress.
    original_normalized = a.normalized
    def normalized(obj):
        value = original_normalized(obj)
        if value['kind'] == 'NetworkPolicy': value['spec'].setdefault('egress', [])
        return value
    a.normalized = normalized
    original_validate = a.validate
    def validate(actual, expected, uid=None):
        if actual.get('kind') == 'Pod':
            a.need(not actual.get('spec', {}).get('hostAliases'), 'diagnostic_injected_host_aliases')
        return original_validate(actual, expected, uid)
    a.validate = validate


def run(a):
    a.need(not os.path.lexists(str(STATE)) and not os.path.lexists(str(NEW_STATE)), 'diagnostic_exists_USE_STATUS')
    binding = bind_previous(a)
    a.audit.directory(STATE.parent); STATE.mkdir(mode=0o700)
    def save(name, value):
        fd = os.open(str(STATE/name), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:
            f.write((json.dumps(value, sort_keys=True)+'\n').encode()); f.flush(); os.fsync(f.fileno())
    save('STARTED.json', binding)
    result = dict(startup_diagnostic='INCOMPLETE', provider_requests=0, production_writes=0,
        credentials_requested=False, native_enrichment_acceptance='NOT_TESTED', ready_for_rollout=False,
        build_started=False, images_imported=False, original_evidence_modified=False, automatic_retry=False)
    try:
        install_diagnostic(a)
        a.emit(dict(binding, startup_diagnostic='START_ONE_ISOLATED_BOOTSTRAP', provider_requests=0,
            production_writes=0, network_policy='DENY_ALL_INGRESS_AND_EGRESS_ENFORCEMENT_NOT_INDEPENDENTLY_VERIFIED'))
        result['fixture_exit_code'] = a.run()
        receipt = json.loads(a.audit.read(NEW_STATE/'RESULT.json'))
        result['cleanup'] = receipt.get('cleanup')
        result['fixture_failed_check'] = receipt.get('failed_check')
        a.need(receipt.get('production_baseline') == 'UNCHANGED', 'production_baseline_recheck_required')
        a.need(receipt.get('document_submission_attempted') is False and
               receipt.get('cleanup') == 'NAMESPACE_ABSENT_VERIFIED', 'diagnostic_cleanup_or_submission_review')
        if (NEW_STATE/'FAILURE-DIAGNOSTICS.json').exists():
            diag = json.loads(a.audit.read(NEW_STATE/'FAILURE-DIAGNOSTICS.json'))
            stop = diag.get('runtime', {}).get('STOPPED-bootstrap.json', {})
            result['native_bootstrap_exit_code'] = stop.get('exit_code')
            result['startup_evidence'] = stop.get('startup')
            result['startup_classes'] = stop.get('classes')
            if stop.get('classes', {}).get('panic') and stop.get('startup', {}).get('stack') and not stop.get('logs_truncated'):
                result.update(startup_diagnostic='COMPLETE', native_bootstrap='FAILED_PANIC_CAPTURED')
        if receipt.get('failed_check') == STOP_HEALTHY:
            result.update(startup_diagnostic='COMPLETE', native_bootstrap='HEALTHY_NOT_FULL_ACCEPTANCE',
                schema=json.loads(a.audit.read(NEW_STATE/'DIAGNOSTIC-SCHEMA.json')))
        a.need(bind_previous(a) == binding, 'previous_evidence_changed')
    except BaseException as exc:
        result.update(startup_diagnostic='INCOMPLETE', error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, a.native.AcceptanceFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
            raw_error_printed=False, code_frames=[dict(file=Path(x.filename).name, function=x.name, line=x.lineno)
                for x in traceback.extract_tb(exc.__traceback__)[-6:]])
    save('RESULT.json', result)
    a.emit(dict(result, report=str(STATE/'RESULT.json'),
        report_sha256=hashlib.sha256(a.audit.read(STATE/'RESULT.json')).hexdigest(),
        instruction='SEND_SAFE_STACK_USE_STATUS_NO_REPEAT_DIAGNOSTIC_OR_ACCEPT'))
    return 0 if result['startup_diagnostic'] == 'COMPLETE' else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__); group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--diagnose-once', action='store_true'); group.add_argument('--status', action='store_true')
    group.add_argument('--cleanup-only', action='store_true'); args = parser.parse_args()
    previous.need(os.name == 'posix' and os.geteuid() == 0 and platform.machine() == 'aarch64' and
                  socket.gethostname().split('.')[0] == 'datax-01', 'root_arm64_datax01_required')
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); a = previous.load_original()
    def interrupt(signum, frame): raise a.native.AcceptanceFailure('interrupted_USE_STATUS')
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP): signal.signal(sig, interrupt)
    a.STATE = NEW_STATE
    if args.status:
        if STATE.exists():
            a.audit.directory(STATE)
            a.emit(dict(startup_diagnostic_status='READ_ONLY', production_writes=0, provider_requests=0,
                records={n: json.loads(a.audit.read(STATE/n)) for n in ('STARTED.json', 'RESULT.json') if (STATE/n).exists()}))
        a.status(); return 0
    if args.cleanup_only:
        ns = json.loads(a.audit.read(NEW_STATE/'STARTED.json'))['namespace']
        a.emit(dict(cleanup=a.cleanup(ns), provider_requests=0, production_writes=0)); return 0
    return run(a)


if __name__ == '__main__': sys.exit(main())
