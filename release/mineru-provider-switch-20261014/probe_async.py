#!/usr/bin/env python3
"""Python 3.7+ controller; at most one synthetic provider task, never a rollout."""
import argparse
import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import resource
import socket
import stat
import subprocess
import sys
import tempfile
import time
import warnings

import audit_mineru as a
import audit_capacity as c

EXPECTED = {
    c.WORKER: ('235e4126-c10d-41b4-a25a-75cfe22a151a', '0f058a60b1a21c3c872511e80d626552e94809efdbda0b148804ff96a6509aa1'),
    c.DISPATCHER: ('34787b4e-a1f6-446c-a559-971ddd4bc187', '32ab71f2ac96e9f120b2126461a4bb738b6f78a888df0daa5700a27ca0c66e49'),
    'weknora-app': ('44aca5b9-8fc1-44d6-af61-37c6d62723d5', '99e3d286851159e7ee7ff4d3bbda6396b3fd777c69649b26a2f6d95ae7dd3815'),
    'weknora-docreader': ('f6a96f1b-b0d2-4639-9db0-45028a65ad55', 'c436851c52db03bb28359df9ac8c2bcd92e7429c9509770348193b43eb29d273'),
}
SOURCE_SHA = '1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39'
GATE = Path('/var/lib/mineru-async-smoke-20261014-r1')
BOOTSTRAP = "import json,sys; p=json.load(sys.stdin); n={'__name__':'async_probe'}; exec(compile(p['code'],'<bounded-async-probe>','exec'),n); sys.exit(n['entry'](p['token'],p['source'],p['source_sha']))"


