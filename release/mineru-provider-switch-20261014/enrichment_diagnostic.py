#!/usr/bin/env python3
"""Bounded read-only enrichment diagnosis after the accepted88 change. Python3.7+."""
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
import time

import observe_pipeline as obs
import tuning_review as review
import tune_weknora88 as tune

SAMPLES = 13
INTERVAL = 15
CATEGORIES = ('docreader','chunking','embedding','multimodal','postprocess','multimodal.image',
              'embedding.batch','postprocess.question','postprocess.summary','postprocess.graph',
              'postprocess.wiki','other')
TX = "BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; "
WAIT_CATEGORIES = ('CPU_OR_UNINSTRUMENTED','IO','Lock','LWLock','BufferPin','Client','IPC',
                   'Activity','Extension','Timeout','other')
DB_WAITS_SQL = TX + """
WITH active AS (
 SELECT CASE WHEN wait_event_type IS NULL THEN 'CPU_OR_UNINSTRUMENTED'
  WHEN wait_event_type IN ('IO','Lock','LWLock','BufferPin','Client','IPC','Activity','Extension','Timeout')
  THEN wait_event_type ELSE 'other' END AS category,
  greatest(0,extract(epoch FROM now()-query_start)) AS age_seconds
 FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND pid<>pg_backend_pid()
), grouped AS (SELECT category,count(*) AS connections,max(age_seconds) AS max_query_age_seconds
 FROM active GROUP BY category)
SELECT json_build_object('transaction_read_only',current_setting('transaction_read_only'),
 'groups',coalesce((SELECT json_agg(grouped ORDER BY category) FROM grouped),'[]'::json));
ROLLBACK;"""

SCHEMA_SQL = TX + """
WITH wanted(attname,typname) AS (VALUES ('id','int8'),('name','varchar'),('kind','varchar'),
 ('status','varchar'),('duration_ms','int8'),('started_at','timestamptz'),
 ('finished_at','timestamptz'),('created_at','timestamptz'))
SELECT json_build_object('transaction_read_only',current_setting('transaction_read_only'),
 'ordinary_table',exists(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname='knowledge_processing_spans' AND c.relkind='r'),
 'id_primary_key',exists(SELECT 1 FROM pg_index i JOIN pg_attribute a
    ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0]
    WHERE i.indrelid=to_regclass('public.knowledge_processing_spans') AND i.indisprimary
      AND i.indisvalid AND i.indisready AND i.indnkeyatts=1 AND a.attname='id'),
 'columns_match',(SELECT count(*)=8 FROM wanted w JOIN pg_attribute a ON a.attname=w.attname
    JOIN pg_type t ON t.oid=a.atttypid AND t.typname=w.typname
    WHERE a.attrelid=to_regclass('public.knowledge_processing_spans') AND NOT a.attisdropped));
ROLLBACK;"""

SAMPLE_SELECT = """SELECT id,name,kind,status,started_at,finished_at,duration_ms,created_at
 FROM public.knowledge_processing_spans ORDER BY id DESC LIMIT 20000"""


def span_sql(cutoff):
    obs.require(type(cutoff) is int and 0 < cutoff < 10000000000, 'invalid_cutoff')
    # Sample the indexed newest IDs before filtering timestamps. No full history
    # scan; no JSON content, document identities, error messages or prompts.
    groups = ','.join("'%s'" % x for x in CATEGORIES[:5])
    return TX + """
WITH recent AS MATERIALIZED (%s), chosen AS MATERIALIZED (
 SELECT CASE WHEN kind='stage' AND name IN (%s) THEN name
  WHEN name ~ '^multimodal[.]image\\[[0-9]+\\]$' THEN 'multimodal.image'
  WHEN name ~ '^embedding[.]batch\\[[0-9]+\\]$' THEN 'embedding.batch'
  WHEN name ~ '^postprocess[.]question([.]|$)' THEN 'postprocess.question'
  WHEN name ~ '^postprocess[.]summary([.]|$)' THEN 'postprocess.summary'
  WHEN name ~ '^postprocess[.]graph([.]|$)' THEN 'postprocess.graph'
  WHEN name ~ '^postprocess[.]wiki([.]|$)' THEN 'postprocess.wiki' ELSE 'other' END AS category,
  status, started_at,
  CASE WHEN status='done' AND duration_ms BETWEEN 0 AND 604800000 AND finished_at IS NOT NULL
       THEN duration_ms/1000.0 ELSE NULL END AS duration_seconds
 FROM recent WHERE started_at >= to_timestamp(%d)
), aggregated AS (
 SELECT category,count(*) AS total,
  count(*) FILTER(WHERE status='done') AS done,
  count(*) FILTER(WHERE status='running') AS running,
  count(*) FILTER(WHERE status='failed') AS failed,
  count(*) FILTER(WHERE status='skipped') AS skipped,
  count(*) FILTER(WHERE status='cancelled') AS cancelled,
  count(*) FILTER(WHERE status NOT IN ('done','running','failed','skipped','cancelled')) AS other_status,
  count(duration_seconds) AS valid_done_duration_count,
  percentile_cont(0.5) WITHIN GROUP(ORDER BY duration_seconds) AS done_p50_seconds,
  percentile_cont(0.95) WITHIN GROUP(ORDER BY duration_seconds) AS done_p95_seconds,
  max(greatest(0,extract(epoch FROM (now()-started_at)))) FILTER(WHERE status='running') AS running_max_age_seconds
 FROM chosen GROUP BY category
)
SELECT json_build_object('transaction_read_only',current_setting('transaction_read_only'),
 'sample_limit',20000,'sample_rows',(SELECT count(*) FROM recent),
 'recent_rows',(SELECT count(*) FROM chosen),'sample_cap_reached',(SELECT count(*)=20000 FROM recent),
 'cutoff_epoch',%d,'oldest_sample_created_epoch',(SELECT extract(epoch FROM min(created_at)) FROM recent),
 'groups',coalesce((SELECT json_agg(aggregated ORDER BY category) FROM aggregated),'[]'::json));
ROLLBACK;""" % (SAMPLE_SELECT, groups, cutoff, cutoff)


