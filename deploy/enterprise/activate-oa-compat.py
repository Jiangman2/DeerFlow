"""Resume proven no-op r2 with explicit task policy. Image-only rollout; no migration."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time

EXPECTED = {}
PRELOADS = []
CHILD = ''
NODE_GUARD = ''
ACCEPTANCE = {'path': '/var/lib/portal-oa-compat-image-check-20261022-r1/REPORT.json', 'bytes': 2578,
              'sha256': '4da64e59998e89d00c1f61c5783fcf9805d0b20ad365057400f9daa94792f98e'}
PRIOR_FAILURE = {'path': '/var/lib/portal-oa-compat-activation-20261022-r2/REPORT.json', 'bytes': 2671,
                 'sha256': '4842725be3362a76e7745b83450b1d80f44f8c67b9875299c4dd09e92dfce55b'}
REVIEWED_EXTRA_NODES = {
    'ecs-zdz-pro-0000': '42686547-6db2-41b0-be47-ac75c6c070ff',
    'ecs-zdz-pro-0005': 'e977432d-e039-4914-b0ab-0f9f871e36b5',
    'ecs-zdz-pro-0008': '5eb3e2cf-053f-4158-b118-c3c3dde06d8f',
}
CONTROL = '/var/lib/portal-oa-compat-activation-20261022-r4'
BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']
FRONT = {'uid': 'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 'generation': 225,
         'spec_sha256': '1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123'}


class Refused(Exception):
    def __init__(self, code, observation=None):
        super().__init__(code)
        self.observation = observation


def require(ok, code):
    if not ok:
        raise Refused(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def binding(d):
    return {'uid': d['metadata']['uid'], 'generation': d['metadata']['generation'], 'spec_sha256': digest(d['spec'])}


def ready(d):
    s = d.get('status', {})
    return (s.get('observedGeneration') == d['metadata']['generation']
            and all(s.get(k, 0) == d['spec']['replicas'] for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and not s.get('terminatingReplicas') and not s.get('unavailableReplicas'))


def command(args, value=None, raw=False):
    # Only reads are retried. Never repeat an uncertain PATCH.
    attempts = 3 if args[0] == 'get' else 1
    for attempt in range(attempts):
        try:
            p = subprocess.run(BASE + args, input=json.dumps(value).encode() if value is not None else None,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
            require(p.returncode == 0 and len(p.stdout) <= 16 * 1024**2, 'KUBERNETES_COMMAND_FAILED')
            return p.stdout if raw else json.loads(p.stdout)
        except (Refused, subprocess.TimeoutExpired):
            if attempt + 1 == attempts:
                raise
            time.sleep(2)


def evidence():
    p = Path(ACCEPTANCE['path'])
    require(os.path.realpath(str(p)) == str(p), 'ACCEPTANCE_SYMLINK')
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_size == ACCEPTANCE['bytes'], 'ACCEPTANCE_SIZE')
        raw = f.read(ACCEPTANCE['bytes'] + 1)
    require(hashlib.sha256(raw).hexdigest() == ACCEPTANCE['sha256'], 'ACCEPTANCE_HASH')
    value = json.loads(raw)
    require(value.get('status') == 'COMPAT_IMAGES_ACCEPTED_NOT_DEPLOYED'
            and value.get('production_database_access') is False and value.get('schema_migrated') is False,
            'ACCEPTANCE_NOT_PASSED')
    require(len(value['checks']) == 2 and {x['deployment'] for x in value['checks']} == set(EXPECTED), 'ACCEPTANCE_ROLES')
    for row in value['checks']:
        pin = EXPECTED[row['deployment']]
        require(row['status'] == 'PASS' and row['observation']['status'] == 'INSTALLED_COMPAT_IMAGE_ACCEPTED'
                and row['pods'][0]['containers'][0]['imageID'] == pin['candidate_image'], 'ACCEPTANCE_IMAGE_CHANGED')
    require(len(PRELOADS) == 15 and len({r['hostname'] for r in PRELOADS}) == 15, 'PRELOAD_COUNT')
    for pin in EXPECTED.values():
        for node, uid in pin['nodes'].items():
            row = next((r for r in PRELOADS if r['hostname'] == node), None)
            require(row is not None and row['node_preload_passed'] is True and row['old_references_preserved'] is True
                    and row['expected_node_uid'] == uid and row['image_import_may_be_partial'] is False, 'PRELOAD_NOT_ACCEPTED')
            role = row['roles'][0]
            require(role['manifest_digest'] == pin['candidate_image'].split('@')[1]
                    and role['config_digest'] == pin['config_digest']
                    and all(role[k] == 'PASS' for k in ('phase_a', 'phase_b', 'content_and_unpack', 'cri_visibility')), 'PRELOAD_IMAGE_CHANGED')


def prior_failure_evidence():
    p = Path(PRIOR_FAILURE['path'])
    require(os.path.realpath(str(p)) == str(p), 'PRIOR_REPORT_SYMLINK')
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and s.st_size == PRIOR_FAILURE['bytes'], 'PRIOR_REPORT_METADATA')
        raw = f.read(PRIOR_FAILURE['bytes'] + 1)
    require(hashlib.sha256(raw).hexdigest() == PRIOR_FAILURE['sha256'], 'PRIOR_REPORT_HASH')
    value = json.loads(raw)
    require(value.get('schema') == 'portal-oa-compat-activation.v2'
            and value.get('status') == 'INCOMPLETE' and value.get('error_code') == 'TASKS_STILL_ACTIVE'
            and value.get('patches') == [] and value.get('runtime') == {}
            and value.get('production_workload_mutation') is False
            and value.get('node_coverage', {}).get('status') == 'ROLE_NODE_COVERAGE_ACCEPTED'
            and value.get('schema_migration_requested') is False, 'PRIOR_REPORT_NOT_NOOP')
    return dict(PRIOR_FAILURE, error_code='TASKS_STILL_ACTIVE', production_workload_mutation=False)


def nodes_gate(deployments):
    nodes = command(['get', 'nodes', '-o', 'json'])['items']
    expected = {n: uid for p in EXPECTED.values() for n, uid in p['nodes'].items()}
    reviewed = dict(expected, **REVIEWED_EXTRA_NODES)
    names = {n['metadata']['name'] for n in nodes}
    observation = {'observed_node_count': len(nodes), 'reviewed_node_count': len(reviewed),
                   'missing_nodes': sorted(set(reviewed) - names), 'unexpected_nodes': sorted(names - set(reviewed))}
    if names != set(reviewed):
        raise Refused('REVIEWED_NODE_MATRIX_CHANGED', observation)
    for n in nodes:
        if n['metadata']['uid'] != reviewed[n['metadata']['name']]:
            raise Refused('REVIEWED_NODE_UID_CHANGED', {'node': n['metadata']['name']})
    guard = {'__name__': 'node_coverage_guard'}
    exec(NODE_GUARD, guard)
    try:
        return guard['assess_nodes'](nodes, deployments, EXPECTED)
    except guard['NodeGateRefused'] as exc:
        raise Refused(str(exc), exc.observation)


def configuration_refs(deployments):
    refs = set()
    for d in deployments.values():
        spec = d['spec']['template']['spec']
        for v in spec.get('volumes', []):
            if v.get('configMap'):
                refs.add(('configmap', v['configMap']['name']))
            if v.get('secret'):
                refs.add(('secret', v['secret']['secretName']))
            for s in v.get('projected', {}).get('sources', []):
                for key, kind in (('configMap', 'configmap'), ('secret', 'secret')):
                    if key in s:
                        refs.add((kind, s[key]['name']))
        for c in spec.get('containers', []) + spec.get('initContainers', []):
            for env in c.get('env', []):
                for key, kind in (('configMapKeyRef', 'configmap'), ('secretKeyRef', 'secret')):
                    if key in env.get('valueFrom', {}):
                        refs.add((kind, env['valueFrom'][key]['name']))
            for env in c.get('envFrom', []):
                for key, kind in (('configMapRef', 'configmap'), ('secretRef', 'secret')):
                    if key in env:
                        refs.add((kind, env[key]['name']))
    return sorted(refs)


def configuration_metadata(refs):
    # kubectl projects only UID/resourceVersion; credential values are not emitted.
    result = {}
    for kind, name in refs:
        raw = command(['get', kind, name, '-o', 'jsonpath={.metadata.uid}{"\\n"}{.metadata.resourceVersion}'], raw=True)
        parts = raw.decode('ascii').splitlines()
        require(len(parts) == 2 and all(parts), 'CONFIGURATION_METADATA_SHAPE')
        result[kind + '/' + name] = parts
    return result


def plan(d, pin):
    require(binding(d) == {k: pin[k] for k in ('uid', 'generation', 'spec_sha256')} and ready(d), 'DEPLOYMENT_BASELINE_CHANGED')
    containers = d['spec']['template']['spec']['containers']
    require(len(containers) == 1 and containers[0]['image'] == pin['old_image'], 'CONTAINER_SHAPE_CHANGED')
    expected = copy.deepcopy(d['spec'])
    expected['template']['spec']['containers'][0]['image'] = pin['candidate_image']
    patch = [{'op': 'test', 'path': '/metadata/uid', 'value': d['metadata']['uid']},
             {'op': 'test', 'path': '/metadata/resourceVersion', 'value': d['metadata']['resourceVersion']},
             {'op': 'test', 'path': '/spec', 'value': d['spec']},
             {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': pin['candidate_image']}]
    return patch, expected


def pods_for(d, pin, image):
    selector = d['spec']['selector']
    require(not selector.get('matchExpressions') and bool(selector.get('matchLabels')), 'SELECTOR_SHAPE')
    label = ','.join(k + '=' + v for k, v in sorted(selector['matchLabels'].items()))
    sets = command(['get', 'replicasets', '-l', label, '-o', 'json'])['items']
    owned = {r['metadata']['uid'] for r in sets if any(x.get('uid') == d['metadata']['uid'] and x.get('controller') for x in r['metadata'].get('ownerReferences', []))}
    pods = command(['get', 'pods', '-l', label, '-o', 'json'])['items']
    pods = [p for p in pods if any(x.get('uid') in owned and x.get('controller') for x in p['metadata'].get('ownerReferences', []))]
    require(len(pods) == d['spec']['replicas'], 'POD_COUNT_CHANGED')
    for p in pods:
        cs = p.get('status', {}).get('containerStatuses', [])
        require(not p['metadata'].get('deletionTimestamp') and p['spec']['nodeName'] in pin['nodes']
                and len(cs) == 1 and cs[0].get('ready') is True and p['spec']['containers'][0]['image'] == image, 'POD_NOT_READY')
        if image == pin['candidate_image']:
            require(cs[0]['imageID'].split('@')[-1] in (image.split('@')[-1], pin['config_digest']), 'POD_IMAGE_CHANGED')
    return sorted(pods, key=lambda p: p['metadata']['name'])


def identities(pods):
    return {p['metadata']['uid']: [(c.get('containerID'), c.get('restartCount'), c.get('imageID')) for c in p['status']['containerStatuses']] for p in pods}


def remote(pod, args):
    result = command(['exec', pod['metadata']['name'], '-c', pod['spec']['containers'][0]['name'], '--',
                      '/app/backend/.venv/bin/python', '-I', '-B', '-c', CHILD, json.dumps(args)])
    require(result.get('status') == 'PASS', 'LIVE_CHECK_FAILED')
    return result


def database_gate(pod, drained):
    result = remote(pod, {'action': 'database'})
    require(result.get('revisions') == ['0020_ppt_confirmation'] and result.get('database_readonly') is True, 'DATABASE_BASELINE_CHANGED')
    require(all(type(result.get(k)) is int and result[k] >= 0
                for k in ('active_runs', 'active_oa_jobs', 'active_callback_leases')), 'TASK_COUNT_SHAPE')
    if drained:
        if any(result[k] for k in ('active_runs', 'active_oa_jobs', 'active_callback_leases')):
            raise Refused('TASKS_STILL_ACTIVE', result)
    return result


def endpoints(service_name, pods):
    slices = command(['get', 'endpointslices', '-l', 'kubernetes.io/service-name=' + service_name, '-o', 'json'])['items']
    ready_uids = {e.get('targetRef', {}).get('uid') for s in slices for e in s.get('endpoints', [])
                  if e.get('conditions', {}).get('ready') is True and not e.get('conditions', {}).get('terminating')}
    require(ready_uids == {p['metadata']['uid'] for p in pods}, 'SERVICE_ENDPOINTS_NOT_READY')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--resume-compatibility-once', action='store_true')
    mode.add_argument('--inspect-tasks-readonly', action='store_true')
    task_policy = parser.add_mutually_exclusive_group()
    task_policy.add_argument('--tasks-drained', action='store_true')
    task_policy.add_argument('--allow-interrupt-active-tasks', action='store_true')
    args = parser.parse_args()
    if args.resume_compatibility_once and not (args.tasks_drained or args.allow_interrupt_active_tasks):
        parser.error('--resume-compatibility-once requires an explicit task policy')
    if args.inspect_tasks_readonly and (args.tasks_drained or args.allow_interrupt_active_tasks):
        parser.error('read-only inspection does not accept a task policy')
    require(os.geteuid() == 0 and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
    prior = prior_failure_evidence()
    evidence()
    if args.resume_compatibility_once:
        require(not os.path.lexists(CONTROL), 'RESUME_ALREADY_CLAIMED')
    claimed = False
    report = {'schema': 'portal-oa-compat-activation.v4', 'status': 'INCOMPLETE', 'patches': [], 'runtime': {},
              'prior_noop_failure': prior,
              'tasks_drained_operator_attested': bool(args.tasks_drained), 'automatic_retry': False, 'schema_migration_requested': False,
              'active_task_interruption_operator_authorized': bool(args.allow_interrupt_active_tasks),
              'task_policy': 'ALLOW_ACTIVE_TASK_INTERRUPTION' if args.allow_interrupt_active_tasks else
                             ('REQUIRE_TASKS_DRAINED' if args.tasks_drained else 'READONLY_INSPECTION'),
              'provider_requests': 0, 'database_access': 'READONLY_INSPECTION', 'once_claimed': False,
              'production_workload_mutation': False, 'ready_for_oa_migration': False}
    def save():
        if not claimed:
            return
        with (Path(CONTROL) / 'REPORT.tmp').open('w', encoding='utf-8') as f:
            json.dump(report, f, sort_keys=True)
            f.write('\n'); f.flush(); os.fsync(f.fileno())
        os.replace(str(Path(CONTROL) / 'REPORT.tmp'), str(Path(CONTROL) / 'REPORT.json'))
    try:
        baseline = {name: command(['get', 'deployment', name, '-o', 'json']) for name in EXPECTED}
        for name, d in baseline.items():
            plan(d, EXPECTED[name])
        front = command(['get', 'deployment', 'frontend', '-o', 'json'])
        require(binding(front) == FRONT and ready(front), 'FRONTEND_BASELINE_CHANGED')
        report['node_coverage'] = nodes_gate(baseline)
        gateway = baseline['gateway']
        oldpods = pods_for(gateway, EXPECTED['gateway'], EXPECTED['gateway']['old_image'])
        if args.inspect_tasks_readonly:
            observation = remote(oldpods[0], {'action': 'tasks'})
            require(observation.get('revisions') == ['0020_ppt_confirmation']
                    and observation.get('database_readonly') is True, 'DATABASE_BASELINE_CHANGED')
            final = command(['get', 'deployment', 'gateway', '-o', 'json'])
            require(binding(final) == binding(gateway) and ready(final), 'DEPLOYMENT_CHANGED_DURING_INSPECTION')
            require(identities(pods_for(final, EXPECTED['gateway'], EXPECTED['gateway']['old_image'])) == identities(oldpods),
                    'CONTAINERS_CHANGED_DURING_INSPECTION')
            report.update(status='TASKS_ACTIVE_NOT_DRAINED' if any(observation[k] for k in ('active_runs', 'active_oa_jobs', 'active_callback_leases'))
                          else 'TASKS_DRAINED_AT_SAMPLE_ONLY', task_observation=observation,
                          before={n: binding(d) for n, d in baseline.items()}, files_modified=False)
            print(json.dumps({k: v for k, v in report.items() if k != 'runtime'}, sort_keys=True), flush=True)
            return 0
        services = {n: command(['get', 'service', n, '-o', 'json']) for n in EXPECTED}
        config_refs = configuration_refs(baseline)
        config_metadata = configuration_metadata(config_refs)
        report['database_before'] = database_gate(oldpods[0], not args.allow_interrupt_active_tasks)
        report['before'] = {n: binding(d) for n, d in baseline.items()}
        save()
        completed = {}
        for name in ('ppt-master-runner', 'gateway'):
            pin = EXPECTED[name]
            for other in EXPECTED:
                current = command(['get', 'deployment', other, '-o', 'json'])
                require(binding(current) == completed.get(other, binding(baseline[other])) and ready(current), 'CONCURRENT_DEPLOYMENT_CHANGE')
            require(binding(command(['get', 'deployment', 'frontend', '-o', 'json'])) == FRONT, 'FRONTEND_CHANGED')
            report['node_coverage'] = nodes_gate(baseline)
            require(configuration_metadata(config_refs) == config_metadata, 'CONFIGURATION_CHANGED')
            report['last_task_check'] = database_gate(oldpods[0], not args.allow_interrupt_active_tasks)
            d = command(['get', 'deployment', name, '-o', 'json'])
            patch, expected = plan(d, pin)
            if not claimed:
                os.mkdir(CONTROL, 0o700)
                claimed = True
                report.update(once_claimed=True, database_access='READONLY_INSPECTION_PLUS_ORDINARY_SERVICE_STARTUP')
            attempt = {'deployment': name, 'before': binding(d), 'expected_spec_sha256': digest(expected),
                       'active_task_observation': report['last_task_check'],
                       'patch_requested': True, 'patch_may_have_applied': True, 'acknowledged': False}
            report['patches'].append(attempt)
            report['production_workload_mutation'] = None
            save()
            acknowledged = command(['patch', 'deployment', name, '--type=json', '--patch-file=/dev/stdin', '-o', 'json'], patch)
            require(acknowledged['metadata']['uid'] == pin['uid'] and acknowledged['spec'] == expected, 'PATCH_ACK_CHANGED')
            attempt.update(acknowledged=True, after=binding(acknowledged))
            report['production_workload_mutation'] = True
            save()
            deadline = time.monotonic() + (4200 if name == 'gateway' else 1200)
            last_progress = 0
            while True:
                current = command(['get', 'deployment', name, '-o', 'json'])
                require(binding(current) == attempt['after'], 'DEPLOYMENT_CHANGED_DURING_ROLLOUT')
                # Recheck the exclusion of non-preloaded nodes while replacements
                # are being scheduled; a removed hard taint must stop acceptance.
                report['node_coverage'] = nodes_gate(baseline)
                if ready(current):
                    try:
                        pods = pods_for(current, pin, pin['candidate_image'])
                        endpoints(name, pods)
                        break
                    except Refused as exc:
                        require(str(exc) in ('POD_COUNT_CHANGED', 'POD_NOT_READY', 'SERVICE_ENDPOINTS_NOT_READY'), str(exc))
                require(time.monotonic() < deadline, 'ROLLOUT_DEADLINE')
                if time.monotonic() - last_progress >= 20:
                    print(json.dumps({'phase': 'COMPATIBILITY_ROLLOUT', 'deployment': name,
                        'updated': current.get('status', {}).get('updatedReplicas', 0), 'ready': current.get('status', {}).get('readyReplicas', 0),
                        'desired': current['spec']['replicas']}), flush=True)
                    last_progress = time.monotonic()
                time.sleep(10)
            rows = []
            report['runtime'][name] = rows
            for pod in pods:
                result = remote(pod, {'action': 'runtime', 'pin': pin})
                rows.append({'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'observation': result})
                save()
                if len(rows) % 10 == 0 or len(rows) == len(pods):
                    print(json.dumps({'phase': 'COMPATIBILITY_RUNTIME_ACCEPTANCE', 'deployment': name, 'accepted': len(rows), 'total': len(pods)}), flush=True)
            final = command(['get', 'deployment', name, '-o', 'json'])
            require(binding(final) == attempt['after'] and ready(final), 'ROLLOUT_CHANGED_DURING_ACCEPTANCE')
            require(identities(pods_for(final, pin, pin['candidate_image'])) == identities(pods), 'CONTAINERS_CHANGED_DURING_ACCEPTANCE')
            completed[name] = binding(final)
            if name == 'gateway':
                report['database_after'] = database_gate(pods[0], False)
            save()
        for name in EXPECTED:
            final = command(['get', 'deployment', name, '-o', 'json'])
            require(binding(final) == completed[name] and ready(final), 'FINAL_DEPLOYMENT_CHANGED')
            endpoints(name, pods_for(final, EXPECTED[name], EXPECTED[name]['candidate_image']))
            require(command(['get', 'service', name, '-o', 'json'])['spec'] == services[name]['spec'], 'SERVICE_SPEC_CHANGED')
        require(binding(command(['get', 'deployment', 'frontend', '-o', 'json'])) == FRONT, 'FRONTEND_CHANGED')
        report['node_coverage'] = nodes_gate(baseline)
        require(configuration_metadata(config_refs) == config_metadata, 'CONFIGURATION_CHANGED')
        report.update(status='COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED', after=completed,
                      frontend_preserved=True, configuration_metadata_preserved=True,
                      schema_revision='0020_ppt_confirmation', ready_for_oa_migration=False)
    except Exception as exc:
        report['error_code'] = str(exc) if isinstance(exc, Refused) else type(exc).__name__
        if isinstance(exc, Refused) and exc.observation is not None:
            report['task_observation' if str(exc) == 'TASKS_STILL_ACTIVE' else 'node_gate_failure'] = exc.observation
        if str(exc) == 'TASKS_STILL_ACTIVE' and not claimed:
            report['status'] = 'WAITING_FOR_TASKS_NO_MUTATION'
    save()
    raw = (Path(CONTROL) / 'REPORT.json').read_bytes() if claimed else None
    summary = {k: v for k, v in report.items() if k != 'runtime'}
    summary.update(runtime_accepted={n: len(v) for n, v in report['runtime'].items()},
                   report_path=CONTROL + '/REPORT.json' if claimed else None,
                   report_bytes=len(raw) if raw is not None else None,
                   report_sha256=hashlib.sha256(raw).hexdigest() if raw is not None else None,
                   preflight_retry_available=not claimed, files_modified=claimed)
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 0 if report['status'] == 'COMPATIBILITY_ROLLOUT_ACCEPTED_SCHEMA_UNCHANGED' else 1


if __name__ == '__main__':
    try:
        code = main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
                          'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                          'automatic_retry': False}))
        code = 1
    raise SystemExit(code)
