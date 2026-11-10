#!/usr/bin/env python3
"""Scoped SkillHub server activation bound to the accepted preload receipt.

One image-only CAS update of the single-replica SkillHub server. Its unchanged
Recreate strategy causes a brief SkillHub outage. Normal application startup
uses its existing database and Redis; no explicit SQL, repair, schema change,
configuration update, automatic retry or automatic rollback is requested.
Python 3.7+. This file must undergo review before operator handoff.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

NS = 'deer-flow'
SERVER = 'skillhub-server'
NODE = 'datax-01'
NODE_UID = 'ec5698b0-8d82-4eed-bae0-3711c7b7f36c'
BASELINE = {'uid': '009babb5-b084-4397-a5ee-56a4b4b91032', 'generation': 14,
            'spec_sha256': 'dc58fef71aaf1f8dc3b1dcc5b12ab6d376ed25208cf58fd3c511dcd7f4685b6f'}
OLD = 'deerflow.local/skillhub-server:20260908-enterprise-arm64-rc15-skillhubsession2'
OLD_ROOT = 'sha256:71a60be927744fca0c78c922f2aa4083283347cc4b17df60dde50263f20076e2'
OLD_MANIFEST = 'sha256:b4a18e1955e51ba8fb7ec65df91ee081378ba4f84a63177a850f25e82fd9971a'
OLD_CONFIG = 'sha256:85ccab985d39d521b640b3d364dee3350c246eb022ccc0ba29bb7c32564f512f'
OLD_JAR = '6fc23f8056b763a1590a9953a83e6bee873e7bb2e935b2dddf93589585500363'
MANIFEST = 'sha256:4f3118867605fea38508bbc38014c731155b43296771c2cd61fb53d82f43bb68'
CONFIG = 'sha256:a879b4980c86299373079e63fbda8df76bb62873aea32e4a9021fa50ca094db7'
JAR = '342d0e02914d790a1c01a845cce4047af8b634fd34ecef0b01a66d5425fe7e3f'
NEW = 'deerflow.local/skillhub-server@' + MANIFEST
TAG = 'deerflow.local/skillhub-server:20261106-registry-auth-r1'
SELECTOR = 'app.kubernetes.io/component=server,app.kubernetes.io/name=skillhub'
PROTECTED = ('frontend', 'gateway', 'provisioner', 'ppt-master-runner', 'nginx',
             'nginx-security-heai-20261012-r1', 'skillhub-web')
PRELOAD_PATH = '/var/lib/portal-skillhub-registry-auth-preload-20261106-r1/REPORT.json'
# Operator-supplied receipt; full fixed-path bytes/hash are revalidated on datax-01.
PRELOAD_BINDING = {'bytes': 62575,
    'sha256': '1d1e580d2c5846d5e9b16f6a449c782fcc93409ddd024d21d7a9fb7c1f2f144d',
    'operation': '796641e830e84b05bf098d1ba309300d'}
CONTROL = '/var/lib/portal-skillhub-registry-auth-activation-20261106-r1'
SAFE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
SOCKET = '/run/containerd/containerd.sock'
LIMIT = 4 * 1024 * 1024
TOTAL_SECONDS = 900


class Refused(Exception):
    pass


def check(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parse(raw):
    def pairs(rows):
        result = {}
        for key, value in rows:
            check(key not in result, 'DUPLICATE_JSON_KEY')
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(Refused('NONFINITE_JSON')))
    check(isinstance(value, dict), 'JSON_OBJECT_REQUIRED')
    return value


def binding(value):
    return {'uid': value['metadata']['uid'], 'generation': value['metadata'].get('generation'),
            'spec_sha256': sha(canonical(value['spec']))}


def converged(value):
    s, t = value['spec'], value.get('status', {})
    n = s.get('replicas', 1)
    return (not s.get('paused', False) and n > 0
            and t.get('observedGeneration') == value['metadata']['generation']
            and all(t.get(k, 0) == n for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and t.get('terminatingReplicas', 0) == 0)


def validate_server(value, expected=BASELINE, image=OLD, require_ready=True):
    m, s = value.get('metadata', {}), value.get('spec', {})
    check(m.get('name') == SERVER and m.get('namespace') == NS and binding(value) == expected,
          'SERVER_BASELINE_CHANGED')
    check(s.get('replicas') == 1 and s.get('strategy') == {'type': 'Recreate'}, 'SERVER_STRATEGY_CHANGED')
    p = s.get('template', {}).get('spec', {})
    cs = p.get('containers', [])
    check(len(cs) == 1 and cs[0].get('name') == 'server' and cs[0].get('image') == image
          and cs[0].get('imagePullPolicy') == 'Never', 'SERVER_CONTAINER_CHANGED')
    flags = [x for x in cs[0].get('env', []) if x.get('name') == 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED']
    check(flags == [{'name': 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED', 'value': 'true'}], 'NATIVE_AUTH_NOT_EXPLICIT')
    check(p.get('nodeSelector') == {'kubernetes.io/hostname': NODE}
          and p.get('nodeName') in (None, NODE), 'SERVER_NODE_BINDING_CHANGED')
    if require_ready:
        check(converged(value), 'SERVER_NOT_CONVERGED')
    return value


def validate_node(value):
    m, s, t = value['metadata'], value.get('spec', {}), value.get('status', {})
    check(m.get('name') == NODE and m.get('uid') == NODE_UID and not s.get('unschedulable'), 'NODE_IDENTITY_OR_CORDON')
    info = t.get('nodeInfo', {})
    check(info.get('architecture') == 'arm64' and info.get('operatingSystem') == 'linux', 'NODE_PLATFORM')
    conditions = {r['type']: r['status'] for r in t.get('conditions', [])}
    check(conditions.get('Ready') == 'True'
          and all(conditions.get(k) == 'False' for k in ('DiskPressure', 'MemoryPressure', 'PIDPressure')),
          'NODE_UNHEALTHY')


def receipt_gate(value, seal):
    check(isinstance(seal, dict) and set(seal) == {'bytes', 'sha256', 'operation'}
          and type(seal.get('bytes')) is int and 0 < seal['bytes'] <= LIMIT
          and re.fullmatch('[0-9a-f]{64}', seal.get('sha256', ''))
          and re.fullmatch('[0-9a-f]{32}', seal.get('operation', '')), 'PRELOAD_RECEIPT_NOT_SEALED')
    check(value.get('schema') == 'portal-skillhub-registry-auth-cache-preload.v1'
          and value.get('operation') == seal['operation'] and value.get('hostname') == NODE
          and value.get('expected_node_uid') == NODE_UID and value.get('manifest_digest') == MANIFEST
          and value.get('config_digest') == CONFIG, 'PRELOAD_IDENTITY_CHANGED')
    check(value.get('status') == 'SKILLHUB_AUTH_CACHE_PRELOADED_NOT_DEPLOYED'
          and value.get('error_code') is None
          and value.get('preclaim', {}).get('pass') is True and value.get('phase_a') == 'PASS'
          and value.get('phase_b') == 'PASS' and value.get('content_and_unpack') == 'PASS'
          and value.get('cri_visibility') == 'PASS' and value.get('old_references_preserved') is True
          and value.get('production_workload_mutation') is False, 'PRELOAD_NOT_ACCEPTED')


def trusted_dir(path):
    check(path.startswith('/') and os.path.normpath(path) == path, 'DIRECTORY_PATH')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.strip('/').split('/'):
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
            st = os.fstat(fd)
            check(st.st_uid == 0 and not st.st_mode & 0o022, 'UNTRUSTED_DIRECTORY')
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_preload():
    # Refuse an unprepared build before any filesystem or Kubernetes side effect.
    seal = PRELOAD_BINDING
    check(isinstance(seal, dict), 'PRELOAD_RECEIPT_NOT_SEALED')
    parent = trusted_dir(os.path.dirname(PRELOAD_PATH))
    fd = None
    try:
        fd = os.open('REPORT.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        st = os.fstat(fd)
        check(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1
              and not st.st_mode & 0o022 and st.st_size == seal['bytes'], 'PRELOAD_FILE_GUARD')
        raw = os.read(fd, LIMIT + 1)
        after = os.fstat(fd)
        check((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
              == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
              and len(raw) == seal['bytes'] and sha(raw) == seal['sha256'], 'PRELOAD_BYTES_CHANGED')
        value = parse(raw)
        receipt_gate(value, seal)
        return value
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def make_patch(before):
    validate_server(before)
    m = before['metadata']
    check(isinstance(m.get('resourceVersion'), str) and m['resourceVersion'], 'RESOURCE_VERSION_MISSING')
    expected = copy.deepcopy(before)
    expected['spec']['template']['spec']['containers'][0]['image'] = NEW
    expected['metadata']['generation'] += 1
    operations = [
        {'op': 'test', 'path': '/metadata/uid', 'value': m['uid']},
        {'op': 'test', 'path': '/metadata/resourceVersion', 'value': m['resourceVersion']},
        {'op': 'test', 'path': '/metadata/generation', 'value': m['generation']},
        {'op': 'test', 'path': '/spec', 'value': copy.deepcopy(before['spec'])},
        {'op': 'replace', 'path': '/spec/template/spec/containers/0/image', 'value': NEW},
    ]
    return operations, binding(expected)


def check_pod_manifest(operation):
    check(re.fullmatch('[0-9a-f]{32}', operation), 'OPERATION_SHAPE')
    return {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': 'skillhub-auth-check-' + operation[:16],
        'namespace': NS, 'labels': {'app': 'skillhub-auth-image-check', 'deerflow-release': operation}},
        'spec': {'nodeName': NODE, 'automountServiceAccountToken': False, 'enableServiceLinks': False, 'restartPolicy': 'Never',
            'activeDeadlineSeconds': 120, 'terminationGracePeriodSeconds': 5,
            'securityContext': {'runAsNonRoot': True, 'runAsUser': 100, 'runAsGroup': 101,
                                'seccompProfile': {'type': 'RuntimeDefault'}},
            'containers': [{'name': 'image-check', 'image': NEW, 'imagePullPolicy': 'Never',
                'command': ['/bin/sh', '-c'], 'args': ['exec sha256sum /app/app.jar'],
                'securityContext': {'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                    'capabilities': {'drop': ['ALL']}},
                'resources': {'requests': {'cpu': '100m', 'memory': '32Mi'},
                              'limits': {'cpu': '500m', 'memory': '128Mi'}}}]}}


def validate_check_pod(pod, desired, uid):
    m, p = pod['metadata'], pod['spec']
    check(m.get('name') == desired['metadata']['name'] and m.get('namespace') == NS
          and m.get('uid') == uid and m.get('labels', {}).get('deerflow-release') == desired['metadata']['labels']['deerflow-release']
          and not m.get('ownerReferences'), 'CHECK_POD_IDENTITY_CHANGED')
    check(p.get('nodeName') == NODE and p.get('automountServiceAccountToken') is False
          and p.get('enableServiceLinks') is False and p.get('restartPolicy') == 'Never'
          and p.get('activeDeadlineSeconds') == 120 and p.get('terminationGracePeriodSeconds') == 5
          and not p.get('volumes') and not p.get('initContainers') and not p.get('ephemeralContainers')
          and not p.get('hostNetwork') and not p.get('hostPID') and not p.get('hostIPC')
          and p.get('securityContext') == desired['spec']['securityContext'], 'CHECK_POD_EXTRA_ACCESS')
    cs = p.get('containers', [])
    check(len(cs) == 1 and all(cs[0].get(k) == v for k, v in desired['spec']['containers'][0].items())
          and not cs[0].get('env') and not cs[0].get('envFrom') and not cs[0].get('volumeMounts'), 'CHECK_POD_CONTAINER_CHANGED')


def delete_options(uid):
    check(isinstance(uid, str) and re.fullmatch('[0-9a-f-]{36}', uid), 'DELETE_UID_REQUIRED')
    return {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {'uid': uid}}


def endpoint_gate(slices, pod):
    found = set()
    for row in slices.get('items', []):
        check(row.get('metadata', {}).get('labels', {}).get('kubernetes.io/service-name') == SERVER,
              'ENDPOINT_SERVICE_CHANGED')
        for endpoint in row.get('endpoints', []):
            c = endpoint.get('conditions', {})
            check(c.get('ready') is True and c.get('terminating', False) is False, 'ENDPOINT_NOT_READY')
            ref = endpoint.get('targetRef', {})
            check(ref.get('kind') == 'Pod' and ref.get('namespace') == NS
                  and ref.get('name') == pod['metadata']['name'] and ref.get('uid') == pod['metadata']['uid'],
                  'ENDPOINT_NOT_CURRENT_POD')
            found.add(ref['uid'])
    check(found == {pod['metadata']['uid']}, 'CURRENT_ENDPOINT_MISSING')


def bounded(argv, timeout=30, payload=None, limit=LIMIT):
    result = {'out': bytearray(), 'err': bytearray()}
    too_big = threading.Event()
    def drain(pipe, key):
        try:
            while True:
                chunk = pipe.read(65536)
                if not chunk:
                    break
                room = max(0, limit - len(result[key]))
                result[key].extend(chunk[:room])
                if len(chunk) > room:
                    too_big.set()
        finally:
            pipe.close()
    p = subprocess.Popen(argv, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, start_new_session=True,
        env={'PATH': SAFE_PATH, 'HOME': '/root', 'LANG': 'C.UTF-8'})
    threads = [threading.Thread(target=drain, args=(p.stdout, 'out'), daemon=True),
               threading.Thread(target=drain, args=(p.stderr, 'err'), daemon=True)]
    for thread in threads:
        thread.start()
    try:
        if payload is not None:
            p.stdin.write(payload)
            p.stdin.close()
        start = time.monotonic()
        while p.poll() is None:
            check(not too_big.is_set(), 'COMMAND_OUTPUT_BOUND')
            check(time.monotonic() - start < timeout, 'COMMAND_TIMEOUT')
            time.sleep(0.05)
        for thread in threads:
            thread.join(timeout=1)
        check(not any(t.is_alive() for t in threads) and not too_big.is_set(), 'COMMAND_OUTPUT_BOUND')
        check(p.returncode == 0, 'COMMAND_FAILED')
        return bytes(result['out'])
    finally:
        if p.poll() is None:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        p.wait(timeout=3)


class Engine:
    def __init__(self):
        self.started = time.monotonic()
        self.fd = None
        self.operation = uuid.uuid4().hex
        self.report = {'schema': 'portal-skillhub-registry-auth-activation.v1', 'status': 'INCOMPLETE',
            'operation': self.operation, 'once_claimed': False, 'patch_request_count': 0,
            'patch_may_have_applied': False, 'automatic_retry': False, 'automatic_rollback': False,
            'normal_application_startup_database_and_redis_access_may_occur': False,
            'explicit_database_queries': 0, 'database_configuration_modified': False,
            'database_repair_requested': False, 'schema_change_requested': False,
            'provider_requests': 0, 'secret_api_reads': 0, 'raw_errors_exported': False,
            'credentials_exported': False, 'other_deployments_modified': False,
            'temporary_pod_create_requests': 0, 'temporary_pod_delete_requests': 0,
            'expected_brief_skillhub_outage': True, 'preserved_strategy': 'Recreate',
            'browser_business_acceptance_pending': True, 'nginx_edge_update_pending': True,
            'commands': 0}
        self.tools = {}

    def call(self, kind, args, payload=None, timeout=30, limit=LIMIT):
        check(self.report['commands'] < 240 and time.monotonic() - self.started < TOTAL_SECONDS,
              'COMMAND_OR_TOTAL_TIME_LIMIT')
        self.report['commands'] += 1
        self.report['last_step'] = kind + ':' + args[0]
        prefix = {'kubectl': ['--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', NS],
                  'ctr': ['--address', SOCKET, '--namespace', 'k8s.io'],
                  'crictl': ['--runtime-endpoint=unix://' + SOCKET]}[kind]
        return bounded([self.tools[kind]] + prefix + args, timeout=timeout, payload=payload, limit=limit)

    def get(self, kind, *args):
        return parse(self.call('kubectl', ['get', kind] + list(args) + ['-o', 'json']))

    def persist(self, name, value, replace=False):
        check(self.fd is not None and re.fullmatch(r'[A-Z][A-Za-z0-9.-]*\.json', name), 'PRIVATE_WRITE_SCOPE')
        dest = name + '.pending' if replace else name
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd)
        try:
            raw = canonical(value)
            check(len(raw) <= LIMIT, 'PRIVATE_WRITE_BOUND')
            view = memoryview(raw)
            while view:
                n = os.write(fd, view)
                check(n > 0, 'SHORT_PRIVATE_WRITE')
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if replace:
            os.replace(dest, name, src_dir_fd=self.fd, dst_dir_fd=self.fd)
        os.fsync(self.fd)

    def save(self):
        if self.fd is not None:
            self.persist('REPORT.json', self.report, replace=True)

    def claim(self, before, patch, preload):
        parent = trusted_dir('/var/lib')
        try:
            os.mkdir(os.path.basename(CONTROL), 0o700, dir_fd=parent)
            self.report['once_claimed'] = True
            os.fsync(parent)
            self.fd = os.open(os.path.basename(CONTROL), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        self.persist('BEFORE.private.json', before)
        self.persist('PATCH.private.json', patch)
        self.persist('PRELOAD.private.json', preload)
        self.save()

    def deployments(self):
        values = self.get('deployments', SERVER, *PROTECTED).get('items', [])
        names = [r.get('metadata', {}).get('name') for r in values]
        check(len(names) == len(set(names)) and set(names) == {SERVER}.union(PROTECTED), 'DEPLOYMENT_SET')
        return {r['metadata']['name']: r for r in values}

    def preserve(self, before):
        after = self.deployments()
        check(all(binding(before[k]) == binding(after[k]) for k in PROTECTED), 'UNRELATED_DEPLOYMENT_CHANGED')
        return after

    def cache(self):
        for ref, target, manifest, config, layers in ((OLD, OLD_ROOT, OLD_MANIFEST, OLD_CONFIG, 14),
                                                   (NEW, MANIFEST, MANIFEST, CONFIG, 15)):
            rows = self.call('ctr', ['images', 'list', 'name==' + ref]).decode().splitlines()
            check(len(rows) == 2 and rows[0].split()[:3] == ['REF', 'TYPE', 'DIGEST']
                  and rows[1].split()[:3:2] == [ref, target], 'CACHED_REFERENCE_CHANGED')
            raw = self.call('ctr', ['content', 'get', manifest], limit=262144)
            check('sha256:' + sha(raw) == manifest, 'CACHED_MANIFEST_HASH')
            m = parse(raw)
            check(m.get('schemaVersion') == 2 and m.get('config', {}).get('digest') == config
                  and len(m.get('layers', [])) == layers, 'CACHED_MANIFEST_SHAPE')
            raw = self.call('ctr', ['content', 'get', config], limit=262144)
            check('sha256:' + sha(raw) == config, 'CACHED_CONFIG_HASH')
            c = parse(raw)
            check(c.get('architecture') == 'arm64' and c.get('os') == 'linux', 'CACHED_PLATFORM')
            status = parse(self.call('crictl', ['inspecti', '--output=json', ref])).get('status', {})
            check(status.get('id') == config, 'CRI_CONFIG_IDENTITY')
            if ref == NEW:
                check(NEW in status.get('repoDigests', []) and TAG in status.get('repoTags', []), 'CRI_CANDIDATE_NAMES')
            else:
                check(OLD in status.get('repoTags', []), 'CRI_OLD_REFERENCE')
        self.report['fresh_cache_checks'] = 'PASS'

    def server_pod(self, deployment, image):
        pods = self.get('pods', '-l', SELECTOR).get('items', [])
        check(len(pods) == 1, 'ONE_SERVER_POD_REQUIRED')
        pod = pods[0]
        p, s, m = pod['spec'], pod.get('status', {}), pod['metadata']
        check(not m.get('deletionTimestamp') and p.get('nodeName') == NODE and s.get('phase') == 'Running', 'SERVER_POD_STATE')
        cs, sts = p.get('containers', []), s.get('containerStatuses', [])
        check(len(cs) == len(sts) == 1 and cs[0].get('name') == sts[0].get('name') == 'server'
              and cs[0].get('image') == image and cs[0].get('imagePullPolicy') == 'Never'
              and sts[0].get('ready') is True and sts[0].get('state', {}).get('running'), 'SERVER_POD_CONTAINER')
        owner = m.get('ownerReferences', [])
        check(len(owner) == 1 and owner[0].get('kind') == 'ReplicaSet' and owner[0].get('controller') is True,
              'SERVER_POD_OWNER')
        rs = self.get('replicaset', owner[0]['name'])
        check(rs['metadata']['uid'] == owner[0]['uid'] and rs['metadata'].get('ownerReferences') ==
              [{'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': SERVER,
                'uid': deployment['metadata']['uid'], 'controller': True, 'blockOwnerDeletion': True}], 'SERVER_RS_OWNER')
        wanted = {MANIFEST, CONFIG} if image == NEW else {OLD_ROOT, OLD_MANIFEST, OLD_CONFIG}
        check(sts[0].get('imageID', '').split('@')[-1].replace('containerd://', '') in wanted, 'SERVER_RUNTIME_IMAGE')
        return pod

    def jar(self, pod, expected):
        raw = self.call('kubectl', ['exec', pod['metadata']['name'], '-c', 'server', '--', '/bin/sh', '-c',
                                   'exec sha256sum /app/app.jar'], timeout=30, limit=4096)
        check(raw.decode().strip() == expected + '  /app/app.jar', 'RUNTIME_JAR_HASH')

    def installed_check(self):
        desired = check_pod_manifest(self.operation)
        name = desired['metadata']['name']
        self.persist('CHECK-POD.json', desired)
        self.report.update(temporary_pod=name, temporary_pod_create_requests=1, temporary_pod_may_exist=True)
        self.save()
        created = parse(self.call('kubectl', ['create', '-f', '-', '-o', 'json'], canonical(desired)))
        uid = created['metadata']['uid']
        self.report['temporary_pod_uid'] = uid
        self.save()
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            pod = self.get('pod', name)
            validate_check_pod(pod, desired, uid)
            phase = pod.get('status', {}).get('phase')
            check(phase != 'Failed', 'CANDIDATE_INSTALLED_CHECK_FAILED')
            if phase == 'Succeeded':
                sts = pod['status'].get('containerStatuses', [])
                check(len(sts) == 1 and sts[0].get('state', {}).get('terminated', {}).get('exitCode') == 0
                      and sts[0].get('restartCount') == 0
                      and sts[0].get('imageID', '').split('@')[-1].replace('containerd://', '') in {MANIFEST, CONFIG},
                      'CHECK_RUNTIME_IDENTITY')
                raw = self.call('kubectl', ['logs', name, '-c', 'image-check', '--limit-bytes=1024'], limit=2048)
                check(raw.decode().strip() == JAR + '  /app/app.jar', 'INSTALLED_CANDIDATE_JAR_HASH')
                self.report['installed_arm64_jar_check'] = 'PASS'
                break
            time.sleep(3)
        else:
            raise Refused('CANDIDATE_INSTALLED_CHECK_TIMEOUT')
        self.cleanup_check()
        check(self.report.get('temporary_pod_absent') is True, 'CHECK_POD_CLEANUP_REQUIRED_BEFORE_PATCH')

    def cleanup_check(self):
        if not self.report.get('temporary_pod_uid') or self.report.get('temporary_pod_delete_requests'):
            return
        name, uid = self.report['temporary_pod'], self.report['temporary_pod_uid']
        pod = self.get('pod', name)
        validate_check_pod(pod, check_pod_manifest(self.operation), uid)
        self.report.update(temporary_pod_delete_requests=1, temporary_pod_delete_may_have_applied=True)
        self.save()
        self.call('kubectl', ['delete', '--raw=/api/v1/namespaces/' + NS + '/pods/' + name, '-f', '-'],
                  canonical(delete_options(uid)))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            raw = self.call('kubectl', ['get', 'pod', name, '--ignore-not-found', '-o', 'json'])
            if not raw.strip():
                self.report.update(temporary_pod_absent=True, temporary_pod_may_exist=False)
                self.save()
                return
            check(parse(raw)['metadata']['uid'] == uid, 'CHECK_POD_REPLACED_DURING_CLEANUP')
            time.sleep(2)
        raise Refused('CHECK_POD_CLEANUP_TIMEOUT')

    def http(self, pod):
        rows = []
        for path, expected, bearer in (('/api/v1/health', 200, False), ('/api/web/labels', 401, False),
                ('/api/web/skills?page=0&size=1', 401, False), ('/api/web/labels', 401, True),
                ('/api/web/skills?page=0&size=1', 401, True)):
            args = ['exec', pod['metadata']['name'], '-c', 'server', '--', '/usr/bin/curl',
                    '--noproxy', '*', '--proto', '=http', '--connect-timeout', '2', '--max-time', '5',
                    '--max-filesize', '65536', '--silent', '--show-error', '--output', '/dev/null',
                    '--write-out', '%{http_code}', '--request', 'GET']
            if bearer:
                args += ['--header', 'Authorization: Bearer invalid-synthetic-registry-auth-verification']
            args += ['http://127.0.0.1:8080' + path]
            raw = self.call('kubectl', args, timeout=20, limit=4096)
            check(raw.decode() == str(expected), 'DIRECT_NATIVE_HTTP_STATUS')
            rows.append({'path': path, 'fabricated_bearer': bearer, 'status': expected})
        self.report['native_http_checks'] = rows

    def execute(self):
        check(sys.platform.startswith('linux') and socket.gethostname().split('.')[0] == NODE
              and hasattr(os, 'geteuid') and os.geteuid() == 0
              and sys.flags.isolated and sys.flags.dont_write_bytecode
              and os.uname().machine in ('aarch64', 'arm64'), 'ROOT_ON_DATAX01_REQUIRED')
        preload = read_preload()
        self.report['accepted_preload'] = dict(PRELOAD_BINDING, path=PRELOAD_PATH)
        check(not os.path.lexists(CONTROL), 'ONCE_EXISTS_REVIEW_REQUIRED')
        self.tools = {k: shutil.which(k, path=SAFE_PATH) for k in ('kubectl', 'ctr', 'crictl')}
        check(all(self.tools.values()), 'REQUIRED_TOOLS_MISSING')
        before = self.deployments()
        validate_server(before[SERVER])
        validate_node(self.get('node', NODE))
        service = self.get('service', SERVER)
        oldpod = self.server_pod(before[SERVER], OLD)
        self.jar(oldpod, OLD_JAR)
        self.cache()
        patch, after_binding = make_patch(before[SERVER])
        self.claim(before[SERVER], patch, preload)
        self.report.update(before=BASELINE, expected_after=after_binding,
                           protected_before={k: binding(before[k]) for k in PROTECTED})
        self.save()
        print(json.dumps({'phase': 'VERIFY_CACHED_ARM64_JAR_WITHOUT_APPLICATION_START'}), flush=True)
        self.installed_check()
        # Re-read immediately before the only mutation of a production Deployment.
        fresh = self.preserve(before)
        validate_server(fresh[SERVER])
        check(fresh[SERVER]['metadata']['resourceVersion'] == before[SERVER]['metadata']['resourceVersion'],
              'SERVER_RESOURCE_VERSION_CHANGED')
        validate_node(self.get('node', NODE))
        check(binding(self.get('service', SERVER)) == binding(service), 'SERVER_SERVICE_CHANGED')
        self.cache()
        read_preload()
        self.report.update(patch_request_count=1, patch_may_have_applied=True,
                           normal_application_startup_database_and_redis_access_may_occur=True)
        self.save()
        print(json.dumps({'phase': 'RECREATE_SINGLE_SKILLHUB_SERVER_IMAGE_ONLY', 'brief_skillhub_outage_expected': True}), flush=True)
        self.call('kubectl', ['patch', 'deployment', SERVER, '--type=json', '--patch-file',
                             CONTROL + '/PATCH.private.json', '-o', 'json'])
        self.report['patch_command_acknowledged'] = True
        self.save()
        deadline = time.monotonic() + 360
        while time.monotonic() < deadline:
            current = self.preserve(before)
            validate_server(current[SERVER], after_binding, NEW, require_ready=False)
            if converged(current[SERVER]):
                break
            time.sleep(4)
        else:
            raise Refused('SERVER_ROLLOUT_TIMEOUT_REVIEW_REQUIRED')
        pod = self.server_pod(current[SERVER], NEW)
        self.jar(pod, JAR)
        self.http(pod)
        endpoint_gate(self.get('endpointslices', '-l', 'kubernetes.io/service-name=' + SERVER), pod)
        check(binding(self.get('service', SERVER)) == binding(service), 'SERVER_SERVICE_CHANGED')
        validate_node(self.get('node', NODE))
        final = self.preserve(before)
        validate_server(final[SERVER], after_binding, NEW)
        stable = self.server_pod(final[SERVER], NEW)
        check(stable['metadata']['uid'] == pod['metadata']['uid']
              and stable['status']['containerStatuses'] == pod['status']['containerStatuses'], 'RUNTIME_CHANGED_DURING_PROBES')
        self.jar(stable, JAR)
        self.report.update(status='SKILLHUB_REGISTRY_AUTH_RUNTIME_ACCEPTED_EDGE_PENDING', after=after_binding,
            runtime={'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'node': NODE,
                     'jar_sha256': JAR, 'image': NEW}, service_endpoints_verified=True,
            unrelated_deployments_preserved=True, image_only_patch_verified=True, stable_readback=True)

    def run(self):
        try:
            self.execute()
        except BaseException as exc:
            self.report.update(error_class=type(exc).__name__,
                error_code=str(exc) if isinstance(exc, Refused) else 'LOCAL_FAILURE_OR_INTERRUPTED')
        finally:
            try:
                if self.fd is not None:
                    self.cleanup_check()
            except BaseException as exc:
                self.report.update(status='INCOMPLETE', cleanup_error_class=type(exc).__name__)
            self.report['elapsed_seconds'] = round(time.monotonic() - self.started, 3)
            try:
                self.save()
                if self.fd is not None:
                    raw = canonical(self.report)
                    self.report.update(report_path=CONTROL + '/REPORT.json', report_bytes=len(raw), report_sha256=sha(raw))
            except BaseException:
                self.report.update(status='INCOMPLETE', error_code='REPORT_PERSISTENCE_FAILED')
            if self.fd is not None:
                os.close(self.fd)
        print(json.dumps(self.report, sort_keys=True))
        return 0 if self.report['status'] == 'SKILLHUB_REGISTRY_AUTH_RUNTIME_ACCEPTED_EDGE_PENDING' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--activate-skillhub-registry-auth-once', action='store_true', required=True)
    parser.parse_args()
    sys.exit(Engine().run())