def check_schema(row):
    obs.require(row.get('transaction_read_only') == 'on' and all(row.get(k) is True for k in
        ('ordinary_table','id_primary_key','columns_match')), 'span_schema_or_index_not_reviewed')


def clean_plan(row):
    obs.require(isinstance(row,list) and len(row)==1 and isinstance(row[0],dict), 'explain_shape')
    pending = [row[0].get('Plan',{})]; names = []
    while pending:
        node = pending.pop(); kind = node.get('Node Type')
        obs.require(kind in ('Limit','Index Scan','Index Only Scan') and len(names)<8, 'unbounded_span_sample_plan')
        if kind != 'Limit':
            obs.require(node.get('Relation Name') == 'knowledge_processing_spans', 'unexpected_plan_relation')
        names.append(kind); pending.extend(node.get('Plans',[]))
    obs.require('Limit' in names and any(x.startswith('Index') for x in names), 'indexed_limit_required')
    return names  # Never retain planner SQL, constants or relation statistics.


def numeric(value, nullable=False):
    obs.require((nullable and value is None) or (type(value) in (int,float) and math.isfinite(value)
                and 0 <= value <= 10**15), 'aggregate_number_invalid')
    return value


def clean_spans(row):
    obs.require(row.get('transaction_read_only')=='on' and row.get('sample_limit')==20000
                and type(row.get('sample_cap_reached')) is bool, 'span_aggregate_shape')
    out = {k:numeric(row.get(k), nullable=(k=='oldest_sample_created_epoch')) for k in
           ('sample_rows','recent_rows','cutoff_epoch','oldest_sample_created_epoch')}
    obs.require(out['recent_rows'] <= out['sample_rows'] <= 20000, 'span_row_limit')
    out.update(transaction_read_only='on',sample_limit=20000,sample_cap_reached=row['sample_cap_reached'],groups=[])
    rows = row.get('groups'); obs.require(isinstance(rows,list) and len(rows)<=len(CATEGORIES), 'span_group_limit')
    seen = set()
    for r in rows:
        cat = r.get('category'); obs.require(cat in CATEGORIES and cat not in seen, 'unknown_span_category')
        seen.add(cat); g = {'category':cat}
        for key in ('total','done','running','failed','skipped','cancelled','other_status','valid_done_duration_count'):
            g[key] = numeric(r.get(key)); obs.require(type(g[key]) is int and g[key]<=20000,'span_count_invalid')
        for key in ('done_p50_seconds','done_p95_seconds','running_max_age_seconds'):
            g[key] = numeric(r.get(key), nullable=True)
        out['groups'].append(g)
    return out


def sql_read(pg, sql):
    return json.loads(obs.call(['exec',pg['metadata']['name'],'-c',obs.container(pg)['name'],
        '--','sh','-c',review.DB_SHELL,'diagnostic',sql],timeout=20,limit=65536))


