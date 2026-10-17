#!/usr/bin/env python3
"""Python 3.7+ controller: read-only inventory plus opt-in two synthetic POSTs.

Never patches production, reads Kubernetes Secrets, copies credentials into a
file/environment/argv, scales or restarts workloads. Reports are root-private.
"""
import argparse
import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import socket
import stat
import subprocess
import sys
import tempfile
import time
import warnings

import audit_mineru as a

IMAGE = 'deerflow.local/deer-flow-mineru-adapter:20260921-enterprise-arm64-rc15-conversation-runtime4'
IMAGE_ID = 'sha256:bfbdd4d3309110be003549b2959d5080204b27bcd6440e33d4c67d80ee3775e9'
EXPECTED = {
    'deer-flow-mineru-adapter': ('235e4126-c10d-41b4-a25a-75cfe22a151a',
                               '0f058a60b1a21c3c872511e80d626552e94809efdbda0b148804ff96a6509aa1'),
    'deer-flow-mineru-dispatcher': ('34787b4e-a1f6-446c-a559-971ddd4bc187',
                                  '32ab71f2ac96e9f120b2126461a4bb738b6f78a888df0daa5700a27ca0c66e49'),
}
BOOTSTRAP = "import json,sys; p=json.load(sys.stdin); n={'__name__':'fc01_probe'}; exec(compile(p['code'],'<bounded-fc01-probe>','exec'),n); sys.exit(n['entry'](p['token']))"
HEALTH = r'''
import json,os,hashlib
from pathlib import Path
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError
class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None
out = {'provider_requests':0, 'files_modified':False}
p=Path('/app/deerflow_mineru_adapter.py')
if p.is_file() and p.stat().st_size < 2097152:
    out['source_sha256']=hashlib.sha256(p.read_bytes()).hexdigest()
port=os.getenv('MINERU_LISTEN_PORT','50052')
if not port.isdigit() or not 1 <= int(port) <= 65535:
    raise ValueError('invalid_local_health_port')
opener=build_opener(ProxyHandler({}),NoRedirect())
try:
    response=opener.open('http://127.0.0.1:'+port+'/health',timeout=12)
except HTTPError as e:
    response=e
with response:
    raw=response.read(65537)
    if len(raw)>65536: raise ValueError('health_size_limit')
    out['http_status']=response.code
    data=json.loads(raw)
    for section in ('parser','dispatcher'):
        value=data.get(section)
        if isinstance(value,dict):
            out[section]={k:v for k,v in value.items() if k in (
              'active','waiting','rejected','discovered','minimum','busy','live',
              'quarantined','ready','capacity_target_met') and type(v) in (int,bool)}
print(json.dumps(out,sort_keys=True))
'''


