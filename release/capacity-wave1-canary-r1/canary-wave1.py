#!/usr/bin/env python3
"""One isolated frontend Pod + ClusterIP Service; NEVER production rollout."""
import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
helper = ROOT / 'prepare-wave1.py'
if not helper.is_file():  # Local development only; the delivered package includes it.
    helper = ROOT.parent / 'capacity-wave1-r1' / 'prepare-wave1.py'
if hashlib.sha256(helper.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != '7df921c0675ada9544e33e7d7627e3881e143a331fd0df40d9bde36a80395383':
    raise RuntimeError('preparation_reader_digest_mismatch')
spec = importlib.util.spec_from_file_location('wave1_preparation', str(helper))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)
VERSION = '20260924-capacity-wave1-canary-r1'
LABEL = 'capacity.deerflow.local/wave1-canary'
ANNOTATION = 'capacity.deerflow.local/plan-sha256'
EVIDENCE = Path('/opt/deerflow-hotfix-evidence')
HTTP_PROBE = (ROOT / 'http-probe.cjs').read_text(encoding='utf-8')
NAME_RE = r'cap-w1-fe-[0-9a-f]{16}'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def validate_preload(report, plan):
    p.validate_plan(plan)
    p.require(report.get('version') == p.VERSION and report.get('phase') == 'preload'
              and report.get('target') == p.TARGET, 'preload_wrong_release_or_target')
    p.require(-60 <= time.time()-report['collected_epoch'] <= 21600, 'preload_expired_6h')
    p.require(report.get('image_preload') == 'PASS' and report.get('image') == p.IMAGE
              and report.get('image_id') == plan['frontend']['image_id'], 'preload_image_identity_mismatch')
    p.require(report.get('containerd_root') == '/mnt/data/containerd', 'preload_data_disk_changed')
    p.require(report.get('rollout') == 'NOT_PERFORMED' and report.get('kubernetes_mutation') == 'NONE', 'preload_scope_changed')


def compare(plan, fresh):
    for key in ('cluster_uid', 'service_spec_sha256', 'protected_workloads'):
        p.require(plan[key] == fresh[key], 'production_baseline_changed=' + key)
    for key in ('uid', 'spec_sha256', 'image_id', 'next_runtime', 'pod_uid'):
        p.require(plan['frontend'][key] == fresh['frontend'][key], 'frontend_baseline_changed=' + key)
    p.require(plan['target_node']['uid'] == fresh['target_node']['uid'], 'target_node_replaced')
    p.require(plan['target_node']['cordoned'] is True and fresh['target_node']['cordoned'] is True, 'target_must_remain_cordoned')


def empty_target(fresh):
    for pod in fresh['target_node']['pods']:
        owners = pod.get('owners', [])
        p.require(pod['namespace'] in ('kube-system', 'kube-flannel') and pod['phase'] == 'Running'
                  and len(owners) == 1 and owners[0] in (
                      {'kind': 'DaemonSet', 'name': 'kube-proxy'},
                      {'kind': 'DaemonSet', 'name': 'kube-flannel-ds'}), 'target_has_unreviewed_pods')