def span_snapshot(pg, cutoff):
    check_schema(sql_read(pg, SCHEMA_SQL))
    plan = clean_plan(sql_read(pg, TX+'EXPLAIN (FORMAT JSON) '+SAMPLE_SELECT+'; ROLLBACK;'))
    out = clean_spans(sql_read(pg, span_sql(cutoff)))
    out['sample_plan_node_types'] = plan
    out['scope'] = 'NEWEST_20000_IDS_STARTED_SINCE_CUTOFF_NOT_FULL_DATABASE'
    out['durations_are_not_exclusive_or_additive'] = True
    out['image_span_includes_read_two_possible_vlm_calls_and_indexing'] = True
    return out


def database_waits(pg):
    row = sql_read(pg, DB_WAITS_SQL)
    obs.require(row.get('transaction_read_only')=='on' and isinstance(row.get('groups'),list)
                and len(row['groups'])<=len(WAIT_CATEGORIES), 'database_wait_shape')
    result = []; seen = set()
    for r in row['groups']:
        cat = r.get('category')
        obs.require(cat in WAIT_CATEGORIES and cat not in seen, 'database_wait_category')
        seen.add(cat); count = numeric(r.get('connections'))
        obs.require(type(count) is int and count <= 1000000, 'database_wait_count')
        result.append({'category':cat,'connections':count,
                       'max_query_age_seconds':numeric(r.get('max_query_age_seconds'),nullable=True)})
    return {'scope':'CURRENT_DATABASE_ACTIVE_CONNECTIONS_INSTANTANEOUS_NOT_STAGE_LATENCY',
            'groups':result,'raw_queries_read':False}


MODEL_SHELL = r'''set +x
set -eu
test -n "${REDIS_PASSWORD:-}"
export REDISCLI_AUTH="$REDIS_PASSWORD"
db="$1"
case "$db" in ''|*[!0-9]*) exit 20;; esac
r() { redis-cli --no-auth-warning --raw -h 127.0.0.1 -p 6379 -n "$db" "$@"; }
integer() { case "$1" in ''|*[!0-9]*) exit 21;; esac; }
clock=$(r TIME); set -- $clock
test "$#" -eq 2; integer "$1"; integer "$2"
millis=$(($1*1000+$2/1000))
printf 'T\t%s\n' "$1"
for model in '''+' '.join(review.MODEL_IDS)+r'''; do
 count=$(r ZCOUNT "weknora:modelsem:$model" "($millis" +inf)
 integer "$count"
 printf 'M\t%s\t%s\n' "$model" "$count"
done
printf 'END\n'
'''


def parse_models(raw):
    rows = raw.decode('ascii').splitlines()
    obs.require(len(rows)==5 and rows[-1]=='END','model_snapshot_shape')
    h = rows[0].split('\t'); obs.require(len(h)==2 and h[0]=='T' and h[1].isdigit(),'model_clock')
    values = {}
    for r in rows[1:-1]:
        p = r.split('\t')
        obs.require(len(p)==3 and p[0]=='M' and p[1] in review.MODEL_IDS and p[1] not in values
                    and p[2].isdigit() and len(p[2])<=8, 'model_count_invalid')
        values[p[1]] = int(p[2])
    return {'epoch':int(h[1]),'holders':values,'scope':'UNEXPIRED_REDIS_LEASES_NOT_GPU_JOBS'}


def model_snapshot(binding):
    pg, db, _ = binding
    return parse_models(obs.call(['exec',pg['metadata']['name'],'-c',obs.container(pg)['name'],
        '--','sh','-c',MODEL_SHELL,'diagnostic',db],timeout=20,limit=4096))


LOG_PATTERNS = {
    'vlm_start_lines':r'\[VLM\] Calling (?:OpenAI-compatible|WeKnoraCloud|Ollama) API',
    'vlm_response_lines':r'\[VLM\] (?:OpenAI|WeKnoraCloud|Ollama) response received',
    'ocr_error_lines':r'\[ImageMultimodal\] OCR failed',
    'caption_error_lines':r'\[ImageMultimodal\] Caption failed',
    'image_read_error_lines':r'\[ImageMultimodal\] Skip unreadable image',
    'image_index_error_lines':r'\[ImageMultimodal\] Failed to index multimodal chunks',
    'orphan_drop_lines':r'\[ImageMultimodal\] Dropping task',
    'limiter_fail_open_lines':r'\[ModelLimiter\] acquire failed.*failing open',
}


def log_counts(raw):
    lines = raw.decode('utf-8',errors='replace').splitlines()
    return {name:sum(bool(re.search(pattern,line)) for line in lines) for name,pattern in LOG_PATTERNS.items()}


