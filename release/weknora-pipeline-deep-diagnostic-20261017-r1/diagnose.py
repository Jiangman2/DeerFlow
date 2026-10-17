#!/usr/bin/env python3
"""Five-minute read-only GLM104 diagnosis. Python3.7+, no third-party imports."""
import argparse
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
import threading
import time
import types

PRIOR = Path('/var/lib/weknora-glm104-diagnostic-tool-7whC6f8S/check.py')
PRIOR_SHA = '9756acd719f1fc78e84afbda621bdb0af32bbb15334d08ca0d197bb076dc5c74'
PREFIX = 'weknora-pipeline-deep-'
CAPS = dict(core=16, enrichment=64, shared=4, postprocess=4, maintenance=4, wiki=12)
MODELS = ('deerflow-cnooc-deepseek-v4-flash', 'deerflow-cnooc-qwen3-embedding-8b',
          'deerflow-cnooc-glm-5.3-flash', 'deerflow-dashscope-vlm')
SAMPLES, INTERVAL = 11, 30
LOG_BYTES = 256 * 1024**2  # per stream, never accumulated in memory or saved
LINE_BYTES = 64 * 1024
TIMING_CAP = 20000
SPAN_HELPER = None  # Filled from reviewed source by package.py, not server files.


class Failure(RuntimeError):
    pass


def need(ok, label):
    if not ok:
        raise Failure(label)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def emit(row):
    print(json.dumps(row, sort_keys=True, ensure_ascii=True, allow_nan=False), flush=True)


def safe_error(exc):
    # Untrusted program stderr, exception arguments and frames are never printed.
    label = str(exc) if isinstance(exc, Failure) and re.fullmatch('[a-z0-9_]{1,100}', str(exc)) else 'read_unavailable'
    return dict(check=label, error_type=type(exc).__name__, raw_error_printed=False)


def safe_read(path, expected=None, maximum=4*1024**2):
    path = Path(path)
    for p in (path.parent,) + tuple(path.parent.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_private_parent')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
             and not before.st_mode & 0o022 and before.st_size <= maximum, 'unsafe_private_file')
        raw = f.read(maximum+1)
        after = os.fstat(f.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_uid, s.st_mode, s.st_nlink)
    need(identity(before) == identity(after) == identity(path.lstat()) and len(raw) == before.st_size, 'private_file_changed')
    need(expected is None or hashlib.sha256(raw).hexdigest() == expected, 'input_hash_changed')
    return raw


