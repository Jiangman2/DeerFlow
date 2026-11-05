"""Finish verification of Gateway 389, then roll only Frontend 230 to the sealed candidate."""
import argparse
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import threading
import time
import uuid

RELEASE_HELPERS = None
DIAGNOSTIC_HELPERS = None
CONTROL = '/var/lib/portal-combined-features-resume-20261029-r1'
DIAG_RECEIPT = {'path': '/var/lib/portal-combined-activation-diagnosis-20261029-r1/eed53dd84a60415faaacc521fe04da38.json',
                'bytes': 54567, 'sha256': '4301990df2a032d5f64fe92c1b5e4b585041023a5d5e26582c6b68b0d78148de'}


def previous_receipts():
    prior = read_receipt(PRIOR)
    require(prior.get('status') == 'INCOMPLETE' and prior.get('phase') == 'VERIFY_GATEWAY_RUNTIME'
            and len(prior.get('patches', [])) == 1 and prior['patches'][0].get('deployment') == 'gateway'
            and prior['patches'][0].get('planned') == PLANNED and prior['patches'][0].get('readback_accepted') is True
            and len(prior.get('runtime', {}).get('gateway', [])) == 17
            and not prior.get('runtime', {}).get('frontend'), 'PARTIAL_RECEIPT_CHANGED')
    diag = read_receipt(DIAG_RECEIPT)
    require(diag.get('status') == 'READONLY_PARTIAL_ROLLOUT_DIAGNOSIS_COLLECTED'
            and diag.get('prior_receipt') == PRIOR and diag.get('workload_modified') is False
            and diag.get('changed_deployment_bindings') == [] and diag.get('changed_service_bindings') == []
            and diag.get('changed_configuration_bindings') == []
            and len(diag.get('probes', [])) == 3 and all(p.get('status') == 'PASS' for p in diag['probes']),
            'DIAGNOSIS_RECEIPT_NOT_ACCEPTED')
    require({k: diag['deployments']['gateway'][k] for k in PLANNED} == PLANNED, 'DIAGNOSIS_GATEWAY_CHANGED')
    return prior


class ResumeReader(Reader):
    """Enforce the small remaining mutation scope independently of orchestration."""
    def __init__(self):
        super().__init__(); self.last_command = None; self.frontend_patch_requests = 0; self.job_names = set()

    def validate_command(self, args, data):
        require(args[:len(BASE)] == BASE and len(args) > len(BASE), 'KUBECTL_SCOPE')
        action = args[len(BASE)]
        require(action in ('get', 'exec', 'logs', 'create', 'patch'), 'COMMAND_SCOPE')
        if action == 'patch':
            require(args[len(BASE):] == ['patch', 'deployment', 'frontend', '--type=json', '--patch-file=/dev/stdin', '-o', 'name'], 'ONLY_FRONTEND_PATCH_ALLOWED')
            ops = json.loads(data)
            require(isinstance(ops, list) and len(ops) == 4
                    and [(o.get('op'), o.get('path')) for o in ops] == [('test', '/metadata/uid'),
                        ('test', '/metadata/resourceVersion'), ('test', '/spec'), ('replace', '/spec/template/spec/containers/0/image')]
                    and ops[0].get('value') == BASELINES['frontend']['uid']
                    and digest(ops[2].get('value')) == BASELINES['frontend']['spec_sha256']
                    and ops[3].get('value') == TARGETS['frontend']['image'], 'FRONTEND_PATCH_SCOPE')
            require(self.frontend_patch_requests == 0, 'SECOND_FRONTEND_PATCH_FORBIDDEN')
            self.frontend_patch_requests += 1
        if action == 'create':
            require(args[len(BASE):] == ['create', '-f', '-', '-o', 'json'], 'CREATE_SCOPE')
            job = json.loads(data); name = job.get('metadata', {}).get('name', '')
            node = job.get('spec', {}).get('template', {}).get('spec', {}).get('nodeName')
            require(name.startswith('portal-combined-front-') and node in TARGETS['frontend']['nodes'], 'JOB_SCOPE')
            helper = {'__name__': 'resume_frontend_helper'}; exec(FRONTEND_HELPER, helper)
            require(job == helper['job_spec'](name, node, TARGETS['frontend']['image'], FRONTEND_PINS), 'JOB_SPEC_CHANGED')
            require(name not in self.job_names and len(self.job_names) < 2, 'JOB_CREATE_LIMIT')
            self.job_names.add(name)
        if action == 'exec':
            require('--' in args, 'EXEC_SCOPE')
            suffix = args[args.index('--') + 1:]
            helper = {'__name__': 'resume_frontend_helper'}; exec(FRONTEND_HELPER, helper)
            require(suffix == ['cat', '/tmp/nginx.conf'] or suffix == ['node', '-e', helper['runtime_code'](), json.dumps(FRONTEND_PINS)], 'EXEC_SCOPE')

    def read(self, args, data=None, timeout=25, cap=8388608):
        self.validate_command(args, data)
        action = args[len(BASE)]
        self.last_command = dict(ordinal=self.commands + 1, action=action, resource=args[len(BASE)+1], raw_output_exported=False)
        return super().read(args, data, timeout, cap)


