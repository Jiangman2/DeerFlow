"""Recover only the receipt-bound, pre-Pod NetworkPolicy empty-ingress failure.

Original script/evidence stay immutable. One new isolated native request maximum.
Linux Python3.7+, root datax-01. No build/import or production configuration writes.
"""
import argparse
import copy
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import stat
import sys
import traceback

OLD_TOOL = Path('/var/lib/weknora-glm-native-tool-SIU1w7Hx/accept.py')
OLD_TOOL_SHA = '2a28baa0c51cc92156b92fe0479c196a7351f5bc6277a3bd437b6c0b492555a1'
OLD_STATE = Path('/var/lib/weknora-glm-native-acceptance-20261017-r1')
OLD_RESULT_SHA = '4d99b991279aab409341cb530f6393dc7c05dd27ed20bae00e91aa4303f8815d'
OLD_NAMESPACE = 'weknora-glm-check-7c225c8c10594cbc'
STATE = Path('/var/lib/weknora-glm-native-admission-recovery-20261017-r1')
NEW_STATE = Path('/var/lib/weknora-glm-native-acceptance-20261017-r1-fix1')


class RecoveryFailure(RuntimeError):
    pass


def require(ok, check):
    if not ok: raise RecoveryFailure(check)


def load_original():
    for path in OLD_TOOL.parents:
        s = path.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_original_parent')
    fd = os.open(str(OLD_TOOL), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and 0 < s.st_size < 1048576, 'unsafe_original_tool')
        raw = f.read(1048576)
    require(hashlib.sha256(raw).hexdigest() == OLD_TOOL_SHA, 'original_tool_changed')
    spec = importlib.util.spec_from_loader('native_admission_original', loader=None)
    a = importlib.util.module_from_spec(spec); a.__file__ = str(OLD_TOOL)
    exec(compile(raw, str(OLD_TOOL), 'exec'), a.__dict__)
    return a


def policy_equivalent(expected, actual, compare_metadata):
    """Only missing spec.ingress == [] is normalized, not selectors/rules/ports."""
    if not isinstance(actual, dict): return False
    if actual.get('apiVersion') != expected['apiVersion'] or actual.get('kind') != 'NetworkPolicy': return False
    if not compare_metadata(expected['metadata'], actual.get('metadata')): return False
    before, after = expected.get('spec'), actual.get('spec')
    if not isinstance(before, dict) or not isinstance(after, dict): return False
    if before.get('policyTypes') != ['Ingress', 'Egress'] or before.get('ingress') != []: return False
    after = copy.deepcopy(after)
    if 'ingress' not in after: after['ingress'] = []
    # Unlike recursive subset, exact spec equality also rejects narrowed selectors,
    # added ports/rules, missing egress and other admission mutations.
    return before == after


def install_comparison(a):
    original = a.subset
    def compare(expected, actual):
        if isinstance(expected, dict) and expected.get('kind') == 'NetworkPolicy':
            return policy_equivalent(expected, actual, original)
        if isinstance(expected, dict) and expected.get('kind') == 'ConfigMap':
            return (original(expected, actual) and actual.get('data') == expected.get('data')
                    and actual.get('binaryData') == expected.get('binaryData'))
        return original(expected, actual)
    a.subset = compare
    return compare


def failure_binding(q):
    q.directory(OLD_STATE)
    raw = q.read(OLD_STATE/'RESULT.json')
    require(hashlib.sha256(raw).hexdigest() == OLD_RESULT_SHA, 'previous_failure_receipt_changed')
    r = json.loads(raw)
    require(r.get('native_glm_acceptance') == 'UNCONFIRMED'
            and r.get('failed_stage') == 'CREATE_ISOLATED_FIXTURE_NO_PRODUCTION_STORES'
            and r.get('failed_check') == 'admission_changed_isolation_or_probe'
            and r.get('provider_submission_attempted') is False
            and r.get('namespace') == OLD_NAMESPACE and r.get('production_writes') == 0,
            'previous_failure_not_known_pre_pod_boundary')
    expected = {'STARTED.json', 'POD-EXPECTED.json', 'NAMESPACE-REQUESTED.json', 'NAMESPACE-ACK.json', 'RESULT.json'}
    names = {p.name for p in OLD_STATE.iterdir()}
    require(names == expected, 'previous_resource_or_send_boundary_requires_review')
    # Freeze every predecessor record, not only RESULT, across the recovery.
    hashes = {n: hashlib.sha256(q.read(OLD_STATE/n)).hexdigest() for n in sorted(names)}
    start = q.document(OLD_STATE/'STARTED.json'); ack = q.document(OLD_STATE/'NAMESPACE-ACK.json')
    require(start.get('namespace') == ack.get('name') == OLD_NAMESPACE
            and isinstance(ack.get('uid'), str) and len(ack['uid']) <= 64, 'previous_namespace_binding')
    return hashes, ack['uid']