def save(root, name, value):
    need(Path(name).name == name, 'invalid_evidence_name')
    raw = (json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, indent=2)+'\n').encode()
    fd = os.open(str(root/name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())
    return hashlib.sha256(raw).hexdigest()


def initialize():
    raw = safe_read(PRIOR, PRIOR_SHA)
    d = types.ModuleType('frozen_readonly_diagnostic'); d.__file__ = str(PRIOR)
    exec(compile(raw, str(PRIOR), 'exec'), d.__dict__)
    d.bind_receipt()
    sw = types.ModuleType('frozen_readonly_switch'); sw.__file__ = str(d.TOOL)
    exec(compile(d.safe_read(d.TOOL, d.TOOL_SHA), str(d.TOOL), 'exec'), sw.__dict__)
    chain = sw.load_review()  # imports hash-verified readers; never invokes a CLI
    obs, act = chain.o.r.obs, chain.o.r.e
    need(CAPS == chain.TARGET, 'reviewed_capacity_contract_changed')
    need(isinstance(SPAN_HELPER, str), 'packaged_span_helper_required')
    h = types.ModuleType('readonly_span_projection')
    h.__dict__.update(obs=obs, review=act.review, math=math, json=json)
    exec(compile(SPAN_HELPER, 'reviewed_span_projection.py', 'exec'), h.__dict__)
    return d, obs, act, h


CGROUP_CODE = r'''import json,os
from pathlib import Path
result={"source":"CURRENT_EXEC_CONTAINER_CGROUP","values":{},"unavailable":[],"identity_binding":"PROC_SELF_CGROUP_AND_MOUNTINFO"}
def bounded(path,maximum):
 with open(path) as f: value=f.read(maximum+1)
 if len(value)>maximum: raise ValueError("bounded")
 return value
groups={}
for line in bounded('/proc/self/cgroup',65536).splitlines():
 bits=line.split(':',2)
 if len(bits)!=3 or not bits[2].startswith('/'): raise ValueError('cgroup_shape')
 for control in bits[1].split(','): groups[control]=bits[2]
mounts=[]
for line in bounded('/proc/self/mountinfo',262144).splitlines():
 parts=line.split(); sep=parts.index('-')
 if parts[sep+1] not in ('cgroup','cgroup2'): continue
 root,mount=parts[3],parts[4]
 if '\\' in root+mount: continue
 if mount!='/sys/fs/cgroup' and not mount.startswith('/sys/fs/cgroup/'): continue
 mounts.append((root,mount,parts[sep+1],parts[sep+3].split(',')))
def resolve(controller):
 current=groups.get(controller)
 if current is None: return None
 for root,mount,kind,controls in mounts:
  if not ((controller=='' and kind=='cgroup2') or (kind=='cgroup' and controller in controls)): continue
  if current==root: relative=''
  elif root=='/': relative=current.lstrip('/')
  elif current.startswith(root.rstrip('/')+'/'): relative=current[len(root):].lstrip('/')
  else: continue
  if '..' in relative.split('/'): continue
  return str(Path(mount)/relative)
 return None
paths={}
v2=resolve('')
if v2:
 for name in ('memory.max','memory.current','memory.peak','memory.events','cpu.stat','cpu.max'):
  paths['v2_'+name.replace('.','_')]=v2+'/'+name
v1memory=resolve('memory')
if v1memory:
 for label,name in (('limit','memory.limit_in_bytes'),('usage','memory.usage_in_bytes'),('peak','memory.max_usage_in_bytes'),('failcnt','memory.failcnt'),('oom_control','memory.oom_control')):
  paths['v1_memory_'+label]=v1memory+'/'+name
v1cpu=resolve('cpu')
if v1cpu:
 for name in ('cpu.stat','cpu.cfs_quota_us','cpu.cfs_period_us'):
  paths['v1_'+name.replace('.','_')]=v1cpu+'/'+name
for label,path in paths.items():
 try:
  text=bounded(path,4096)
  rows=[line.split() for line in text.splitlines()]
  if len(rows)==1 and all(v=="max" or v.lstrip("-").isdigit() for v in rows[0]):
   result["values"][label]=[v if v=="max" else int(v) for v in rows[0]]
  elif rows and all(len(r)==2 and r[0].replace("_", "").isalnum() and r[1].isdigit() for r in rows):
   result["values"][label]={r[0]:int(r[1]) for r in rows}
  else: raise ValueError()
 except FileNotFoundError: pass
 except Exception: result["unavailable"].append(label)
print(json.dumps(result))
'''


def cgroup(obs, pod):
    raw = obs.call(['exec', pod['metadata']['name'], '-c', obs.container(pod)['name'], '--',
                    'python3', '-I', '-B', '-c', CGROUP_CODE], timeout=15, limit=16384)
    row = json.loads(raw)
    need(row.get('source') == 'CURRENT_EXEC_CONTAINER_CGROUP' and isinstance(row.get('values'), dict)
         and len(row['values']) <= 30 and isinstance(row.get('unavailable'), list), 'cgroup_shape')
    # Keys and values are projected by our literal probe, never process args/env.
    need(bool(row['values']) and row.get('identity_binding') == 'PROC_SELF_CGROUP_AND_MOUNTINFO', 'cgroup_metrics_unavailable')
    return dict(pod=pod['metadata']['name'], utc=utc(), container_identity=obs.identity(pod), **row)


def cgroup_delta(before, after):
    if before['container_identity'] != after['container_identity']:
        return dict(available=False, reason='container_changed')
    out = {}
    for key, v in before['values'].items():
        end = after['values'].get(key)
        if isinstance(v, dict) and isinstance(end, dict):
            delta = {k:end[k]-x for k,x in v.items() if k in end and isinstance(x, int) and isinstance(end[k], int)}
            if any(x < 0 for x in delta.values()):
                out[key] = dict(counter_reset=True); continue
            out[key] = delta
            if 'nr_periods' in delta:
                out[key]['throttled_period_fraction'] = (round(delta.get('nr_throttled', 0)/delta['nr_periods'], 4)
                                                           if delta['nr_periods'] else None)
        elif 'failcnt' in key and isinstance(v, list) and isinstance(end, list) and len(v) == len(end) == 1:
            out[key] = dict(delta=end[0]-v[0], counter_reset=end[0] < v[0])
    return dict(available=True, pod=before['pod'], counters=out,
                throttled_period_fraction_is_not_cpu_time_fraction=True)


def resource_spec(obs, pod):
    c = obs.container(pod)
    resources = c.get('resources', {})
    def fields(section):
        return {k:v for k,v in resources.get(section, {}).items() if k in ('cpu', 'memory', 'ephemeral-storage')}
    return dict(pod=pod['metadata']['name'], node=pod['spec'].get('nodeName'), requests=fields('requests'),
                limits=fields('limits'), runtime_identity=obs.identity(pod))


def inventory(d, obs):
    pods, signature = d.inventory(obs)
    dep = obs.get('deployments', 'weknora-docreader')
    readers = obs.owned(dep)
    need(len(readers) == 1 and obs.ready(readers[0]), 'docreader_identity_requires_review')
    pods['weknora-docreader'] = readers
    signature['docreader'] = dict(uid=dep['metadata']['uid'], spec=obs.digest(dep['spec']),
                                 pods=[obs.identity(p) for p in readers])
    return pods, signature


MODEL_SHELL = r'''set +x
set -eu
test -n "${REDIS_PASSWORD:-}"
export REDISCLI_AUTH="$REDIS_PASSWORD"
db="$1"
case "$db" in ''|*[!0-9]*) exit 20;; esac
r() { redis-cli --no-auth-warning --raw -h 127.0.0.1 -p 6379 -n "$db" "$@"; }
clock=$(r TIME); set -- $clock
test "$#" -eq 2
case "$1$2" in ''|*[!0-9]*) exit 21;; esac
millis=$(($1*1000+$2/1000))
printf 'T\t%s\n' "$1"
for model in '''+' '.join(MODELS)+r'''; do
 count=$(r ZCOUNT "weknora:modelsem:$model" "($millis" +inf)
 case "$count" in ''|*[!0-9]*) exit 22;; esac
 printf 'M\t%s\t%s\n' "$model" "$count"
done
printf 'END\n'
'''


def model_snapshot(obs, binding):
    redis, db, _ = binding
    raw = obs.call(['exec', redis['metadata']['name'], '-c', obs.container(redis)['name'], '--',
                    'sh', '-c', MODEL_SHELL, 'diagnostic', db], timeout=20, limit=4096)
    lines = raw.decode('ascii').splitlines()
    need(len(lines) == len(MODELS)+2 and lines[-1] == 'END', 'model_counter_shape')
    head = lines[0].split('\t'); need(len(head) == 2 and head[0] == 'T' and head[1].isdigit(), 'model_clock')
    counts = {}
    for line in lines[1:-1]:
        v = line.split('\t')
        need(len(v) == 3 and v[0] == 'M' and v[1] in MODELS and v[1] not in counts
             and v[2].isdigit() and len(v[2]) <= 8, 'model_counter_invalid')
        counts[v[1]] = int(v[2])
    need(abs(int(head[1])-time.time()) < 45, 'model_clock_stale')
    return dict(epoch=int(head[1]), holders=counts, local_waiters='NOT_MEASURED',
                scope='UNEXPIRED_REDIS_LEASES_NOT_PROVIDER_JOBS_CAN_INCLUDE_STALE_LEASES')


DB_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
SELECT json_build_object('transaction_read_only',current_setting('transaction_read_only'),
 'max_connections',current_setting('max_connections')::int,
 'all_connections',(SELECT count(*) FROM pg_stat_activity),
 'this_database',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()),
 'idle_in_transaction',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'),
 'models',(SELECT coalesce(json_agg(json_build_object('id',id,'max_concurrency',max_concurrency)),'[]'::json)
 FROM public.models WHERE id IN (%s)));
