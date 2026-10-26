#!/usr/bin/env python3
"""Install the exact member UI patch after the current Office preview init.

Python 3.7+. No member, model, Secret, database or Gateway writes.
The installed-image Job must pass before the single frontend rollout patch.
"""
import argparse
import base64
import copy
import hashlib
import json
import os
import socket
import subprocess
import time
import uuid
import zlib
from pathlib import Path

PAYLOAD = '__SEALED_PAYLOAD__'
CONTROL = Path('/var/lib/portal-weknora-member-ui-fix-20261025-r1')
UID = 'e2d0c5f6-abfe-4ba7-ae17-1700bcd3e401'
SPEC = '0c7d4eeafb51d7e6bc87afd917a74e811a1ff9decbfb6efe8af0630e936eebfa'
IMAGE = 'deerflow.local/weknora-ui:admin-upload-r2-4ccbd940db0c-20261022103101'
DEPLOYMENT = 'weknora-frontend'
OLD_CM = 'weknora-v08-preview-patch'
VOLUME = 'weknora-members-fix-r1'
INIT = 'patch-large-space-members'
RUNTIME_VOLUME = 'weknora-ui-runtime'
BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s', '-n', 'deer-flow']
OTHER = ['gateway', 'frontend', 'weknora-app', 'ppt-master-runner', 'nginx', 'provisioner']


class Refused(Exception):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def raw_hash(value):
    return hashlib.sha256(value).hexdigest()