def list_resources(a, kind):
    raw = a.call(['get', kind, '-n', OLD_NAMESPACE, '--show-managed-fields=false', '-o', 'json'])[1]
    value = json.loads(raw)
    require(isinstance(value.get('items'), list), 'unexpected_inventory')
    return value['items']


def empty_namespace(a, uid, allow_absent=False):
    ns = a.get('namespace', OLD_NAMESPACE, missing=True)
    if ns is None:
        require(allow_absent, 'previous_namespace_absent_requires_review')
        return False
    require(ns['metadata']['uid'] == uid and not ns['metadata'].get('deletionTimestamp'), 'previous_namespace_changed')
    require(ns['metadata'].get('labels', {}).get('app') == 'weknora-native-qualification', 'previous_namespace_label_changed')
    for kind in ('pods', 'deployments', 'replicasets', 'statefulsets', 'daemonsets',
                 'jobs', 'cronjobs', 'services', 'networkpolicies', 'secrets', 'persistentvolumeclaims'):
        require(list_resources(a, kind) == [], 'unexpected_old_namespace_resources:'+kind)
    cms = list_resources(a, 'configmaps')
    require(all(x['metadata']['name'] == 'kube-root-ca.crt' for x in cms), 'unexpected_old_configmap')
    return True


def expected_policy(a, p, q):
    pod = q.document(OLD_STATE/'POD-EXPECTED.json')
    require(pod['metadata']['namespace'] == OLD_NAMESPACE and pod['metadata']['name'] == 'native-glm'
            and pod['spec']['containers'][0]['image'] == p.IMAGE, 'previous_expected_pod_changed')
    aliases = pod['spec']['hostAliases']
    require(isinstance(aliases, list) and 0 < len(aliases) <= 8, 'previous_aliases_shape')
    ips = []
    for row in aliases:
        require(set(row) == {'ip', 'hostnames'} and row['hostnames'] == [a.HOST], 'previous_alias_hostname')
        ip = ipaddress.ip_address(row['ip'])
        require(ip.version == 4 and not (ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified), 'previous_alias_address')
        ips.append(str(ip))
    return a.manifests(OLD_NAMESPACE, p.IMAGE, b'', {}, ips, [])[0]


def diagnose(a, p, q):
    hashes, uid = failure_binding(q)
    empty_namespace(a, uid)
    expected = expected_policy(a, p, q)
    actual = json.loads(a.call(['create', '--dry-run=server', '-f', '-', '-o', 'json'], json.dumps(expected).encode())[1])
    spec = actual.get('spec', {})
    equivalent = policy_equivalent(expected, actual, a.subset)
    diagnostic = {'resource': 'NetworkPolicy', 'operation': 'SERVER_DRY_RUN_ONLY',
        'expected_empty_ingress': True, 'returned_ingress_present': 'ingress' in spec,
        'exact_policy_after_empty_ingress_normalization': equivalent,
        'spec_field_names': sorted(spec) if isinstance(spec, dict) else [],
        'old_provider_submissions': 'ZERO_VERIFIED_PRE_POD_BOUNDARY',
        'production_writes': 0, 'provider_requests': 0}
    a.emit({'native_admission_diagnostic': diagnostic})
    require(equivalent and 'ingress' not in spec, 'not_the_known_empty_ingress_omission_STOP')
    require(failure_binding(q)[0] == hashes, 'previous_evidence_changed_during_review')
    return hashes, uid, diagnostic


