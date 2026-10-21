#!/usr/bin/env python3
"""Python 3.7+ bounded, read-only production observation; no provider submissions.

Writes only new local evidence/download files. Kubernetes reads + health/log execs;
Redis command allowlist is read-only, not Asynq Inspector's mutating cleanup Lua.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import subprocess
import sys
import tempfile
import time

VERSION = '20261016-r1'
NS = 'deer-flow'
WORKERS = ['deer-flow-mineru-dedicated-r1-w-ecs-%04d' % n for n in range(11, 17)]
DISPATCHER = 'deer-flow-mineru-dedicated-dispatch-r1'
APPS = ['weknora-app', 'weknora-docreader', 'weknora-redis']
OLD = ['deer-flow-mineru-adapter', 'deer-flow-mineru-dispatcher']
IMAGE = 'deerflow.local/deer-flow-mineru-adapter:20261015-dedicated-async-r1-fix1-a5c21e7fbe155ea3'
EXPANSION = '/var/lib/mineru-expand16-20261016-r1/RESULT.json'
EXPANSION_SHA = 'b11e5cca07870f57c36b3627fd2fd7566189a215d8690f5ead451af15e3673e8'
QUEUES = ('default', 'chat_attachment', 'postprocess', 'summary', 'multimodal',
          'graph', 'question', 'memory', 'sync', 'low', 'wiki')
ENV_KEYS = ['WEKNORA_ASYNQ_%s_CONCURRENCY' % x for x in
            ('CORE', 'POSTPROCESS', 'ENRICHMENT', 'MAINTENANCE', 'SHARED')]
ENV_KEYS += ['WEKNORA_WIKI_ASYNQ_CONCURRENCY', 'WEKNORA_REDIS_OP_TIMEOUT_MS']
ENRICHMENT = {'summary', 'multimodal', 'graph', 'question', 'memory'}


class CheckFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise CheckFailure(label)


def emit(value):
    print(json.dumps(value, ensure_ascii=True, sort_keys=True), flush=True)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def failure(exc):
    return {'check': str(exc) if isinstance(exc, CheckFailure) else 'READ_UNAVAILABLE',
            'error_type': type(exc).__name__, 'raw_error_printed': False}


def safe_parent(path):
    for p in reversed([Path(path)] + list(Path(path).parents)):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_evidence_ancestor')


def read_expansion():
    p = Path(EXPANSION)
    safe_parent(p.parent)
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and s.st_size < 2097152, 'unsafe_expansion_receipt')
        raw = f.read(2097152)
    require(hashlib.sha256(raw).hexdigest() == EXPANSION_SHA, 'expansion_receipt_changed')
    r = json.loads(raw)
    require(r.get('mineru_expand16') == 'PASS' and r.get('new_pool_slots') == 16,
            'expansion_receipt_not_pass')


def write_new(path, value):
    raw = (json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    return raw


def call(args, timeout=30, limit=4194304):
    # No generic externally supplied kubectl arguments.
    require(args[0] in ('get', 'logs', 'exec'), 'kubernetes_verb_out_of_scope')
    proc = subprocess.Popen(['kubectl', '--request-timeout=20s', '-n', NS] + args,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf'))
    data = {'out': bytearray(), 'err': bytearray()}
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ, 'out')
            sel.register(proc.stderr, selectors.EVENT_READ, 'err')
            while sel.get_map():
                require(time.monotonic() < deadline, 'read_deadline')
                for key, _ in sel.select(0.2):
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        sel.unregister(key.fileobj)
                        continue
                    require(len(data[key.data]) + len(part) <= (limit if key.data == 'out' else 65536),
                            'read_output_limit')
                    data[key.data].extend(part)
        code = proc.wait(timeout=max(.01, deadline - time.monotonic()))
        require(code == 0, 'kubernetes_read_failed_' + args[0])
        return bytes(data['out'])
    finally:
        if proc.poll() is None:
            proc.kill()  # Only this local kubectl client, never a production process.
        proc.wait()
        proc.stdout.close()
        proc.stderr.close()


def get(kind, name=None, selector=None):
    require(kind in ('deployments', 'replicasets', 'pods', 'services', 'endpointslices', 'nodes'), 'get_kind')
    args = ['get', kind]
    if name:
        require(re.fullmatch('[a-z0-9.-]{1,253}', name), 'get_name')
        args.append(name)
    if selector:
        args += ['-l', selector]
    return json.loads(call(args + ['--show-managed-fields=false', '-o', 'json']))


def controlled_by(obj, uids):
    return any(x.get('controller') is True and x.get('uid') in uids
               for x in obj['metadata'].get('ownerReferences', []))


def owned(d):
    sel = d['spec']['selector']
    require(not sel.get('matchExpressions') and bool(sel.get('matchLabels')), 'unsupported_selector')
    labels = sel['matchLabels']
    selector = ','.join(k + '=' + v for k, v in sorted(labels.items()))
    rs = get('replicasets', selector=selector)['items']
    uids = {x['metadata']['uid'] for x in rs if controlled_by(x, {d['metadata']['uid']})}
    return [p for p in get('pods', selector=selector)['items'] if controlled_by(p, uids)
            and p.get('status', {}).get('phase') not in ('Failed', 'Succeeded')]


def ready(p):
    return (not p['metadata'].get('deletionTimestamp') and p['status'].get('phase') == 'Running'
            and any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    for c in p['status'].get('conditions', [])))


def identity(p):
    return [p['metadata']['uid'], sorted((c['name'], c.get('containerID'), c.get('restartCount'))
            for c in p['status'].get('containerStatuses', []))]


def container(p):
    require(len(p['spec']['containers']) == 1, 'single_container_expected')
    return p['spec']['containers'][0]


def literal_env(c):
    require(not c.get('envFrom'), 'envfrom_requires_separate_review')
    return {x['name']: x.get('value') for x in c.get('env', [])}


def baseline():
    deps = {n: get('deployments', n) for n in WORKERS + [DISPATCHER] + APPS + OLD}
    pods = {n: owned(d) for n, d in deps.items()}
    for n in WORKERS + [DISPATCHER] + APPS:
        require(len(pods[n]) == deps[n]['spec']['replicas'] and all(ready(p) for p in pods[n]),
                'deployment_not_stably_ready')
    require([len(pods[n]) for n in WORKERS] == [3, 3, 3, 3, 2, 2], 'candidate_worker_layout_changed')
    require(all(len(pods[n]) == 1 for n in [DISPATCHER] + APPS), 'singleton_layout_changed')
    require(all(deps[n]['spec']['replicas'] == 0 and not pods[n] for n in OLD), 'old_pool_not_retired')
    for n in WORKERS + [DISPATCHER]:
        require(all(container(p)['image'] == IMAGE for p in pods[n]), 'candidate_image_changed')
    svc = get('services', 'deer-flow-mineru-adapter')
    slices = get('endpointslices', selector='kubernetes.io/service-name=deer-flow-mineru-adapter')['items']
    refs = [e for s in slices for e in s.get('endpoints', [])]
    dispatch_uid = pods[DISPATCHER][0]['metadata']['uid']
    require(bool(refs) and all(e.get('targetRef', {}).get('uid') == dispatch_uid
            and e.get('conditions', {}).get('ready') is True
            and not e.get('conditions', {}).get('terminating', False) for e in refs), 'public_route_not_candidate')
    signature = {'deployments': {n: [d['metadata']['uid'], digest(d['spec'])] for n, d in deps.items()},
                 'service': [svc['metadata']['uid'], digest(svc['spec'])],
                 'pods': {n: sorted(identity(p) for p in ps) for n, ps in pods.items()}}
    safe = {}
    for n, d in deps.items():
        c = d['spec']['template']['spec']['containers'][0]
        vals = {x['name']: x.get('value') for x in c.get('env', []) if x['name'] in ENV_KEYS}
        safe[n] = {'replicas': d['spec']['replicas'], 'ready': len(pods[n]), 'image': c['image'],
                   'resources': c.get('resources', {}), 'concurrency_env_not_effective_settings':
                   {k: int(v) if v and re.fullmatch('[0-9]{1,6}', v) else 'UNRESOLVED' for k, v in vals.items()}}
    return deps, pods, signature, safe


HEALTH_CODE = '''import http.client,json
c=http.client.HTTPConnection('127.0.0.1',50052,timeout=5)
try:
 c.request('GET','/health',headers={'Connection':'close'})
 r=c.getresponse(); b=r.read(32769)
 assert r.status==200 and len(b)<=32768
 d=json.loads(b); role=d.get('role'); assert role in ('worker','dispatcher')
 k=('active','waiting','rejected') if role=='worker' else ('discovered','live','minimum','busy','waiting','quarantined','ready','capacity_target_met')
 v=d['parser' if role=='worker' else 'dispatcher']
 print(json.dumps({'role':role,'counters':{x:v.get(x) for x in k}}))
finally: c.close()
'''


def health(p):
    try:
        row = json.loads(call(['exec', p['metadata']['name'], '-c', container(p)['name'], '--',
                             'python3', '-B', '-c', HEALTH_CODE], timeout=12, limit=32768))
        keys = ('active', 'waiting', 'rejected') if row['role'] == 'worker' else (
                'discovered', 'live', 'minimum', 'busy', 'waiting', 'quarantined')
        require(all(type(row['counters'].get(k)) is int and 0 <= row['counters'][k] < 10**12
                    for k in keys), 'invalid_health_counters')
        if row['role'] == 'worker':
            require(row['counters']['active'] <= 1, 'worker_serialization_changed')
        return {'pod': p['metadata']['name'], 'role': row['role'], 'counters': row['counters']}
    except Exception as exc:
        return {'pod': p['metadata']['name'], 'unavailable': failure(exc)}


# All Redis traffic remains inside the existing Redis container. No credentials
# are retrieved, put on argv, written to evidence, or passed through this host.
REDIS_SHELL = r'''set +x
set -eu
test -n "${REDIS_PASSWORD:-}"
export REDISCLI_AUTH="$REDIS_PASSWORD"
db="$1"
case "$db" in ''|*[!0-9]*) exit 20;; esac
r() { redis-cli --no-auth-warning --raw -h 127.0.0.1 -p 6379 -n "$db" "$@"; }
integer() { case "$1" in ''|*[!0-9]*) exit 21;; esac; }
now=$(r TIME | head -n 1); integer "$now"
total=$(r SCARD asynq:queues); integer "$total"
printf 'T\t%s\t%s\n' "$now" "$total"
for q in default chat_attachment postprocess summary multimodal graph question memory sync low wiki; do
 exists=$(r SISMEMBER asynq:queues "$q"); integer "$exists"
 test "$exists" = 1 || continue
 prefix="asynq:{$q}:"
 printf 'Q\t%s' "$q"
 for pair in 'LLEN pending' 'LLEN active' 'ZCARD scheduled' 'ZCARD retry' 'ZCARD archived' 'ZCARD completed' 'GET processed' 'GET failed' 'EXISTS paused'; do
  set -- $pair
  v=$(r "$1" "$prefix$2")
  if test "$1" = GET && test -z "$v"; then v=0; fi
  integer "$v"; printf '\t%s' "$v"
 done
 id=$(r LINDEX "${prefix}pending" -1)
 since=0
 case "$id" in ''|*[!a-zA-Z0-9_-]*) ;; *)
  test "${#id}" -le 128 || exit 22
  since=$(r HGET "${prefix}t:$id" pending_since); since=${since:-0};;
 esac
 integer "$since"; printf '\t%s\n' "$since"
done
keys=$(r ZRANGEBYSCORE asynq:servers "$now" +inf LIMIT 0 33)
count=0
for key in $keys; do
 count=$((count+1)); test "$count" -le 32 || exit 23
 case "$key" in asynq:servers:\{*\}) ;; *) exit 24;; esac
 case "$key" in *[!a-zA-Z0-9_.:{}-]*) exit 24;; esac
 size=$(r STRLEN "$key"); integer "$size"; test "$size" -le 32768 || exit 25
 test "$size" -gt 0 || continue
 printf 'H\t%s\t' "$size"
 r GET "$key" | base64 | tr -d '\n'
 printf '\n'
done
printf 'END\n'
'''


def redis_binding(pods):
    app, redis = pods['weknora-app'][0], pods['weknora-redis'][0]
    ac, rc = container(app), container(redis)
    ae = literal_env(ac)
    literal_env(rc)
    allowed = ['weknora-redis:6379', 'weknora-redis.deer-flow:6379',
               'weknora-redis.deer-flow.svc:6379', 'weknora-redis.deer-flow.svc.cluster.local:6379']
    require(ae.get('REDIS_ADDR') in allowed and ae.get('REDIS_USERNAME', '') in ('', 'default'),
            'redis_connection_not_reviewed')
    require(ae.get('REDIS_USE_TLS', 'false') in ('', 'false', 'False', 'FALSE'), 'redis_tls_needs_separate_review')
    db = ae.get('REDIS_DB', '0')
    require(isinstance(db, str) and re.fullmatch('[0-9]{1,2}', db) and int(db) <= 15, 'redis_db_unresolved')
    def ref(c):
        xs = [e for e in c.get('env', []) if e['name'] == 'REDIS_PASSWORD']
        require(len(xs) == 1, 'redis_credential_binding_missing')
        v = xs[0].get('valueFrom', {}).get('secretKeyRef')
        require(v and v.get('name') and v.get('key') and not v.get('optional'), 'redis_credential_reference_required')
        return v
    require(ref(ac) == ref(rc), 'redis_credential_reference_mismatch')
    svc = get('services', 'weknora-redis')
    labels = redis['metadata']['labels']
    require(svc['spec'].get('selector') and all(labels.get(k) == v for k, v in svc['spec']['selector'].items()),
            'redis_service_binding_mismatch')
    slices = get('endpointslices', selector='kubernetes.io/service-name=weknora-redis')['items']
    eps = [e for s in slices for e in s.get('endpoints', [])]
    require(eps and all(e.get('targetRef', {}).get('uid') == redis['metadata']['uid'] for e in eps),
            'redis_endpoint_binding_mismatch')
    return redis, db, app['spec'].get('hostname', app['metadata']['name'])


def varint(raw, pos):
    n = 0
    for shift in range(0, 70, 7):
        require(pos < len(raw), 'heartbeat_truncated')
        b = raw[pos]; pos += 1
        n |= (b & 127) << shift
        if b < 128:
            return n, pos
    raise CheckFailure('heartbeat_varint_overflow')


def protobuf(raw):
    require(len(raw) <= 32768, 'heartbeat_size_limit')
    out, pos = {}, 0
    while pos < len(raw):
        tag, pos = varint(raw, pos)
        field, wire = tag >> 3, tag & 7
        require(field > 0, 'heartbeat_field_invalid')
        if wire == 0:
            value, pos = varint(raw, pos)
        elif wire in (1, 2, 5):
            if wire == 2:
                size, pos = varint(raw, pos)
            else:
                size = 8 if wire == 1 else 4
            require(size <= len(raw) - pos, 'heartbeat_truncated')
            value = raw[pos:pos+size]; pos += size
        else:
            raise CheckFailure('heartbeat_wire_type')
        out.setdefault(field, []).append(value)
    return out


def pool_kind(queues):
    qs = set(queues)
    if qs and qs <= {'default', 'chat_attachment'} and 'default' in qs:
        return 'core'
    if 'default' in qs and qs & ENRICHMENT and qs <= ENRICHMENT | {'default', 'chat_attachment'}:
        return 'shared'
    if qs and qs <= ENRICHMENT:
        return 'enrichment'
    if qs and qs <= {'sync', 'low'}:
        return 'maintenance'
    if qs == {'postprocess'}:
        return 'postprocess'
    if qs == {'wiki'}:
        return 'wiki'
    return 'UNRECOGNIZED'


def server_info(raw, expected_host):
    f = protobuf(raw)
    def one(k, default):
        xs = f.get(k, [default]); require(len(xs) == 1, 'duplicate_heartbeat_field')
        return xs[0]
    host = one(1, b'').decode('utf-8')
    if host != expected_host:
        return None  # Never export another application's heartbeat.
    concurrency, active = one(4, 0), one(9, 0)
    require(type(concurrency) is int and type(active) is int and 0 < concurrency <= 4096
            and 0 <= active <= concurrency, 'heartbeat_capacity_invalid')
    queues = {}
    for entry in f.get(5, []):
        m = protobuf(entry)
        require(len(m.get(1, [])) == len(m.get(2, [])) == 1, 'heartbeat_queue_invalid')
        name, weight = m[1][0].decode('utf-8'), m[2][0]
        require(name in QUEUES and name not in queues and type(weight) is int and weight > 0,
                'heartbeat_unknown_queue_or_weight')
        queues[name] = weight
    status = one(7, b'').decode('ascii')
    require(status in ('active', 'stopped', 'closed', 'new'), 'heartbeat_status_unknown')
    return {'pool': pool_kind(queues), 'concurrency': concurrency, 'active': active,
            'status': status, 'queue_weights': queues}


def parse_redis(raw, host):
    lines = raw.decode('ascii').splitlines()
    require(lines and lines[-1] == 'END', 'redis_snapshot_incomplete')
    head = lines[0].split('\t')
    require(len(head) == 3 and head[0] == 'T', 'redis_snapshot_header')
    now, registered = map(int, head[1:])
    require(now > 0 and registered >= 0, 'redis_time_or_queue_count')
    queues, servers, expired, heartbeat_errors = {}, [], 0, []
    for line in lines[1:-1]:
        parts = line.split('\t')
        if parts[0] == 'Q':
            require(len(parts) == 12 and parts[1] in QUEUES and parts[1] not in queues, 'redis_queue_record')
            vals = list(map(int, parts[2:])); require(all(v >= 0 for v in vals), 'negative_queue_counter')
            names = ('pending', 'active', 'scheduled', 'retry', 'archived', 'completed',
                     'processed_total', 'failed_total', 'paused', 'pending_since_ns')
            q = dict(zip(names, vals)); since = q.pop('pending_since_ns')
            require(q['paused'] in (0, 1), 'queue_paused_invalid')
            q['oldest_pending_seconds'] = (round(max(0, now - since / 1e9), 2) if since else
                                          (0 if q['pending'] == 0 else None))
            queues[parts[1]] = q
        elif parts[0] == 'H':
            require(len(parts) == 3, 'redis_heartbeat_record')
            data = base64.b64decode(parts[2], validate=True)
            require(data.endswith(b'\n'), 'redis_raw_delimiter')
            data = data[:-1]  # Exactly redis-cli's separator, never strip protobuf bytes.
            if not data:  # ServerInfo TTL may expire between STRLEN and GET.
                expired += 1; continue
            require(len(data) <= 32768, 'heartbeat_size_limit')
            try:
                info = server_info(data, host)
                if info:
                    servers.append(info)
            except Exception as exc:
                heartbeat_errors.append(failure(exc))
        else:
            raise CheckFailure('redis_record_unknown')
    require(registered >= len(queues), 'queue_registry_changed_during_read')
    return {'queues': queues, 'effective_servers': servers, 'expired_heartbeats': expired,
            'heartbeat_unavailable': heartbeat_errors, 'effective_pool_summary': summarize_pools(servers),
            'uninspected_queue_count': registered - len(queues), 'redis_sample_epoch': now,
            'task_payloads_read': False, 'non_atomic_snapshot': True}


def summarize_pools(servers):
    result = {}
    for name in ('core', 'postprocess', 'enrichment', 'maintenance', 'shared', 'wiki'):
        rows = [s for s in servers if s['pool'] == name]
        result[name] = {'heartbeat_records': len(rows), 'effective_concurrency': None, 'active': None}
        if len(rows) == 1 and rows[0]['status'] == 'active':
            result[name].update(effective_concurrency=rows[0]['concurrency'], active=rows[0]['active'])
    return result


def redis_sample(binding):
    p, db, host = binding
    return parse_redis(call(['exec', p['metadata']['name'], '-c', container(p)['name'], '--',
                            'sh', '-c', REDIS_SHELL, 'observe', db], timeout=25, limit=1048576), host)


def summary_stats(vals):
    if not vals:
        return {'count': 0, 'p50_seconds': None, 'p95_seconds': None}
    vals = sorted(vals)
    return {'count': len(vals), 'p50_seconds': vals[math.ceil(.5 * len(vals))-1],
            'p95_seconds': vals[math.ceil(.95 * len(vals))-1], 'max_seconds': vals[-1]}


def log_stats(raw):
    # Only numeric allowlisted suffixes leave this function. No filename/task ID/errors.
    series = {'parse_completed': [], 'parse_failed': [], 'dispatch_elapsed': [],
              'dispatch_wait_interactive': [], 'dispatch_wait_bulk': []}
    transport_errors = 0
    for line in raw.decode('utf-8', errors='replace').splitlines():
        if 'Parse finished request_id=' in line:
            m = re.search(r' status=(completed|failed) elapsed_seconds=([0-9]+\.[0-9]{3})$', line)
            if m:
                series['parse_' + m[1]].append(float(m[2]))
        elif 'Dispatch finished request_id=' in line:
            m = re.search(r' elapsed_seconds=([0-9]+\.[0-9]{3})$', line)
            if m:
                series['dispatch_elapsed'].append(float(m[1]))
        elif 'Dispatch started request_id=' in line:
            m = re.search(r' priority=(interactive|bulk) backend=\S+ wait_seconds=([0-9]+\.[0-9]{3}) bytes=\d+ busy_attempt=\d+$', line)
            if m:
                series['dispatch_wait_' + m[1]].append(float(m[2]))
        elif 'Dispatch transport failure request_id=' in line:
            transport_errors += 1
    return series, transport_errors


def dependency_log_markers(raw):
    patterns = {'rate_limit_or_quota_marker': r'(?i)rate.?limit|too many requests|(?:status|code)[=: ]+429\b|quota.?exceed',
                'timeout_marker': r'(?i)context deadline exceeded|i/o timeout|timed out|readtimeout|connecttimeout',
                'connection_failure_marker': r'(?i)connection refused|connection reset|broken pipe',
                'embedding_failure_marker': r'(?i)(?:failed|error).{0,40}embedding|embedding.{0,40}(?:failed|error)'}
    lines = raw.decode('utf-8', errors='replace').splitlines()
    return {name: sum(bool(re.search(pattern, line)) for line in lines) for name, pattern in patterns.items()}


def logs(p):
    try:
        raw = call(['logs', p['metadata']['name'], '-c', container(p)['name'],
                    '--since=10m', '--timestamps=true', '--tail=2000', '--limit-bytes=262144'], limit=262145)
        values, errors = log_stats(raw)
        return {'pod': p['metadata']['name'], 'values': values, 'transport_errors': errors,
                'marker_line_counts_not_provider_measurements': dependency_log_markers(raw),
                'byte_limit_reached': len(raw) >= 262144}
    except Exception as exc:
        return {'pod': p['metadata']['name'], 'unavailable': failure(exc)}


def interpret(samples):
    valid = [s for s in samples if 'redis' in s and s.get('worker_reads_complete') and s.get('dispatcher')]
    findings = []
    if len(valid) != len(samples):
        findings.append('INCOMPLETE_SAMPLES_NO_TUNING_APPROVAL')
    if valid:
        busy = [s['active_workers'] for s in valid]
        backlog = [s['redis']['queues'].get('default', {}).get('pending', 0) for s in valid]
        findings.append('DEFAULT_QUEUE_BACKLOG_OBSERVED' if max(backlog) else 'NO_DEFAULT_BACKLOG_OBSERVED')
        if max(busy) <= 12:
            findings.append('MINERU_HEADROOM_AT_SAMPLED_INSTANTS_NOT_PROOF_OF_SPARE_THROUGHPUT')
        if any(s['dispatcher'].get('waiting', 0) > 0 for s in valid):
            findings.append('DISPATCHER_WAITING_OBSERVED')
    queues = {}
    for q in QUEUES:
        rows = [s['redis']['queues'][q] for s in samples if q in s.get('redis', {}).get('queues', {})]
        if not rows:
            continue
        deltas = {}
        for k in ('processed_total', 'failed_total'):
            values = [r[k] for r in rows]
            deltas[k] = values[-1] - values[0] if len(values) > 1 and all(a <= b for a, b in zip(values, values[1:])) else None
        queues[q] = {'samples': len(rows), 'pending_first': rows[0]['pending'], 'pending_last': rows[-1]['pending'],
                     'pending_max': max(r['pending'] for r in rows), 'active_max': max(r['active'] for r in rows),
                     'retry_max': max(r['retry'] for r in rows), 'counter_deltas_not_document_counts': deltas}
    return {'findings': findings, 'configuration_change_authorized_by_this_report': False,
            'all_queue_trends': queues,
            'next_action': 'REVIEW_EFFECTIVE_POOLS_QUEUE_TRENDS_AND_RESOURCE_HEADROOM',
            'limitations': ['Heartbeat is delayed; queue reads and worker samples are not atomic.',
                'Active Asynq jobs include work other than MinerU OCR; shared slots serve several queues.',
                'Completed tasks are not document counts; sampled idle slots do not prove safe scaling.',
                'Provider GPU time, embedding latency and business-document quality are not measured.',
                'This observer does not call provider endpoints or infer a safe 32/64 concurrency.']}


def export_safe(raw):
    import pwd
    user = pwd.getpwnam('cnooc')
    home = Path('/home/cnooc'); safe_parent(home.parent)
    s = home.lstat()
    require(stat.S_ISDIR(s.st_mode) and s.st_uid == user.pw_uid and not s.st_mode & 0o022, 'download_parent_unsafe')
    path = Path(tempfile.mkdtemp(prefix='mineru-pipeline-download-', dir=str(home)))
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        f = os.open('RESULT.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        with os.fdopen(f, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400); os.fchown(stream.fileno(), user.pw_uid, user.pw_gid)
        os.fchmod(fd, 0o500); os.fchown(fd, user.pw_uid, user.pw_gid)
    finally:
        os.close(fd)
    return str(path / 'RESULT.json')


def resource_snapshot(pods):
    """Read kubelet samples when metrics-server is absent; no node debug Pods."""
    targets = [p for group in pods.values() for p in group]
    rows = []
    for node in sorted({p['spec']['nodeName'] for p in targets}):
        require(re.fullmatch('[a-z0-9-]{1,63}', node), 'resource_node_name')
        emit({'resource_sample': 'RUNNING', 'node': node})
        try:
            data = json.loads(call(['get', '--raw', '/api/v1/nodes/' + node + '/proxy/stats/summary'], limit=8388608))
            config = get('nodes', node)
            n = data['node']; wanted = {p['metadata']['uid'] for p in targets if p['spec']['nodeName'] == node}
            nr = {'node': node, 'cpu': {k: n.get('cpu', {}).get(k) for k in ('time', 'usageNanoCores')},
                  'allocatable': {k: config.get('status', {}).get('allocatable', {}).get(k)
                                  for k in ('cpu', 'memory', 'ephemeral-storage', 'pods')},
                  'memory': {k: n.get('memory', {}).get(k) for k in ('time', 'availableBytes', 'workingSetBytes')},
                  'filesystem': {k: n.get('fs', {}).get(k) for k in ('time', 'availableBytes', 'capacityBytes')}, 'pods': []}
            for p in data.get('pods', []):
                if p.get('podRef', {}).get('uid') in wanted:
                    nr['pods'].append({'pod': p['podRef']['name'], 'cpu':
                        {k: p.get('cpu', {}).get(k) for k in ('time', 'usageNanoCores')}, 'memory':
                        {k: p.get('memory', {}).get(k) for k in ('time', 'workingSetBytes')}})
            rows.append(nr)
        except Exception as exc:
            rows.append({'node': node, 'unavailable': failure(exc)})
    return rows


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'run_on_datax01_as_root')
    require(len(sys.argv) == 1, 'no_modes_or_extra_arguments')
    os.umask(0o077); safe_parent('/var/lib')
    root = Path(tempfile.mkdtemp(prefix='mineru-pipeline-observe-', dir='/var/lib'))
    os.chmod(str(root), 0o700)
    report = {'version': VERSION, 'utc_started': utc(), 'production_writes': 0, 'provider_requests': 0,
              'documents_submitted': 0, 'secret_api_reads': 0, 'credentials_exported': False, 'samples': [],
              'evidence': str(root), 'production_configuration_modified': False, 'stage': 'BASELINE'}
    emit({'observation_evidence': str(root), 'samples': 7, 'interval_seconds': 30, 'production_writes': 0})
    exit_code = 1
    try:
        read_expansion()
        deps, pods, signature, report['baseline'] = baseline()
        mineru = [p for n in WORKERS + [DISPATCHER] for p in pods[n]]
        try:
            binding = redis_binding(pods)
        except Exception as exc:
            binding = None; report['redis_binding_unavailable'] = failure(exc)
        report['stage'] = 'BOUNDED_OBSERVATION'
        for i in range(7):
            began = time.monotonic()
            row = {'number': i + 1, 'utc': utc()}
            emit({'observation': 'RUNNING', 'sample': i + 1, 'of': 7})
            with ThreadPoolExecutor(max_workers=4) as pool:
                f = pool.submit(redis_sample, binding) if binding else None
                hs = list(pool.map(health, mineru))
                if f:
                    try:
                        row['redis'] = f.result()
                    except Exception as exc:
                        row['redis_unavailable'] = failure(exc)
            row['workers'] = [x for x in hs if x.get('role') == 'worker']
            row['worker_reads_complete'] = len(row['workers']) == 16
            row['active_workers'] = sum(x['counters']['active'] for x in row['workers']) if row['worker_reads_complete'] else None
            row['dispatcher'] = next((x['counters'] for x in hs if x.get('role') == 'dispatcher'), None)
            row['unavailable_reads'] = [x for x in hs if 'unavailable' in x]
            row['collection_seconds'] = round(time.monotonic() - began, 2)
            report['samples'].append(row)
            emit({'sample': i + 1, 'active_workers': row['active_workers'], 'worker_reads_complete': row['worker_reads_complete'],
                  'dispatcher': row['dispatcher'], 'redis_read': 'PASS' if 'redis' in row else 'UNAVAILABLE'})
            if i < 6:
                time.sleep(max(0, 30 - (time.monotonic() - began)))
        report['stage'] = 'BOUNDED_LOG_AGGREGATES'
        emit({'observation': 'RUNNING', 'phase': report['stage'], 'raw_logs_saved': False})
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(logs, mineru))
            dependency_logs = list(pool.map(logs, pods['weknora-app'] + pods['weknora-docreader']))
        report['weknora_docreader_log_indicators'] = [{k: v for k, v in r.items() if k != 'values'} for r in dependency_logs]
        values = {}
        for r in results:
            for k, v in r.get('values', {}).items():
                values.setdefault(k, []).extend(v)
        report['latency_last_10m_bounded_tail'] = {k: summary_stats(v) for k, v in values.items()}
        report['log_coverage'] = [{k: v for k, v in r.items() if k != 'values'} for r in results]
        report['stage'] = 'RESOURCE_SAMPLE_AND_BASELINE_RECHECK'
        emit({'observation': 'RUNNING', 'phase': report['stage']})
        try:
            metrics = json.loads(call(['get', '--raw', '/apis/metrics.k8s.io/v1beta1/namespaces/deer-flow/pods']))
            names = {p['metadata']['name'] for ps in pods.values() for p in ps}
            report['pod_resource_metrics'] = [{'pod': x['metadata']['name'], 'timestamp': x.get('timestamp'),
                    'window': x.get('window'), 'containers': [{'name': c['name'], 'usage':
                    {k: v for k, v in c.get('usage', {}).items() if k in ('cpu', 'memory')}} for c in x['containers']]}
                for x in metrics.get('items', []) if x['metadata']['name'] in names]
        except Exception as exc:
            report['resource_metrics_unavailable'] = failure(exc)
        report['node_and_target_pod_resources'] = resource_snapshot(pods)
        _, _, after, _ = baseline()
        require(signature == after, 'baseline_or_process_changed_during_observation')
        report['baseline_stable'] = True
        report['assessment'] = interpret(report['samples'])
        report['observation'] = 'COMPLETE'
        report['automatic_tuning'] = False
        exit_code = 0
    except Exception as exc:
        report['observation'] = 'INCOMPLETE'; report['failure'] = failure(exc)
    report['utc_finished'] = utc()
    raw = write_new(root / 'RESULT.json', report)
    receipt = {'observation': report['observation'], 'stage': report['stage'], 'report': str(root / 'RESULT.json'),
               'report_sha256': hashlib.sha256(raw).hexdigest(), 'production_writes': 0, 'provider_requests': 0,
               'collection_completion_is_not_tuning_approval': True, 'automatic_retry': False}
    if 'failure' in report:
        receipt['failure'] = report['failure']
    try:
        receipt['download_file'] = export_safe(raw)
    except Exception as exc:
        receipt['download_copy'] = 'UNAVAILABLE_PRIVATE_REPORT_RETAINED'
        receipt['download_failure'] = failure(exc)
    emit(receipt)
    return exit_code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit({'observation': 'INCOMPLETE', 'failure': failure(exc), 'production_writes': 0, 'provider_requests': 0})
        sys.exit(1)