def invoke(args, body=None, timeout=35, limit=2097152):
    payload = None if body is None else json.dumps(body, separators=(',', ':')).encode()
    p = subprocess.run(BASE + args, input=payload, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    require(p.returncode == 0, 'KUBECTL_COMMAND_FAILED')
    require(len(p.stdout) <= limit and len(p.stderr) <= 1048576, 'COMMAND_OUTPUT_LIMIT')
    return p.stdout


def get(kind, name=None, extra=None):
    args = ['get', kind] + ([name] if name else []) + (extra or []) + ['-o', 'json']
    return json.loads(invoke(args))


def identity(d):
    return {'uid': d['metadata']['uid'], 'generation': d['metadata']['generation'], 'spec_sha256': digest(d['spec'])}


def stable(d):
    s = d.get('status', {})
    return (d['spec'].get('replicas') == 1 and not d['spec'].get('paused')
            and s.get('observedGeneration') == d['metadata']['generation']
            and all(s.get(k) == 1 for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and not s.get('unavailableReplicas') and not s.get('terminatingReplicas'))


def selected_pods(d):
    selector = d['spec']['selector']
    require(not selector.get('matchExpressions'), 'SELECTOR_EXPRESSIONS_UNEXPECTED')
    labels = ','.join(k + '=' + v for k, v in sorted(selector['matchLabels'].items()))
    rows = get('pods', extra=['-l', labels])['items']
    return rows


def ready_pod(d):
    pods = selected_pods(d)
    require(len(pods) == 1 and not pods[0]['metadata'].get('deletionTimestamp'), 'POD_SET_NOT_STABLE')
    p = pods[0]
    owner = next((x for x in p['metadata'].get('ownerReferences', []) if x.get('controller')), {})
    require(owner.get('kind') == 'ReplicaSet', 'POD_OWNER_UNEXPECTED')
    rs = get('replicaset', owner['name'])
    require(rs['metadata']['uid'] == owner['uid'] and any(
        x.get('controller') and x.get('uid') == d['metadata']['uid'] and x.get('kind') == 'Deployment'
        for x in rs['metadata'].get('ownerReferences', [])), 'REPLICASET_OWNER_CHANGED')
    statuses = p.get('status', {}).get('containerStatuses', [])
    require(p.get('status', {}).get('phase') == 'Running' and len(statuses) == 1
            and statuses[0]['name'] == 'frontend' and statuses[0].get('ready'), 'POD_NOT_READY')
    require('sha256:' in statuses[0].get('imageID', ''), 'RUNTIME_IMAGE_ID_MISSING')
    require(p['spec']['containers'][0]['image'] == IMAGE, 'POD_IMAGE_CHANGED')
    return p


def pod_identity(p):
    s = p['status']['containerStatuses'][0]
    return {'pod': p['metadata']['name'], 'uid': p['metadata']['uid'], 'node': p['spec']['nodeName'],
            'image_id': s['imageID'], 'container_id': s.get('containerID'), 'restart_count': s.get('restartCount')}


def check_layout(d):
    require(identity(d) == {'uid': UID, 'generation': 38, 'spec_sha256': SPEC}, 'FRONTEND_BASELINE_CHANGED')
    require(stable(d), 'FRONTEND_NOT_STABLE')
    s = d['spec']['template']['spec']
    require(len(s.get('containers', [])) == 1 and s['containers'][0]['name'] == 'frontend'
            and s['containers'][0]['image'] == IMAGE, 'CONTAINER_CHANGED')
    require(len(s.get('initContainers', [])) == 1, 'INIT_CONTAINERS_CHANGED')
    old = s['initContainers'][0]
    require(old['name'] == 'patch-legacy-office-preview' and old['image'] == IMAGE
            and not old.get('env') and not old.get('envFrom'), 'PREVIEW_INIT_CHANGED')
    require(len(s.get('volumes', [])) == 2, 'VOLUMES_CHANGED')
    vols = {v['name']: v for v in s['volumes']}
    require('emptyDir' in vols.get(RUNTIME_VOLUME, {})
            and vols.get('weknora-v08-preview-patch', {}).get('configMap', {}).get('name') == OLD_CM,
            'RUNTIME_MOUNTS_CHANGED')
    require(all(m['name'] in vols for m in old['volumeMounts']), 'INIT_MOUNT_CHANGED')


def verify_files(pod, hashes):
    paths = sorted(hashes)
    require(all(path.startswith('assets/') and path.count('/') == 1 or path == 'index.html'
                for path in paths), 'ASSET_PATH_INVALID')
    command = 'cd /usr/share/nginx/html && sha256sum ' + ' '.join(paths)
    raw = invoke(['exec', pod['metadata']['name'], '-c', 'frontend', '--', '/bin/sh', '-ec', command], limit=16384)
    observed = {}
    for line in raw.decode().splitlines():
        parts = line.split()
        require(len(parts) == 2, 'ASSET_HASH_OUTPUT_INVALID')
        observed[parts[1]] = parts[0]
    require(observed == hashes, 'SERVED_ASSET_HASH_CHANGED')


def addon(image, cm_name):
    return {'name': INIT, 'image': image, 'imagePullPolicy': 'Never',
            'command': ['/bin/sh', '/opt/weknora-member-fix/install.sh'],
            'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
            'volumeMounts': [{'name': RUNTIME_VOLUME, 'mountPath': '/patched-ui'},
                             {'name': VOLUME, 'mountPath': '/opt/weknora-member-fix', 'readOnly': True}]}


def new_volume(cm_name):
    return {'name': VOLUME, 'configMap': {'name': cm_name, 'defaultMode': 420}}


def build_job(before, sample, cm_name, name, files):
    oldspec = before['spec']['template']['spec']
    oldinit = copy.deepcopy(oldspec['initContainers'][0])
    # A validation Job cannot pull a different image under the existing tag.
    oldinit['imagePullPolicy'] = 'Never'
    spec = {'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'nodeName': sample['spec']['nodeName'], 'volumes': copy.deepcopy(oldspec['volumes']) + [new_volume(cm_name)],
            'initContainers': [oldinit, addon(IMAGE, cm_name)],
            'containers': [{'name': 'verify', 'image': IMAGE, 'imagePullPolicy': 'Never',
                'command': ['/bin/sh', '-ec', files['verify.sh']],
                'resources': {'requests': {'cpu': '50m', 'memory': '64Mi'}, 'limits': {'cpu': '500m', 'memory': '256Mi'}},
                'volumeMounts': [{'name': RUNTIME_VOLUME, 'mountPath': '/usr/share/nginx/html', 'readOnly': True},
                                 {'name': VOLUME, 'mountPath': '/opt/weknora-member-fix', 'readOnly': True}]}]}
    for key in ('tolerations', 'imagePullSecrets'):
        if oldspec.get(key):
            spec[key] = copy.deepcopy(oldspec[key])
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'deer-flow'},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 300, 'ttlSecondsAfterFinished': 86400,
                     'template': {'metadata': {'labels': {'portal-weknora-members-check': name}}, 'spec': spec}}}