def write_new(root, name, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(str(root / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def safe_directory(path):
    for p in reversed([path] + list(path.parents)):
        s = p.lstat()
        a.require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_private_directory')


def safe_json(path):
    safe_directory(path.parent)
    s = path.lstat()
    a.require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
              and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_private_record')
    return json.loads(path.read_bytes())


def new_evidence():
    safe_directory(Path('/var/lib'))
    return Path(tempfile.mkdtemp(prefix='mineru-async-smoke-', dir='/var/lib'))


def check_deployments():
    worker = None
    for name, expected in EXPECTED.items():
        dep = a.get('deployment', name)
        a.require((dep['metadata']['uid'], a.digest(dep['spec'])) == expected,
                  'deployment_baseline_changed_no_provider_call')
        if name == c.WORKER: worker = dep
    return worker


def select_worker(dep):
    for p in c.owned(dep):
        rows = p.get('status', {}).get('containerStatuses', [])
        if not any(x.get('name') == 'adapter' and x.get('ready') for x in rows): continue
        a.emit({'probe_selection': 'READONLY_HEALTH', 'pod': p['metadata']['name']})
        receipt = c.health(p, 'adapter')
        if (receipt.get('http_status') == 200 and receipt.get('source_sha256') == SOURCE_SHA
                and receipt.get('parser', {}).get('active') == 0
                and receipt.get('parser', {}).get('waiting') == 0):
            return p
    raise a.CheckFailure('no_verified_idle_worker_no_provider_call')


def reviewed_sources():
    root = Path(__file__).resolve().parent
    safe_directory(root)
    lock = safe_json(root / 'SMOKE-LOCK.json')
    found = {}
    for name in ('async_probe_remote.py', 'candidate-adapter.py'):
        p = root / name
        s = p.lstat()
        a.require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                  and not s.st_mode & 0o022 and s.st_size <= 2097152, 'unsafe_probe_source')
        raw = p.read_bytes()
        a.require(hashlib.sha256(raw).hexdigest() == lock['source_sha256'][name], 'probe_source_hash_mismatch')
        found[name] = raw.decode('utf-8')
    return found, lock['source_sha256']['candidate-adapter.py']


def execute_probe(pod, code, source, source_sha, token):
    argv = ['kubectl', '--request-timeout=400s', '-n', a.NAMESPACE, 'exec', '-i', pod,
            '-c', 'adapter', '--', '/app/.venv/bin/python', '-I', '-B', '-c', BOOTSTRAP]
    payload = json.dumps(dict(code=code, source=source, source_sha=source_sha, token=token)).encode()
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf'))
    started, first, uncertain = time.monotonic(), True, False
    try:
        while True:
            try:
                out, _ = process.communicate(input=payload if first else None, timeout=15)
                break
            except subprocess.TimeoutExpired:
                first = False
                elapsed = round(time.monotonic() - started)
                a.emit({'async_probe': 'RUNNING', 'elapsed_seconds': elapsed, 'maximum_submissions': 1})
                if elapsed > 390:
                    uncertain = True
                    process.kill()
                    out, _ = process.communicate()
                    break
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.communicate()
        raise
    a.require(len(out) <= 262144 and token.encode() not in out, 'probe_output_safety_check_failed')
    receipts = []
    for line in out.splitlines():
        try:
            row = json.loads(line)
            a.require(isinstance(row, dict), 'invalid_remote_receipt')
            receipts.append(row)
        except ValueError:
            uncertain = True
    for row in receipts: a.emit(row)
    return process.returncode, receipts, uncertain


def read_status():
    if not GATE.exists():
        a.emit({'probe_started': False, 'provider_requests': 0, 'status_only': True})
        return
    safe_directory(GATE)
    started = GATE / 'PROBE-STARTED.json'
    if not started.exists():
        a.emit({'probe_started': False, 'provider_requests': 0, 'status_only': True})
        return
    row = safe_json(started)
    root = Path(row['evidence'])
    a.require(root.parent == Path('/var/lib') and root.name.startswith('mineru-async-smoke-'), 'unsafe_evidence_reference')
    a.emit({'probe_started': True, 'evidence': str(root), 'automatic_resubmission': False, 'status_only': True})
    if (root / 'RESULT.json').exists(): a.emit(safe_json(root / 'RESULT.json'))
    else: a.emit({'outcome': 'UNCONFIRMED_DO_NOT_REPEAT', 'result_present': False})


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--probe-once', action='store_true')
    modes.add_argument('--status', action='store_true')
    args = parser.parse_args()
    a.require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if args.status:
        read_status()
        return 0
    safe_directory(Path('/var/lib'))
    if not GATE.exists(): GATE.mkdir(mode=0o700)
    safe_directory(GATE)
    a.require(not (GATE / 'PROBE-STARTED.json').exists(), 'already_started_use_status_do_not_resubmit')
    root = new_evidence()
    a.emit({'async_smoke_evidence': str(root), 'maximum_submissions': 1, 'production_configuration_modified': False})
    result = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'async_smoke': 'UNCONFIRMED',
              'production_configuration_modified': False, 'production_workloads_mutated': False,
              'credentials_saved': False, 'ready_for_rollout': False, 'automatic_resubmission': False}
    exit_code = 1
    try:
        sources, candidate_sha = reviewed_sources()
        worker = select_worker(check_deployments())
        result.update(pod=worker['metadata']['name'], pod_uid=worker['metadata']['uid'], candidate_sha256=candidate_sha)
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            token = getpass.getpass('New dedicated MinerU API key (hidden; never saved): ')
        a.require(8 <= len(token) <= 4096 and token.isascii() and all(32 < ord(ch) < 127 for ch in token), 'invalid_hidden_key')
        check_deployments()
        latest = a.get('pod', worker['metadata']['name'])
        a.require(latest['metadata']['uid'] == worker['metadata']['uid'], 'selected_worker_replaced_no_provider_call')
        health = c.health(latest, 'adapter')
        a.require(health.get('source_sha256') == SOURCE_SHA and health.get('http_status') == 200
                  and health.get('parser', {}).get('active') == 0 and health.get('parser', {}).get('waiting') == 0,
                  'selected_worker_changed_or_busy_no_provider_call')
        # Atomic gate, retained even if the exec connection outcome is uncertain.
        write_new(GATE, 'PROBE-STARTED.json', dict(evidence=str(root), maximum_submissions=1,
                                                utc=result['utc'], pod_uid=result['pod_uid']))
        a.emit({'probe_pod': result['pod'], 'maximum_submissions': 1, 'sample': 'TWO_PAGE_SYNTHETIC_PDF',
                'active_count_is_sample_only': True, 'automatic_resubmission': False})
        rc, receipts, uncertain = execute_probe(result['pod'], sources['async_probe_remote.py'],
            sources['candidate-adapter.py'], candidate_sha, token)
        del token
        result['receipts'] = receipts
        result['transport_uncertain'] = uncertain
        terminal = [r for r in receipts if r.get('async_smoke') in ('PASS', 'FAIL')]
        if rc == 0 and not uncertain and len(terminal) == 1 and terminal[0]['async_smoke'] == 'PASS':
            result['async_smoke'] = 'PASS'
            exit_code = 0
        else:
            result['failed_check'] = 'REMOTE_PROBE_FAILED_OR_UNCERTAIN_DO_NOT_RESUBMIT'
    except (Exception, KeyboardInterrupt) as exc:
        result.update(failed_check=str(exc) if isinstance(exc, a.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                      error_type=type(exc).__name__, raw_error_printed=False)
    write_new(root, 'RESULT.json', result)
    raw = (root / 'RESULT.json').read_bytes()
    a.emit({'async_smoke': result['async_smoke'], 'report': str(root / 'RESULT.json'),
            'report_sha256': hashlib.sha256(raw).hexdigest(), 'exit_code': exit_code,
            'failed_check': result.get('failed_check'), 'ready_for_rollout': False,
            'production_workloads_mutated': False, 'credentials_saved': False,
            'instruction': 'SEND_REPORT; ON_UNCERTAINTY_USE_STATUS_NOT_PROBE_ONCE'})
    return exit_code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as exc:
        a.emit({'controller': 'UNCONFIRMED', 'error_type': type(exc).__name__,
                'failed_check': str(exc) if isinstance(exc, a.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                'production_workloads_mutated': False, 'automatic_resubmission': False,
                'raw_error_printed': False, 'instruction': 'USE_STATUS_DO_NOT_REPEAT'})
        sys.exit(1)