def runtime_check(reader, pod, data, report):
    """Keep the safe result even when kubectl reports nonzero; never retry exec."""
    before = identity(pod)
    report['current_sample'] = before; save(report)
    require(identity(reader.get('pod', pod['metadata']['name'])) == before, 'RUNTIME_SAMPLE_CHANGED')
    transport = DiagnosticReader(); transport.started = reader.started; transport.commands = reader.commands
    try:
        code, raw, command = transport.capture(BASE + ['exec', '-i', pod['metadata']['name'], '-c', 'gateway', '--',
            '/app/backend/.venv/bin/python', '-I', '-B', '-c', RUNTIME_CHILD], json.dumps(data).encode(), timeout=110, cap=65536)
    finally:
        reader.commands = transport.commands; reader.last_command = transport.last_command
    expected_status = 'COMBINED_EDGE_HTTP_ACCEPTED' if data.get('mode') == 'edge' else 'COMBINED_RUNTIME_ACCEPTED'
    summary = child_summary(raw)
    try: result = json.loads(raw)
    except (ValueError, UnicodeError): result = {}
    report['last_runtime_probe'] = dict(sample=before, command=command, child=summary); save(report)
    identity_ok = identity(reader.get('pod', pod['metadata']['name'])) == before
    if code != 0 or command['timeout'] or result.get('status') != expected_status or not identity_ok:
        report['failed_runtime_check'] = dict(sample=before, command=command, child=summary, identity_preserved=identity_ok)
        save(report)
        raise Refused('GATEWAY_RUNTIME_CHECK_NOT_ACCEPTED_SEE_DETAIL')
    report.pop('current_sample', None)
    report.pop('last_runtime_probe', None)
    return result


def current_set(reader, deps, role, candidate=True):
    require(stable(deps[role]), 'DEPLOYMENT_NOT_STABLE_' + role)
    pods = accepted_pods(reader, deps[role], role, candidate)
    require(pods is not None, 'POD_OR_ENDPOINT_SET_NOT_ACCEPTED_' + role)
    return pods


def verified_set_unchanged(reader, deps, role, report):
    pods = current_set(reader, deps, role)
    require([identity(p) for p in pods] == [r['sample'] for r in report['runtime'][role]], 'VERIFIED_RUNTIME_CHANGED_' + role)
    return pods