def app_log_summary(app):
    raw = obs.call(['logs',app['metadata']['name'],'-c','app','--since=10m','--timestamps=true',
                    '--tail=5000','--limit-bytes=524288'],limit=524289)
    return {'counts':log_counts(raw),'error_markers':obs.dependency_log_markers(raw),
            'byte_limit_reached':len(raw)>=524288,'line_limit_reached':len(raw.splitlines())>=5000,
            'raw_logs_saved':False,'counts_are_not_unique_requests':True,
            'duration_pairing':'NOT_ATTEMPTED_NO_RELIABLE_PER_CALL_CORRELATION'}


def new_report():
    return {'diagnostic':'INCOMPLETE','samples':[],'span_snapshots':[],'database_samples':[],
            'unavailable_checks':[],'production_writes':0,'provider_requests':0,'signals_sent':0,
            'credentials_exported':False,'raw_logs_saved':False,'automatic_retry':False,
            'ready_to_expand':False,'utc_started':obs.utc()}


def summarize(report):
    series = [s['models']['holders'] for s in report['samples'] if 'models' in s]
    holders = {k:{'min':min(r[k] for r in series),'max':max(r[k] for r in series),
                  'samples_at_or_above_128':sum(r[k]>=128 for r in series)} for k in review.MODEL_IDS} if series else None
    queue_samples = [s for s in report['samples'] if 'redis' in s]
    pool_rows = [s['redis']['effective_servers'] for s in queue_samples]
    pools = {}
    for pool, capacity in tune.TARGET.items():
        values = [r['active'] for rows in pool_rows for r in rows if r['pool']==pool]
        if values:
            pools[pool] = {'capacity':capacity,'active_min':min(values),'active_max':max(values),
                           'samples_full':sum(v>=capacity for v in values)}
    health = [s for s in report['samples'] if s.get('worker_reads_complete') and s.get('dispatcher')]
    mineru = ({'capacity':16,'active_min':min(s['active_workers'] for s in health),
               'active_max':max(s['active_workers'] for s in health),
               'dispatcher_waiting_max':max(s['dispatcher']['waiting'] for s in health)} if health else None)
    epochs = [s['redis']['redis_sample_epoch'] for s in queue_samples]
    return {'diagnostic':report['diagnostic'],'sample_count':len(report['samples']),
            'queue_trends':obs.interpret(queue_samples)['all_queue_trends'],
            'queue_sample_window_seconds':epochs[-1]-epochs[0] if len(epochs)>1 else None,
            'pools':pools,'mineru':mineru,
            'model_holders':holders,'holder_scope':'UNEXPIRED_SHARED_REDIS_LEASES_MAY_INCLUDE_STALE_LEASES',
            'model_ids_scope':'THREE_REVIEWED_BUILTINS_NOT_CUSTOM_MODELS',
            'local_model_waiter_count':'NOT_MEASURED','provider_actual_concurrency':'NOT_MEASURED',
            'substage_attribution':'NOT_SEPARATELY_INSTRUMENTED',
            'span_snapshots':report['span_snapshots'],'database_samples':report['database_samples'],
            'app_logs':report.get('app_logs'),'resources':report.get('resources'),
            'unavailable_checks':report['unavailable_checks'],'failure':report.get('failure'),
            'performance_acceptance':'PENDING_88_OBSERVATION_AND_USER_DOCUMENT_TESTS',
            'ready_to_expand':False,'production_writes':0,'provider_requests':0}


