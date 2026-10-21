#!/usr/bin/env python3
"""Narrow read-only follow-up to tuning-review p6ns3my4; Python 3.7+."""
import hashlib
import json
import os
from pathlib import Path
import selectors
import socket
import stat
import subprocess
import sys
import tempfile
import time

POD = 'weknora-app-5d8d58b75d-9ffn6'
POD_UID = '0a31d665-d1cc-486f-b0ca-996cbaa97b4f'
CID = 'containerd://c37d549ed044b12f46488b1fdd9059fb48103be2577477dd796ff6b1748de22f'
DEP_UID = '44aca5b9-8fc1-44d6-af61-37c6d62723d5'
DEP_SPEC = '99e3d286851159e7ee7ff4d3bbda6396b3fd777c69649b26a2f6d95ae7dd3815'
LAST = {}

# This code runs only as a fresh diagnostic process. It never reads environ,
# cmdline, application logs, document files, Redis, or a database; no signals.
REMOTE = r'''
import hashlib, json, os, shutil, signal, stat, subprocess, time
def identity():
    raw = open('/proc/1/status', 'r').read(16384)
    fields = dict(x.split(':', 1) for x in raw.splitlines() if ':' in x)
    s = open('/proc/1/stat', 'r').read(8192).rsplit(')', 1)[1].split()
    return {'pid': 1, 'uid': [int(v) for v in fields['Uid'].split()],
            'gid': [int(v) for v in fields['Gid'].split()],
            'start_ticks': int(s[19]),
            'tstp_handler_installed': bool(int(fields['SigCgt'].strip(), 16) &
                                           (1 << (int(signal.SIGTSTP) - 1)))}
result = {'effective_uid': os.geteuid(), 'effective_gid': os.getegid(),
          'signals_sent': 0, 'production_files_modified': False,
          'safe_to_signal_proven': False, 'markers_are_not_behavioral_proof': True}
before = identity()
result['process'] = before
result['tools_present'] = {x: shutil.which(x) is not None
                          for x in ('sha256sum', 'awk', 'grep', 'gosu', 'python3')}
result['original_test_readable'] = {
    'exe': os.access('/proc/1/exe', os.R_OK),
    'status': os.access('/proc/1/status', os.R_OK)}
try:
    result['exe_link_is_expected'] = os.readlink('/proc/1/exe') == '/app/WeKnora'
except OSError as exc:
    result['exe_readlink_errno'] = exc.errno
if result['tools_present']['grep']:
    try:
        q = subprocess.run(['grep', '-aFq', 'probe-marker'], input=b'probe-marker\n',
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
        result['grep_original_options_synthetic_exit'] = q.returncode
    except Exception as exc:
        result['grep_option_test_error'] = type(exc).__name__
deadline = time.monotonic() + 25
markers = {'asynq_package': b'github.com/hibiken/asynq',
           'stop_marker': b'Send signal TSTP to stop processing new tasks',
           'asynq_v0260_buildinfo': b'github.com/hibiken/asynq\tv0.26.0'}
found = dict.fromkeys(markers, False)
try:
    with open('/proc/1/exe', 'rb', buffering=0) as f:
        meta = os.fstat(f.fileno())
        if not stat.S_ISREG(meta.st_mode) or not 0 < meta.st_size < 1073741824:
            raise ValueError('binary_metadata')
        result['binary_metadata'] = {'bytes': meta.st_size, 'uid': meta.st_uid,
                                     'gid': meta.st_gid, 'mode': oct(stat.S_IMODE(meta.st_mode))}
        h = hashlib.sha256(); tail = b''; count = 0
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError('binary_read_deadline')
            part = f.read(1048576)
            if not part:
                break
            count += len(part)
            if count > 1073741824:
                raise ValueError('binary_read_limit')
            h.update(part); block = tail + part
            for name, needle in markers.items():
                found[name] = found[name] or needle in block
            tail = block[-256:]
        if count != meta.st_size:
            raise ValueError('binary_size_changed')
        result['binary_sha256'] = h.hexdigest()
        result['markers'] = found
except OSError as exc:
    result['binary_read_error'] = {'error_type': type(exc).__name__, 'errno': exc.errno}
except Exception as exc:
    result['binary_read_error'] = {'error_type': type(exc).__name__}
result['process_stable'] = identity() == before
print(json.dumps(result, sort_keys=True))
'''


class CheckFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise CheckFailure(label)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':')).encode())


