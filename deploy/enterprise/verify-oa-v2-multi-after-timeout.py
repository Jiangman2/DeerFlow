"""Read current rollout state; verify v2 only after all Gateway replicas are ready."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import sys
import types

COMMON = ''
HELPERS = ''
CONFIG_KEYS = []
PRIOR = {'path': '/var/lib/portal-oa-v2-multi-enable-20261023-r1/REPORT.json',
         'bytes': 8717, 'sha256': 'd656711cd54e0f4aaa1f31703d7b05fe63494246518ebf363e508479986e8270'}
PLANNED = {'generation': 386, 'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1',
           'spec_sha256': '890cc7b2aa7fc8bff6abaebfe99f6f7149ac17e17405bc7e056ecd24263516b4'}


class Refused(Exception):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def load_helpers():
    need(COMMON and HELPERS and CONFIG_KEYS, 'PACKAGE_INCOMPLETE')
    common = types.ModuleType('_read_oa_state')
    exec(compile(COMMON, '<oa-readonly-common>', 'exec'), common.__dict__)
    common.require = need
    helpers = types.ModuleType('_verify_oa_state')
    helpers.__dict__.update(need=need, BASELINE_CONFIG=dict.fromkeys(CONFIG_KEYS))
    exec(compile(HELPERS, '<oa-readonly-helpers>', 'exec'), helpers.__dict__)
    return common, helpers


def read_prior():
    path = Path(PRIOR['path'])
    need(path.resolve() == path, 'PRIOR_PATH_CHANGED')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
             and before.st_size == PRIOR['bytes'], 'PRIOR_METADATA_CHANGED')
        raw = stream.read(PRIOR['bytes'] + 1)
        after = os.fstat(stream.fileno())
    need((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
         (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'PRIOR_CHANGED_DURING_READ')
    need(len(raw) == PRIOR['bytes'] and hashlib.sha256(raw).hexdigest() == PRIOR['sha256'], 'PRIOR_DIGEST_CHANGED')
    return json.loads(raw)


def check_prior(value, common, helpers):
    need(value.get('schema') == 'portal-oa-v2-multi-enable.v1' and value.get('status') == 'INCOMPLETE'
         and value.get('error_code') == 'ROLLOUT_OBSERVATION_TIMEOUT'
         and value.get('patch_acknowledged') is True and value.get('patch_request_count') == 1
         and value.get('planned_gateway') == PLANNED, 'PRIOR_NOT_ACKNOWLEDGED_TIMEOUT')
    expected = {role: {k: item[k] for k in ('uid', 'generation', 'spec_sha256')}
                for role, item in common.EXPECTED.items()}
    need(value.get('before') == expected, 'PRIOR_BASELINE_CHANGED')
    samples = value.get('preflight', {}).get('samples', [])
    config = helpers.current_configuration(samples)
    need(config['multi_result_enabled'] is False, 'PRIOR_MULTI_STATE_CHANGED')
    helpers.BASELINE_CONFIG = config
    for sample in samples:
        helpers.validate_config(sample['observation'], False)
        helpers.validate_route(sample['observation'])


def describe(deployment):
    state = deployment.get('status', {})
    return {'generation': deployment['metadata']['generation'], 'paused': deployment['spec'].get('paused', False),
            'desired': deployment['spec'].get('replicas'),
            'counts': {key: state.get(key) for key in ('observedGeneration', 'replicas', 'updatedReplicas',
                'readyReplicas', 'availableReplicas', 'unavailableReplicas', 'terminatingReplicas')},
            'conditions': [{key: row.get(key) for key in ('type', 'status', 'reason', 'lastTransitionTime')}
                           for row in state.get('conditions', [])]}


def pod_status(common, deployment):
    replicasets = common.query(['get', 'replicasets', '-l', 'app=gateway', '-o', 'json'])['items']
    owners = {row['metadata']['uid'] for row in replicasets
              if any(ref.get('controller') is True and ref.get('kind') == 'Deployment'
                     and ref.get('uid') == deployment['metadata']['uid']
                     for ref in row['metadata'].get('ownerReferences', []))}
    items = common.query(['get', 'pods', '-l', 'app=gateway', '-o', 'json'])['items']
    rows = []
    for pod in items:
        if not any(ref.get('controller') is True and ref.get('kind') == 'ReplicaSet' and ref.get('uid') in owners
                   for ref in pod['metadata'].get('ownerReferences', [])):
            continue
        containers = pod.get('status', {}).get('containerStatuses', [])
        rows.append({'pod': pod['metadata']['name'], 'node': pod.get('spec', {}).get('nodeName'),
                     'phase': pod.get('status', {}).get('phase'),
                     'deleting_since': pod['metadata'].get('deletionTimestamp'),
                     'template_hash': pod['metadata'].get('labels', {}).get('pod-template-hash'),
                     'containers': [{'name': c['name'], 'ready': c.get('ready'), 'restarts': c.get('restartCount'),
                         'waiting_reason': c.get('state', {}).get('waiting', {}).get('reason'),
                         'terminated_reason': c.get('state', {}).get('terminated', {}).get('reason'),
                         'previous_termination_reason': c.get('lastState', {}).get('terminated', {}).get('reason')}
                         for c in containers]})
    return rows


def observe(common, helpers, prior, report, progress=lambda value: None):
    check_prior(prior, common, helpers)
    deployment = helpers.read_deployment(common, 'gateway')
    report['deployment'] = describe(deployment)
    report['gateway'] = common.binding(deployment)
    need(common.binding(deployment) == PLANNED, 'GATEWAY_CHANGED_SINCE_ACKNOWLEDGED_PATCH')
    report['pods'] = pod_status(common, deployment)
    if not common.ready(deployment):
        report.update(status='OA_V2_ROLLOUT_NOT_READY', exitcode=2, runtime_accepted=False)
        return
    need(not deployment['spec'].get('paused'), 'DEPLOYMENT_PAUSED')
    preserved = helpers.other_services(common)
    checked = helpers.verify_pods(common, deployment, progress)
    need(helpers.other_services(common) == preserved, 'OTHER_DEPLOYMENTS_CHANGED_DURING_CHECK')
    final = helpers.read_deployment(common, 'gateway')
    need(common.binding(final) == PLANNED and common.ready(final), 'GATEWAY_CHANGED_DURING_CHECK')
    report.update(status='OA_V2_MULTI_RUNTIME_ACCEPTED_CALLBACK_DISABLED', exitcode=0,
                  accepted_pods=len(checked), v2_enabled=True, callback_enabled=False,
                  other_deployments_preserved=True, runtime_accepted=True,
                  authenticated_loopback_checks=sum(row['local_route'].get('requests', 0) for row in checked),
                  gateway=common.binding(final), deployment=describe(final))
    report.pop('pods', None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-current-readonly', action='store_true', required=True)
    parser.parse_args()
    report = {'schema': 'portal-oa-v2-multi-after-timeout.v1', 'status': 'INCOMPLETE', 'exitcode': 1,
              'prior_receipt': PRIOR, 'workload_modified': False, 'files_modified': False,
              'patch_requests': 0, 'automatic_retry': False, 'schema_migration_requested': False,
              'provider_requests': 0, 'job_submissions': 0, 'callback_requests': 0,
              'maximum_loopback_get_requests': 2, 'credentials_exported': False,
              'oa_business_acceptance_pending': True}
    def progress(value):
        if value.get('checked', 0) % 10 == 0:
            print(json.dumps(value, sort_keys=True), flush=True)
    try:
        need(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname() == 'datax-01'
             and sys.flags.isolated and sys.flags.dont_write_bytecode, 'DATAX01_ISOLATED_ROOT_REQUIRED')
        common, helpers = load_helpers()
        observe(common, helpers, read_prior(), report, progress)
    except Exception as exc:
        report.update(error_class=type(exc).__name__,
                      error_code=str(exc) if isinstance(exc, Refused) else 'DETAILS_WITHHELD')
    print(json.dumps(report, sort_keys=True), flush=True)
    return report['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