def manifests(deployment, intent):
    p.frontend_projection(deployment)
    ps = copy.deepcopy(deployment['spec']['template']['spec'])
    for key in ('nodeName', 'nodeSelector', 'affinity', 'tolerations', 'topologySpreadConstraints',
                'runtimeClassName', 'priorityClassName', 'schedulingGates', 'readinessGates'):
        p.require(not ps.get(key), 'frontend_custom_scheduling_requires_review=' + key)
    p.require(ps.get('schedulerName', 'default-scheduler') == 'default-scheduler', 'custom_scheduler')
    p.require(ps.get('serviceAccountName', 'default') == 'default', 'custom_service_account')
    c = ps['containers'][0]
    p.require(not c.get('lifecycle'), 'frontend_lifecycle_hook_requires_review')
    p.require(not c.get('securityContext', {}).get('privileged'), 'frontend_privileged')
    p.require(not any(x.get('hostPort') for x in c.get('ports', [])), 'host_port_forbidden')
    p.require(c.get('resources') == {'requests': {'cpu': '100m', 'memory': '256Mi'},
                                    'limits': {'cpu': '1', 'memory': '1Gi'}}, 'resource_baseline_changed')
    p.require('readinessProbe' in c, 'frontend_readiness_required')
    c['resources']['limits']['ephemeral-storage'] = '512Mi'
    ps.update(nodeSelector={'kubernetes.io/hostname': p.TARGET},
              tolerations=[{'key': 'node.kubernetes.io/unschedulable', 'operator': 'Exists', 'effect': 'NoSchedule'}],
              automountServiceAccountToken=False, enableServiceLinks=False, restartPolicy='Never',
              activeDeadlineSeconds=900, terminationGracePeriodSeconds=10)
    meta = {'name': intent['name'], 'namespace': p.NS, 'labels': {LABEL: intent['token']},
            'annotations': {ANNOTATION: intent['plan_sha256']}}
    pod = {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': meta, 'spec': ps}
    svc = {'apiVersion': 'v1', 'kind': 'Service', 'metadata': copy.deepcopy(meta),
           'spec': {'type': 'ClusterIP', 'selector': {LABEL: intent['token']},
                    'ports': [{'name': 'http', 'protocol': 'TCP', 'port': 3000, 'targetPort': 3000}]}}
    return pod, svc


def no_service_overlap(services, labels, own=None):
    for svc in services:
        selector = svc['spec'].get('selector')
        if svc['metadata']['name'] != own and selector:
            p.require(not all(labels.get(k) == v for k, v in selector.items()), 'canary_selected_by_existing_service')


def contains(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(contains(a, b) for a, b in zip(actual, expected))
    return actual == expected


def check_admission(actual, expected):
    p.require(actual['metadata'].get('labels') == expected['metadata']['labels'], 'admission_changed_labels')
    aps = actual['spec']; eps = expected['spec']
    p.require(not actual['metadata'].get('ownerReferences'), 'unexpected_canary_owner')
    p.require(not aps.get('initContainers') and not any(aps.get(k) for k in ('hostNetwork', 'hostPID', 'hostIPC')), 'admission_added_privileged_features')
    for c in aps.get('containers', []):
        p.require(not c.get('securityContext', {}).get('privileged') and not any(x.get('hostPort') for x in c.get('ports', [])), 'admission_added_privilege_or_hostport')
    for t in aps.get('tolerations', []):
        p.require(t in eps.get('tolerations', []) or (t.get('key') in ('node.kubernetes.io/not-ready', 'node.kubernetes.io/unreachable')
                  and t.get('effect') == 'NoExecute' and t.get('operator') == 'Exists' and 0 < t.get('tolerationSeconds', 0) <= 300), 'admission_added_unreviewed_toleration')
    for key, value in eps.items():
        if key == 'tolerations':
            p.require(all(t in aps.get(key, []) for t in value), 'canary_toleration_removed')
        else:
            p.require(contains(aps.get(key), value), 'admission_changed_pod_field=' + key)
    if aps.get('nodeName'):
        p.require(aps['nodeName'] == p.TARGET, 'canary_scheduled_on_wrong_node')


def check_service(actual, expected):
    p.require(actual['metadata'].get('labels') == expected['metadata']['labels'], 'service_admission_labels_changed')
    spec = actual['spec']
    p.require(spec.get('type') == 'ClusterIP' and spec.get('selector') == expected['spec']['selector']
              and contains(spec.get('ports'), expected['spec']['ports'])
              and not any(spec.get(k) for k in ('externalIPs', 'loadBalancerIP', 'publishNotReadyAddresses')),
              'service_admission_exposure_changed')


def api(*args, data=None):
    """Allowlist prevents production mutations and arbitrary container commands."""
    allowed = False
    if len(args) == 7 and args[0] == 'get' and args[1] in ('pod', 'service'):
        allowed = bool(re.fullmatch(NAME_RE, args[2])) and args[3:] == ('--ignore-not-found', '-o', 'json', '--show-managed-fields=false')
    if args in (('create', '-f', '-', '-o', 'json'), ('create', '--dry-run=server', '-f', '-', '-o', 'json')) and data:
        meta = data.get('metadata', {})
        allowed = (data.get('kind') in ('Pod', 'Service') and meta.get('namespace') == p.NS
                   and bool(re.fullmatch(NAME_RE, meta.get('name', '')))
                   and meta.get('labels') == {LABEL: meta['name'].replace('cap-w1-fe-', '')})
    if len(args) == 5 and args[:2] == ('delete', '--raw') and args[3:] == ('-f', '-') and data:
        allowed = bool(re.fullmatch('/api/v1/namespaces/deer-flow/(pods|services)/' + NAME_RE, args[2])) and data.get('kind') == 'DeleteOptions' and bool(data.get('preconditions', {}).get('uid'))
    if len(args) >= 8 and args[0] == 'exec' and args[2:7] == ('-c', 'frontend', '--', 'node', '-e'):
        if args[7] == p.PROBE:
            allowed = len(args) == 8 and bool(re.fullmatch(NAME_RE, args[1]))
        elif args[7] == HTTP_PROBE:
            allowed = (len(args) in (10, 11) and bool(re.fullmatch(r'(frontend-[a-z0-9-]+|' + NAME_RE + ')', args[1]))
                       and bool(re.fullmatch(r'(127\.0\.0\.1|frontend\.deer-flow\.svc|' + NAME_RE + r'\.deer-flow\.svc)', args[8]))
                       and args[9] == '3000' and (len(args) == 10 or args[10] in ('/health', '/live', '/ready', '/api/health')))
    p.require(allowed, 'api_operation_not_allowlisted')
    try:
        result = subprocess.run(['kubectl', '--request-timeout=25s', '-n', p.NS] + list(args),
                                input='' if data is None else json.dumps(data), stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True, timeout=40)
    except (OSError, subprocess.TimeoutExpired):
        raise p.Blocked('kubectl_' + args[0] + '_unavailable_or_timeout;_review_evidence')
    category = next((r for r in ('Forbidden', 'NotFound', 'AlreadyExists', 'Invalid', 'Conflict', 'Unauthorized', 'ServiceUnavailable')
                     if '(' + r + ')' in result.stderr), 'UNCLASSIFIED')
    p.require(result.returncode == 0, 'kubectl_{}_failed_exit_{}_category_{};_raw_output_suppressed'.format(args[0], result.returncode, category))
    return json.loads(result.stdout) if result.stdout.strip() else None


def named(kind, name):
    return api('get', kind, name, '--ignore-not-found', '-o', 'json', '--show-managed-fields=false')


def delete_options(obj, intent, expected_uid):
    meta = obj['metadata']
    p.require(meta['name'] == intent['name'] and meta.get('namespace') == p.NS
              and meta.get('labels') == {LABEL: intent['token']}
              and meta.get('annotations', {}).get(ANNOTATION) == intent['plan_sha256'], 'cleanup_ownership_mismatch')
    p.require(expected_uid is None or meta['uid'] == expected_uid, 'cleanup_uid_changed_do_not_delete')
    return {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': meta['uid']},
            'gracePeriodSeconds': 10, 'propagationPolicy': 'Background'}


def cleanup(intent, evidence):
    failures = []
    for kind, plural in (('service', 'services'), ('pod', 'pods')):
        try:
            receipt = evidence / (kind + '-created.json')
            expected = read(receipt)['uid'] if receipt.is_file() else None
            obj = named(kind, intent['name'])
            if obj is None: continue
            options = delete_options(obj, intent, expected)
            api('delete', '--raw', '/api/v1/namespaces/{}/{}/{}'.format(p.NS, plural, intent['name']), '-f', '-', data=options)
            end = time.monotonic() + 60
            while time.monotonic() < end:
                remaining = named(kind, intent['name'])
                if remaining is None: break
                p.require(remaining['metadata']['uid'] == obj['metadata']['uid'], 'cleanup_name_reused')
                time.sleep(2)
            else: raise p.Blocked('cleanup_termination_timeout')
            p.emit(temporary_object_cleanup='PASS', kind=kind, name=intent['name'])
        except (p.Blocked, OSError, ValueError, KeyError) as exc:
            failures.append(kind + ':' + (str(exc) if isinstance(exc, p.Blocked) else type(exc).__name__))
    p.require(not failures, 'cleanup_incomplete=' + ','.join(failures))


def wait_ready(expected, evidence):
    end = time.monotonic() + 600; last = 0
    while time.monotonic() < end:
        obj = named('pod', expected['metadata']['name'])
        p.require(obj is not None and not obj['metadata'].get('deletionTimestamp'), 'canary_disappeared_or_terminating')
        p.require(obj['metadata']['uid'] == read(evidence/'pod-created.json')['uid'], 'canary_uid_changed')
        check_admission(obj, expected)
        status = obj.get('status', {}); states = status.get('containerStatuses', [])
        for c in states:
            p.require(c.get('restartCount', 0) == 0 and not c.get('state', {}).get('terminated'), 'canary_exited_or_restarted')
            reason = c.get('state', {}).get('waiting', {}).get('reason')
            p.require(reason not in ('ErrImageNeverPull', 'ErrImagePull', 'ImagePullBackOff', 'CrashLoopBackOff', 'CreateContainerConfigError'), 'canary_start_failure=' + str(reason))
        p.require(status.get('phase') not in ('Failed', 'Succeeded'), 'canary_terminal_phase')
        if any(x.get('type') == 'Ready' and x.get('status') == 'True' for x in status.get('conditions', [])):
            p.require(obj['spec'].get('nodeName') == p.TARGET and len(states) == 1 and states[0].get('ready'), 'ready_identity_invalid')
            return obj
        if time.monotonic()-last >= 20:
            p.emit(canary_start='WAITING', phase=status.get('phase', 'Unknown'), node=obj['spec'].get('nodeName'), remaining_seconds=int(end-time.monotonic()))
            last = time.monotonic()
        time.sleep(3)
    raise p.Blocked('canary_ready_timeout_600s')


def wait_endpoint(name, uid):
    end = time.monotonic() + 45
    while time.monotonic() < end:
        slices = p.get('endpointslices', '-l', 'kubernetes.io/service-name=' + name)['items']
        ready = [e for s in slices for e in s.get('endpoints', []) if e.get('conditions', {}).get('ready') is True and not e.get('conditions', {}).get('terminating')]
        if len(ready) == 1 and ready[0].get('targetRef', {}).get('uid') == uid: return
        time.sleep(3)
    raise p.Blocked('temporary_service_endpoint_timeout')


def probe(pod, host, health=None):
    for attempt in range(1, 4):
        try:
            result = api('exec', pod, '-c', 'frontend', '--', 'node', '-e', HTTP_PROBE, host, '3000', *([health] if health else []))
            p.require(isinstance(result, dict) and result.get('target') == host, 'probe_missing_result')
            return result
        except p.Blocked:
            if attempt == 3: raise
            p.emit(http_probe='TRANSIENT_RETRY', target=host, attempt=attempt)
            time.sleep(3)


def execute(plan, preload, evidence):
    validate_preload(preload, plan)
    p.emit(canary_preflight='READING_LIVE_BASELINE', production_mutation='NONE')
    fresh = p.cluster(); compare(plan, fresh); empty_target(fresh)
    deployment = p.get('deployment', 'frontend')
    p.require(p.sha(deployment['spec']) == plan['frontend']['spec_sha256'], 'frontend_changed_before_canary')
    gateway = p.get('deployment', 'gateway')
    p.require(p.sha(gateway['spec']) == plan['protected_workloads']['gateway']['spec_sha256'], 'gateway_changed_before_canary')
    gc = [c for c in gateway['spec']['template']['spec']['containers'] if c['name'] == 'gateway']
    hg = gc[0].get('readinessProbe', {}).get('httpGet', {}) if len(gc) == 1 else {}
    p.require(hg.get('path') in ('/health', '/live', '/ready', '/api/health') and hg.get('port') == 8001, 'gateway_health_probe_requires_review')
    token = uuid.uuid4().hex[:16]
    intent = {'version': VERSION, 'name': 'cap-w1-fe-' + token, 'token': token,
              'cluster_uid': plan['cluster_uid'], 'plan_sha256': p.sha(plan)}
    pod, svc = manifests(deployment, intent)
    no_service_overlap(p.get('services')['items'], pod['metadata']['labels'])
    p.write_new(evidence/'intent.json', intent)
    p.write_new(evidence/'baseline.json', fresh)
    p.emit(isolated_canary='START', name=intent['name'], node=p.TARGET, evidence_dir=str(evidence))
    try:
        for obj in (pod, svc):
            dry = api('create', '--dry-run=server', '-f', '-', '-o', 'json', data=obj)
            if obj['kind'] == 'Pod': check_admission(dry, pod)
            else: check_service(dry, svc)
        # Nothing is sent to production Service selectors. Only these two names
        # are cleanup targets, even if create succeeds but the response is lost.
        for obj in (pod, svc):
            result = api('create', '-f', '-', '-o', 'json', data=obj)
            p.write_new(evidence/(obj['kind'].lower() + '-created.json'), {'uid': result['metadata']['uid'], 'name': intent['name']})
            if obj['kind'] == 'Pod': check_admission(result, pod)
            else: check_service(result, svc)
            p.emit(temporary_object='CREATED', kind=obj['kind'], name=intent['name'])
        ready = wait_ready(pod, evidence)
        p.require(p.digest(ready['status']['containerStatuses'][0]['imageID']) == plan['frontend']['image_id'], 'started_image_digest_mismatch')
        runtime = api('exec', intent['name'], '-c', 'frontend', '--', 'node', '-e', p.PROBE)
        p.require(runtime == plan['frontend']['next_runtime'], 'next_build_or_action_key_mismatch')
        no_service_overlap(p.get('services')['items'], ready['metadata']['labels'], own=intent['name'])
        wait_endpoint(intent['name'], ready['metadata']['uid'])
        checks = {'loopback': probe(intent['name'], '127.0.0.1'),
                  'target_to_production_frontend_and_gateway': probe(intent['name'], 'frontend.deer-flow.svc', hg['path']),
                  'production_frontend_to_target': probe(plan['frontend']['pod'], intent['name']+'.deer-flow.svc')}
        p.write_new(evidence/'checks.json', {'image_id': plan['frontend']['image_id'], 'next_runtime': runtime, 'http': checks})
        p.emit(canary_runtime='PASS', image_unpack_and_start='PASS', bidirectional_service_http='PASS')
    finally:
        cleanup(intent, evidence)
    after = p.cluster(); compare(fresh, after); empty_target(after)
    p.write_new(evidence/'after.json', after)
    return {'wave1_frontend_canary': 'PASS', 'node': p.TARGET, 'temporary_objects': 'CLEANED',
            'production_deployment_specs': 'UNCHANGED', 'node_cordon': 'UNCHANGED_TRUE',
            'rollout': 'NOT_PERFORMED', 'browser_acceptance': 'NOT_RUN', 'capacity_certification': 'NOT_PERFORMED',
            'next_step': 'REVIEW_BEFORE_FRONTEND_1_TO_2_EXPANSION', 'evidence_dir': str(evidence)}


def cleanup_existing(evidence):
    p.require(evidence.is_dir() and not evidence.is_symlink() and evidence.resolve().parent == EVIDENCE
              and evidence.name.startswith('capacity-wave1-canary-'), 'unsafe_cleanup_evidence_path')
    intent = read(evidence/'intent.json')
    p.require(intent.get('version') == VERSION and re.fullmatch(r'[0-9a-f]{16}', intent.get('token', ''))
              and intent.get('name') == 'cap-w1-fe-' + intent['token'], 'invalid_cleanup_intent')
    p.require(p.get('namespace', 'kube-system')['metadata']['uid'] == intent['cluster_uid'], 'cleanup_wrong_cluster')
    cleanup(intent, evidence)
    p.emit(canary_cleanup='PASS', evidence_dir=str(evidence), production_mutation='NONE')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path); parser.add_argument('--preload', type=Path)
    parser.add_argument('--ack'); parser.add_argument('--cleanup', type=Path)
    args = parser.parse_args()
    p.require(sys.platform.startswith('linux') and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'sudo_on_datax_01_required')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    import fcntl
    fd = os.open('/run/lock/deerflow-capacity-wave1-canary.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: raise p.Blocked('another_canary_operation_is_running')
    if args.cleanup:
        cleanup_existing(args.cleanup); return
    p.require(args.ack == 'FRONTEND_CANARY_ONLY' and args.plan and args.preload, 'plan_preload_and_explicit_ack_required')
    evidence = EVIDENCE / ('capacity-wave1-canary-' + datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8])
    evidence.mkdir(mode=0o700, parents=True)
    try:
        result = execute(read(args.plan), read(args.preload), evidence)
        p.write_new(evidence/'result.json', result); p.emit(**result)
    except (Exception, KeyboardInterrupt) as exc:
        result = {'wave1_frontend_canary': 'NOT_PASSED', 'reason': str(exc) if isinstance(exc, p.Blocked) else type(exc).__name__,
                  'evidence_dir': str(evidence), 'rollout': 'NOT_PERFORMED', 'instruction': 'send_output_do_not_uncordon_or_scale'}
        p.write_new(evidence/'result.json', result); p.emit(**result)
        sys.exit(1)


if __name__ == '__main__':
    try: main()
    except (p.Blocked, OSError, ValueError, KeyError) as exc:
        p.emit(wave1_frontend_canary='BLOCKED', reason=str(exc) if isinstance(exc, p.Blocked) else type(exc).__name__)
        sys.exit(1)