def execute(reader, report):
    report['phase'] = 'READ_PARTIAL_ROLLOUT_RECEIPTS'
    require(socket.gethostname().split('.')[0] == 'datax-01' and os.geteuid() == 0, 'WRONG_HOST_OR_UID')
    require(not os.path.lexists(CONTROL), 'ONCE_ALREADY_CLAIMED')
    parent = Path(CONTROL).parent
    require(str(parent.resolve()) == str(parent) and parent.stat().st_uid == 0 and not parent.stat().st_mode & 0o022, 'UNSAFE_CONTROL_PARENT')
    prior = previous_receipts()
    report['acceptance_receipts'] = dict(evidence(), partial=PRIOR, diagnosis=DIAG_RECEIPT)
    deps = {d['metadata']['name']: d for d in reader.get('deployment')['items']}
    report['before_deployments'] = prior['before_deployments']
    report['before_services'] = prior['before_services']
    report['before_configuration_metadata'] = prior['before_configuration_metadata']
    report['serving_nginx'] = nginx_gate(reader, deps)
    refs = configuration_refs(deps)
    plans = {'gateway': PLANNED}
    report['phase'] = 'VERIFY_CURRENT_STATE'
    deps = preservation(reader, report, plans, refs)
    gateways = current_set(reader, deps, 'gateway')
    current_set(reader, deps, 'frontend', candidate=False)
    for name in ('ppt-master-runner', 'nginx-security-heai-20261012-r1'):
        require(stable(deps[name]), 'PROTECTED_SERVICE_UNSTABLE')
    key_hash = secret_hash(reader)
    os.mkdir(CONTROL, 0o700); report['once_claimed'] = True; save(report)
    report['phase'] = 'VERIFY_ALL_CURRENT_GATEWAYS'
    report['runtime']['gateway'] = []
    for i, pod in enumerate(gateways):
        observation = runtime_check(reader, pod, dict(mode='runtime', expected=NEW_SOURCES,
            tavily_key_sha256=key_hash, database=i == 0, check_api=i == 0), report)
        report['runtime']['gateway'].append(dict(sample=identity(pod), observation=observation)); save(report)
        if i % 5 == 0 or i + 1 == len(gateways):
            print(json.dumps(dict(phase=report['phase'], checked=i+1, total=len(gateways))), flush=True)
    deps = preservation(reader, report, plans, refs)
    verified_set_unchanged(reader, deps, 'gateway', report)
    report['phase'] = 'VERIFY_FRONTEND_NODE_CACHES'; save(report)
    check_frontend_cache(reader, report)
    deps = preservation(reader, report, plans, refs)
    verified_set_unchanged(reader, deps, 'gateway', report)
    require(secret_hash(reader) == key_hash, 'TAVILY_SECRET_CHANGED')
    report['phase'] = 'PATCH_FRONTEND_ONLY'; save(report)
    operations, planned = plan(reader.get('deployment', 'frontend'), 'frontend')
    patch_once(reader, 'frontend', operations, planned, report)
    plans['frontend'] = planned
    report['phase'] = 'OBSERVE_FRONTEND'
    deployment, frontends = wait_rollout(reader, 'frontend', planned, report)
    report['phase'] = 'VERIFY_FRONTEND_RUNTIME'; report['runtime']['frontend'] = []
    helper = {'__name__': 'resume_frontend_helper'}; exec(FRONTEND_HELPER, helper)
    for pod in frontends:
        before = identity(pod); report['current_sample'] = before; save(report)
        outcome = json.loads(reader.read(BASE + ['exec', pod['metadata']['name'], '-c', 'frontend', '--', 'node', '-e',
            helper['runtime_code'](), json.dumps(FRONTEND_PINS)], timeout=45, cap=65536))
        require(outcome.get('status') == 'PASS' and outcome.get('sources_verified') is True
                and outcome.get('competition_http_status') == 200 and outcome.get('files_verified') == 13, 'FRONTEND_RUNTIME_NOT_ACCEPTED')
        require(identity(reader.get('pod', pod['metadata']['name'])) == before, 'FRONTEND_SAMPLE_CHANGED')
        report['runtime']['frontend'].append(dict(sample=before, observation=outcome)); report.pop('current_sample', None); save(report)
    deps = preservation(reader, report, plans, refs)
    gateways = verified_set_unchanged(reader, deps, 'gateway', report)
    verified_set_unchanged(reader, deps, 'frontend', report)
    report['phase'] = 'VERIFY_NGINX_ENTRY'
    report['edge_check'] = runtime_check(reader, gateways[0], dict(mode='edge'), report)
    report['phase'] = 'FINAL_IDENTITY_AND_PRESERVATION_CHECK'
    deps = preservation(reader, report, plans, refs)
    for role in ('gateway', 'frontend'): verified_set_unchanged(reader, deps, role, report)
    require(secret_hash(reader) == key_hash, 'FINAL_TAVILY_SECRET_CHANGED')
    require(len(report['patches']) == 1 and report['patches'][0]['deployment'] == 'frontend', 'FINAL_MUTATION_SCOPE_CHANGED')
    report.update(status=ACCEPTED, phase='COMPLETE', after=plans, runtime_accepted=True,
        gateway_restarted_by_tool=False, other_deployments_preserved=True, service_and_configuration_metadata_preserved=True,
        tavily_gateway_environment_accepted=True, default_model_preserved=True, candidate_images_deployed=True)


def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('--resume-combined-features-once', action='store_true', required=True); p.parse_args()
    os.umask(0o077)
    report = dict(schema='portal-combined-features-resume.v1', status='INCOMPLETE', operation=uuid.uuid4().hex,
        once_claimed=False, patches=[], temporary_jobs=[], runtime={}, last_rollout_counts={}, automatic_retry=False,
        automatic_rollback=False, provider_requests=0, credential_values_exported=False, production_workload_modified=False,
        production_workload_mutation_may_have_occurred=False, gateway_rollout_requested=False, runner_rollout_requested=False,
        nginx_rollout_requested=False, schema_migration_requested=False, v41_enabled=False, work_update_included=False,
        browser_acceptance_pending=True, business_acceptance_pending=True, original_reports_preserved=True)
    reader = ResumeReader()
    try: execute(reader, report)
    except BaseException as exc:
        report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'RESUME_INCOMPLETE',
                      last_command=reader.last_command, raw_errors_exported=False)
    report['elapsed_seconds'] = round(time.monotonic() - reader.started, 3)
    if report['once_claimed']:
        save(report); raw = (Path(CONTROL)/'REPORT.json').read_bytes()
        report.update(report_path=CONTROL+'/REPORT.json', report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    summary = {k: v for k, v in report.items() if k not in ('runtime', 'before_deployments', 'before_services', 'before_configuration_metadata')}
    summary['accepted_pods'] = {role: len(rows) for role, rows in report['runtime'].items()}
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if report['status'] == ACCEPTED else 1


if __name__ == '__main__': raise SystemExit(main())
