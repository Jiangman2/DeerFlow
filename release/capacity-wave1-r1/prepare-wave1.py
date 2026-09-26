#!/usr/bin/env python3
"""Wave 1 offline FRONTEND preparation. No Kubernetes write operations.

Python 3.6+, standard library only. Reports contain no environment/Secret values.
Export/preload are explicit local container-runtime operations, NOT a rollout.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time

VERSION = '20260924-capacity-wave1-r1'
TARGET = 'ecs-zdz-pro-0015'
NS = 'deer-flow'
IMAGE = 'deerflow.local/deer-flow-frontend:20260923-enterprise-arm64-rc15-channel-pdf-preview-r2'
SOCKET = 'unix:///run/containerd/containerd.sock'
ARCHIVE = 'frontend.current.oci.tar'
PROBE = r'''
const fs = require('node:fs'), crypto = require('node:crypto');
const h = x => crypto.createHash('sha256').update(x).digest('hex');
const root = '/app/frontend/.next/';
const build = fs.readFileSync(root + 'BUILD_ID');
if (!build.toString().trim()) throw Error('missing BUILD_ID');
let out = {build_id_sha256:h(build), server_actions:'UNKNOWN'};
const p = root + 'server/server-reference-manifest.json';
if (fs.existsSync(p)) {
  const m = JSON.parse(fs.readFileSync(p, 'utf8'));
  out.server_actions = Object.keys(m.node || {}).length + Object.keys(m.edge || {}).length;
  out.compiled_actions_key_present = Boolean(m.encryptionKey);
  if (m.encryptionKey) out.compiled_actions_key_sha256 = h(m.encryptionKey);
}
out.runtime_actions_key_present = Boolean(process.env.NEXT_SERVER_ACTIONS_ENCRYPTION_KEY);
if (out.runtime_actions_key_present) out.runtime_actions_key_sha256 = h(process.env.NEXT_SERVER_ACTIONS_ENCRYPTION_KEY);
console.log(JSON.stringify(out));
'''


class Blocked(RuntimeError):
    pass


def require(ok, reason):
    if not ok:
        raise Blocked(reason)


def emit(**fields):
    print(json.dumps(fields, ensure_ascii=True, sort_keys=True), flush=True)


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def hash_file(path):
    h = hashlib.sha256()
    with open(str(path), 'rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def verify_archive(path, expected):
    """Check OCI names/platform/digests without extracting files or reading layers."""
    with tarfile.open(str(path), 'r:') as tar:
        names = tar.getnames()
        require(len(names) == len(set(names)), 'duplicate_tar_member_names')

        def small(name):
            member = tar.getmember(name)
            require(member.isfile() and 0 < member.size <= 2 * 1024**2, 'invalid_oci_metadata_member')
            return tar.extractfile(member).read()

        def blob(desc):
            ident = digest(desc['digest'])
            raw = small('blobs/sha256/' + ident.split(':')[1])
            require(len(raw) == desc['size'] and 'sha256:' + hashlib.sha256(raw).hexdigest() == ident, 'oci_metadata_digest_mismatch')
            return json.loads(raw.decode('utf-8'))

        index = json.loads(small('index.json').decode('utf-8'))
        refs = index.get('manifests', [])
        require(bool(refs), 'empty_oci_index')
        for ref in refs:
            annotations = ref.get('annotations', {})
            for key in ('io.containerd.image.name', 'org.opencontainers.image.ref.name'):
                if key in annotations:
                    require(annotations[key] in (IMAGE, IMAGE.rsplit(':', 1)[1]), 'archive_contains_unexpected_image_name')
        require(len(set(r['digest'] for r in refs)) == 1, 'archive_contains_multiple_root_images')
        descriptor = refs[0]; chain = [digest(descriptor['digest'])]
        manifest = blob(descriptor)
        for _ in range(4):
            if 'config' in manifest: break
            eligible = [r for r in manifest.get('manifests', []) if
                        r.get('platform', {}).get('os') == 'linux' and r.get('platform', {}).get('architecture') == 'arm64'
                        and r.get('platform', {}).get('variant', 'v8') in ('', 'v8')]
            unique = {r['digest']: r for r in eligible}
            require(len(unique) == 1, 'ambiguous_or_missing_linux_arm64_manifest')
            descriptor = next(iter(unique.values())); chain.append(digest(descriptor['digest']))
            manifest = blob(descriptor)
        require('config' in manifest and 'layers' in manifest, 'oci_manifest_not_found')
        config = blob(manifest['config']); chain.append(digest(manifest['config']['digest']))
        require(config.get('os') == 'linux' and config.get('architecture') == 'arm64', 'archive_wrong_platform')
        require(expected in chain, 'archive_not_bound_to_live_image_identity')
        return {'platform': 'linux/arm64', 'identities': chain}


def run(argv, timeout=60):
    # No shell, inherited stdin, remote scripts, credentials or raw stderr output.
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, universal_newlines=True)
    except OSError:
        raise Blocked('command_unavailable=' + Path(argv[0]).name)
    start = time.monotonic()
    while True:
        try:
            out, _err = proc.communicate(timeout=min(20, max(1, timeout - (time.monotonic() - start))))
            break
        except subprocess.TimeoutExpired:
            if time.monotonic() - start >= timeout:
                proc.kill()
                proc.communicate()
                raise Blocked('command_timeout=' + Path(argv[0]).name + '; do_not_retry_blindly')
            emit(command=Path(argv[0]).name, state='RUNNING', elapsed_seconds=int(time.monotonic() - start))
    require(proc.returncode == 0, 'command_failed={} exit={}; raw_output_suppressed'.format(Path(argv[0]).name, proc.returncode))
    return out


def kube(*args):
    require(bool(args) and args[0] in ('get', 'exec'), 'kubernetes_mutation_forbidden')
    require(not any(x.lower() in ('secret', 'secrets') for x in args), 'secret_reads_forbidden')
    if args[0] == 'exec':
        require(len(args) == 8 and args[2:] == ('-c', 'frontend', '--', 'node', '-e', PROBE), 'exec_not_allowlisted')
    return run(['kubectl', '--request-timeout=25s', '-n', NS] + list(args), 45)


def get(kind, *args):
    return json.loads(kube('get', kind, *args, '-o', 'json'))


def digest(value):
    match = re.search(r'(?:^|@|://)(sha256:[0-9a-f]{64})$', value or '')
    require(match is not None, 'image_digest_missing_or_invalid')
    return match.group(1)


def frontend_projection(obj):
    spec = obj['spec']; status = obj.get('status', {})
    require(spec.get('replicas') == 1, 'frontend_expected_one_replica')
    require(status.get('observedGeneration', 0) >= obj['metadata']['generation'], 'frontend_generation_not_observed')
    require(all(status.get(k, 0) == 1 for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas')), 'frontend_not_stable')
    ps = spec['template']['spec']; containers = ps.get('containers', [])
    require(len(containers) == 1 and containers[0].get('name') == 'frontend', 'frontend_container_shape_changed')
    c = containers[0]
    require(c.get('image') == IMAGE and c.get('imagePullPolicy') == 'Never', 'frontend_image_or_pull_policy_changed_requires_review')
    require(not ps.get('initContainers'), 'frontend_init_containers_require_review')
    require(not any(ps.get(x) for x in ('hostNetwork', 'hostPID', 'hostIPC')), 'frontend_host_features_require_review')
    require(not c.get('securityContext', {}).get('privileged'), 'frontend_privileged_requires_review')
    configmaps, secrets = set(), set()
    for v in ps.get('volumes', []):
        kinds = set(v) - {'name'}
        require(kinds.issubset({'projected', 'configMap', 'secret', 'emptyDir', 'downwardAPI'}), 'frontend_volume_requires_review')
        refs = v.get('projected', {}).get('sources', []) + [v]
        for ref in refs:
            if 'configMap' in ref: configmaps.add(ref['configMap']['name'])
            if 'secret' in ref: secrets.add(ref['secret'].get('name') or ref['secret'].get('secretName'))
    return {'uid': obj['metadata']['uid'], 'spec_sha256': sha(spec), 'image': IMAGE,
            'replicas': 1, 'resources': c.get('resources', {}),
            'configmaps': sorted(configmaps), 'secret_values': 'NOT_READ',
            'secret_mount_names': sorted(x for x in secrets if x),
            'env_names': sorted(x['name'] for x in c.get('env', [])),
            'env_from_sources': [{k: v.get('name') for k, v in e.items() if k in ('secretRef', 'configMapRef')}
                                 for e in c.get('envFrom', [])],
            'scheduling': {k: ps.get(k) for k in ('nodeSelector', 'affinity', 'tolerations', 'topologySpreadConstraints')}}


def node_projection(obj):
    meta = obj['metadata']; labels = meta.get('labels', {}); spec = obj.get('spec', {})
    require(meta['name'] == TARGET and labels.get('kubernetes.io/hostname') == TARGET, 'target_node_identity_changed')
    require(labels.get('kubernetes.io/arch') == 'arm64' and labels.get('kubernetes.io/os') == 'linux', 'target_architecture_not_linux_arm64')
    conditions = {c['type']: c['status'] for c in obj['status'].get('conditions', [])}
    require(conditions.get('Ready') == 'True' and all(conditions.get(k) == 'False' for k in ('MemoryPressure', 'DiskPressure', 'PIDPressure')), 'target_node_unhealthy_or_pressure_unknown')
    require(conditions.get('NetworkUnavailable') != 'True', 'target_network_unavailable')
    for t in spec.get('taints', []):
        require(t.get('key') == 'node.kubernetes.io/unschedulable' and t.get('effect') == 'NoSchedule', 'target_extra_taint_requires_review')
    require(not any(k.startswith('node-role.kubernetes.io/') for k in labels), 'target_has_node_role_requires_review')
    return {'uid': meta['uid'], 'cordoned': bool(spec.get('unschedulable')), 'conditions': conditions,
            'labels': labels, 'taints': spec.get('taints', []),
            'allocatable': obj['status']['allocatable'], 'addresses': obj['status'].get('addresses', [])}


def workload_stamp(name):
    obj = get('deployment', name)
    return {'uid': obj['metadata']['uid'], 'spec_sha256': sha(obj['spec']),
            'replicas': obj['spec'].get('replicas'),
            'ready': obj.get('status', {}).get('readyReplicas', 0),
            'images': {c['name']: c['image'] for c in obj['spec']['template']['spec']['containers']}}


def cluster():
    require(socket.gethostname().split('.')[0] == 'datax-01', 'run_cluster_on_datax_01')
    require(Path('/etc/kubernetes/admin.conf').is_file(), 'admin_kubeconfig_missing')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    before = {n: workload_stamp(n) for n in ('gateway', 'ppt-master-runner')}
    d = get('deployment', 'frontend'); front = frontend_projection(d)
    pods = get('pods', '-l', 'app=frontend')['items']
    require(len(pods) == 1 and not pods[0]['metadata'].get('deletionTimestamp'), 'frontend_pod_count_or_termination_changed')
    pod = pods[0]; ps = pod['status']
    require(any(c['type'] == 'Ready' and c['status'] == 'True' for c in ps.get('conditions', [])), 'frontend_pod_not_ready')
    owner = [r for r in pod['metadata'].get('ownerReferences', []) if r.get('controller')]
    require(len(owner) == 1 and owner[0]['kind'] == 'ReplicaSet', 'frontend_pod_owner_unexpected')
    rs = get('replicaset', owner[0]['name'])
    require(rs['metadata']['uid'] == owner[0]['uid'] and any(r.get('controller') and r['uid'] == front['uid'] for r in rs['metadata'].get('ownerReferences', [])), 'frontend_pod_not_owned_by_deployment')
    states = [s for s in ps.get('containerStatuses', []) if s['name'] == 'frontend']
    require(len(states) == 1 and states[0].get('ready'), 'frontend_container_not_ready')
    require(pod['spec']['containers'][0]['image'] == IMAGE, 'frontend_running_pod_image_changed')
    front.update(pod=pod['metadata']['name'], pod_uid=pod['metadata']['uid'],
                 source_node=pod['spec']['nodeName'], image_id=digest(states[0]['imageID']))
    require(front['source_node'] in ('datax-01', 'datax-02', 'datax-03'), 'frontend_source_node_requires_review')
    front['next_runtime'] = json.loads(kube('exec', front['pod'], '-c', 'frontend', '--', 'node', '-e', PROBE))
    svc = get('service', 'frontend')
    require(svc['spec'].get('selector') == {'app': 'frontend'} and svc['spec'].get('type', 'ClusterIP') == 'ClusterIP', 'frontend_service_shape_changed')
    slices = get('endpointslices', '-l', 'kubernetes.io/service-name=frontend')['items']
    ready = [e for s in slices for e in s.get('endpoints', []) if e.get('conditions', {}).get('ready') is True and not e.get('conditions', {}).get('terminating')]
    require(len(ready) == 1 and ready[0].get('targetRef', {}).get('uid') == front['pod_uid'], 'frontend_service_endpoint_not_exact')
    hpas = get('hpa')['items']
    require(not any(h['spec'].get('scaleTargetRef', {}).get('name') == 'frontend' for h in hpas), 'frontend_hpa_requires_review')
    target = node_projection(get('node', TARGET))
    target_pods = get('pods', '-A', '--field-selector=spec.nodeName=' + TARGET)['items']
    target['pods'] = [{'namespace': p['metadata']['namespace'], 'name': p['metadata']['name'],
                       'phase': p.get('status', {}).get('phase'),
                       'owners': [{'kind': r['kind'], 'name': r['name']} for r in p['metadata'].get('ownerReferences', [])],
                       'resources': [c.get('resources', {}) for c in p['spec']['containers']]}
                      for p in target_pods]
    require(front['spec_sha256'] == sha(get('deployment', 'frontend')['spec']), 'frontend_changed_during_read')
    after = {n: workload_stamp(n) for n in before}
    require(before == after, 'other_workload_changed_during_read_retry_later')
    return {'frontend': front, 'target_node': target,
            'cluster_uid': get('namespace', 'kube-system')['metadata']['uid'],
            'protected_workloads': before, 'service_spec_sha256': sha(svc['spec']),
            'next_step': 'REVIEW_REPORT_AND_NODE_CHECK_BEFORE_EXPORT',
            'rollout': 'NOT_IMPLEMENTED_IN_THIS_PACKAGE'}


def parse_root(text):
    top = text.split('[', 1)[0]
    match = re.search(r'''^root\s*=\s*(["'])(/[A-Za-z0-9_./-]+)\1\s*$''', top, re.M)
    require(match is not None, 'containerd_root_not_determined')
    return match.group(2)


def runtime_root():
    pid = run(['systemctl', 'show', 'containerd', '--property=MainPID', '--value']).strip()
    require(pid.isdigit() and int(pid) > 1, 'containerd_service_pid_unknown')
    args = Path('/proc/' + pid + '/cmdline').read_bytes().decode().strip('\0').split('\0')
    require(Path(args[0]).name == 'containerd', 'containerd_service_identity_unexpected')
    config, override = '/etc/containerd/config.toml', None
    for i, a in enumerate(args[1:], 1):
        if a in ('--config', '-c'): config = args[i + 1]
        if a.startswith('--config='): config = a.split('=', 1)[1]
        if a in ('--root', '-r'): override = args[i + 1]
        if a.startswith('--root='): override = a.split('=', 1)[1]
    text = run([args[0], '--config', config, 'config', 'dump'])
    root = override or parse_root(text)
    require(Path(root).is_absolute() and Path(root).is_dir(), 'containerd_root_invalid')
    return str(Path(root).resolve())


def disk(path):
    s = os.statvfs(str(path)); total = s.f_blocks * s.f_frsize
    used = (s.f_blocks - s.f_bfree) * s.f_frsize
    available = s.f_bavail * s.f_frsize
    return {'path': str(path), 'free_gib': round(available / 2**30, 2),
            'used_percent': round(100.0 * used / max(1, used + available), 2),
            'total_gib': round(total / 2**30, 2)}


def disk_gate(facts, minimum, maximum):
    require(facts['free_gib'] >= minimum and facts['used_percent'] <= maximum, 'insufficient_disk_headroom=' + facts.get('path', 'test'))


def active_operations():
    result = []
    for p in Path('/proc').glob('[0-9]*'):
        try:
            args = p.joinpath('cmdline').read_bytes().decode(errors='replace').split('\0')
            exe = Path(args[0]).name
            busy = (exe == 'ctr' and 'images' in args and any(x in args for x in ('import', 'export', 'pull', 'remove', 'rm')))
            busy = busy or (exe in ('buildctl', 'nerdctl', 'docker') and 'build' in args)
            busy = busy or (exe.startswith('python') and '/home/cnooc/server.py' in args)
            if busy: result.append({'pid': int(p.name), 'program': exe})
        except (OSError, IndexError):
            continue
    return result


def check_image(info, expected):
    status = info.get('status', {})
    require(IMAGE in status.get('repoTags', []), 'runtime_exact_image_tag_missing')
    identities = sorted(set(digest(v) for v in [status.get('id', '')] + status.get('repoDigests', [])))
    require(expected in identities, 'runtime_image_identity_mismatch')
    return identities


def inspect_image():
    return json.loads(run(['crictl', '--runtime-endpoint', SOCKET, '--image-endpoint', SOCKET,
                           'inspecti', IMAGE]))


def node_check():
    require(socket.gethostname().split('.')[0] == TARGET, 'node_check_only_on_' + TARGET)
    require(platform.machine() in ('aarch64', 'arm64'), 'node_architecture_mismatch')
    operations = active_operations()
    require(not operations, 'active_image_or_cpu_load_operation_pids=' + ','.join(str(p['pid']) for p in operations))
    root = runtime_root(); require(root.startswith('/mnt/data/'), 'review_containerd_root_not_under_data_disk')
    require(os.stat('/').st_dev != os.stat('/mnt/data').st_dev, 'data_disk_is_not_separate_from_root')
    fs = [disk('/'), disk('/mnt/data'), disk(root)]
    disk_gate(fs[0], 8, 80); disk_gate(fs[1], 40, 75); disk_gate(fs[2], 40, 75)
    require(run(['systemctl', 'is-active', 'containerd']).strip() == 'active', 'containerd_inactive')
    require(run(['systemctl', 'is-active', 'kubelet']).strip() == 'active', 'kubelet_inactive')
    mem = Path('/proc/meminfo').read_text()
    match = re.search(r'^MemAvailable:\s+(\d+) kB$', mem, re.M)
    require(match and int(match.group(1)) >= 4 * 1024**2, 'available_memory_below_4GiB_or_unknown')
    vmstat = run(['vmstat', '1', '3'], 10)
    # No args/process secrets, filesystem walks, du scan or image unpack here.
    return {'node': TARGET, 'containerd_root': root, 'filesystems': fs,
            'memory_available_gib': round(int(match.group(1)) / 1024**2, 2),
            'vmstat': vmstat, 'operations': operations,
            'local_preload_headroom': 'PASS', 'cluster_scheduling': 'NOT_CHANGED',
            'next_step': 'REVIEW_CLUSTER_REPORT_AND_NODE_REPORT'}


def validate_plan(plan):
    require(plan.get('version') == VERSION and plan.get('phase') == 'cluster' and plan.get('target') == TARGET, 'wrong_plan_version_or_target')
    age = time.time() - plan['collected_epoch']
    require(-60 <= age <= 21600, 'plan_older_than_6h_or_clock_skew_refresh_cluster')
    require(plan['frontend']['image'] == IMAGE, 'plan_image_changed')
    digest(plan['frontend']['image_id'])


def safe_stage(base):
    path = Path(base)
    require(path.is_dir() and not path.is_symlink(), 'create_stage_base_on_data_disk_first=' + base)
    require(path.resolve() == path, 'stage_base_symlink_forbidden')
    require(os.stat('/').st_dev != os.stat(str(path)).st_dev, 'stage_must_be_on_separate_data_disk')
    out = path / VERSION
    if out.exists():
        require(out.is_dir() and not out.is_symlink() and out.resolve() == out, 'unsafe_stage_path')
    else:
        out.mkdir(mode=0o750)
    return out


def supply(mode, plan, ack):
    validate_plan(plan)
    require(ack == 'FRONTEND_IMAGE_ONLY', 'explicit_ack_required')
    host = socket.gethostname().split('.')[0]
    require(host == (plan['frontend']['source_node'] if mode == 'export' else TARGET), 'wrong_image_operation_node')
    require(not active_operations(), 'active_image_or_cpu_load_operation')
    expected = plan['frontend']['image_id']
    if mode == 'export':
        check_image(inspect_image(), expected)
        stage = safe_stage('/opt/deerflow-build-artifacts')
        disk_gate(disk(stage), 20, 80)
        archive = stage / ARCHIVE
        require(not archive.exists() and not archive.is_symlink(), 'archive_exists_do_not_overwrite')
        part = stage / (ARCHIVE + '.partial')
        require(not part.exists() and not part.is_symlink(), 'partial_archive_exists_review_previous_attempt')
        emit(image_export='START', path=str(part), image=IMAGE)
        run(['ionice', '-c', '2', '-n', '7', 'nice', '-n', '10', 'ctr', '-n', 'k8s.io',
             'images', 'export', '--platform', 'linux/arm64', str(part), IMAGE], 1800)
        check_image(inspect_image(), expected)
        require(part.is_file() and part.stat().st_size > 0, 'empty_export')
        verify_archive(part, expected)
        emit(archive_checksum='RUNNING')
        checksum = hash_file(part)
        # No overwriting: hard link publishes the complete file, then removes only
        # this command's own temporary link (not an image, cache or user archive).
        os.link(str(part), str(archive)); part.unlink()
        write_new(stage / 'image-receipt.json', {'version': VERSION, 'image': IMAGE,
                  'image_id': expected, 'archive': ARCHIVE, 'sha256': checksum,
                  'size': archive.stat().st_size, 'plan_sha256': sha(plan)})
        return {'image_export': 'PASS', 'artifact_dir': str(stage), 'sha256': checksum,
                'image_import': 'NOT_PERFORMED', 'production_mutation': 'NONE'}
    facts = node_check()
    stage = safe_stage('/mnt/data/deerflow-image-stage')
    receipt_path = stage / 'image-receipt.json'; archive = stage / ARCHIVE
    require(receipt_path.is_file() and not receipt_path.is_symlink() and archive.is_file() and not archive.is_symlink(), 'staged_files_missing_or_symlink')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    require(receipt.get('version') == VERSION and receipt.get('image') == IMAGE and receipt.get('image_id') == expected
            and receipt.get('archive') == ARCHIVE and receipt.get('plan_sha256') == sha(plan), 'receipt_plan_identity_mismatch')
    require(receipt.get('size') == archive.stat().st_size, 'archive_size_mismatch')
    emit(archive_checksum='RUNNING')
    require(hash_file(archive) == receipt['sha256'], 'archive_checksum_mismatch')
    verify_archive(archive, expected)
    # Never overwrite a different tag target. Exit-code failures aren't interpreted
    # as absence; the successful image listing determines exact presence first.
    refs = run(['ctr', '-n', 'k8s.io', 'images', 'list', '-q']).splitlines()
    if IMAGE in refs:
        check_image(inspect_image(), expected)
        emit(target_image='ALREADY_PRESENT_EXACT')
    else:
        emit(image_import='START_NO_UNPACK', image=IMAGE)
        run(['ionice', '-c', '2', '-n', '7', 'nice', '-n', '10', 'ctr', '-n', 'k8s.io',
             'images', 'import', '--no-unpack', '--platform', 'linux/arm64',
             '--label', 'io.cri-containerd.pinned=pinned', str(archive)], 1800)
    run(['ctr', '-n', 'k8s.io', 'images', 'label', IMAGE,
         'io.cri-containerd.image=managed', 'io.cri-containerd.pinned=pinned'])
    check_image(inspect_image(), expected)
    return {'image_preload': 'PASS', 'image': IMAGE, 'image_id': expected,
            'containerd_root': facts['containerd_root'], 'unpack_and_pod_start': 'NOT_TESTED',
            'next_step': 'ISOLATED_CANARY_THEN_REVIEW_BEFORE_PRODUCTION_EXPANSION'}


def write_new(path, value):
    # Reports are new, private, regular files only; never replace/follow a link.
    path = Path(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
        stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    if hasattr(os, 'chown') and os.environ.get('SUDO_UID', '').isdigit() and os.environ.get('SUDO_GID', '').isdigit():
        os.chown(str(path), int(os.environ['SUDO_UID']), int(os.environ['SUDO_GID']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('cluster', 'node', 'export', 'preload'))
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--ack')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(platform.system() == 'Linux' and os.geteuid() == 0, 'run_with_sudo_on_linux')
    require(not args.output.exists() and not args.output.is_symlink(), 'output_exists_do_not_overwrite')
    if args.phase == 'cluster': result = cluster()
    elif args.phase == 'node': result = node_check()
    else:
        require(args.plan is not None, '--plan_required')
        plan = json.loads(args.plan.read_text(encoding='utf-8'))
        result = supply(args.phase, plan, args.ack)
    result.update(version=VERSION, phase=args.phase, target=TARGET, collected_epoch=time.time(),
                  kubernetes_mutation='NONE', service_restart='NONE', rollout='NOT_PERFORMED',
                  capacity_certification='NOT_PERFORMED')
    write_new(args.output, result)
    emit(wave1_preparation='PASS', phase=args.phase, report=str(args.output),
         kubernetes_mutation='NONE', rollout='NOT_PERFORMED')


if __name__ == '__main__':
    try:
        main()
    except (Blocked, OSError, ValueError, KeyError, IndexError, tarfile.TarError) as exc:
        # Don't print arbitrary data/parser errors that may contain private values.
        emit(wave1_preparation='BLOCKED', reason=str(exc) if isinstance(exc, Blocked) else type(exc).__name__,
             automatic_cleanup='NONE', instruction='send_output_do_not_force_or_retry_blindly')
        sys.exit(1)
