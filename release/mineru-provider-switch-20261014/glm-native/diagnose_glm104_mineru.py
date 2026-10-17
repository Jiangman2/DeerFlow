#!/usr/bin/env python3
"""Read-only diagnosis after acknowledged GLM104 patch; Python 3.7+.

No mutation/reconcile, SQL, Redis, model requests, raw logs, or credentials.
Health GETs may run the dispatcher's existing due readiness reprobe.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor

TOOL = Path('/var/lib/weknora-glm104-switch-tool-gZZZfsdn/switch.py')
TOOL_SHA = '761d0f8de8ec6e6938216d1aba73c6c17dc69ae6f38475220740223f60079176'
RECEIPT = Path('/var/lib/weknora-glm104-switch-20261017-r1/RESULT-68d1b166669541598c46aa02c90b4f5e.json')
RECEIPT_SHA = '788426d520770ebb98f48cc356529ef00742b2aba27bb3d55acaf389cb570cd4'
APP_UID = '2fb12206-2d91-4b12-806b-471e1ca19c61'
LIMIT = 524288
TAIL = 5000
REASONS = {'ReadTimeout', 'ConnectTimeout', 'ConnectionError', 'Timeout',
           'HTTP 429 busy admission race'} | {'HTTP %d' % x for x in range(500, 600)}
PATTERNS = {
    'adapter_exception': r'MinerU adapter request failed',
    'upstream_http_rejected': r'MinerU upstream request rejected:',
    'async_task_failed': r'Enterprise MinerU asynchronous task failed:',
    'async_deadline': r'Enterprise MinerU (?:task|result for task|task status for task|task result for task) .* (?:exceeded|was not ready within)',
    'read_timeout': r'\bReadTimeout(?:Error)?\b',
    'connect_timeout': r'\bConnectTimeout(?:Error)?\b',
    'connection_error': r'\b(?:ConnectionError|ConnectionResetError|ConnectionRefusedError)\b',
    'tls_error': r'\bSSLError\b|CERTIFICATE_VERIFY_FAILED',
    'libreoffice_deadline': r'LibreOffice timed out after',
    'docreader_deadline': r'DocReader timed out after',
    'anydoc_deadline': r'AnyDoc timed out after',
    'visual_evidence_missing': r'Visual evidence (?:rendering omitted|rendering returned no)',
    'invalid_or_empty_parse_result': r'Enterprise MinerU returned (?:an invalid parse result|no parseable content|an invalid images field)',
    'upstream_non_json': r'Enterprise MinerU returned a non-JSON response',
    'upstream_unexpected_task_state': r'Enterprise MinerU returned unexpected task status:',
    'worker_busy': r'MinerU adapter worker busy:',
    'dispatcher_unavailable': r'MinerU dispatcher unavailable:',
    'client_disconnected': r'Parser response client disconnected before body delivery',
    'document_rejected': r'Rejected MinerU adapter request:',
    'memory_error': r'\bMemoryError\b|Cannot allocate memory',
    'disk_full': r'No space left on device',
    'glm_deadline': r'enterprise GLM: (?:context deadline exceeded|stream deadline exceeded)',
    'glm_http_429': r'enterprise GLM: HTTP status 429\b',
    'glm_http_503': r'enterprise GLM: HTTP status 503\b',
    'glm_transport_failed': r'enterprise GLM: transport failed',
    'image_caption_failed': r'\[ImageMultimodal\] Caption failed',
    'image_index_failed': r'\[ImageMultimodal\] Failed to index multimodal chunks',
}
OPERATIONS = r'(asynchronous submission|synchronous parse|task status|task result)'


class DiagnosticFailure(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise DiagnosticFailure(code)


def emit(row):
    print(json.dumps(row, sort_keys=True, ensure_ascii=True, allow_nan=False), flush=True)


def safe_read(path, expected):
    for p in (path.parent,) + tuple(path.parent.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_private_ancestor')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_private_file')
        raw = f.read(1048577)
    need(hashlib.sha256(raw).hexdigest() == expected, 'pinned_input_changed')
    return raw


def load_observer():
    raw = safe_read(TOOL, TOOL_SHA)
    module = types.ModuleType('readonly_glm104_import')
    module.__file__ = str(TOOL)
    exec(compile(raw, str(TOOL), 'exec'), module.__dict__)
    # Import/init only: never invoke switch.main, status, apply or reconcile.
    return module.load_review().o.r.obs


def bind_receipt():
    r = json.loads(safe_read(RECEIPT, RECEIPT_SHA))
    need(r.get('live_patch_acknowledged') is True
         and r.get('failed_check') == 'mineru_16_not_healthy_for_expansion'
         and r.get('glm104_switch') == 'UNCONFIRMED', 'unexpected_switch_receipt')


def stamp(line):
    m = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))\s', line)
    if m:
        try:
            value = re.sub(r'\.\d+(?=Z|[+-])', '', m[1]).replace('Z', '+00:00')
            return datetime.datetime.fromisoformat(value).astimezone(
                datetime.timezone.utc).isoformat()
        except ValueError:
            pass
    return None


def aggregate(raw, mapping, since):
    lines = raw.decode('utf-8', 'replace').splitlines()
    stamps = [t for t in (stamp(x) for x in lines) if t]
    markers = {k: 0 for k in PATTERNS}
    quarantine, upstream, minutes = {}, {}, {}
    unknown = 0
    for line in lines:
        t = stamp(line)
        for k, pattern in PATTERNS.items():
            if re.search(pattern, line):
                markers[k] += 1
        m = re.search(r'MinerU dispatcher quarantined backend=([0-9.]+) cooldown_seconds=[0-9.]+ reason=(.*)$', line)
        if m:
            pod = mapping.get(m[1])
            if pod is None:
                unknown += 1
            else:
                reason = m[2] if m[2] in REASONS else 'UNCLASSIFIED'
                key = (pod, reason)
                row = quarantine.setdefault(key, dict(pod=pod, reason=reason, count=0, last_utc=None))
                row['count'] += 1
                row['last_utc'] = t
                minute = t[:16] + 'Z' if t else 'UNKNOWN'
                minutes[minute] = minutes.get(minute, 0) + 1
        m = re.search(r'Enterprise MinerU HTTP ([1-5][0-9]{2}) during ' + OPERATIONS + r':', line)
        if m:
            key = ('http', m[1], m[2])
        else:
            m = re.search(r'Enterprise MinerU business error during ' + OPERATIONS + r'; code=([0-9]{1,8});', line)
            key = ('business', m[2], m[1]) if m else None
        if key:
            row = upstream.setdefault(key, dict(kind=key[0], code=key[1], operation=key[2], marker_lines=0, last_utc=None))
            row['marker_lines'] += 1
            row['last_utc'] = t
    return dict(coverage=dict(requested_since_utc=since, bytes_read=len(raw), lines_read=len(lines),
        byte_limit_reached=len(raw) >= LIMIT, line_limit_reached=len(lines) >= TAIL,
        first_retained_utc=stamps[0] if stamps else None, last_retained_utc=stamps[-1] if stamps else None,
        complete_history_proven=False), error_marker_lines={k:v for k,v in markers.items() if v},
        upstream_codes=list(upstream.values()), quarantine_events=list(quarantine.values()),
        quarantine_events_per_utc_minute=minutes, unmapped_quarantine_events=unknown,
        historical_events_prove_current_membership=False, marker_counts_are_not_unique_failures=True,
        raw_logs_saved=False)


def safe_error(exc):
    return dict(error_type=type(exc).__name__,
                check=str(exc) if isinstance(exc, DiagnosticFailure) else 'READ_UNAVAILABLE',
                raw_error_printed=False)


def pod_status(obs, pod):
    c = obs.container(pod)
    rows = [x for x in pod.get('status', {}).get('containerStatuses', []) if x['name'] == c['name']]
    cs = rows[0] if len(rows) == 1 else {}
    term = cs.get('lastState', {}).get('terminated', {})
    reason = term.get('reason')
    return dict(pod=pod['metadata']['name'], uid=pod['metadata']['uid'], node=pod['spec'].get('nodeName'),
        phase=pod.get('status', {}).get('phase'), ready=obs.ready(pod),
        deleting=bool(pod['metadata'].get('deletionTimestamp')), restarts=cs.get('restartCount'),
        last_termination_reason=reason if reason in ('OOMKilled','Error','Completed','ContainerCannotRun') else ('OTHER' if reason else None),
        last_exit_code=term.get('exitCode'), last_finished_utc=term.get('finishedAt'))


def inventory(obs):
    names = obs.WORKERS + [obs.DISPATCHER, 'weknora-app']
    deps = {n: obs.get('deployments', n) for n in names}
    pods = {n: obs.owned(d) for n, d in deps.items()}
    app = pods['weknora-app']
    need(len(app) == 1 and app[0]['metadata']['uid'] == APP_UID, 'app_changed_use_new_review')
    targets = [p for ps in pods.values() for p in ps]
    need(len(targets) <= 40, 'pod_inventory_bound')
    for name in obs.WORKERS + [obs.DISPATCHER]:
        need(all(obs.container(p)['image'] == obs.IMAGE for p in pods[name]), 'mineru_image_changed')
    signature = dict(deployments={n:[d['metadata']['uid'],obs.digest(d['spec'])] for n,d in deps.items()},
                     pods={n: sorted(obs.identity(p) for p in ps) for n,ps in pods.items()})
    return pods, signature


def logs(obs, pod, mapping, since, previous=False):
    row = dict(pod=pod['metadata']['name'], previous_container=previous)
    args = ['logs', pod['metadata']['name'], '-c', obs.container(pod)['name'],
            '--since-time='+since, '--timestamps=true', '--tail='+str(TAIL), '--limit-bytes='+str(LIMIT)]
    if previous:
        args.append('--previous=true')
    try:
        raw = obs.call(args, timeout=25, limit=LIMIT+4096)
        row.update(aggregate(raw, mapping, since))
        series, count = obs.log_stats(raw)
        row['parse_and_dispatch_timings'] = {k:obs.summary_stats(v) for k,v in series.items()}
        row['dispatch_transport_failure_lines'] = count
    except Exception as exc:
        row['unavailable'] = safe_error(exc)
    return row


def collect(obs):
    pods, signature = inventory(obs)
    workers = [p for n in obs.WORKERS for p in pods[n]]
    targets = workers + pods[obs.DISPATCHER]
    mapping = {p['status']['podIP']:p['metadata']['name'] for p in workers if p.get('status', {}).get('podIP')}
    since = (datetime.datetime.now(datetime.timezone.utc)-datetime.timedelta(minutes=20)).strftime('%Y-%m-%dT%H:%M:%SZ')
    emit(dict(diagnostic='RUNNING', phase='POD_STATE_AND_BOUNDED_LOGS', since_utc=since,
              worker_count=len(workers), dispatcher_count=len(pods[obs.DISPATCHER])))
    statuses = [pod_status(obs,p) for p in targets+pods['weknora-app']]
    emit(dict(pod_states=statuses))
    requests = [(p,False) for p in targets+pods['weknora-app']]
    requests += [(p,True) for p in targets if (pod_status(obs,p).get('restarts') or 0)>0]
    with ThreadPoolExecutor(max_workers=4) as pool:
        # All16 workers, not only currently mapped historical quarantine names.
        results = list(pool.map(lambda x: logs(obs,x[0],mapping,since,x[1]), requests))
    for row in results:
        emit(dict(bounded_log_evidence=row))
    health = []
    for i in range(3):
        emit(dict(diagnostic='RUNNING', phase='HEALTH_ONLY', sample=i+1))
        selected = targets if i == 0 else pods[obs.DISPATCHER]
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows = list(pool.map(obs.health, selected))
        health.extend(rows)
        emit(dict(sample=i+1, utc=obs.utc(), health=rows))
        if i<2:
            time.sleep(10)
    emit(dict(diagnostic='RUNNING', phase='KUBELET_RESOURCE_SAMPLE'))
    resources = obs.resource_snapshot(pods)
    emit(dict(resources=resources))
    _, after = inventory(obs)
    failures = sum('unavailable' in x for x in results+health+resources)
    stable = signature == after
    topology = len(workers)==16 and len(pods[obs.DISPATCHER])==1
    emit(dict(diagnostic='COMPLETE' if stable and not failures and topology else 'INCOMPLETE',
        baseline_stable=stable, unavailable_reads=failures, worker_count=len(workers),
        expected_topology_present=topology,
        production_writes=0, provider_probe_requests=0, documents_submitted=0,
        files_modified=False, raw_logs_saved=False, signals_sent=0, explicit_quarantine_reset=False,
        collection_is_not_runtime_acceptance=True, ready_to_expand=False,
        health_uses_existing_automatic_readiness_reprobe=True))
    return 0 if stable and not failures and topology else 1


def main():
    try:
        need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01',
             'run_on_datax01_as_root')
        need(len(sys.argv)==1, 'no_arguments')
        emit(dict(diagnostic='RUNNING', phase='BIND_ACKNOWLEDGED_GLM104_RECEIPT', production_writes=0))
        bind_receipt()
        return collect(load_observer())
    except Exception as exc:
        emit(dict(diagnostic='INCOMPLETE', failure=safe_error(exc), production_writes=0,
                  provider_probe_requests=0, automatic_mutation_retry=False))
        return 1


if __name__=='__main__':
    sys.exit(main())