def patch_body(before, cm_name):
    return [{'op': 'test', 'path': '/metadata/uid', 'value': UID},
            {'op': 'test', 'path': '/metadata/resourceVersion', 'value': before['metadata']['resourceVersion']},
            {'op': 'test', 'path': '/spec', 'value': before['spec']},
            {'op': 'add', 'path': '/spec/template/spec/volumes/-', 'value': new_volume(cm_name)},
            {'op': 'add', 'path': '/spec/template/spec/initContainers/-', 'value': addon(IMAGE, cm_name)}]


def require_preserved(before, after, cm_name):
    require(after['metadata']['uid'] == UID and after['metadata']['generation'] == 39, 'FRONTEND_IDENTITY_CHANGED')
    spec = copy.deepcopy(after['spec'])
    podspec = spec['template']['spec']
    require(len(podspec['initContainers']) == 2 and len(podspec['volumes']) == 3, 'UNEXPECTED_TEMPLATE_DELTA')
    init = podspec['initContainers'].pop()
    vol = podspec['volumes'].pop()
    require(vol == new_volume(cm_name), 'PATCH_VOLUME_CHANGED')
    expected = addon(IMAGE, cm_name)
    for key, value in expected.items():
        require(init.get(key) == value, 'PATCH_INIT_CHANGED')
    require(set(init) - set(expected) <= {'terminationMessagePath', 'terminationMessagePolicy'}, 'EXTRA_INIT_SETTING')
    require(spec == before['spec'], 'UNRELATED_FRONTEND_SPEC_CHANGED')