def collect(root, report):
    plan = tune.read_plan()
    success = tune.load(tune.ROOT/'forward'/'SUCCESS.json')
    obs.require(success.get('configuration_switch')=='PASS' and success.get('effective_pools')==tune.TARGET
                and success.get('model_governor_startup_limit')==128, 'accepted_88_configuration_required')
    app = tune.app_for(tune.deployment(),plan['candidate'])
    tune.runtime_env(app,tune.TARGET_ENV); tune.binary_probe(app)
    _, pods, signature, report['baseline'] = obs.baseline()
    obs.require(obs.identity(pods['weknora-app'][0])==obs.identity(app),'app_changed')
    binding = tune.redis_binding(app); pg, _ = review.pg_binding(app)
    started = app['status']['containerStatuses'][0]['state']['running']['startedAt']
    obs.require(re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.[0-9]+)?Z',started) is not None,'startup_timestamp')
    epoch = int(datetime.datetime.strptime(started[:19],'%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp())
    cutoff = max(epoch,int(time.time())-1800)
    targets = [p for n in obs.WORKERS+[obs.DISPATCHER] for p in pods[n]]
    for i in range(SAMPLES):
        began = time.monotonic()
        tune.app_for(tune.deployment(),plan['candidate'],obs.identity(app))
        sample = {'number':i+1,'utc':obs.utc()}
        with obs.ThreadPoolExecutor(max_workers=4) as pool:
            f = pool.submit(tune.snapshot,binding); g = pool.submit(model_snapshot,binding)
            states = list(pool.map(obs.health,targets))
            sample['redis'] = f.result(); sample['models'] = g.result()
        tune.check_capacities(sample['redis'],tune.TARGET,'active')
        workers = [x for x in states if x.get('role')=='worker']
        sample['worker_reads_complete'] = len(workers)==16
        sample['active_workers'] = sum(x['counters']['active'] for x in workers) if len(workers)==16 else None
        sample['dispatcher'] = next((x['counters'] for x in states if x.get('role')=='dispatcher'),None)
        report['samples'].append(sample); obs.write_new(root/('sample-%02d.json'%(i+1)),sample)
        obs.emit({'enrichment_diagnostic':'RUNNING','sample':i+1,'of':SAMPLES,
                  'model_holders':sample['models']['holders'],'mineru_active':sample['active_workers']})
        if i in (0,6,SAMPLES-1):
            db = tune.database(app)
            report['database_samples'].append({'sample':i+1,'connections':db['connections'],
                'numeric_overrides':'NONE_VERIFIED','per_background_model_default':128})
            try:
                report['database_samples'][-1]['active_waits'] = database_waits(pg)
            except Exception as exc:
                report['unavailable_checks'].append({'check_scope':'DATABASE_WAITS','sample':i+1,**obs.failure(exc)})
        if i in (0,SAMPLES-1):
            try:
                report['span_snapshots'].append({'sample':i+1,'data':span_snapshot(pg,cutoff)})
            except Exception as exc:
                report['unavailable_checks'].append({'check_scope':'SPAN_AGGREGATES','sample':i+1,**obs.failure(exc)})
        if i<SAMPLES-1:
            time.sleep(max(0,INTERVAL-(time.monotonic()-began)))
    report['app_logs'] = app_log_summary(app)
    report['resources'] = obs.resource_snapshot({'app':[app],'docreader':pods['weknora-docreader'],
                                                 'redis':pods['weknora-redis']})
    _,_,after,_ = obs.baseline()
    obs.require(after==signature,'baseline_changed_during_diagnostic')
    obs.require(all(s['worker_reads_complete'] and s['dispatcher'] for s in report['samples']), 'health_samples_incomplete')
    report['baseline_stable'] = True
    report['diagnostic'] = 'PARTIAL' if report['unavailable_checks'] else 'COMPLETE'


def read_lock():
    obs.safe_parent(tune.ROOT)
    fd = os.open(str(tune.ROOT/'controller.lock'), os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        s = os.fstat(fd)
        obs.require(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022,'unsafe_tune_lock')
        tune.fcntl.flock(fd,tune.fcntl.LOCK_SH|tune.fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd); raise


def host_gate():
    obs.require(sys.platform=='linux' and tune.fcntl is not None and os.geteuid()==0
                and socket.gethostname().split('.')[0]=='datax-01','run_on_datax01_as_root')
    obs.require(len(sys.argv)==1,'no_mutation_modes_or_arguments')


def main():
    host_gate(); os.umask(0o077)
    fd = read_lock()
    try:
        obs.safe_parent('/var/lib')
        root = Path(tempfile.mkdtemp(prefix='weknora-enrichment-diagnostic-',dir='/var/lib')); root.chmod(0o700)
        report = new_report(); code = 1
        obs.emit({'enrichment_diagnostic_evidence':str(root),'production_writes':0,'provider_requests':0})
        try:
            collect(root,report); code = 0 if report['diagnostic']=='COMPLETE' else 2
        except KeyboardInterrupt:
            report['failure'] = {'check':'OPERATOR_INTERRUPTED','raw_error_printed':False}; code = 130
        except Exception as exc:
            report['failure'] = obs.failure(exc)
        report['utc_finished'] = obs.utc()
        raw = obs.write_new(root/'RESULT.json',report)
        obs.emit({'enrichment_diagnostic_summary':summarize(report)})
        obs.emit({'report':str(root/'RESULT.json'),'report_sha256':hashlib.sha256(raw).hexdigest(),
                  'diagnostic':report['diagnostic'],'production_writes':0,'provider_requests':0,
                  'collection_completion_is_not_expansion_approval':True})
        return code
    finally:
        os.close(fd)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        obs.emit({'diagnostic':'INCOMPLETE','failure':obs.failure(exc),'production_writes':0,
                  'provider_requests':0,'automatic_retry':False})
        sys.exit(1)