def recover(a, p, q):
    require(not os.path.lexists(str(STATE)) and not os.path.lexists(str(NEW_STATE)), 'recovery_exists_USE_STATUS')
    q.directory(STATE.parent)
    # No retry/cleanup/new state until predecessor and the precise API behavior
    # are reconciled. A different failure is not authorized by this recovery.
    hashes, uid, diagnostic = diagnose(a, p, q)
    STATE.mkdir(mode=0o700)
    q.record(STATE/'STARTED.json', {'previous_file_hashes': hashes, 'previous_namespace_uid': uid,
        'diagnostic': diagnostic, 'fresh_state': str(NEW_STATE), 'automatic_retry': False})
    summary = {'native_admission_recovery': 'UNCONFIRMED', 'original_evidence_modified': False,
        'production_writes': 0, 'images_imported': False, 'build_started': False,
        'maximum_new_submissions': 1, 'automatic_retry': False, 'ready_for_rollout': False,
        'new_acceptance_state': str(NEW_STATE), 'old_empty_namespace_cleanup': 'NOT_REQUESTED'}
    try:
        install_comparison(a)
        a.STATE = NEW_STATE
        a.emit({'native_admission_recovery': 'START_NEW_ISOLATED_ATTEMPT',
                'previous_submissions': 0, 'original_evidence_modified': False, 'maximum_submissions': 1})
        code = a.run(p, q)  # All baseline/image/CA/hidden-input/no-retry gates unchanged.
        summary['acceptance_exit_code'] = code
        require(failure_binding(q)[0] == hashes, 'original_evidence_changed')
        if code == 0:
            # Only the verified empty namespace from the failed pre-Pod attempt.
            if empty_namespace(a, uid, allow_absent=True):
                q.record(STATE/'OLD-CLEANUP-REQUESTED.json', {'namespace': OLD_NAMESPACE, 'uid': uid})
                options = {'apiVersion': 'v1', 'kind': 'DeleteOptions',
                    'preconditions': {'uid': uid}, 'propagationPolicy': 'Foreground'}
                a.call(['delete', '--raw', '/api/v1/namespaces/'+OLD_NAMESPACE, '-f', '-'], json.dumps(options).encode())
                summary['old_empty_namespace_cleanup'] = 'REQUESTED_WITH_UID_PRECONDITION'
            else: summary['old_empty_namespace_cleanup'] = 'ALREADY_ABSENT'
            summary['native_admission_recovery'] = 'PASS'
    except BaseException as exc:
        summary.update(error_type=type(exc).__name__, failed_check=safe_detail(exc), raw_error_printed=False,
            code_frames=[{'file': Path(x.filename).name, 'function': x.name, 'line': x.lineno}
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
    q.record(STATE/'RESULT.json', summary)
    a.emit(dict(summary, report=str(STATE/'RESULT.json'), report_sha256=q.digest(STATE/'RESULT.json'),
                instruction='USE_STATUS_NO_REPEAT_RECOVERY_OR_PROBE'))
    return 0 if summary['native_admission_recovery'] == 'PASS' else 1


def safe_detail(exc):
    value = str(exc)
    return value if isinstance(exc, RecoveryFailure) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value) else 'SAFE_DIAGNOSTIC_REQUIRED'


def status(a, q):
    row = {'native_admission_recovery_status': 'READ_ONLY', 'production_writes': 0,
           'provider_requests': 0, 'files_modified': False, 'automatic_retry': False}
    if STATE.exists():
        q.directory(STATE)
        row['records'] = {name: q.document(STATE/name) for name in ('STARTED.json', 'RESULT.json', 'OLD-CLEANUP-REQUESTED.json')
                          if (STATE/name).exists()}
    a.emit(row)
    install_comparison(a); a.STATE = NEW_STATE
    a.status(q)


def main():
    parser = argparse.ArgumentParser(); group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--recover-once', action='store_true'); group.add_argument('--status', action='store_true')
    group.add_argument('--diagnose', action='store_true')
    args = parser.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and platform.machine() == 'aarch64'
            and socket.gethostname().split('.')[0] == 'datax-01', 'root_arm64_datax01_required')
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    a = load_original(); p, q = a.initialize()
    for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT): signal.signal(sig, a.interrupt)
    if args.status: status(a, q); return 0
    if args.diagnose: diagnose(a, p, q); return 0
    return recover(a, p, q)


if __name__ == '__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(json.dumps({'native_admission_recovery': 'UNCONFIRMED', 'failed_check': safe_detail(exc),
            'error_type': type(exc).__name__, 'production_writes': 0, 'raw_error_printed': False,
            'instruction': 'USE_STATUS_NO_REPEAT_RECOVERY_OR_PROBE'}), flush=True)
        sys.exit(1)
