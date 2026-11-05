#!/usr/bin/env python3
"""Read-only, credential-free WeKnora GLM build inventory; Python 3.7+."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import socket
import stat
import subprocess
import sys
import time

POD = 'weknora-app-f6fb87c6d-gqtf5'
POD_UID = 'd64b460f-b27e-4a6f-afde-c5583b7d0455'
DEP_UID = '44aca5b9-8fc1-44d6-af61-37c6d62723d5'
BASE_IMAGE = 'deerflow.local/weknora-app:0.8.0-enterprise-proxy1-anydoc1-arm64-20260912'
BASE_CONFIG = 'sha256:93d6900e88edcd1b5f389c5756f849b34f5051d832a72b0bc17b0af86c709194'
BASE_BINARY = 'aa1c5bfc46c5d9b3e3a0ef4c9e487f999ac9b67772b446d16164c5dc621f0bfd'
BUILDER_IMAGE = 'm.daocloud.io/docker.io/library/golang@sha256:9566de04f98bfc5918e63d8d6f3d5cf47385e7e920e92bf40cf1299d3a0d968d'
LAST = {}


class CheckFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise CheckFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=True), flush=True)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def run(argv, timeout=30, limit=1048576):
    # argv is constructed exclusively by this script. No shell, no input,
    # no user-supplied path, credential, task, or model request.
    LAST.clear()
    LAST.update(program=Path(argv[0]).name)
    env = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf', GOTOOLCHAIN='local')
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, env=env)
    deadline = time.monotonic() + timeout
    data = {'stdout': bytearray(), 'stderr': bytearray()}
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(p.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(p.stderr, selectors.EVENT_READ, 'stderr')
            while selector.get_map():
                require(time.monotonic() < deadline, 'command_deadline')
                for key, _ in selector.select(.2):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        continue
                    data[key.data].extend(block)
                    require(len(data[key.data]) <= (limit if key.data == 'stdout' else 65536), 'command_output_limit')
        code = p.wait(timeout=max(.01, deadline-time.monotonic()))
        LAST.update(exit_code=code, stdout_bytes=len(data['stdout']), stderr_bytes=len(data['stderr']))
        require(code == 0, 'read_command_failed')
        return bytes(data['stdout'])
    finally:
        if p.poll() is None:
            p.kill()  # Only our diagnostic client, never a workload process.
        p.wait()
        p.stdout.close()
        p.stderr.close()


def kube(args, **kwargs):
    require(args[0] in ('get', 'exec'), 'read_only_kubernetes_required')
    return run(['kubectl', '--request-timeout=20s', '-n', 'deer-flow']+args, **kwargs)


def baseline():
    d = json.loads(kube(['get', 'deployment', 'weknora-app', '--show-managed-fields=false', '-o', 'json']))
    p = json.loads(kube(['get', 'pod', POD, '--show-managed-fields=false', '-o', 'json']))
    require(d['metadata']['uid'] == DEP_UID and d['spec']['replicas'] == 1, 'deployment_changed')
    require(p['metadata']['uid'] == POD_UID and not p['metadata'].get('deletionTimestamp'), 'recovered56_pod_changed')
    containers = p['spec']['containers']
    statuses = p['status'].get('containerStatuses', [])
    require(len(containers) == len(statuses) == 1, 'container_shape_changed')
    c, s = containers[0], statuses[0]
    require(c['name'] == s['name'] == 'app' and c['image'] == BASE_IMAGE and
            s.get('ready') is True and s.get('imageID') == BASE_CONFIG and
            s.get('state', {}).get('running') is not None, 'live_image_or_readiness_changed')
    return {'deployment_uid': DEP_UID, 'spec_sha256': digest(d['spec']),
            'generation': d['metadata']['generation'], 'pod': POD, 'pod_uid': POD_UID,
            'container_id': s.get('containerID'), 'restarts': s.get('restartCount'),
            'node': p['spec']['nodeName'], 'image': BASE_IMAGE, 'image_id': s['imageID']}


REMOTE_BINARY = r'''
import hashlib,json,os,stat,time
def identity():
 s=open('/proc/1/stat').read(8192).rsplit(')',1)[1].split()
 return (int(s[19]),os.readlink('/proc/1/exe'))
before=identity()
assert os.geteuid()==1000 and os.getegid()==1000 and before[1]=='/app/WeKnora'
h=hashlib.sha256(); n=0; deadline=time.monotonic()+40
with open('/proc/1/exe','rb',buffering=0) as f:
 s=os.fstat(f.fileno()); assert stat.S_ISREG(s.st_mode) and 0<s.st_size<1073741824
 while True:
  assert time.monotonic()<deadline
  b=f.read(1048576)
  if not b: break
  n+=len(b); assert n<=s.st_size; h.update(b)
 assert n==s.st_size
assert identity()==before
print(json.dumps({'sha256':h.hexdigest(),'bytes':n,'process_stable':True,'process_user':1000}))
'''


def storage(path):
    p = Path(path)
    if not p.exists():
        return {'path': path, 'exists': False}
    meta = p.lstat()
    unsafe = []
    for item in [p] + list(p.parents):
        s = item.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            unsafe.append(str(item))
    usage = os.statvfs(str(p))
    return {'path': path, 'exists': True, 'uid': meta.st_uid,
            'mode': oct(stat.S_IMODE(meta.st_mode)), 'unsafe_private_ancestors': unsafe,
            'available_bytes': usage.f_bavail * usage.f_frsize, 'free_inodes': usage.f_favail,
            'read_only': bool(usage.f_flag & os.ST_RDONLY)}


def collect():
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'run_as_root_on_datax01')
    emit({'weknora_glm_build_inventory': 'RUNNING', 'production_writes': 0, 'provider_requests': 0})
    before = baseline()
    emit({'live_baseline': before})
    binary = json.loads(kube(['exec', POD, '-c', 'app', '--', 'gosu', '1000:1000',
                             'python3', '-I', '-B', '-c', REMOTE_BINARY], timeout=50, limit=4096))
    require(binary['sha256'] == BASE_BINARY and binary['process_stable'], 'binary_baseline_changed')
    emit({'live_binary': binary, 'raw_binary_exported': False})
    emit({'storage': [storage(p) for p in ('/', '/opt', '/var/lib/containerd')], 'permissions_changed': False})
    emit({'local_build_tools_present': {x: shutil.which(x) is not None for x in
          ('ctr', 'crictl', 'buildctl', 'buildkitd', 'runc', 'go', 'rustc', 'cargo', 'cc', 'pkg-config')},
          'toolchains_installed_or_started': False})
    images = run(['ctr', '-n', 'k8s.io', 'images', 'list', '-q'], limit=4194304).decode('utf-8').splitlines()
    emit({'local_image_inventory': {
        'production_base_present': BASE_IMAGE in images,
        'historical_builder_reference_present': BUILDER_IMAGE in images,
        'base_layers_rehashed': False, 'images_imported_or_pulled': False},
        'full_image_list_printed': False})
    if BASE_IMAGE in images and shutil.which('crictl'):
        raw = json.loads(run(['crictl', '--runtime-endpoint', 'unix:///run/containerd/containerd.sock',
                              'inspecti', BASE_IMAGE], limit=2097152))
        s = raw['status']
        require(s['id'] == BASE_CONFIG, 'local_base_config_mismatch')
        emit({'local_base': {'config_id': s['id'], 'size': s.get('size'),
                            'repo_digests': [x for x in s.get('repoDigests', []) if x.startswith('deerflow.local/weknora-app@sha256:')]},
              'image_environment_printed': False})
    require(baseline() == before, 'baseline_changed_during_inventory')
    emit({'weknora_glm_build_inventory': 'COMPLETE', 'production_writes': 0,
          'provider_requests': 0, 'signals_sent': 0, 'credentials_requested': False,
          'build_started': False, 'ready_for_build': False, 'ready_for_rollout': False,
          'next_gate': 'PIN_LINUX_ARM64_GO_CGO_ANYDOC_BUILD_INPUTS_AND_EXACT_RUNTIME_BASE'})


if __name__ == '__main__':
    try:
        collect()
    except Exception as exc:
        emit({'weknora_glm_build_inventory': 'INCOMPLETE', 'error_type': type(exc).__name__,
              'failed_check': str(exc) if type(exc) is CheckFailure else 'SAFE_DIAGNOSTIC_REQUIRED',
              'last_operation': LAST, 'raw_error_printed': False, 'automatic_retry': False,
              'production_writes': 0, 'provider_requests': 0, 'signals_sent': 0, 'build_started': False})
        sys.exit(1)