def run_release(report, payload, save):
    before = get('deployment', DEPLOYMENT)
    check_layout(before)
    sample = ready_pod(before)
    node = get('node', sample['spec']['nodeName'])
    require(not node['spec'].get('unschedulable') and any(x.get('type') == 'Ready' and x.get('status') == 'True'
            for x in node.get('status', {}).get('conditions', [])), 'SAMPLE_NODE_NOT_READY')
    others = {name: identity(get('deployment', name)) for name in OTHER}
    original_cm = get('configmap', OLD_CM)
    cm_identity = {'uid': original_cm['metadata']['uid'], 'data_sha256': digest(original_cm.get('data', {}))}
    verify_files(sample, payload['baseline_hashes'])
    report.update(before=identity(before), sample_before=pod_identity(sample), other_deployments_before=others,
                  old_preview_configmap=cm_identity, phase='CREATE_CANDIDATE')
    os.mkdir(str(CONTROL), 0o700)
    report['once_claimed'] = True
    save()
    operation = uuid.uuid4().hex[:12]
    cm_name = 'weknora-members-fix-20261025-' + payload['payload_sha256'][:12]
    job_name = 'weknora-member-check-' + operation
    report.update(configmap_name=cm_name, job_name=job_name, configmap_create_requested=True)
    save()
    created_cm = json.loads(invoke(['create', '-f', '-', '-o', 'json'], {
        'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': {'name': cm_name, 'namespace': 'deer-flow'},
        'immutable': True, 'data': payload['files']}))
    require(created_cm.get('immutable') is True and created_cm.get('data') == payload['files'], 'CANDIDATE_CONFIGMAP_MISMATCH')
    report['candidate_configmap_uid'] = created_cm['metadata']['uid']
    report.update(job_create_requested=True, phase='VALIDATE_CANDIDATE_JOB')
    save()
    job = json.loads(invoke(['create', '-f', '-', '-o', 'json'], build_job(before, sample, cm_name, job_name, payload['files'])))
    report['job_uid'] = job['metadata']['uid']
    save()
    for step in range(66):
        current = get('job', job_name)
        require(current['metadata']['uid'] == report['job_uid'], 'CHECK_JOB_REPLACED')
        conditions = current.get('status', {}).get('conditions', [])
        require(not any(x['type'] == 'Failed' and x['status'] == 'True' for x in conditions), 'CANDIDATE_JOB_FAILED')
        if any(x['type'] == 'Complete' and x['status'] == 'True' for x in conditions):
            break
        if step % 6 == 0:
            print(json.dumps({'phase': report['phase'], 'elapsed_seconds': step * 5}), flush=True)
        time.sleep(5)
    else:
        raise Refused('CANDIDATE_JOB_TIMEOUT')
    pods = get('pods', extra=['-l', 'job-name=' + job_name])['items']
    require(len(pods) == 1 and pods[0].get('status', {}).get('phase') == 'Succeeded', 'CHECK_POD_NOT_SUCCEEDED')
    jp = pods[0]
    require(any(x.get('controller') and x.get('uid') == report['job_uid'] for x in jp['metadata'].get('ownerReferences', [])), 'CHECK_POD_OWNER_CHANGED')
    statuses = jp['status'].get('initContainerStatuses', []) + jp['status'].get('containerStatuses', [])
    require(len(statuses) == 3 and all(x.get('state', {}).get('terminated', {}).get('exitCode') == 0
            and x.get('imageID') == sample['status']['containerStatuses'][0]['imageID'] for x in statuses), 'CHECK_IMAGE_OR_EXIT_MISMATCH')
    outcome = json.loads(invoke(['logs', jp['metadata']['name'], '-c', 'verify', '--limit-bytes=4096'], limit=4096))
    require(outcome == payload['job_expected'], 'CANDIDATE_OBSERVATION_MISMATCH')
    report.update(installed_candidate_accepted=True, candidate_observation=outcome, phase='FRONTEND_PREFLIGHT')
    save()
    fresh = get('deployment', DEPLOYMENT)
    check_layout(fresh)
    require(pod_identity(ready_pod(fresh)) == pod_identity(sample), 'FRONTEND_POD_CHANGED_DURING_CHECK')
    require({name: identity(get('deployment', name)) for name in OTHER} == others, 'OTHER_DEPLOYMENT_CHANGED')
    cm_now = get('configmap', OLD_CM)
    require({'uid': cm_now['metadata']['uid'], 'data_sha256': digest(cm_now.get('data', {}))} == cm_identity, 'PREVIEW_CONFIGMAP_CHANGED')
    patch = patch_body(fresh, cm_name)
    report['dry_run_requests'] = 1
    planned = json.loads(invoke(['patch', 'deployment', DEPLOYMENT, '--type=json', '--patch-file=/dev/stdin', '--dry-run=server', '-o', 'json'], patch))
    require_preserved(fresh, planned, cm_name)
    report.update(planned=identity(planned), phase='PATCH_FRONTEND', patch_requests=1,
                  patch_may_have_applied=True, production_workload_mutation_may_have_occurred=True)
    save()
    # After this point no retry/replay is allowed, even on a lost acknowledgement.
    result = json.loads(invoke(['patch', 'deployment', DEPLOYMENT, '--type=json', '--patch-file=/dev/stdin', '-o', 'json'], patch))
    require(identity(result) == identity(planned), 'PATCH_REPLY_CHANGED')
    report.update(patch_acknowledged=True, production_workload_mutation=True, phase='OBSERVE_FRONTEND')
    save()
    for step in range(180):
        result = get('deployment', DEPLOYMENT)
        require(identity(result) == identity(planned), 'FRONTEND_CHANGED_DURING_ROLLOUT')
        report['last_counts'] = {k: result.get('status', {}).get(k) for k in (
            'replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas', 'terminatingReplicas')}
        if stable(result) and len(selected_pods(result)) == 1:
            break
        if step % 6 == 0:
            print(json.dumps({'phase': report['phase'], 'counts': report['last_counts']}), flush=True)
            save()
        time.sleep(5)
    else:
        raise Refused('ROLLOUT_OBSERVATION_TIMEOUT')
    serving = ready_pod(result)
    require(serving['status']['containerStatuses'][0]['imageID'] == sample['status']['containerStatuses'][0]['imageID'], 'FRONTEND_IMAGE_ID_CHANGED')
    verify_files(serving, dict(payload['final_hashes'], **{k:v for k,v in payload['baseline_hashes'].items() if k != 'index.html'}))
    marker = json.loads(invoke(['exec', serving['metadata']['name'], '-c', 'frontend', '--',
        '/bin/sh', '-ec', 'wget -q -O - http://127.0.0.1:80/weknora-members-fix-r1.json'], limit=4096))
    require(marker.get('status') == 'MEMBER_UI_PATCH_INSTALLED' and marker.get('version') == 'members-r1'
            and 40 <= marker.get('module_count', 0) <= 250, 'HTTP_MARKER_FAILED')
    others_after = {name: identity(get('deployment', name)) for name in OTHER}
    require(others_after == others, 'OTHER_DEPLOYMENT_CHANGED_AFTER_ROLLOUT')
    cm_after = get('configmap', OLD_CM)
    require({'uid': cm_after['metadata']['uid'], 'data_sha256': digest(cm_after.get('data', {}))} == cm_identity, 'PREVIEW_CONFIGMAP_CHANGED_AFTER_ROLLOUT')
    end = get('deployment', DEPLOYMENT)
    require(identity(end) == identity(planned) and stable(end), 'FINAL_STATE_CHANGED')
    require(pod_identity(ready_pod(end)) == pod_identity(serving), 'FINAL_POD_CHANGED')
    report.update(status='WEKNORA_MEMBER_UI_RUNTIME_ACCEPTED_BROWSER_PENDING', runtime_accepted=True,
                  after=identity(end), sample_after=pod_identity(serving), http_marker=marker,
                  old_image_preserved=True, old_preview_patch_preserved=True, other_deployments_preserved=True,
                  phase='COMPLETE', exitcode=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-member-ui-fix-once', required=True, action='store_true')
    parser.parse_args()
    report = {'schema': 'portal-weknora-member-ui-fix.v1', 'status': 'INCOMPLETE', 'exitcode': 1,
              'automatic_retry': False, 'automatic_rollback': False, 'credentials_exported': False,
              'membership_writes': 0, 'provider_requests': 0, 'database_access': False,
              'gateway_restart_requested': False, 'patch_requests': 0, 'dry_run_requests': 0,
              'patch_may_have_applied': False, 'production_workload_mutation': False,
              'production_workload_mutation_may_have_occurred': False, 'once_claimed': False,
              'runtime_accepted': False, 'browser_acceptance_pending': True, 'phase': 'BASELINE'}
    started = time.monotonic()

    def save():
        if not report['once_claimed']:
            return
        raw = (json.dumps(report, sort_keys=True, indent=2) + '\n').encode()
        temp = CONTROL / 'REPORT.tmp'
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as out:
            out.write(raw)
            out.flush()
            os.fsync(out.fileno())
        os.replace(str(temp), str(CONTROL / 'REPORT.json'))

    try:
        require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'WRONG_EXECUTION_HOST')
        require(not CONTROL.exists() and not CONTROL.is_symlink(), 'ONCE_DIRECTORY_EXISTS_DO_NOT_RERUN')
        payload = json.loads(zlib.decompress(base64.b64decode(PAYLOAD)))
        require(digest(payload['files']) == payload['payload_sha256'], 'EMBEDDED_PAYLOAD_CHANGED')
        run_release(report, payload, save)
    except (Exception, KeyboardInterrupt) as error:
        report['error_class'] = type(error).__name__
        report['error_code'] = str(error) if isinstance(error, Refused) else 'EXECUTION_FAILED_OR_INTERRUPTED'
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    save()
    output = dict(report)
    if report['once_claimed']:
        raw = (CONTROL / 'REPORT.json').read_bytes()
        output.update(report_path=str(CONTROL / 'REPORT.json'), report_bytes=len(raw), report_sha256=raw_hash(raw))
    print(json.dumps(output, sort_keys=True), flush=True)
    return report['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())