ROLLBACK;""" % ','.join("'%s'" % x for x in MODELS)


def database_snapshot(h, pg):
    row = h.sql_read(pg, DB_SQL)
    need(row.get('transaction_read_only') == 'on', 'database_not_read_only')
    out = dict(transaction_read_only='on', models=[])
    for key in ('max_connections', 'all_connections', 'this_database', 'idle_in_transaction'):
        need(type(row.get(key)) is int and 0 <= row[key] <= 1000000, 'database_counter_shape')
        out[key] = row[key]
    need(isinstance(row.get('models'), list) and len(row['models']) <= len(MODELS), 'model_db_shape')
    seen = set()
    for v in row['models']:
        need(v.get('id') in MODELS and v['id'] not in seen
             and type(v.get('max_concurrency')) is int and 0 <= v['max_concurrency'] <= 1000000, 'model_db_counter')
        seen.add(v['id']); out['models'].append(dict(id=v['id'], max_concurrency=v['max_concurrency']))
    out['zero_model_limit_means_startup_default_not_infinite'] = True
    out['go_sql_pool_wait_count'] = 'NOT_EXPOSED_BY_EXISTING_INSTRUMENTATION'
    return out


PATTERNS = {
 'image_task_start':r'\[ImageMultimodal\] Processing image:',
 'image_read_failure':r'\[ImageMultimodal\] Skip unreadable image',
 'image_downloaded':r'\[ImageMultimodal\] Image downloaded from URL',
 'image_ocr_failure':r'\[ImageMultimodal\] OCR failed',
 'image_caption_failure':r'\[ImageMultimodal\] Caption failed',
 'image_index_failure':r'\[ImageMultimodal\] Failed to index multimodal chunks',
 'image_index_done':r'\[ImageMultimodal\] Indexed \d+ multimodal chunks',
 'image_orphan_drop':r'\[ImageMultimodal\] Dropping task',
 'core_index_done':r'processChunks batch index successfully',
 'core_chunks_done':r'processChunks successfully',
 'summary_failure':r'GetSummary failed|Summary refresh failed',
 'summary_done':r'GetSummary success',
 'limiter_fail_open':r'\[ModelLimiter\] acquire failed.*failing open',
 'glm_timeout':r'enterprise GLM: (?:context deadline exceeded|stream deadline exceeded)',
 'glm_http_429':r'enterprise GLM: HTTP status 429\b',
 'glm_http_5xx':r'enterprise GLM: HTTP status 5\d\d\b',
 'glm_transport':r'enterprise GLM: transport failed',
 'glm_invalid_completion':r'enterprise GLM: (?:incomplete or empty completion|stream incomplete)',
 'embedding_failure':r'(?:[Ee]mbedder|[Ee]mbedding).*(?:failed|error|Error)',
 'timeout_marker':r'(?i)deadline exceeded|timed out|ReadTimeout|ConnectTimeout',
 'rate_or_quota_marker':r'(?i)rate.?limit|too many requests|quota.?exceed|status[ :=]+429\b',
 'mineru_submission_400':r'Enterprise MinerU HTTP 400 during asynchronous submission:',
 'mineru_http_5xx':r'Enterprise MinerU HTTP 5\d\d during ',
 'mineru_async_deadline':r'Enterprise MinerU .* (?:exceeded|was not ready within) .*seconds',
 'mineru_empty_result':r'Enterprise MinerU returned (?:an invalid parse result|no parseable content|an invalid images field)',
 'dispatch_admission_timeout':r'MinerU dispatcher unavailable:.*(?:wait|capacity|queue)',
 'dispatch_quarantine':r'MinerU dispatcher quarantined backend=',
}
COMPILED = {k:re.compile(v) for k,v in PATTERNS.items()}


def timestamp(line):
    m = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:\d\d)\s', line)
    if not m:
        return None
    try:
        return datetime.datetime.fromisoformat(m[1]+m[2].replace('Z', '+00:00')).astimezone(datetime.timezone.utc).isoformat()
    except ValueError:
        return None


class Aggregate:
    def __init__(self, obs, name):
        self.obs, self.name = obs, name
        self.counts = {k:0 for k in PATTERNS}
        self.times = {k:[] for k in ('parse_completed', 'parse_failed', 'dispatch_elapsed',
                                    'dispatch_wait_interactive', 'dispatch_wait_bulk')}
        self.lines = self.oversized = self.unstamped = 0
        self.first = self.last = None
        self.timing_cap = False

    def line(self, raw):
        self.lines += 1
        if len(raw) > LINE_BYTES:
            self.oversized += 1; return
        text = raw.decode('utf-8', 'replace')
        stamp = timestamp(text)
        if stamp:
            if self.first is None: self.first = stamp
            self.last = stamp
        else:
            self.unstamped += 1
        for label, pattern in COMPILED.items():
            if pattern.search(text): self.counts[label] += 1
        if 'Parse finished request_id=' in text or 'Dispatch finished request_id=' in text or 'Dispatch started request_id=' in text:
            series, _ = self.obs.log_stats(raw)
            for key, values in series.items():
                if len(self.times[key])+len(values) <= TIMING_CAP:
                    self.times[key].extend(values)
                else: self.timing_cap = True

    def result(self):
        return dict(pod=self.name, lines=self.lines, oversized_lines_dropped=self.oversized,
                    untimestamped_lines=self.unstamped, first_retained_utc=self.first, last_retained_utc=self.last,
                    marker_lines={k:v for k,v in self.counts.items() if v},
                    timings={k:self.obs.summary_stats(v) for k,v in self.times.items()},
                    timing_cap_reached=self.timing_cap, counts_are_not_unique_documents=True,
                    pure_glm_vs_image_embedding_latency='NOT_INSTRUMENTED_NO_FABRICATED_PAIRING', raw_logs_saved=False)


def follow_command(pod, container, since):
    need(re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}', pod) and re.fullmatch('[a-z0-9][a-z0-9-]{0,62}', container), 'invalid_log_target')
    return ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=0', '-n', 'deer-flow',
            'logs', pod, '-c', container, '--follow=true', '--since-time='+since, '--timestamps=true', '--tail=-1']


def stream(command, aggregate, stop, ready, budget=LOG_BYTES, popen=subprocess.Popen):
    proc = None
    received = stderr_bytes = 0
    buffer = bytearray()
    dropping = False
    result = dict(log_client_started_utc=utc(), followed_requested_window=False, automatic_reconnect=False)
    try:
        proc = popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        ready.set()
        with selectors.DefaultSelector() as sel:
            sel.register(proc.stdout, selectors.EVENT_READ, 'out')
            sel.register(proc.stderr, selectors.EVENT_READ, 'err')
            while not stop.is_set() and sel.get_map():
                for key, _ in sel.select(.2):
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        sel.unregister(key.fileobj); continue
                    if key.data == 'err':
                        stderr_bytes += len(part)
                        need(stderr_bytes <= 65536, 'log_stderr_bound'); continue
                    received += len(part)
                    need(received <= budget, 'log_stream_byte_budget')
                    # Never keep an unbounded JSON/base64/content log line.
                    for piece in part.splitlines(keepends=True):
                        ended = piece.endswith(b'\n')
                        if not dropping:
                            if len(buffer)+len(piece) <= LINE_BYTES:
                                buffer.extend(piece)
                            else:
                                dropping = True; buffer.clear(); aggregate.oversized += 1
                        if ended:
                            if not dropping: aggregate.line(bytes(buffer))
                            else: aggregate.lines += 1
                            buffer.clear(); dropping = False
            result['followed_requested_window'] = stop.is_set() and proc.poll() is None
            result['client_exit_before_window_end'] = not stop.is_set()
            result['client_exit_code_before_stop'] = proc.poll()
    except Exception as exc:
        result['failure'] = safe_error(exc)
    finally:
        ready.set()
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()  # This is only the reader's LOCAL kubectl PID.
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
            for handle in (proc.stdout, proc.stderr):
                if handle is not None: handle.close()
    result.update(bytes_read=received, stderr_bytes=stderr_bytes, partial_line_discarded=bool(buffer) or dropping,
                  byte_budget=budget, stop_kills_only_local_log_client=True, log_client_finished_utc=utc(),
                  complete_history_proven=False, empty_stream_is_not_proof_of_no_activity=True)
    return result


def attempt(report, scope, fn):
    try:
        return fn()
    except Exception as exc:
        report['warnings'].append(dict(scope=scope, **safe_error(exc)))
        return None


def queue_summary(samples):
    valid = [s['queues'] for s in samples if s.get('queues')]
    if len(valid) < 2: return {}
    first, last = valid[0], valid[-1]
    seconds = last['redis_sample_epoch']-first['redis_sample_epoch']
    out = {}
    for q in sorted(set(first['queues']) & set(last['queues'])):
        a, b = first['queues'][q], last['queues'][q]
        reset = any(y['queues'].get(q, {}).get(k, -1) < x['queues'].get(q, {}).get(k, 0)
                    for x,y in zip(valid, valid[1:]) for k in ('processed_total', 'failed_total'))
        out[q] = dict(pending_first=a['pending'], pending_last=b['pending'], pending_net_change=b['pending']-a['pending'],
                      oldest_pending_seconds_last=b.get('oldest_pending_seconds'), retry_first=a['retry'], retry_last=b['retry'],
                      counter_reset_or_rollover=reset, sample_window_seconds=seconds)
        if not reset and seconds > 0:
            processed, failed = b['processed_total']-a['processed_total'], b['failed_total']-a['failed_total']
            out[q].update(processed_attempts_delta=processed, failed_attempts_delta=failed,
                          estimated_successful_attempts_delta=processed-failed if processed >= failed else None,
                          processed_attempts_per_minute=round(processed*60/seconds, 3))
        out[q]['attempts_are_not_unique_documents_or_fully_enriched_documents'] = True
    return out


def collect(root, report):
    d, obs, act, h = initialize()
    emit(dict(deep_diagnostic='RUNNING', phase='CURRENT_BASELINE_AND_CONTAINER_LIMITS'))
    pods, signature = inventory(d, obs)
    app, dispatcher = pods['weknora-app'][0], pods[obs.DISPATCHER][0]
    workers = [p for n in obs.WORKERS for p in pods[n]]
    need(len(workers) == 16, 'mineru_worker_count_changed')
    targets = [app, dispatcher]+workers+pods['weknora-docreader']
    report['resource_specs'] = [resource_spec(obs,p) for p in targets]
    report['pod_states'] = [d.pod_status(obs,p) for p in targets]
    pg, pg_identity = act.review.pg_binding(app)
    binding = act.redis_binding(app)
    cutoff = int(time.time())-3600
    report['span_start'] = attempt(report, 'span_start', lambda:h.span_snapshot(pg, cutoff))
    report['database_start'] = attempt(report, 'database_start', lambda:database_snapshot(h, pg))
    report['resources_start'] = attempt(report, 'resources_start', lambda:obs.resource_snapshot(pods))
    with ThreadPoolExecutor(max_workers=4) as pool:
        before = list(pool.map(lambda p:attempt(report, 'cgroup_start_'+p['metadata']['name'], lambda:cgroup(obs,p)), targets))
    report['cgroup_start'] = before
    save(root, 'PREFLIGHT.json', {k:v for k,v in report.items() if k != 'samples'})

    emit(dict(deep_diagnostic='RUNNING', phase='FIVE_MINUTE_LIVE_AGGREGATION', samples=SAMPLES,
              interval_seconds=INTERVAL, logs_aggregated_not_saved=True, keep_existing_backlog_no_new_submission=True))
    stop = threading.Event()
    started, jobs = time.monotonic(), []
    since = utc().split('.')[0]+'Z'
    try:
        # Existing logs only; live follow avoids the previous 512KiB last-second truncation.
        for p in targets:
            aggregate = Aggregate(obs, p['metadata']['name'])
            ready, box = threading.Event(), {}
            command = follow_command(p['metadata']['name'], obs.container(p)['name'], since)
            def runner(command=command, aggregate=aggregate, ready=ready, box=box):
                box['stream'] = stream(command, aggregate, stop, ready)
            thread = threading.Thread(target=runner, daemon=True)
            thread.start(); jobs.append((thread, ready, box, aggregate))
        for _, ready, _, _ in jobs: need(ready.wait(5), 'log_reader_start_timeout')
        for i in range(SAMPLES):
            delay = started+i*INTERVAL-time.monotonic()
            if delay > 0: stop.wait(delay)
            row = dict(sample=i+1, utc=utc())
            def queues():
                value = act.snapshot(binding); act.check_capacities(value, CAPS, 'active'); return value
            with ThreadPoolExecutor(max_workers=4) as pool:
                fq = pool.submit(attempt, report, 'queues', queues)
                fm = pool.submit(attempt, report, 'model_leases', lambda:model_snapshot(obs, binding))
                fd = pool.submit(attempt, report, 'dispatcher', lambda:obs.health(dispatcher))
                row.update(queues=fq.result(), models=fm.result(), dispatcher=fd.result())
            if i % 2 == 0:
                row['database_waits'] = attempt(report, 'database_waits', lambda:h.database_waits(pg))
            if i in (0, SAMPLES-1):
                row['resources'] = attempt(report, 'resources', lambda:obs.resource_snapshot({'weknora-app':[app]}))
            report['samples'].append(row); save(root, 'SAMPLE-%02d.json' % (i+1), row)
            emit(dict(deep_sample=i+1, of=SAMPLES, utc=row['utc'],
                      pools=row['queues']['effective_servers'] if row['queues'] else None,
                      queues={n:{k:v[k] for k in ('active', 'pending', 'retry')} for n,v in row['queues']['queues'].items()}
                         if row['queues'] else None,
                      models=row['models'], dispatcher=row['dispatcher'], database_waits=row.get('database_waits')))
    finally:
        stop.set()
        for thread, _, _, _ in jobs: thread.join(timeout=6)
        report['log_aggregates'] = []
        for thread, _, box, aggregate in jobs:
            need(not thread.is_alive(), 'local_log_reader_did_not_close')
            row = aggregate.result(); row['stream'] = box.get('stream', dict(failure=dict(check='reader_no_result')))
            report['log_aggregates'].append(row)
            if not row['stream'].get('followed_requested_window') or row['oversized_lines_dropped'] or row['timing_cap_reached']:
                report['warnings'].append(dict(scope='log_'+row['pod'], check='log_coverage_incomplete'))

    emit(dict(deep_diagnostic='RUNNING', phase='POST_WINDOW_RECHECK_AND_SEAL'))
    _, after = inventory(d, obs)
    need(signature == after, 'dependent_runtime_changed')
    _, pg_end = act.review.pg_binding(app)
    need(pg_identity == pg_end, 'postgres_identity_changed')
    act.redis_binding(app)  # reverify secret references/endpoints, not values
    report['span_end'] = attempt(report, 'span_end', lambda:h.span_snapshot(pg, cutoff))
    report['database_end'] = attempt(report, 'database_end', lambda:database_snapshot(h, pg))
    with ThreadPoolExecutor(max_workers=4) as pool:
        end = list(pool.map(lambda p:attempt(report, 'cgroup_end_'+p['metadata']['name'], lambda:cgroup(obs,p)), targets))
    report['cgroup_end'] = end
    report['cgroup_deltas'] = [cgroup_delta(a,b) for a,b in zip(before,end) if a and b]
    report['baseline_stable'] = True
    report['diagnostic'] = 'COMPLETE_WITH_UNAVAILABLE_CHECKS' if report['warnings'] else 'COMPLETE'


def summarize(report):
    rows = report['samples']
    dispatch = [r['dispatcher']['counters'] for r in rows if r.get('dispatcher') and 'counters' in r['dispatcher']]
    return dict(diagnostic=report['diagnostic'], baseline_stable=report.get('baseline_stable', False),
                warnings=report['warnings'], failure=report.get('failure'), utc_started=report['utc_started'], utc_finished=report['utc_finished'],
                sample_count=len(rows), queue_trends=queue_summary(rows),
                mineru={k:dict(min=min(x[k] for x in dispatch), max=max(x[k] for x in dispatch))
                        for k in ('busy','waiting','quarantined') if dispatch and all(type(x.get(k)) is int for x in dispatch)},
                model_leases=[r['models'] for r in rows if r.get('models')],
                database_start=report.get('database_start'), database_end=report.get('database_end'),
                database_waits=[r['database_waits'] for r in rows if r.get('database_waits')],
                spans={k:report.get(k) for k in ('span_start','span_end')},
                pod_states=report.get('pod_states'), resource_specs=report.get('resource_specs'),
                cgroup_start=report.get('cgroup_start'), cgroup_end=report.get('cgroup_end'),
                cgroup_deltas=report.get('cgroup_deltas'), log_aggregates=report.get('log_aggregates'),
                production_writes=0, provider_requests=0, signals_sent=0, tasks_retried=0,
                ready_to_expand=False, performance_acceptance='NOT_GRANTED_BY_DIAGNOSTIC',
                pure_glm_vs_image_embedding_latency='NOT_INSTRUMENTED', local_model_waiters='NOT_MEASURED',
                spans_are_inclusive_not_additive=True, go_sql_pool_wait_count='NOT_MEASURED',
                raw_logs_saved=False, original_evidence_modified=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--observe', action='store_true')
    modes.add_argument('--status', type=Path)
    args = parser.parse_args(argv)
    need(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'root_datax01_required')
    import resource
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    if args.status:
        need(args.status.parent == Path('/var/lib') and re.fullmatch(PREFIX+'[a-z0-9_]{8}', args.status.name), 'exact_evidence_directory_required')
        emit(json.loads(safe_read(args.status/'SUMMARY.json')))
        emit(dict(status='STORED_SUMMARY_ONLY_NOT_LIVE', files_modified=False)); return 0
    # New local evidence only; --observe never reuses or modifies a previous run.
    for p in (Path('/var/lib'), Path('/var'), Path('/')):
        s = p.lstat(); need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_state_parent')
    root = Path(tempfile.mkdtemp(prefix=PREFIX, dir='/var/lib'))
    report = dict(diagnostic='INCOMPLETE', warnings=[], samples=[], utc_started=utc())
    emit(dict(evidence=str(root), production_writes=0, provider_requests=0, sampling_seconds=300,
              status_command='sudo python3 -I -B '+str(Path(__file__).resolve())+' --status '+str(root)))
    try:
        collect(root, report)
    except BaseException as exc:
        report['failure'] = safe_error(exc)
    report['utc_finished'] = utc()
    result = summarize(report)
    save(root, 'RESULT.json', report)
    sha = save(root, 'SUMMARY.json', result)
    emit(dict(deep_diagnostic_summary=result))
    emit(dict(summary=str(root/'SUMMARY.json'), summary_sha256=sha, production_writes=0,
              status_command='sudo python3 -I -B '+str(Path(__file__).resolve())+' --status '+str(root),
              status_reads_stored_summary_not_live_cluster=True))
    return 0 if report['diagnostic'].startswith('COMPLETE') else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        emit(dict(diagnostic='INCOMPLETE', failure=safe_error(exc), production_writes=0, provider_requests=0)); sys.exit(1)
