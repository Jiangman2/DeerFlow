"""Two bounded image qualification Jobs; no production deployment or DB changes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

EXPECTED = {}  # Package builder embeds the reviewed deployment/image/node pins.
CHILD = ''
CONTROL = '/var/lib/portal-oa-compat-image-check-20261022-r1'
BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def command(args, value=None, raw=False):
    data = json.dumps(value).encode() if value is not None else None
    p = subprocess.run(BASE + args, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    require(p.returncode == 0 and len(p.stdout) <= 2097152, 'KUBERNETES_COMMAND_FAILED')
    return p.stdout if raw else json.loads(p.stdout)


def validate_deployment(d, pin):
    meta, spec, status = d['metadata'], d['spec'], d.get('status', {})
    require(meta['uid'] == pin['uid'] and meta['generation'] == pin['generation']
            and digest(spec) == pin['spec_sha256'], 'DEPLOYMENT_BASELINE_CHANGED')
    require(status.get('observedGeneration') == meta['generation']
            and all(status.get(k, 0) == spec['replicas'] for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and not status.get('terminatingReplicas') and not status.get('unavailableReplicas'), 'DEPLOYMENT_NOT_STABLE')


def job_spec(name, pin, node):
    require(node['metadata']['uid'] == pin['node_uid'] and node['metadata']['name'] == pin['node'], 'NODE_CHANGED')
    conditions = {r['type']: r['status'] for r in node.get('status', {}).get('conditions', [])}
    require(conditions.get('Ready') == 'True' and conditions.get('DiskPressure') == 'False'
            and not node.get('spec', {}).get('unschedulable'), 'NODE_NOT_READY')
    taints = node.get('spec', {}).get('taints', [])
    require(all(t.get('effect') in ('NoSchedule', 'PreferNoSchedule') for t in taints), 'NODE_NOEXECUTE_TAINT')
    tolerations = [dict(key=t['key'], operator='Equal', value=t.get('value', ''), effect=t['effect']) for t in taints]
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'deer-flow'},
        'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 180, 'ttlSecondsAfterFinished': 600,
            'template': {'metadata': {'labels': {'portal-oa-image-check': name}}, 'spec': {
                'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'enableServiceLinks': False,
                'nodeSelector': {'kubernetes.io/hostname': pin['node']}, 'tolerations': tolerations,
                'containers': [{'name': 'check', 'image': pin['candidate_image'], 'imagePullPolicy': 'Never',
                    'command': ['/app/backend/.venv/bin/python', '-I', '-B', '-c', CHILD, json.dumps(pin['sources'], sort_keys=True)],
                    'env': [{'name': 'HOME', 'value': '/tmp'}, {'name': 'TMPDIR', 'value': '/tmp'}],
                    'securityContext': {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True,
                                        'capabilities': {'drop': ['ALL']}},
                    'resources': {'requests': {'cpu': '100m', 'memory': '128Mi', 'ephemeral-storage': '64Mi'},
                                  'limits': {'cpu': '1', 'memory': '512Mi', 'ephemeral-storage': '128Mi'}},
                    'volumeMounts': [{'name': 'scratch', 'mountPath': '/tmp'}]}],
                'volumes': [{'name': 'scratch', 'emptyDir': {'sizeLimit': '64Mi'}}]}}}}


def inspect_pods(job, pin):
    pods = command(['get', 'pods', '-l', 'job-name=' + job['metadata']['name'], '-o', 'json'])['items']
    require(len(pods) <= 1, 'UNEXPECTED_POD_COUNT')
    result = []
    for p in pods:
        require(any(r.get('uid') == job['metadata']['uid'] and r.get('controller') is True
                    for r in p['metadata'].get('ownerReferences', [])), 'POD_OWNER_CHANGED')
        cs = p.get('status', {}).get('containerStatuses', [])
        result.append({'name': p['metadata']['name'], 'uid': p['metadata']['uid'], 'phase': p.get('status', {}).get('phase'),
                       'containers': [{'imageID': c.get('imageID'), 'waiting_reason': c.get('state', {}).get('waiting', {}).get('reason'),
                                       'exit_code': c.get('state', {}).get('terminated', {}).get('exitCode')} for c in cs]})
        if cs and cs[0].get('imageID'):
            require(p['spec']['nodeName'] == pin['node'] and p['spec']['containers'][0]['image'] == pin['candidate_image']
                    and cs[0]['imageID'].split('@')[-1] in (pin['candidate_image'].split('@')[-1], pin['config_digest']), 'RUNTIME_IMAGE_CHANGED')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--verify-preloaded-images-once', action='store_true', required=True)
    p.parse_args()
    require(os.geteuid() == 0 and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
    os.mkdir(CONTROL, 0o700)
    report = {'schema': 'portal-oa-compat-installed-images.v1', 'status': 'INCOMPLETE', 'checks': [],
              'temporary_jobs_created': [], 'production_workload_modified': False, 'production_database_access': False,
              'schema_migrated': False, 'provider_requests': 0, 'automatic_retry': False, 'ready_for_rollout': False}
    def save():
        target = Path(CONTROL) / 'REPORT.json'
        temp = Path(CONTROL) / 'REPORT.tmp'
        with temp.open('w', encoding='utf-8') as f:
            json.dump(report, f, sort_keys=True)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(temp), str(target))
    try:
        # Validate both consumers before creating either Job.
        for deployment, pin in EXPECTED.items():
            validate_deployment(command(['get', 'deployment', deployment, '-o', 'json']), pin)
        for deployment, pin in EXPECTED.items():
            name = 'portal-oa-compat-check-' + uuid.uuid4().hex[:12]
            node = command(['get', 'node', pin['node'], '-o', 'json'])
            spec = job_spec(name, pin, node)
            report['temporary_jobs_created'].append({'name': name, 'creation_requested': True})
            save()
            job = command(['create', '-f', '-', '-o', 'json'], spec)
            row = {'deployment': deployment, 'job': name, 'job_uid': job['metadata']['uid'], 'status': 'RUNNING'}
            report['checks'].append(row)
            save()
            print(json.dumps({'phase': 'INSTALLED_COMPAT_IMAGE_CHECK', 'deployment': deployment, 'job': name}), flush=True)
            deadline = time.monotonic() + 240
            while True:
                observed = command(['get', 'job', name, '-o', 'json'])
                require(observed['metadata']['uid'] == row['job_uid'], 'JOB_CHANGED')
                row['pods'] = inspect_pods(job, pin)
                row['job_conditions'] = [{k: c.get(k) for k in ('type', 'status', 'reason')} for c in observed.get('status', {}).get('conditions', [])]
                save()
                if observed.get('status', {}).get('succeeded') == 1 or observed.get('status', {}).get('failed'):
                    break
                require(time.monotonic() < deadline, 'JOB_WAIT_TIMEOUT')
                time.sleep(5)
            if observed.get('status', {}).get('succeeded') != 1:
                if len(row['pods']) == 1 and row['pods'][0]['phase'] == 'Failed':
                    try:
                        failure = json.loads(command(['logs', row['pods'][0]['name'], '-c', 'check', '--limit-bytes=16384'], raw=True))
                        row['failure'] = {k: failure.get(k) for k in ('status', 'error_class', 'production_database_access')}
                    except Exception:
                        row['failure'] = {'status': 'NO_STRUCTURED_CHILD_RESULT'}
                raise Refused('CANDIDATE_JOB_FAILED')
            require(len(row['pods']) == 1 and len(row['pods'][0]['containers']) == 1
                    and row['pods'][0]['containers'][0]['imageID'], 'MISSING_RUNTIME_IMAGE_RECEIPT')
            raw = command(['logs', row['pods'][0]['name'], '-c', 'check', '--limit-bytes=16384'], raw=True)
            outcome = json.loads(raw.decode().strip())
            require(outcome.get('status') == 'INSTALLED_COMPAT_IMAGE_ACCEPTED'
                    and len(outcome.get('checks', [])) == 5 and outcome.get('production_database_access') is False, 'IMAGE_CHECK_FAILED')
            row.update(status='PASS', observation=outcome)
            save()
        for deployment, pin in EXPECTED.items():
            validate_deployment(command(['get', 'deployment', deployment, '-o', 'json']), pin)
        report['status'] = 'COMPAT_IMAGES_ACCEPTED_NOT_DEPLOYED'
    except Exception as exc:
        report['error_code'] = str(exc) if isinstance(exc, Refused) else type(exc).__name__
    save()
    raw = (Path(CONTROL) / 'REPORT.json').read_bytes()
    report.update(report_path=CONTROL + '/REPORT.json', report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report['status'] == 'COMPAT_IMAGES_ACCEPTED_NOT_DEPLOYED' else 1


if __name__ == '__main__':
    try:
        code = main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_class': type(exc).__name__, 'automatic_retry': False}))
        code = 1
    raise SystemExit(code)
