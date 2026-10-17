#!/usr/bin/env python3
"""Bounded read-only quarantine diagnosis, Python 3.7+. Never resumes consumers.

Uses hash-pinned already-delivered read helpers. Health GETs can invoke the
dispatcher's existing due-quarantine readiness rechecks; no explicit reset is
performed. Raw logs, environment values, and document content are never saved.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
import time

TOOL = Path('/var/lib/weknora-tune88-tool-1z7lNY2B/tune.py')
TOOL_SHA = '1ea5394a1814285df0cfc023a912b8d3cb0c8c4da0003238a84b04ca07fd8587'
STATE = Path('/var/lib/weknora-tune88-20261016-r1')
DISPATCH_UID = 'd5c46d17-6c89-4978-a91b-5ea17bf26f97'
APP_UID = 'e1ae4727-a544-4000-986f-edcc8bff5000'
ERRORS = {'TimeoutError', 'ConnectionRefusedError', 'ConnectionResetError',
          'ConnectionAbortedError', 'OSError', 'ValueError', 'AssertionError',
          'RemoteDisconnected', 'IncompleteRead', 'JSONDecodeError', 'BadStatusLine'}
REASONS = {'ReadTimeout', 'ConnectTimeout', 'ConnectionError', 'Timeout',
           'HTTP 429 busy admission race'} | {'HTTP %d' % n for n in range(500, 600)}


class AuditFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise AuditFailure(label)


def emit(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=True), flush=True)


def safe_parent(path):
    for p in reversed([Path(path)] + list(Path(path).parents)):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_private_ancestor')


def open_safe(path):
    safe_parent(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022, 'unsafe_private_file')
        return fd
    except BaseException:
        os.close(fd)
        raise


def load_helpers():
    with os.fdopen(open_safe(TOOL), 'rb') as f:
        require(os.fstat(f.fileno()).st_size == 162325, 'installed_helper_size_changed')
        raw = f.read(162326)
    require(hashlib.sha256(raw).hexdigest() == TOOL_SHA, 'installed_helper_digest_changed')
    # Not __main__: embedded modules are loaded, no apply/status/observe executed.
    exec(compile(raw, str(TOOL), 'exec'), {'__name__':'quarantine_diagnostic_import', '__file__':str(TOOL)})
    return sys.modules['observe_pipeline']


def safe_counters(value):
    require(isinstance(value, dict), 'worker_counters_shape')
    result = {}
    for k in ('active', 'waiting'):
        n = value.get(k)
        require(type(n) is int and 0 <= n < 10**9, 'worker_counters_value')
        result[k] = n
    return result


def quarantine_events(raw, ip_to_name):
    # Only known pod names + reason enums + timestamp syntax are exported.
    last, matched, unmapped = {}, 0, 0
    pattern = re.compile(r'MinerU dispatcher quarantined backend=([0-9.]+) '
                         r'cooldown_seconds=([0-9]+\.[0-9]+) reason=(.*)$')
    for line in raw.decode('utf-8', errors='replace').splitlines():
        m = pattern.search(line)
        if not m:
            continue
        matched += 1
        if m[1] not in ip_to_name:
            unmapped += 1
            continue
        stamp = re.match(r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) ', line)
        pod = ip_to_name[m[1]]
        last[pod] = {'pod':pod, 'reason':m[3] if m[3] in REASONS else 'UNCLASSIFIED',
                     'utc':stamp[1] if stamp else None}
    return {'events':list(last.values()), 'matched_event_count':matched,
            'unmapped_event_count':unmapped, 'proves_current_quarantine_membership':False,
            'scope':'LAST_2_HOURS_MAX_5000_LINES_512KiB', 'raw_logs_saved':False}


REMOTE = r'''import concurrent.futures,http.client,ipaddress,json,sys
targets=json.loads(sys.argv[1])
assert len(targets)==16 and len(set(targets))==16
assert all(str(ipaddress.IPv4Address(x))==x for x in targets)
def read(item):
 i,ip,path=item
 c=http.client.HTTPConnection(ip,50052,timeout=1.5)
 out={'index':i,'path':path}
 try:
  c.request('GET', path,headers={'Connection':'close'})
  r=c.getresponse(); b=r.read(32769)
  assert len(b)<=32768
  d=json.loads(b); v=d.get('parser',{})
  out.update(status=r.status,role=d.get('role'),counters={k:v.get(k) for k in ('active','waiting')})
 except Exception as e:
  out.update(error_type=type(e).__name__)
 finally:
  c.close()
 return out
items=[(i,ip,path) for i,ip in enumerate(targets) for path in ('/health','/ready')]
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
 print(json.dumps(list(pool.map(read,items))))
'''


def remote_rows(raw, names):
    data = json.loads(raw)
    require(isinstance(data, list) and len(data) == 2*len(names), 'remote_result_count')
    seen, result = set(), []
    for row in data:
        i, path = row.get('index'), row.get('path')
        require(type(i) is int and 0 <= i < len(names) and path in ('/health', '/ready'), 'remote_target_changed')
        require((i, path) not in seen, 'duplicate_remote_result')
        seen.add((i, path))
        clean = {'pod':names[i], 'path':path}
        if 'error_type' in row:
            clean['error_type'] = row['error_type'] if row['error_type'] in ERRORS else 'UNCLASSIFIED'
        else:
            code = row.get('status')
            require(type(code) is int and 100 <= code <= 599, 'remote_http_status')
            clean.update(status=code, role='worker' if row.get('role') == 'worker' else 'UNEXPECTED',
                         counters=safe_counters(row.get('counters')))
        result.append(clean)
    return result


def remote_health(obs, dispatcher, workers):
    ips = [p['status'].get('podIP') for p in workers]
    require(all(isinstance(x, str) and str(ipaddress.IPv4Address(x)) == x for x in ips)
            and len(set(ips)) == 16, 'candidate_worker_ip_inventory')
    raw = obs.call(['exec', dispatcher['metadata']['name'], '-c', 'dispatcher', '--',
                    'python3', '-B', '-c', REMOTE, json.dumps(ips)], timeout=45, limit=32768)
    return remote_rows(raw, [p['metadata']['name'] for p in workers])


def read_logs(obs, pod, ip_to_name):
    raw = obs.call(['logs', pod['metadata']['name'], '-c', obs.container(pod)['name'],
                    '--timestamps=true', '--since=2h', '--tail=5000', '--limit-bytes=524288'],
                   timeout=25, limit=525312)
    result = {'bytes_read':len(raw), 'aggregates':obs.log_stats(raw)}
    if ip_to_name is not None:
        result['quarantine'] = quarantine_events(raw, ip_to_name)
    return result


def run(obs, report):
    _, pods, signature, _ = obs.baseline()
    dispatcher, app = pods[obs.DISPATCHER][0], pods['weknora-app'][0]
    require(dispatcher['metadata']['uid'] == DISPATCH_UID and app['metadata']['uid'] == APP_UID,
            'production_pod_changed_use_fresh_review')
    workers = [p for n in obs.WORKERS for p in pods[n]]
    ip_to_name = {p['status']['podIP']:p['metadata']['name'] for p in workers}
    report['worker_nodes'] = {p['metadata']['name']:p['spec']['nodeName'] for p in workers}
    report['samples'] = []
    started = time.monotonic()
    for i in range(3):
        delay = started + i*35 - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        emit({'quarantine_audit':'RUNNING', 'sample':i+1, 'samples':3})
        # No parsing request is made. These are normal health/readiness GETs.
        dispatch = obs.health(dispatcher)
        with ThreadPoolExecutor(max_workers=4) as pool:
            local = list(pool.map(obs.health, workers))
        remote = remote_health(obs, dispatcher, workers)
        row = {'sample':i+1, 'utc':obs.utc(), 'dispatcher':dispatch,
               'worker_local_health':local, 'dispatcher_to_worker':remote}
        report['samples'].append(row)
        important = [x for x in remote if x.get('error_type') or x.get('status') != 200
                     or x.get('counters', {}).get('active') or x.get('counters', {}).get('waiting')]
        emit({'sample':i+1, 'dispatcher':dispatch, 'worker_network_busy_or_not_ready':important,
              'worker_local_unavailable':[x for x in local if x.get('unavailable')],
              'ready_503_while_active_is_expected':True})
    report['dispatcher_logs'] = read_logs(obs, dispatcher, ip_to_name)
    emit({'bounded_dispatcher_log_evidence':report['dispatcher_logs']})
    names = {x['pod'] for x in report['dispatcher_logs']['quarantine']['events']}
    report['worker_log_aggregates'] = {}
    for pod in workers:
        if pod['metadata']['name'] in names:
            emit({'quarantine_audit':'WORKER_LOG_AGGREGATES', 'pod':pod['metadata']['name']})
            report['worker_log_aggregates'][pod['metadata']['name']] = read_logs(obs, pod, None)
    emit({'worker_log_aggregates':report['worker_log_aggregates']})
    _, _, after, _ = obs.baseline()
    require(after == signature, 'production_baseline_changed_during_diagnostic')
    report.update(diagnostic='COMPLETE', production_baseline='UNCHANGED',
                  current_quarantined_member_identity='NOT_EXPOSED_BY_HEALTH_API',
                  interpretation='HISTORICAL_LOG_EVENTS_ARE_NOT_CURRENT_MEMBERSHIP_PROOF',
                  ready_to_continue_88=False, consumers_resumed=False)


def main():
    obs, directory, lock = None, None, None
    report = {'diagnostic':'INCOMPLETE', 'production_configuration_writes':0,
              'provider_requests':0, 'signals_sent':0, 'processes_restarted':False,
              'documents_submitted':0, 'quarantine_explicitly_cleared':False,
              'raw_logs_saved':False, 'credentials_read':False, 'automatic_retry':False,
              'health_uses_existing_automatic_readiness_reprobe':True}
    try:
        require(socket.gethostname().split('.')[0] == 'datax-01'
                and hasattr(os, 'geteuid') and os.geteuid() == 0, 'run_on_datax01_as_root')
        import fcntl
        os.umask(0o077)
        obs = load_helpers()
        lock = open_safe(STATE/'controller.lock')
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AuditFailure('tuning_controller_busy_no_parallel_diagnostic')
        safe_parent(Path('/var/lib'))
        directory = Path(tempfile.mkdtemp(prefix='mineru-quarantine-audit-', dir='/var/lib'))
        report['utc_started'] = obs.utc()
        emit({'quarantine_audit_evidence':str(directory), 'consumer_changes':False,
              'estimated_seconds':'90_TO_180', 'production_configuration_writes':0})
        run(obs, report)
    except Exception as exc:
        report.update(error_type=type(exc).__name__, failed_check=str(exc) if isinstance(exc, AuditFailure)
                      else (obs.failure(exc)['check'] if obs else 'SAFE_DIAGNOSTIC_REQUIRED'),
                      raw_error_printed=False)
    finally:
        if lock is not None:
            os.close(lock)
    if directory is not None:
        report['utc_finished'] = obs.utc()
        raw = obs.write_new(directory/'RESULT.json', report)
        emit({'diagnostic':report['diagnostic'], 'failed_check':report.get('failed_check'),
              'report':str(directory/'RESULT.json'), 'report_sha256':hashlib.sha256(raw).hexdigest(),
              'consumers_resumed':False, 'ready_to_continue_88':False,
              'next_action':'SEND_OUTPUT_FOR_RECOVERY_REVIEW', 'production_configuration_writes':0})
    else:
        emit(report)
    return 0 if report['diagnostic'] == 'COMPLETE' else 1


if __name__ == '__main__':
    sys.exit(main())