def kube(args, timeout=45, limit=1048576):
    require(args[0] in ('get', 'exec'), 'read_only_commands_required')
    LAST.clear(); LAST.update(operation=args[0])
    p = subprocess.Popen(['kubectl', '--request-timeout=20s', '-n', 'deer-flow'] + args,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf'))
    data = {'stdout': bytearray(), 'stderr': bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as s:
            s.register(p.stdout, selectors.EVENT_READ, 'stdout')
            s.register(p.stderr, selectors.EVENT_READ, 'stderr')
            while s.get_map():
                require(time.monotonic() < deadline, 'kubernetes_read_timeout')
                for key, _ in s.select(0.2):
                    block = os.read(key.fileobj.fileno(), 65536)
                    if not block:
                        s.unregister(key.fileobj); continue
                    data[key.data].extend(block)
                    require(len(data[key.data]) <= (limit if key.data == 'stdout' else 65536), 'output_limit')
        code = p.wait(timeout=max(.01, deadline-time.monotonic()))
        err = bytes(data['stderr']).lower()
        LAST.update(exit_code=code, stdout_bytes=len(data['stdout']), stderr_bytes=len(err),
                    stderr_classes=[name for name, needle in (
                        ('permission_denied', b'permission denied'), ('not_found', b'not found'),
                        ('executable_missing', b'executable file not found'), ('forbidden', b'forbidden'),
                        ('connection_error', b'unable to upgrade connection')) if needle in err])
        require(code == 0, 'kubernetes_read_failed')
        return bytes(data['stdout'])
    finally:
        if p.poll() is None:
            p.kill()  # Only this local kubectl client; never a workload process.
        p.wait(); p.stdout.close(); p.stderr.close()


def baseline():
    d = json.loads(kube(['get', 'deployment', 'weknora-app', '--show-managed-fields=false', '-o', 'json']))
    p = json.loads(kube(['get', 'pod', POD, '--show-managed-fields=false', '-o', 'json']))
    require(d['metadata']['uid'] == DEP_UID and digest(d['spec']) == DEP_SPEC, 'app_deployment_changed')
    require(p['metadata']['uid'] == POD_UID and not p['metadata'].get('deletionTimestamp'), 'app_pod_changed')
    statuses = p['status'].get('containerStatuses', [])
    require(len(statuses) == 1 and statuses[0]['name'] == 'app' and statuses[0].get('ready') is True
            and statuses[0].get('containerID') == CID and statuses[0].get('restartCount') == 0,
            'app_container_changed_or_unready')


def probe(identity=None):
    cmd = ['python3', '-I', '-B', '-c', REMOTE]
    if identity is not None:
        cmd = ['gosu', '%d:%d' % identity] + cmd
    raw = kube(['exec', POD, '-c', 'app', '--'] + cmd, timeout=40, limit=16384)
    r = json.loads(raw)
    require(r.get('process_stable') is True and r.get('signals_sent') == 0, 'process_probe_invalid')
    return r


def may_probe_as_process_user(r):
    # Same non-root UID/GID only; never elevate or change the running process.
    uids, gids = r['process']['uid'], r['process']['gid']
    return (r.get('effective_uid') == 0 and r.get('binary_read_error', {}).get('errno') in (1, 13)
            and r.get('tools_present', {}).get('gosu') is True and len(set(uids)) == 1
            and len(set(gids)) == 1 and 0 < uids[0] < 2147483647 and 0 < gids[0] < 2147483647)


def classify(first, second):
    if second and second.get('binary_sha256') and not first.get('binary_sha256'):
        return 'PROC_EXE_READ_DENIED_IN_EXEC_IDENTITY_SUCCEEDED_AS_SAME_NONROOT_PROCESS_USER'
    if not all(first.get('original_test_readable', {}).values()):
        return 'ORIGINAL_PROC_READABILITY_GATE_FAILED'
    if any(not first['tools_present'].get(x) for x in ('sha256sum', 'awk', 'grep')):
        return 'ORIGINAL_REQUIRED_UTILITY_MISSING'
    if first.get('grep_original_options_synthetic_exit') != 0:
        return 'ORIGINAL_GREP_OPTIONS_TEST_FAILED'
    if first.get('binary_sha256'):
        return 'CURRENT_PROCESS_READ_PASS_PREVIOUS_FAILURE_NOT_REPRODUCED'
    return 'PROCESS_READ_INCOMPLETE'


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'run_on_datax01_as_root')
    require(len(sys.argv) == 1, 'no_arguments_no_signal_mode')
    for v in ('/', '/var', '/var/lib'):
        s = os.lstat(v)
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_evidence_parent')
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix='weknora-process-audit-', dir='/var/lib'))
    os.chmod(str(root), 0o700)
    report = {'production_writes': 0, 'provider_requests': 0, 'signals_sent': 0,
              'safe_to_signal_proven': False, 'ready_to_apply': False, 'raw_output_printed': False,
              'previous_evidence_modified': False, 'pod': POD, 'pod_uid': POD_UID}
    code = 1
    try:
        baseline()
        first = probe(); report['exec_identity_probe'] = first
        second = None
        if may_probe_as_process_user(first):
            baseline()
            second = probe((first['process']['uid'][0], first['process']['gid'][0]))
            require(second['process'] == first['process'], 'process_identity_changed')
            require((second['effective_uid'], second['effective_gid']) ==
                    (first['process']['uid'][0], first['process']['gid'][0]), 'same_user_probe_identity_mismatch')
            report['same_nonroot_user_probe'] = second
        baseline()
        report['diagnosis'] = classify(first, second)
        report['process_audit'] = 'COMPLETE'
        report['process_read'] = 'PASS' if (second or first).get('binary_sha256') else 'INCOMPLETE'
        code = 0
    except Exception as exc:
        report['process_audit'] = 'INCOMPLETE'
        report['failure'] = {'check': str(exc) if isinstance(exc, CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                             'error_type': type(exc).__name__, 'last_operation': dict(LAST)}
    raw = (json.dumps(report, sort_keys=True, indent=2)+'\n').encode()
    fd = os.open(str(root/'RESULT.json'), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), 0o600); f.write(raw); f.flush(); os.fsync(f.fileno())
    print(json.dumps(report, sort_keys=True), flush=True)
    print(json.dumps({'report': str(root/'RESULT.json'), 'report_sha256': sha(raw),
                      'production_writes': 0, 'signals_sent': 0, 'ready_to_apply': False}), flush=True)
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({'process_audit': 'INCOMPLETE', 'error_type': type(exc).__name__,
                          'failed_check': str(exc) if isinstance(exc, CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                          'production_writes': 0, 'signals_sent': 0, 'raw_error_printed': False}))
        sys.exit(1)
