#!/usr/bin/env python3
"""Remove only the maintenance Pod from the accepted Skill update; Python 3.7+."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import time

NAMESPACE = 'deer-flow'
POD = 'fund-skill-maint-9a64554cc9bd'
UID = '414ee1eb-d995-48a2-a37a-409f16960703'
OPERATION = '9a64554cc9bd4b07bf398a4f68ce07d4'
TREE = 'b65fc087957f540c0ea7b04037c3f6955fbcdca18faf356d93aa996b0224b4b8'
PRIOR = Path('/var/lib/portal-research-fund-skill-update-20261104-r1/REPORT.json')
PRIOR_SHA = '6f8bb30e1c2c7af9ce21d65adc16b702abba666384dc07c3ee9c8b8d9f1ce910'
REPORT_ROOT = Path('/var/lib/portal-research-fund-skill-cleanup-20261104-r1')
BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', NAMESPACE]
GET_ARGS = ['get', 'pod', POD, '--ignore-not-found', '-o', 'json']
DELETE_ARGS = ['delete', '--raw=/api/v1/namespaces/' + NAMESPACE + '/pods/' + POD, '-f', '-']


class Refused(Exception):
    pass


def check(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def validate_prior(value):
    check(value.get('operation') == OPERATION and value.get('maintenance_pod') == POD
          and value.get('maintenance_pod_uid') == UID, 'PRIOR_IDENTITY_CHANGED')
    check(value.get('skill_updated') is True and value.get('runtime_node_views_accepted') is True
          and value.get('after_tree_sha256') == TREE, 'PRIOR_UPDATE_NOT_ACCEPTED')
    nodes = value.get('after_consumers', [])
    check(len(nodes) == 5 and {x.get('node') for x in nodes} ==
          {'ecs-zdz-pro-00' + str(n) for n in range(18, 23)}
          and all(x.get('status') == 'PASS' and x.get('tree_sha256') == TREE for x in nodes),
          'PRIOR_NODE_CHECKS_NOT_ACCEPTED')
    check(value.get('workload_patch_requests') == 0 and value.get('production_database_access') is False,
          'PRIOR_SCOPE_CHANGED')
    return value


def read_prior():
    check(all(not p.is_symlink() for p in (PRIOR,) + tuple(PRIOR.parents)), 'PRIOR_SYMLINK')
    fd = os.open(str(PRIOR), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        info = os.fstat(fd)
        check(stat.S_ISREG(info.st_mode) and info.st_size == 6718, 'PRIOR_SIZE_CHANGED')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(6719)
        check(len(raw) == 6718 and hashlib.sha256(raw).hexdigest() == PRIOR_SHA, 'PRIOR_HASH_CHANGED')
        return validate_prior(json.loads(raw))
    finally:
        os.close(fd)


def command(args, payload=None):
    check(args == GET_ARGS or args == DELETE_ARGS, 'UNEXPECTED_COMMAND')
    if args == DELETE_ARGS:
        check(payload == delete_options(), 'DELETE_PRECONDITION_CHANGED')
    proc = subprocess.run(BASE + args, input=payload, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, timeout=20)
    check(len(proc.stdout) <= 262144 and len(proc.stderr) <= 65536, 'COMMAND_OUTPUT_BOUND')
    check(proc.returncode == 0, 'CLEANUP_COMMAND_NOT_ACCEPTED')
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def delete_options():
    # UID is checked atomically by the API server, including between GET and DELETE.
    return canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': UID}})


def verify_pod(pod):
    meta = pod.get('metadata', {})
    check(meta.get('name') == POD and meta.get('namespace') == NAMESPACE and meta.get('uid') == UID,
          'MAINTENANCE_POD_IDENTITY_CHANGED')
    check(meta.get('labels', {}).get('deerflow-release') == OPERATION
          and meta.get('labels', {}).get('app') == 'research-fund-skill-maintenance'
          and not meta.get('ownerReferences'), 'MAINTENANCE_POD_OWNERSHIP_CHANGED')
    spec = pod.get('spec', {})
    containers = spec.get('containers', [])
    check(len(containers) == 1 and containers[0].get('name') == 'maintenance'
          and containers[0].get('command') == ['python3', '-I', '-B', '-c', 'import time; time.sleep(1800)']
          and spec.get('restartPolicy') == 'Never', 'MAINTENANCE_POD_SPEC_CHANGED')


def cleanup(report):
    report['last_step'] = 'read_fixed_maintenance_pod'
    pod = command(GET_ARGS)
    if pod is None:
        report.update(maintenance_pod_absent=True, status='MAINTENANCE_POD_ALREADY_ABSENT')
        return
    verify_pod(pod)
    if not pod['metadata'].get('deletionTimestamp'):
        report['last_step'] = 'delete_fixed_uid_only'
        report['delete_request_count'] = 1
        report['delete_may_have_applied'] = True
        persist(report)
        command(DELETE_ARGS, delete_options())
        report['delete_acknowledged'] = True
    else:
        report['already_terminating'] = True
    report['last_step'] = 'verify_pod_absent'
    deadline = time.monotonic() + 60
    while True:
        pod = command(GET_ARGS)
        if pod is None:
            report.update(maintenance_pod_absent=True, status='MAINTENANCE_POD_CLEANED')
            return
        verify_pod(pod)
        if time.monotonic() >= deadline:
            raise Refused('POD_DELETION_STILL_PENDING_NO_RETRY')
        time.sleep(3)


def persist(report):
    tmp = REPORT_ROOT / 'REPORT.next.json'
    with tmp.open('xb') as stream:
        stream.write(canonical(report) + b'\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(str(tmp), str(REPORT_ROOT / 'REPORT.json'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cleanup-fixed-maintenance-pod-once', action='store_true', required=True)
    parser.parse_args()
    start = time.monotonic()
    report = {'schema': 'portal-research-fund-maintenance-cleanup.v1', 'status': 'INCOMPLETE',
              'automatic_retry': False, 'delete_request_count': 0, 'delete_may_have_applied': False,
              'skill_files_modified': False, 'backup_modified': False, 'original_receipt_modified': False,
              'deployment_modified': False, 'database_access': False, 'provider_requests': 0,
              'maintenance_pod': POD, 'maintenance_pod_uid': UID, 'original_operation': OPERATION,
              'original_report_sha256': PRIOR_SHA, 'once_claimed': False,
              'business_acceptance_pending': True, 'raw_errors_exported': False}
    claimed = False
    try:
        check(socket.gethostname().split('.')[0] == 'datax-01', 'WRONG_HOST')
        check(os.geteuid() == 0, 'ROOT_REQUIRED')
        check(not REPORT_ROOT.exists() and not REPORT_ROOT.is_symlink(), 'CLEANUP_REPORT_ALREADY_PRESENT')
        report['last_step'] = 'verify_original_update_receipt'
        read_prior()
        REPORT_ROOT.mkdir(mode=0o700)
        claimed = True
        report['once_claimed'] = True
        persist(report)
        cleanup(report)
        report['exitcode'] = 0
    except Exception as exc:
        report['exitcode'] = 1
        report['error_class'] = type(exc).__name__
        report['error_code'] = str(exc) if isinstance(exc, Refused) else 'BOUNDED_CLEANUP_FAILED'
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    if claimed:
        persist(report)
        data = (REPORT_ROOT / 'REPORT.json').read_bytes()
        report.update(report_path=str(REPORT_ROOT/'REPORT.json'), report_bytes=len(data),
                      report_sha256=hashlib.sha256(data).hexdigest())
    print(json.dumps(report, sort_keys=True))
    return report['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
