#!/usr/bin/env python3
"""One-Skill project-type update. Requires accepted Round3 receipt and fresh unchanged runtime checks."""
import argparse
import base64
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time
import types
import uuid

BUNDLE = None
REPORT_ROOT = Path('/var/lib/portal-research-fund-skill-update-project-type-20261105-r1')
BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']
STARTED = 0
COMMANDS = 0
AUTH = {}


class Refused(Exception):
    pass


def check(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(value):
    return isinstance(value, str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}', value) is not None


def delete_options(uid):
    check(isinstance(uid, str) and re.fullmatch(r'[a-f0-9-]{36}', uid) is not None, 'DELETE_UID_INVALID')
    return canonical({'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}})


def authorize_command(args, input_bytes):
    """Keep mutations restricted to the exact locally constructed maintenance Pod."""
    check(isinstance(args, list) and args, 'COMMAND_INVALID')
    if args[0] == 'get':
        check(input_bytes is None and len(args) >= 4 and args[-2:] == ['-o', 'json'], 'GET_SHAPE')
        check(args[1] in ('deployment', 'replicaset', 'replicasets', 'pods', 'pod', 'pvc', 'pv', 'node'), 'GET_KIND')
        middle = args[2:-2]
        if len(middle) == 1:
            check(safe_name(middle[0]), 'GET_NAME')
        elif len(middle) == 2 and middle[0] == '-l':
            check(args[1] in ('pods', 'replicasets') and re.fullmatch(r'[A-Za-z0-9_./=-]+(?:,[A-Za-z0-9_./=-]+)*', middle[1]) is not None, 'GET_SELECTOR')
        elif len(middle) == 2 and middle[1] == '--ignore-not-found':
            check(args[1] == 'pod' and middle[0] == AUTH.get('name'), 'GET_CLEANUP_TARGET')
        else:
            raise Refused('GET_ARGUMENTS')
        return
    if args == ['create', '-f', '-', '-o', 'json']:
        check(AUTH.get('manifest') is not None and input_bytes == canonical(AUTH['manifest']), 'CREATE_SCOPE')
        check(not AUTH.get('create_dispatched'), 'CREATE_ALREADY_DISPATCHED')
        AUTH['create_dispatched'] = True
        return
    if args[0] == 'delete':
        check(AUTH.get('uid') and args == ['delete', '--raw=/api/v1/namespaces/deer-flow/pods/' + AUTH['name'], '-f', '-'], 'DELETE_SCOPE')
        check(input_bytes == delete_options(AUTH['uid']), 'DELETE_PRECONDITION')
        check(not AUTH.get('delete_dispatched'), 'DELETE_ALREADY_DISPATCHED')
        AUTH['delete_dispatched'] = True
        return
    if args[0] == 'exec':
        check(len(args) == 11 and args[1] == '-i' and safe_name(args[2]) and args[3] == '-c' and args[5:9] == ['--', 'python3', '-I', '-B'], 'EXEC_SHAPE')
        if args[4] in ('gateway', 'sandbox'):
            check(args[9:] == ['-', args[4]] and input_bytes == BUNDLE['readonly_child'].encode(), 'READONLY_EXEC_SOURCE')
        else:
            check(args[4] == 'maintenance' and args[2] == AUTH.get('name') and args[9:] == ['-c', BUNDLE['remote_source']], 'MAINTENANCE_EXEC_SCOPE')
            request = json.loads(input_bytes)
            check(canonical(request) == input_bytes and request == AUTH.get('remote_request'), 'MAINTENANCE_REQUEST_SCOPE')
        return
    raise Refused('COMMAND_NOT_ALLOWED')


def command(args, input_bytes=None, timeout=45, allow_failure=False):
    global COMMANDS
    check(time.monotonic() - STARTED < 900, 'HOST_DEADLINE')
    COMMANDS += 1
    check(COMMANDS <= 180, 'COMMAND_BUDGET')
    authorize_command(args, input_bytes)
    proc = subprocess.run(BASE + args, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    check(len(proc.stdout) <= 16777216 and len(proc.stderr) <= 65536, 'COMMAND_OUTPUT_BOUND')
    check(proc.returncode == 0 or allow_failure, 'KUBERNETES_COMMAND_NOT_ACCEPTED')
    if args[0] == 'delete':
        return {'delete_acknowledged': True}
    value = json.loads(proc.stdout) if proc.stdout.strip() else {}
    return (proc.returncode, value) if allow_failure else value


def library():
    module = types.ModuleType('reviewed_round3_readonly_inspector')
    exec(compile(BUNDLE['inspector_source'], '<reviewed-readonly-inspector>', 'exec'), module.__dict__)
    check(module.CHILD == BUNDLE['readonly_child'], 'READONLY_CHILD_BINDING')
    module.command = command
    return module


def pin_cluster(lib):
    deps = {name: lib.get('deployment', name) for name in BUNDLE['deployments']}
    for name, value in deps.items():
        check(lib.fingerprint(value) == BUNDLE['deployments'][name], 'DEPLOYMENT_BASELINE_CHANGED_' + name)
        if name in ('gateway', 'provisioner'):
            check(lib.converged(value), 'DEPLOYMENT_NOT_CONVERGED_' + name)
    pvc = lib.get('pvc', BUNDLE['pvc']['name'])
    expected_pvc = {k: v for k, v in BUNDLE['pvc'].items() if k != 'phase'}
    check(lib.fingerprint(pvc) == expected_pvc and pvc.get('status', {}).get('phase') == 'Bound' and 'ReadWriteMany' in pvc['spec'].get('accessModes', []), 'SKILLS_PVC_CHANGED')
    pv = lib.get('pv', BUNDLE['pv']['name'])
    check(lib.fingerprint(pv) == BUNDLE['pv'] and pvc['spec'].get('volumeName') == BUNDLE['pv']['name'] and pv['spec'].get('claimRef', {}).get('uid') == BUNDLE['pvc']['uid'], 'SKILLS_PV_CHANGED')
    for name in ('gateway', 'ppt-master-runner'):
        check(lib.mounts(deps[name]['spec']['template']['spec'], name, '/app/skills') == BUNDLE['skills_mounts'][name], 'SKILL_MOUNT_CHANGED_' + name)
    config = next(c for c in deps['provisioner']['spec']['template']['spec']['containers'] if c['name'] == 'provisioner')
    env = {e['name']: e.get('value') for e in config.get('env', [])}
    check(env.get('SKILLS_PVC_NAME') == BUNDLE['pvc']['name'] and env.get('SANDBOX_IMAGE') == BUNDLE['sandbox_image'], 'PROVISIONER_SKILL_CONFIG_CHANGED')
    lib.select_current_pods(deps['provisioner'], 'provisioner', {})
    frontend = lib.get('deployment', 'frontend')
    check(lib.fingerprint(frontend) == BUNDLE['frontend_observation']['deployment'], 'FRONTEND_BASELINE_CHANGED')
    check(lib.converged(frontend), 'FRONTEND_NOT_CONVERGED')
    return deps


def verify_consumers(lib, deployment, expected):
    pods = lib.select_current_pods(deployment, 'gateway', {})
    bynode, _ = lib.gateway_inventory(deployment, pods)
    check(sorted(bynode) == BUNDLE['gateway_nodes'], 'GATEWAY_NODE_COVERAGE_CHANGED')
    results = []
    for node, pod in sorted(bynode.items()):
        before_identity = lib.pod_identity(pod, 'gateway')
        value = lib.observe(pod, 'gateway', 'gateway')
        check(value.get('status') != 'INCOMPLETE', 'GATEWAY_READ_FAILED')
        check(value['public'].get('tree_sha256') == expected['tree_sha256'] and value['public'].get('files') == expected['files'], 'GATEWAY_SKILL_MANIFEST_CHANGED')
        check(not value['custom'].get('present'), 'CUSTOM_SKILL_SHADOW_PRESENT')
        check(value['public'].get('frontmatter_sha256') == BUNDLE['frontmatter_sha256'], 'SKILL_METADATA_CHANGED')
        check(value['runtime_sources'] == BUNDLE['runtime_sources'], 'GATEWAY_SKILL_LOADER_CHANGED')
        fresh = lib.get('pod', pod['metadata']['name'])
        check(lib.ready(fresh, 'gateway') and lib.pod_identity(fresh, 'gateway') == before_identity, 'GATEWAY_CHANGED_DURING_OBSERVATION')
        results.append({'node': node, 'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'tree_sha256': expected['tree_sha256'], 'status': 'PASS'})
    return results


def verify_after_publication(lib, deployment, expected):
    started = time.monotonic()
    while True:
        try:
            return verify_consumers(lib, deployment, expected)
        except Refused as exc:
            if str(exc) != 'GATEWAY_SKILL_MANIFEST_CHANGED' or time.monotonic() - started >= 45:
                raise
            time.sleep(3)


def sandbox_seed(lib):
    pods = lib.get('pods', selector='app=deer-flow-sandbox')['items']
    check(len(pods) <= 2000, 'SANDBOX_INVENTORY_BOUND')
    for pod in sorted(pods, key=lambda p: p['metadata']['name']):
        if not lib.ready(pod, 'sandbox'):
            continue
        if not any(m['kind'] == 'shared_public' and m['claim'] == BUNDLE['pvc']['name'] and m['read_only'] for m in lib.sandbox_mounts(pod['spec'])):
            continue
        container = next(c for c in pod['spec']['containers'] if c['name'] == 'sandbox')
        status = lib.cstatus(pod, 'sandbox')
        if container['image'] != BUNDLE['sandbox_image']:
            continue
        check(status['imageID'] in BUNDLE['sandbox_image_ids'], 'SANDBOX_CACHED_IMAGE_CHANGED')
        node = command(['get', 'node', pod['spec']['nodeName'], '-o', 'json'])
        check(any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in node.get('status', {}).get('conditions', [])), 'MAINTENANCE_NODE_NOT_READY')
        return {'image': container['image'], 'image_id': status['imageID'], 'node': node['metadata']['name'], 'node_uid': node['metadata']['uid'], 'source_pod_identity_sha256': sha(canonical(pod['metadata']['uid']))}
    raise Refused('READY_CURRENT_SHARED_SANDBOX_IMAGE_NOT_OBSERVED')


def maintenance_pod(name, operation, seed):
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': 'deer-flow', 'labels': {'app': 'research-fund-skill-maintenance', 'deerflow-release': operation}}, 'spec': {'nodeName': seed['node'], 'restartPolicy': 'Never', 'automountServiceAccountToken': False, 'enableServiceLinks': False, 'activeDeadlineSeconds': 1800, 'terminationGracePeriodSeconds': 10, 'containers': [{'name': 'maintenance', 'image': seed['image'], 'imagePullPolicy': 'Never', 'command': ['python3', '-I', '-B', '-c', 'import time; time.sleep(1800)'], 'securityContext': {'runAsUser': 0, 'allowPrivilegeEscalation': False, 'capabilities': {'drop': ['ALL']}}, 'resources': {'requests': {'cpu': '100m', 'memory': '128Mi'}, 'limits': {'cpu': '1', 'memory': '512Mi'}}, 'volumeMounts': [{'name': 'skills', 'mountPath': '/mnt/skills'}]}], 'volumes': [{'name': 'skills', 'persistentVolumeClaim': {'claimName': BUNDLE['pvc']['name']}}]}}


def resource_quantity(value, resource):
    check(isinstance(value, str) and len(value) < 32, 'RESOURCE_QUANTITY_INVALID')
    match = re.fullmatch(r'(\d+(?:\.\d+)?|\.\d+)(m|Ki|Mi|Gi|K|M|G)?', value)
    check(match is not None, 'RESOURCE_QUANTITY_INVALID')
    unit = match.group(2) or ''
    factors = {'': Decimal(1), 'm': Decimal('0.001')} if resource == 'cpu' else {'': Decimal(1), 'Ki': Decimal(1024), 'Mi': Decimal(1048576), 'Gi': Decimal(1073741824), 'K': Decimal(1000), 'M': Decimal(1000000), 'G': Decimal(1000000000)}
    check(unit in factors, 'RESOURCE_UNIT_INVALID')
    return Decimal(match.group(1)) * factors[unit]


def verify_maintenance(pod, uid, operation):
    meta = pod.get('metadata', {})
    check(meta.get('name') == AUTH['name'] and meta.get('namespace') == 'deer-flow' and meta.get('uid') == uid, 'MAINTENANCE_POD_IDENTITY_CHANGED')
    check(not meta.get('ownerReferences') and meta.get('labels', {}).get('app') == 'research-fund-skill-maintenance' and meta.get('labels', {}).get('deerflow-release') == operation, 'MAINTENANCE_POD_OWNER_CHANGED')
    observed = pod['spec']; expected = AUTH['manifest']['spec']
    for key in ('nodeName', 'restartPolicy', 'automountServiceAccountToken', 'enableServiceLinks', 'activeDeadlineSeconds', 'terminationGracePeriodSeconds'):
        check(observed.get(key) == expected[key], 'MAINTENANCE_POD_SPEC_CHANGED')
    check(not observed.get('initContainers') and not observed.get('ephemeralContainers') and not observed.get('hostNetwork') and not observed.get('hostPID') and not observed.get('hostIPC'), 'MAINTENANCE_POD_ADDED_ACCESS')
    check(not observed.get('hostAliases') and not observed.get('shareProcessNamespace'), 'MAINTENANCE_POD_HOST_ALIASES_OR_PROCESS_SHARING')
    pod_context = observed.get('securityContext', {})
    check(set(pod_context) <= {'seccompProfile'} and pod_context.get('seccompProfile', {}).get('type') in (None, 'RuntimeDefault'), 'MAINTENANCE_POD_PERMISSION_MUTATION')
    check(len(observed.get('containers', [])) == 1 and len(observed.get('volumes', [])) == 1, 'MAINTENANCE_POD_EXTRA_CONTAINER_OR_VOLUME')
    container = observed['containers'][0]
    for key, value in expected['containers'][0].items():
        if key in ('securityContext', 'resources', 'volumeMounts'):
            continue
        check(container.get(key) == value, 'MAINTENANCE_CONTAINER_CHANGED')
    check(not container.get('args') and not container.get('env') and not container.get('envFrom'), 'MAINTENANCE_CONTAINER_ENV_CHANGED')
    check(not any(container.get(k) for k in ('lifecycle', 'livenessProbe', 'readinessProbe', 'startupProbe', 'volumeDevices', 'workingDir', 'stdin', 'tty', 'ports')), 'MAINTENANCE_CONTAINER_ADDITIONAL_ACTION')
    context = container.get('securityContext', {})
    check(set(context) <= {'runAsUser', 'allowPrivilegeEscalation', 'capabilities', 'privileged', 'readOnlyRootFilesystem', 'runAsNonRoot', 'procMount', 'seccompProfile'} and context.get('seccompProfile', {}).get('type') in (None, 'RuntimeDefault'), 'MAINTENANCE_CONTAINER_PERMISSION_MUTATION')
    check(context.get('runAsUser') == 0 and context.get('allowPrivilegeEscalation') is False and not context.get('privileged') and not context.get('readOnlyRootFilesystem') and not context.get('runAsNonRoot'), 'MAINTENANCE_SECURITY_CONTEXT_CHANGED')
    check(context.get('capabilities', {}).get('drop') == ['ALL'] and not context.get('capabilities', {}).get('add'), 'MAINTENANCE_CAPABILITIES_CHANGED')
    check(context.get('procMount') in (None, 'Default'), 'MAINTENANCE_PROC_MOUNT_CHANGED')
    resources = container.get('resources', {})
    check(set(resources) <= {'requests', 'limits'}, 'MAINTENANCE_RESOURCES_CHANGED')
    for scope, amounts in expected['containers'][0]['resources'].items():
        check(set(resources.get(scope, {})) == set(amounts), 'MAINTENANCE_RESOURCE_KEYS_CHANGED')
        for resource, quantity in amounts.items():
            check(resource_quantity(resources[scope][resource], resource) == resource_quantity(quantity, resource), 'MAINTENANCE_RESOURCE_AMOUNT_CHANGED')
    mounts = container.get('volumeMounts', [])
    check(len(mounts) == 1 and mounts[0].get('name') == 'skills' and mounts[0].get('mountPath') == '/mnt/skills' and not mounts[0].get('readOnly') and not mounts[0].get('subPath') and not mounts[0].get('subPathExpr') and mounts[0].get('mountPropagation') in (None, 'None'), 'MAINTENANCE_MOUNT_CHANGED')
    volume = observed['volumes'][0]
    check(set(volume) == {'name', 'persistentVolumeClaim'}, 'MAINTENANCE_VOLUME_SOURCE_CHANGED')
    check(volume.get('name') == 'skills' and volume.get('persistentVolumeClaim', {}).get('claimName') == BUNDLE['pvc']['name'] and not volume.get('persistentVolumeClaim', {}).get('readOnly'), 'MAINTENANCE_SKILL_VOLUME_CHANGED')


def wait_pod(lib, uid, operation, seed):
    started = time.monotonic()
    while time.monotonic() - started < 180:
        pod = lib.get('pod', AUTH['name'])
        verify_maintenance(pod, uid, operation)
        check(not pod['metadata'].get('deletionTimestamp'), 'MAINTENANCE_TERMINATING')
        if lib.ready(pod, 'maintenance'):
            check(lib.cstatus(pod, 'maintenance')['imageID'] == seed['image_id'], 'CACHED_SANDBOX_IMAGE_CHANGED')
            AUTH['runtime_identity'] = lib.pod_identity(pod, 'maintenance')
            return
        check(pod.get('status', {}).get('phase') not in ('Failed', 'Succeeded'), 'MAINTENANCE_POD_FAILED')
        reason = lib.cstatus(pod, 'maintenance').get('state', {}).get('waiting', {}).get('reason')
        check(reason not in ('ErrImageNeverPull', 'ImagePullBackOff', 'CreateContainerConfigError', 'CreateContainerError', 'InvalidImageName'), 'MAINTENANCE_POD_NOT_STARTABLE')
        time.sleep(3)
    raise Refused('MAINTENANCE_POD_READY_TIMEOUT')


def remote(lib, mode, operation):
    pod = lib.get('pod', AUTH['name'])
    verify_maintenance(pod, AUTH['uid'], operation)
    check(lib.ready(pod, 'maintenance'), 'MAINTENANCE_NOT_READY')
    check(lib.pod_identity(pod, 'maintenance') == AUTH.get('runtime_identity'), 'MAINTENANCE_RUNTIME_CHANGED')
    request = {'mode': mode, 'operation': operation, 'before': BUNDLE['before'], 'after': BUNDLE['after']}
    if mode == 'stage':
        request.update(archive_b64=BUNDLE['archive_b64'], archive_sha256=BUNDLE['archive_sha256'])
    check(mode in ('stage', 'publish'), 'REMOTE_MODE')
    AUTH['remote_request'] = request
    return command(['exec', '-i', AUTH['name'], '-c', 'maintenance', '--', 'python3', '-I', '-B', '-c', BUNDLE['remote_source']], canonical(request), timeout=330, allow_failure=True)


def persist(report):
    temp = REPORT_ROOT / 'REPORT.next.json'
    with temp.open('xb') as handle:
        handle.write(canonical(report) + b'\n'); handle.flush(); os.fsync(handle.fileno())
    os.replace(str(temp), str(REPORT_ROOT / 'REPORT.json'))


def cleanup_maintenance(report):
    args = ['get', 'pod', AUTH['name'], '--ignore-not-found', '-o', 'json']
    pod = command(args)
    if pod:
        verify_maintenance(pod, AUTH['uid'], report['operation'])
        if not pod['metadata'].get('deletionTimestamp'):
            report.update(maintenance_pod_delete_requested=True, maintenance_pod_delete_may_have_applied=True, maintenance_pod_delete_request_count=1)
            persist(report)
            command(['delete', '--raw=/api/v1/namespaces/deer-flow/pods/' + AUTH['name'], '-f', '-'], delete_options(AUTH['uid']))
            report['maintenance_pod_delete_acknowledged'] = True
        started = time.monotonic()
        while time.monotonic() - started < 60:
            pod = command(args)
            if not pod:
                break
            verify_maintenance(pod, AUTH['uid'], report['operation'])
            time.sleep(3)
        check(not pod, 'MAINTENANCE_CLEANUP_ABSENCE_NOT_OBSERVED')
    report['maintenance_pod_absent'] = True


def classify_failure(report, exc):
    report.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'BOUNDED_OPERATION_NOT_ACCEPTED', exitcode=1)
    if report.get('runtime_node_views_accepted'):
        report['status'] = 'SKILL_UPDATED_RUNTIME_ACCEPTED_CLEANUP_PENDING'
    elif report.get('skill_updated'):
        report['status'] = 'SKILL_UPDATED_CONSUMER_ACCEPTANCE_PENDING'


def validate_bundle():
    check(isinstance(BUNDLE, dict) and BUNDLE.get('schema') == 'research-fund-project-type-sealed-bundle.v1', 'UNBOUND_TEMPLATE_NO_PRODUCTION_ACTION')
    check(BUNDLE.get('baseline_tool_revision') == 'round3-accepted-20261105' and BUNDLE.get('baseline_status') == 'RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED', 'ACCEPTED_ROUND3_RELEASE_REQUIRED')
    check(BUNDLE.get('deployments', {}).get('gateway', {}).get('generation', 0) >= 407, 'OLD_GATEWAY_BASELINE_REFUSED')
    check(BUNDLE['before']['tree_sha256'] == '0d935a5e5c45e8bf916b6b52ee310826d68acb22fffb0cbc70ab5d3304840a4c', 'DEPLOYED_NOV05_TREE_REQUIRED')
    check(BUNDLE['after']['tree_sha256'] == BUNDLE['approved_candidate_tree_sha256'], 'CANDIDATE_APPROVAL_CHANGED')
    check(BUNDLE['frontend_observation'].get('stable_readback') is True and BUNDLE['frontend_observation'].get('included_in_skill_update') is False, 'FRONTEND_OBSERVATION_REQUIRED')
    for key in ('before', 'after'):
        value = BUNDLE[key]
        check(sha(canonical(value['files'])) == value['tree_sha256'] and len(value['files']) == value['file_count'] and sum(r['bytes'] for r in value['files']) == value['bytes'], 'EMBEDDED_MANIFEST_INVALID')
    check(sha(base64.b64decode(BUNDLE['archive_b64'], validate=True)) == BUNDLE['archive_sha256'], 'EMBEDDED_ARCHIVE_INVALID')
    for key in ('inspector_source', 'remote_source', 'readonly_child'):
        check(sha(BUNDLE[key].encode()) == BUNDLE[key + '_sha256'], 'EMBEDDED_SOURCE_INVALID')



def verify_prior_release():
    """Read the immutable successful production receipt before claiming this operation."""
    path = Path(BUNDLE['prior_release']['path'])
    check(path.is_absolute() and all(not p.is_symlink() for p in (path,) + tuple(path.parents)), 'PRIOR_RECEIPT_SYMLINK')
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        check(stat.S_ISREG(before.st_mode) and before.st_size == BUNDLE['prior_release']['bytes'], 'PRIOR_RECEIPT_SIZE_CHANGED')
        with os.fdopen(fd, 'rb', closefd=False) as handle:
            raw = handle.read(BUNDLE['prior_release']['bytes'] + 1)
        after = os.fstat(fd)
        check((before.st_ino, before.st_dev, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_dev, after.st_size, after.st_mtime_ns), 'PRIOR_RECEIPT_CHANGED_DURING_READ')
    finally:
        os.close(fd)
    check(len(raw) == BUNDLE['prior_release']['bytes'] and sha(raw) == BUNDLE['prior_release']['sha256'], 'PRIOR_RECEIPT_DIGEST_CHANGED')
    receipt = json.loads(raw)
    check(receipt.get('status') == 'RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED' and receipt.get('exitcode') == 0 and receipt.get('maintenance_pod_absent') is True and receipt.get('runtime_node_views_accepted') is True, 'PRIOR_RELEASE_NOT_ACCEPTED')
    check(receipt.get('after_tree_sha256') == BUNDLE['before']['tree_sha256'], 'PRIOR_RELEASE_TREE_CHANGED')

def main():
    global STARTED
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--update-research-fund-skill-once', action='store_true', required=True)
    parser.parse_args(); STARTED = time.monotonic()
    report = {'schema': 'portal-research-fund-skill-update.v3', 'status': 'INCOMPLETE', 'once_claimed': False, 'automatic_retry': False, 'automatic_rollback': False, 'provider_requests': 0, 'production_database_access': False, 'production_workload_mutation': False, 'credentials_exported': False, 'scope': 'one_shared_public_skill_no_deployment_rollout', 'workload_patch_requests': 0, 'skill_switch_requested': False, 'skill_switch_may_have_applied': False, 'skill_updated': False, 'browser_business_acceptance_pending': True, 'maintenance_pod_delete_request_count': 0}
    try:
        validate_bundle()
        check(socket.gethostname().split('.')[0] == 'datax-01', 'WRONG_HOST')
        check(os.geteuid() == 0, 'ROOT_REQUIRED')
        verify_prior_release()
        check(not REPORT_ROOT.exists() and not REPORT_ROOT.is_symlink(), 'ONCE_REPORT_DIRECTORY_ALREADY_PRESENT')
        lib = library(); deps = pin_cluster(lib)
        report['before_consumers'] = verify_consumers(lib, deps['gateway'], BUNDLE['before'])
        seed = sandbox_seed(lib); operation = uuid.uuid4().hex; name = 'fund-skill-maint-' + operation[:12]
        AUTH.update(name=name, manifest=maintenance_pod(name, operation, seed))
        report.update(operation=operation, maintenance_pod=name, maintenance_seed=seed, before_tree_sha256=BUNDLE['before']['tree_sha256'], after_tree_sha256=BUNDLE['after']['tree_sha256'], accepted_production_baseline_sha256=BUNDLE['baseline_receipt_sha256'])
        REPORT_ROOT.mkdir(mode=0o700); report.update(once_claimed=True, maintenance_pod_create_requested=True); persist(report)
        created = command(['create', '-f', '-', '-o', 'json'], canonical(AUTH['manifest']))
        AUTH['uid'] = created['metadata']['uid']; report['maintenance_pod_uid'] = AUTH['uid']; persist(report)
        verify_maintenance(created, AUTH['uid'], operation)
        wait_pod(lib, AUTH['uid'], operation, seed)
        report['maintenance_runtime_identity'] = AUTH['runtime_identity']; persist(report)
        print(json.dumps({'phase': 'BACKUP_STAGE_AND_SYNTHETIC_REPORT_CHECKS'}), flush=True)
        code, observation = remote(lib, 'stage', operation); report['staging'] = observation; persist(report)
        check(code == 0 and observation.get('status') == 'STAGED_SYNTHETIC_ACCEPTED', 'STAGING_OR_SYNTHETIC_CHECK_NOT_ACCEPTED')
        pin_cluster(lib)
        report.update(skill_switch_requested=True, skill_switch_may_have_applied=True); persist(report)
        print(json.dumps({'phase': 'PUBLISH_RESEARCH_FUND_SKILL'}), flush=True)
        code, observation = remote(lib, 'publish', operation); report['publication'] = observation; persist(report)
        check(code == 0 and observation.get('status') == 'SKILL_PACKAGE_UPDATED', 'SKILL_PUBLICATION_NOT_ACCEPTED_SEE_JOURNAL')
        report.update(skill_updated=True, backup_path=observation['backup_path']); persist(report)
        report['after_consumers'] = verify_after_publication(lib, deps['gateway'], BUNDLE['after'])
        pin_cluster(lib); report.update(runtime_node_views_accepted=True, checked_deployments_unchanged=sorted(BUNDLE['deployments'])); persist(report)
        cleanup_maintenance(report)
        report.update(status='RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED', exitcode=0)
    except Exception as exc:
        classify_failure(report, exc)
    report.update(read_and_maintenance_commands=COMMANDS, elapsed_seconds=round(time.monotonic() - STARTED, 3), raw_errors_exported=False)
    if report['once_claimed']:
        try:
            persist(report); raw = (REPORT_ROOT / 'REPORT.json').read_bytes()
            report.update(report_path=str(REPORT_ROOT / 'REPORT.json'), report_bytes=len(raw), report_sha256=sha(raw))
        except Exception as exc:
            report.update(final_receipt_write_failed=True, final_receipt_error_class=type(exc).__name__, exitcode=1)
    print(json.dumps(report, sort_keys=True)); return report['exitcode']


if __name__ == '__main__':
    sys.exit(main())