def label(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', value) else 'REDACTED_OR_UNSET'


def pod_summary(p):
    s = p.get('status', {})
    containers = []
    for c in s.get('containerStatuses', []) + s.get('initContainerStatuses', []):
        row = {'name': label(c.get('name')), 'ready': bool(c.get('ready')),
               'restarts': c.get('restartCount', 0), 'state': {}, 'last_termination': {}}
        for kind, v in c.get('state', {}).items():
            row['state'][kind] = {k: (label(v[k]) if k == 'reason' else v[k])
                                  for k in ('reason', 'exitCode', 'signal', 'startedAt', 'finishedAt') if k in v}
        last = c.get('lastState', {}).get('terminated', {})
        row['last_termination'] = {k: (label(last[k]) if k == 'reason' else last[k])
                                    for k in ('reason', 'exitCode', 'signal', 'finishedAt') if k in last}
        containers.append(row)
    return {'pod': p['metadata']['name'], 'uid': p['metadata']['uid'],
            'node': p['spec'].get('nodeName'), 'phase': label(s.get('phase')),
            'reason': label(s.get('reason')), 'deleting': bool(p['metadata'].get('deletionTimestamp')),
            'conditions': [{k: label(c.get(k)) for k in ('type', 'status', 'reason')}
                           for c in s.get('conditions', [])], 'containers': containers}


def owned_pods(dep):
    selector = a.selector_text(dep['spec']['selector'])
    owners = {r['metadata']['uid'] for r in a.get('replicasets', selector=selector)['items']
              if a.controlled_by(r, dep['metadata']['uid'])}
    pods = [p for p in a.get('pods', selector=selector)['items']
            if any(a.controlled_by(p, uid) for uid in owners)]
    a.require(len(pods) <= 24, 'bounded_pod_inventory')
    return sorted(pods, key=lambda p: p['metadata']['name'])


def ready_container(p, container):
    rows = [x for x in p.get('status', {}).get('containerStatuses', []) if x['name'] == container]
    return (not p['metadata'].get('deletionTimestamp') and p.get('status', {}).get('phase') == 'Running'
            and len(rows) == 1 and rows[0].get('ready') is True
            and rows[0].get('imageID', '').split('://')[-1] == IMAGE_ID
            and any(c['name'] == container and c['image'] == IMAGE for c in p['spec']['containers']))


def health(p, container):
    raw = a.capture(['exec', '-i', p['metadata']['name'], '-c', container, '--',
                     '/app/.venv/bin/python', '-I', '-B', '-c',
                     "import sys; exec(compile(sys.stdin.read(),'<mineru-health-only>','exec'))"],
                    HEALTH.encode(), timeout=25)
    a.require(len(raw) < 65536, 'bounded_health_receipt')
    return json.loads(raw)


def inventory():
    records, selected, expected_ok = [], None, True
    for name in EXPECTED:
        dep = a.get('deployment', name)
        uid, sha = EXPECTED[name]
        matches = dep['metadata']['uid'] == uid and a.digest(dep['spec']) == sha
        expected_ok = expected_ok and matches
        pods = owned_pods(dep)
        row = {'deployment': name, 'baseline_matches': matches,
               'desired': dep['spec'].get('replicas', 1),
               'ready': dep.get('status', {}).get('readyReplicas', 0),
               'owned_pods': len(pods), 'pod_statuses': [pod_summary(p) for p in pods]}
        container = 'dispatcher' if name.endswith('dispatcher') else 'adapter'
        row['health_receipts'] = []
        for p in pods:
            if not ready_container(p, container):
                # Read only scoped reasons, never raw log/event messages or env.
                uid = p['metadata']['uid']
                try:
                    ev = json.loads(a.capture(['get', 'events', '--field-selector',
                                               'involvedObject.uid=' + uid, '-o', 'json']))
                    reasons = {}
                    for item in ev.get('items', []):
                        reason = label(item.get('reason'))
                        reasons[reason] = reasons.get(reason, 0) + 1
                    row.setdefault('pod_event_reasons', []).append({'pod': p['metadata']['name'], 'reasons': reasons})
                except Exception:
                    row.setdefault('pod_event_reasons', []).append({'pod': p['metadata']['name'], 'events': 'UNAVAILABLE'})
                continue
            try:
                receipt = health(p, container)
            except Exception:
                receipt = {'local_health': 'INCOMPLETE', 'raw_error_printed': False}
            row['health_receipts'].append({'pod': p['metadata']['name'], 'receipt': receipt})
            if (container == 'adapter' and selected is None and receipt.get('http_status') == 200
                    and receipt.get('source_sha256') == '1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39'
                    and receipt.get('parser', {}).get('active') == 0):
                selected = p
        after = a.get('deployment', name)
        stable = (after['metadata']['uid'] == dep['metadata']['uid'] and a.digest(after['spec']) == a.digest(dep['spec']))
        expected_ok = expected_ok and stable
        row['stable_during_audit'] = stable
        records.append(row)
        a.emit(row)
    return records, selected, expected_ok


def safe_write(root, name, value):
    raw = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(str(root / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())


def new_evidence():
    parent = Path('/var/lib')
    for path in (Path('/'), Path('/var'), parent):
        s = path.lstat()
        a.require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                  'unsafe_private_report_parent')
    return Path(tempfile.mkdtemp(prefix='mineru-fc01-probe-', dir=str(parent)))


def execute_probe(pod, code, token):
    args = ['kubectl', '--request-timeout=12m', '-n', a.NAMESPACE,
            'exec', '-i', pod, '-c', 'adapter', '--', '/app/.venv/bin/python', '-I', '-B', '-c', BOOTSTRAP]
    payload = json.dumps({'code': code, 'token': token}).encode()
    process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               env=dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf'))
    started, first = time.monotonic(), True
    try:
        while True:
            try:
                out, err = process.communicate(input=payload if first else None, timeout=15)
                break
            except subprocess.TimeoutExpired:
                first = False
                elapsed = round(time.monotonic() - started)
                a.emit({'sync_probe': 'RUNNING', 'elapsed_seconds': elapsed, 'maximum_posts': 2,
                        'production_configuration_modified': False})
                if elapsed > 700:
                    process.kill()
                    process.communicate()
                    raise a.CheckFailure('transport_deadline_outcome_uncertain_do_not_repeat')
    except BaseException:
        if process.poll() is None:
            process.kill()
            process.communicate()
        raise
    a.require(len(out) <= 65536, 'bounded_probe_output')
    # The exact in-memory credential may never appear in an exported receipt.
    a.require(token.encode() not in out, 'credential_redaction_guard')
    receipts = [json.loads(line) for line in out.splitlines() if line.strip()]
    a.require(bool(receipts), 'remote_receipt_missing_outcome_uncertain_do_not_repeat')
    for receipt in receipts:
        a.emit(receipt)
    return process.returncode, receipts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--probe-sync', action='store_true', help='At most two sequential synthetic provider POSTs; no retry')
    args = parser.parse_args()
    a.require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    root = new_evidence()
    a.emit({'private_evidence': str(root), 'mode': 'SYNC_SMOKE' if args.probe_sync else 'READONLY_AUDIT',
            'production_configuration_modified': False, 'images_changed': False})
    result = {'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'production_configuration_modified': False, 'workloads_restarted': False,
              'credentials_saved': False, 'ready_for_config_switch': False}
    exit_code = 1
    try:
        records, pod, stable = inventory()
        result['inventory'] = records
        safe_write(root, 'INVENTORY.json', records)
        if not args.probe_sync:
            result.update(audit='COMPLETE', provider_requests=0)
            exit_code = 0
        else:
            a.require(stable, 'deployment_baseline_changed_no_provider_call')
            a.require(pod is not None, 'no_verified_ready_worker_no_provider_call')
            code = Path(__file__).with_name('sync_probe_remote.py').read_text(encoding='utf-8')
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass('New MinerU API key (hidden; never saved): ')
            a.require(8 <= len(token) <= 4096 and token.isascii()
                      and all(32 < ord(c) < 127 for c in token), 'invalid_credential_no_provider_call')
            latest = a.get('pod', pod['metadata']['name'])
            a.require(latest['metadata']['uid'] == pod['metadata']['uid'] and ready_container(latest, 'adapter'),
                      'probe_pod_changed_no_provider_call')
            check = health(latest, 'adapter')
            a.require(check.get('parser', {}).get('active') == 0, 'worker_became_busy_no_provider_call')
            safe_write(root, 'PROBE-STARTED.json', {'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
                                                  'maximum_posts': 2, 'automatic_retry': False})
            a.emit({'probe_pod': pod['metadata']['name'], 'maximum_posts': 2,
                    'queue_state_is_sample_only': True, 'automatic_retry': False})
            rc, receipts = execute_probe(pod['metadata']['name'], code, token)
            del token
            result['probe_receipts'] = receipts
            result['sync_smoke'] = receipts[-1].get('sync_smoke', 'UNCONFIRMED')
            exit_code = 0 if rc == 0 and result['sync_smoke'] == 'PASS' else 1
    except (Exception, KeyboardInterrupt) as exc:
        result.update(sync_smoke='UNCONFIRMED', automatic_retry=False,
                      failed_check=str(exc) if isinstance(exc, a.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                      error_type=type(exc).__name__, raw_error_printed=False,
                      instruction='SEND_REPORT_DO_NOT_AUTOMATICALLY_REPEAT')
    safe_write(root, 'RESULT.json', result)
    a.emit({'report': str(root / 'RESULT.json'), 'exit_code': exit_code,
            'sync_smoke': result.get('sync_smoke', 'NOT_REQUESTED'),
            'failed_check': result.get('failed_check'), 'production_configuration_modified': False,
            'credentials_saved': False, 'ready_for_config_switch': False})
    return exit_code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        a.emit({'controller': 'INCOMPLETE', 'error_type': type(exc).__name__,
                'failed_check': str(exc) if isinstance(exc, a.CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                'raw_error_printed': False, 'production_configuration_modified': False,
                'automatic_retry': False})
        sys.exit(1)
