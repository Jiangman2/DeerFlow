"""Expand the reviewed OA schema once through an existing Gateway process."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

COMMON = ''
EXPECTED = {}
CHILD = ''
RECEIPTS = {}
FRONT = {}
MIGRATION_SOURCE = ''

# The packager extracts only the reviewed read-only Kubernetes helpers. Their
# global lookups intentionally use the restricted command implementation below.
exec(COMMON, globals())

CONTROL = '/var/lib/portal-oa-schema-expansion-20261022-r1'
DATABASE_FINGERPRINT = 'b0fdaf30f3160cd52abf01520da71a20570c23795938bf73165153083fae9d91'
FEATURE_IMAGE = 'deerflow.local/deer-flow-gateway@sha256:0a47db87acd2b219ca7bd548b8635b6f4cabd85ce34161b1935817786811006c'
BASE_REVISION = '0020_ppt_confirmation'
TARGET_REVISION = '0021_oa_result_sets'
ACCEPTED = 'OA_SCHEMA_EXPANSION_ACCEPTED_FEATURE_NOT_DEPLOYED'


def command(args, value=None, raw=False, expansion=False):
    require(args and args[0] in ('get', 'exec') and value is None,
            'KUBERNETES_MUTATION_FORBIDDEN')
    require(not expansion or args[0] == 'exec', 'EXPANSION_TRANSPORT_SHAPE')
    # Exec is never retried, including an exec whose output could not be decoded.
    attempts = 3 if args[0] == 'get' else 1
    base = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf',
            '--request-timeout=' + ('180s' if expansion else '15s'), '-n', 'deer-flow']
    for attempt in range(attempts):
        try:
            p = subprocess.run(base + args, input=None, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=210 if expansion else 45)
            require(len(p.stdout) <= (65536 if args[0] == 'exec' else 16 * 1024**2),
                    'KUBERNETES_OUTPUT_LIMIT')
            if args[0] == 'exec' and p.returncode in (1, 124):
                result = json.loads(p.stdout)
                require(isinstance(result, dict) and result.get('status') == 'INCOMPLETE'
                        and result.get('raw_errors_exported') is False
                        and result.get('provider_requests') == 0
                        and (result.get('migration_outcome') in ('not_requested', 'uncertain')
                             or (result.get('migration_outcome') == 'committed'
                                 and result.get('schema_migrated') is True
                                 and result.get('revisions') == [TARGET_REVISION])),
                        'CHILD_FAILURE_RECEIPT_INVALID')
                return result
            require(p.returncode == 0, 'KUBERNETES_COMMAND_FAILED')
            return p.stdout if raw else json.loads(p.stdout)
        except (Refused, subprocess.TimeoutExpired):
            if attempt + 1 == attempts:
                raise
            time.sleep(2)


def receipt(pin):
    p = Path(pin['path'])
    require(os.path.realpath(str(p)) == str(p), 'RECEIPT_SYMLINK')
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and s.st_size == pin['bytes'] and 0 < s.st_size <= 65536,
                'RECEIPT_METADATA')
        raw = f.read(pin['bytes'] + 1)
    require(hashlib.sha256(raw).hexdigest() == pin['sha256'], 'RECEIPT_HASH')
    return json.loads(raw)


def evidence():
    require(set(RECEIPTS) == {'compatibility', 'feature_image'}, 'RECEIPT_PINS_MISSING')
    compatibility = receipt(RECEIPTS['compatibility'])
    require(compatibility.get('status') == 'COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED'
            and compatibility.get('schema_revision') == BASE_REVISION
            and compatibility.get('schema_migration_requested') is False
            and compatibility.get('frontend_preserved') is True
            and compatibility.get('configuration_metadata_preserved') is True,
            'COMPATIBILITY_RECEIPT_NOT_ACCEPTED')
    require(compatibility.get('after') == {name: {k: pin[k] for k in ('uid', 'generation', 'spec_sha256')}
                                         for name, pin in EXPECTED.items()},
            'COMPATIBILITY_RECEIPT_BASELINE_CHANGED')
    runtime = compatibility.get('runtime', {})
    require(set(runtime) == set(EXPECTED) and sum(len(rows) for rows in runtime.values()) == 71,
            'COMPATIBILITY_RECEIPT_POD_COUNT')
    for name, rows in runtime.items():
        require(len({r['uid'] for r in rows}) == len(rows)
                and all(r.get('observation', {}).get('status') == 'PASS' for r in rows),
                'COMPATIBILITY_RECEIPT_RUNTIME_FAILED')
    feature = receipt(RECEIPTS['feature_image'])
    require(feature.get('status') == 'OA_DATA_QA_IMAGE_ACCEPTED_NOT_DEPLOYED'
            and feature.get('production_database_access') is False
            and feature.get('production_workload_modified') is False
            and feature.get('schema_migrated') is False and feature.get('provider_requests') == 0,
            'FEATURE_IMAGE_RECEIPT_NOT_ACCEPTED')
    rows = feature.get('checks', [])
    require(len(rows) == 1 and rows[0].get('deployment') == 'gateway' and rows[0].get('status') == 'PASS'
            and rows[0].get('observation', {}).get('status') == 'INSTALLED_OA_DATA_QA_IMAGE_ACCEPTED'
            and len(rows[0].get('pods', [])) == 1 and len(rows[0]['pods'][0].get('containers', [])) == 1
            and rows[0]['pods'][0]['containers'][0].get('imageID') == FEATURE_IMAGE,
            'FEATURE_IMAGE_RECEIPT_IMAGE_CHANGED')
    return runtime


def snapshot():
    deployments = {name: command(['get', 'deployment', name, '-o', 'json']) for name in EXPECTED}
    pods = {}
    for name, d in deployments.items():
        pin = EXPECTED[name]
        require(binding(d) == {k: pin[k] for k in ('uid', 'generation', 'spec_sha256')} and ready(d),
                'DEPLOYMENT_BASELINE_CHANGED')
        containers = d['spec']['template']['spec']['containers']
        require(len(containers) == 1 and containers[0]['image'] == pin['candidate_image'],
                'LIVE_COMPATIBILITY_IMAGE_CHANGED')
        pods[name] = pods_for(d, pin, pin['candidate_image'])
        endpoints(name, pods[name])
    require(sum(len(v) for v in pods.values()) == 71, 'LIVE_POD_COUNT_CHANGED')
    front = command(['get', 'deployment', 'frontend', '-o', 'json'])
    require(binding(front) == FRONT and ready(front), 'FRONTEND_BASELINE_CHANGED')
    nodes = nodes_gate(deployments)
    services = {name: command(['get', 'service', name, '-o', 'json']) for name in EXPECTED}
    configurations = configuration_metadata(configuration_refs(dict(deployments, frontend=front)))
    observation = {'deployments': {n: binding(d) for n, d in deployments.items()},
                   'frontend': binding(front), 'node_coverage': nodes,
                   'pods': {n: identities(rows) for n, rows in pods.items()},
                   'service_bindings': {n: {'uid': d['metadata']['uid'], 'spec_sha256': digest(d['spec'])}
                                        for n, d in services.items()},
                   'configuration_metadata': configurations}
    return observation, pods


def samples(pods):
    # All 71 Pods are checked above; runtime/database checks cover every Gateway
    # node and the independently configured Runner role.
    result = []
    gateway_nodes = sorted({p['spec']['nodeName'] for p in pods['gateway']})
    require(set(gateway_nodes) == set(EXPECTED['gateway']['nodes']), 'GATEWAY_NODE_COVERAGE_CHANGED')
    for node in gateway_nodes:
        result.append(('gateway', next(p for p in pods['gateway'] if p['spec']['nodeName'] == node)))
    result.append(('ppt-master-runner', pods['ppt-master-runner'][0]))
    return result


def remote(pod, role, action, migration_tree=None):
    require(action in ('inspect', 'expand', 'verify'), 'CHILD_ACTION_FORBIDDEN')
    args = {'action': action, 'pin': EXPECTED[role],
            'expected_database_fingerprint': DATABASE_FINGERPRINT}
    if action == 'expand':
        require(role == 'gateway' and bool(MIGRATION_SOURCE), 'MIGRATION_SOURCE_MISSING')
        require(isinstance(migration_tree, str) and re.fullmatch(r'[0-9a-f]{64}', migration_tree),
                'MIGRATION_TREE_RECEIPT_MISSING')
        args['migration_source'] = MIGRATION_SOURCE
        args['expected_migration_tree_sha256'] = migration_tree
    result = command(['exec', pod['metadata']['name'], '-c', pod['spec']['containers'][0]['name'], '--',
                      '/app/backend/.venv/bin/python', '-I', '-B', '-c', CHILD, json.dumps(args)],
                     expansion=action == 'expand')
    require(isinstance(result, dict), 'CHILD_RESULT_SHAPE')
    return result


def accepted_result(value, role, action):
    code = value.get('error_code', value.get('check_code', 'CHILD_CHECK_FAILED'))
    code = code if isinstance(code, str) and re.fullmatch(r'[A-Z0-9_]{1,64}', code) else 'CHILD_CHECK_FAILED'
    require(value.get('status') == 'PASS' and value.get('action') == action, code)
    require(value.get('database_fingerprint') == DATABASE_FINGERPRINT, 'DATABASE_FINGERPRINT_CHANGED')
    require(value.get('source_hashes') == EXPECTED[role]['sources'], 'INSTALLED_SOURCES_CHANGED')
    require(value.get('migration_head') == BASE_REVISION, 'INSTALLED_HEAD_CHANGED')
    require(isinstance(value.get('migration_tree_sha256'), str)
            and re.fullmatch(r'[0-9a-f]{64}', value['migration_tree_sha256']), 'MIGRATION_TREE_RECEIPT_MISSING')
    require(value.get('provider_requests') == 0 and value.get('raw_errors_exported') is False,
            'CHILD_SAFETY_CONTRACT_CHANGED')
    require(value.get('revisions') == [BASE_REVISION if action == 'inspect' else TARGET_REVISION],
            'DATABASE_REVISION_CHANGED')
    if action == 'expand':
        require(value.get('database_readonly') is False and value.get('schema_migrated') is True
                and value.get('migration_outcome') == 'committed', 'EXPANSION_NOT_ACKNOWLEDGED')
    else:
        require(value.get('database_readonly') is True, 'READONLY_CHECK_CONTRACT_CHANGED')
    if action == 'verify':
        require(value.get('compatibility_verified') is True, 'OLD_CONSUMER_COMPATIBILITY_FAILED')


def safe_child_result(value):
    # Never export arbitrary child strings or SQL/provider/credential diagnostics.
    safe = {}
    for key in ('status', 'action', 'migration_head', 'migration_outcome'):
        v = value.get(key)
        if isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', v):
            safe[key] = v
    if isinstance(value.get('check_code'), str) and re.fullmatch(r'[A-Z0-9_]{1,64}', value['check_code']):
        safe['check_code'] = value['check_code']
    if isinstance(value.get('error_code'), str) and re.fullmatch(r'[A-Z0-9_]{1,64}', value['error_code']):
        safe['error_code'] = value['error_code']
    if value.get('error_category') in ('refused', 'lock_timeout', 'statement_timeout', 'database_error', 'timeout', 'runtime_error'):
        safe['error_category'] = value['error_category']
    if isinstance(value.get('sqlstate'), str) and re.fullmatch(r'[A-Z0-9]{5}', value['sqlstate']):
        safe['sqlstate'] = value['sqlstate']
    for key in ('database_fingerprint', 'migration_tree_sha256'):
        v = value.get(key)
        if isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v):
            safe[key] = v
    for key in ('database_readonly', 'schema_migrated', 'raw_errors_exported', 'compatibility_verified'):
        if type(value.get(key)) is bool:
            safe[key] = value[key]
    if type(value.get('provider_requests')) is int and 0 <= value['provider_requests'] <= 100:
        safe['provider_requests'] = value['provider_requests']
    revisions = value.get('revisions')
    if isinstance(revisions, list) and len(revisions) <= 2 and all(
            isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', v) for v in revisions):
        safe['revisions'] = revisions
    sources = value.get('source_hashes')
    if isinstance(sources, dict) and set(sources) == {'bootstrap', 'helper'} and all(
            isinstance(v, str) and re.fullmatch(r'[0-9a-f]{64}', v) for v in sources.values()):
        safe['source_hashes'] = sources
    return safe


def claim_control(report):
    parent = Path(CONTROL).parent
    require(os.path.realpath(str(parent)) == str(parent), 'CONTROL_PARENT_SYMLINK')
    s = os.lstat(str(parent))
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not stat.S_IMODE(s.st_mode) & 0o022,
            'CONTROL_PARENT_UNSAFE')
    os.mkdir(CONTROL, 0o700)
    report['once_claimed'] = True
    s = os.lstat(CONTROL)
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and stat.S_IMODE(s.st_mode) == 0o700,
            'CONTROL_DIRECTORY_UNSAFE')
    fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_report(report):
    directory = Path(CONTROL)
    tmp = directory / 'REPORT.tmp'
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f, sort_keys=True)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())
    os.replace(str(tmp), str(directory / 'REPORT.json'))
    fd = os.open(str(directory), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expand-oa-schema-once', action='store_true', required=True)
    parser.parse_args()
    report = {'schema': 'portal-oa-schema-expansion.v1', 'status': 'INCOMPLETE', 'once_claimed': False,
              'automatic_retry': False, 'automatic_rollback': False, 'feature_deployed': False,
              'production_workload_mutation': False, 'business_writes_requested': False,
              'provider_requests': 0, 'task_drain_required': False, 'active_tasks_allowed': True,
              'expand_requests': 0, 'schema_migration_requested': False, 'schema_migrated': False,
              'database_schema_mutation': False, 'ddl_may_have_executed': False,
              'migration_may_have_applied': False,
              'expansion_acknowledged': False, 'migration_outcome': 'not_requested',
              'receipts': RECEIPTS, 'inspect': [], 'verify': []}
    expansion_dispatched = False
    try:
        require(os.geteuid() == 0 and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
        require(set(EXPECTED) == {'gateway', 'ppt-master-runner'} and bool(CHILD)
                and bool(MIGRATION_SOURCE), 'PACKAGE_INCOMPLETE')
        require(not os.path.lexists(CONTROL), 'EXPANSION_ALREADY_CLAIMED')
        evidence()
        before, pods = snapshot()
        report['before'] = before
        selected = samples(pods)
        for role, pod in selected:
            result = remote(pod, role, 'inspect')
            if result.get('status') != 'PASS':
                report['last_child_failure'] = safe_child_result(result)
            accepted_result(result, role, 'inspect')
            report['inspect'].append({'role': role, 'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
                                      'observation': safe_child_result(result)})
        trees = {row['uid']: row['observation']['migration_tree_sha256'] for row in report['inspect']}
        last, unused = snapshot()
        require(last == before, 'LIVE_STATE_CHANGED_DURING_PREFLIGHT')
        # Nothing is written before all preflight checks succeed. mkdir is the
        # exclusive once claim; even an unknown/failed outcome retains it.
        claim_control(report)
        report.update(expand_requests=1, schema_migration_requested=True, schema_migrated=None,
                      database_schema_mutation=None, ddl_may_have_executed=True,
                      migration_may_have_applied=True, migration_outcome='uncertain',
                      expand_request_may_have_been_sent=True)
        save_report(report)
        role, pod = selected[0]
        expansion_dispatched = True
        result = remote(pod, role, 'expand', trees[pod['metadata']['uid']])
        report['expansion'] = safe_child_result(result)
        if result.get('status') != 'PASS':
            report['last_child_failure'] = safe_child_result(result)
        # An explicit pre-DDL refusal is distinguishable from an interrupted
        # migration. Any other failure remains uncertain, never retried here.
        if result.get('migration_outcome') == 'not_requested':
            report.update(schema_migrated=False, database_schema_mutation=False,
                          ddl_may_have_executed=False, migration_may_have_applied=False,
                          migration_outcome='not_requested')
        elif (result.get('migration_outcome') == 'committed' and result.get('schema_migrated') is True
              and result.get('database_fingerprint') == DATABASE_FINGERPRINT
              and result.get('revisions') == [TARGET_REVISION]):
            # A later child cleanup/check failure cannot erase its already
            # observed commit, but it also cannot satisfy final acceptance.
            report.update(schema_migrated=True, database_schema_mutation=True,
                          migration_outcome='committed', migration_commit_reported=True)
        accepted_result(result, role, 'expand')
        report.update(expansion_acknowledged=True, schema_migrated=True,
                      database_schema_mutation=True, migration_outcome='committed')
        save_report(report)
        for role, pod in selected:
            result = remote(pod, role, 'verify')
            if result.get('status') != 'PASS':
                report['last_child_failure'] = safe_child_result(result)
            accepted_result(result, role, 'verify')
            require(result['migration_tree_sha256'] == trees[pod['metadata']['uid']],
                    'INSTALLED_MIGRATION_TREE_CHANGED')
            report['verify'].append({'role': role, 'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
                                     'observation': safe_child_result(result)})
            save_report(report)
        after, unused = snapshot()
        require(after == before, 'LIVE_STATE_CHANGED_AFTER_EXPANSION')
        report.update(status=ACCEPTED, after=after, schema_revision=TARGET_REVISION,
                      old_consumer_compatibility_verified=True, frontend_preserved=True,
                      configuration_metadata_preserved=True)
    except Exception as exc:
        if not expansion_dispatched:
            report.update(expand_requests=0, schema_migration_requested=False, schema_migrated=False,
                          database_schema_mutation=False, ddl_may_have_executed=False,
                          migration_may_have_applied=False, migration_outcome='not_requested',
                          expand_request_may_have_been_sent=False)
        code = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        report['error_code'] = code if re.fullmatch(r'[A-Za-z0-9_]{1,96}', code) else 'UNCLASSIFIED_FAILURE'
    if report['once_claimed']:
        try:
            save_report(report)
        except Exception:
            # The previously fsynced uncertain/acknowledged journal is retained.
            report['status'] = 'INCOMPLETE'
            report['report_update_failed'] = True
    raw = None
    if report['once_claimed'] and (Path(CONTROL) / 'REPORT.json').is_file():
        raw = (Path(CONTROL) / 'REPORT.json').read_bytes()
    summary = {k: v for k, v in report.items() if k not in ('before', 'after', 'inspect', 'verify', 'expansion')}
    summary.update(inspected=len(report['inspect']), verified=len(report['verify']),
                   report_path=CONTROL + '/REPORT.json' if raw is not None else None,
                   report_bytes=len(raw) if raw is not None else None,
                   report_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,
                   preflight_retry_available=not report['once_claimed'], files_modified=report['once_claimed'])
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if report['status'] == ACCEPTED else 1


if __name__ == '__main__':
    raise SystemExit(main())
