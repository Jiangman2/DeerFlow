#!/usr/bin/env python3
"""Targeted read-only follow-up; fixes model JSON projection, not production."""
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

TOOL = Path('/var/lib/weknora-pipeline-deep-tool-GRY7Ffc9/observe.py')
TOOL_SHA = 'a4986f8045d8bac6951319469a5ba02d798ab432e73b273b8c093c4052f57a76'
ROOT = Path('/var/lib/weknora-pipeline-deep-hnit5ouh')
SUMMARY_SHA = '640936742fbccc4848c2496781d019787c5b870b68716c9d8796e20558ddd7ec'
MODELS = ('deerflow-cnooc-deepseek-v4-flash','deerflow-cnooc-qwen3-embedding-8b',
          'deerflow-cnooc-glm-5.3-flash','deerflow-dashscope-vlm')
BACKENDS = ('client backend','parallel worker','autovacuum worker','background worker','other')
WAIT_TYPES = ('CPU_OR_UNINSTRUMENTED','IPC','IO','LWLock','Lock','Client','BufferPin','Activity','Timeout','Extension','other')
EVENTS = ('MessageQueueReceive','MessageQueueSend','MessageQueueInternal','MessageQueuePutMessage',
          'ParallelFinish','ParallelBitmapScan','ParallelCreateIndexScan','ExecuteGather',
          'HashBatchAllocate','HashBatchElect','HashBatchLoad','HashBuildAllocate','HashBuildElect',
          'HashBuildHashInner','HashBuildHashOuter','HashGrowBatchesDecide','HashGrowBatchesElect',
          'HashGrowBatchesFinish','HashGrowBatchesReallocate','HashGrowBatchesRepartition',
          'HashGrowBucketsElect','HashGrowBucketsReallocate','HashGrowBucketsReinsert',
          'ProcArrayGroupUpdate','ProcSignalBarrier','XactGroupUpdate',
          'ParallelHashBatchAllocate','ParallelHashBatchElect','ParallelHashBatchLoad','ParallelHashBuildElect',
          'ParallelHashBuildHashInner','ParallelHashBuildHashOuter','ParallelHashBuildRun',
          'ParallelHashGrowthElect','ParallelHashGrowthRepartition','BgWorkerStartup','BgWorkerShutdown',
          'DataFileRead','DataFileWrite','BufferIO','BufferContent','BufferMapping','WALWrite','WALSync',
          'WALInsert','SyncRep','ClientRead','ClientWrite','transactionid','tuple','relation','extend',
          'ProcArray','LockManager','Xact','BufferPin','NOT_WAITING','OTHER')


class Failure(RuntimeError): pass


def need(ok,label):
    if not ok: raise Failure(label)


def emit(row):
    print(json.dumps(row,sort_keys=True,ensure_ascii=True,allow_nan=False),flush=True)


def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def safe_error(exc):
    return dict(error_type=type(exc).__name__,check=str(exc) if isinstance(exc,Failure) else 'read_unavailable',raw_error_printed=False)


def safe_read(path,expected=None,maximum=4*1024**2):
    for p in (path.parent,)+tuple(path.parent.parents):
        s=p.lstat(); need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'unsafe_private_parent')
    fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
             and not before.st_mode&0o022 and before.st_size<=maximum,'unsafe_private_file')
        raw=f.read(maximum+1); after=os.fstat(f.fileno())
    ident=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_uid,s.st_nlink)
    need(ident(before)==ident(after)==ident(path.lstat()) and len(raw)==before.st_size,'private_file_changed')
    need(expected is None or hashlib.sha256(raw).hexdigest()==expected,'input_hash_changed')
    return raw


def initialize():
    old=types.ModuleType('pinned_readonly_observer'); old.__file__=str(TOOL)
    exec(compile(safe_read(TOOL,TOOL_SHA),str(TOOL),'exec'),old.__dict__)
    return old


TX="BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; "
MODEL_SQL=TX+"""
WITH model_rows AS (
 SELECT id,parameters->>'max_concurrency' AS v FROM public.models
 WHERE deleted_at IS NULL AND id IN (%s)
), setting_row AS (
 SELECT is_secret,value#>>'{}' AS v FROM public.system_settings WHERE key='model.max_concurrency'
)
SELECT json_build_object('transaction_read_only',current_setting('transaction_read_only'),
 'max_connections',current_setting('max_connections')::int,
 'reserved_connections',current_setting('superuser_reserved_connections')::int,
 'client_backends',(SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'),
 'database_client_backends',(SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND datname=current_database()),
 'database_parallel_workers',(SELECT count(*) FROM pg_stat_activity WHERE backend_type='parallel worker' AND datname=current_database()),
 'idle_in_transaction',(SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND datname=current_database() AND state='idle in transaction'),
 'global_setting',(SELECT json_build_object('present',true,
   'value',CASE WHEN NOT is_secret AND v ~ '^-?[0-9]{1,9}$' THEN v::bigint ELSE NULL END,
   'valid_numeric_nonsecret',NOT is_secret AND coalesce(v ~ '^-?[0-9]{1,9}$',false)) FROM setting_row),
 'models',coalesce((SELECT json_agg(json_build_object('id',id,
   'stored_override',CASE WHEN v ~ '^-?[0-9]{1,9}$' THEN v::bigint ELSE NULL END,
   'mode',CASE WHEN v IS NULL THEN 'INHERIT_GLOBAL' WHEN v ~ '^-?[0-9]{1,9}$' THEN
     CASE WHEN v::bigint>0 THEN 'EXPLICIT' ELSE 'INHERIT_GLOBAL' END ELSE 'INVALID' END)) FROM model_rows),'[]'::json));
ROLLBACK;""" % ','.join("'%s'"%x for x in MODELS)

WAIT_SQL=TX+"""
WITH chosen AS (
 SELECT CASE WHEN backend_type IN (%s) THEN backend_type ELSE 'other' END AS backend_type,
 CASE WHEN wait_event_type IS NULL THEN 'CPU_OR_UNINSTRUMENTED'
  WHEN wait_event_type IN (%s) THEN wait_event_type ELSE 'other' END AS wait_type,
 CASE WHEN wait_event IS NULL THEN 'NOT_WAITING' WHEN wait_event IN (%s) THEN wait_event ELSE 'OTHER' END AS wait_event,
 greatest(0,extract(epoch FROM now()-query_start)) AS age_seconds,
 leader_pid IS NOT NULL AS has_parallel_leader
 FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND pid<>pg_backend_pid()
), grouped AS (
 SELECT backend_type,wait_type,wait_event,count(*) AS records,
 count(*) FILTER(WHERE has_parallel_leader) AS parallel_follower_records,
 max(age_seconds) AS max_query_age_seconds FROM chosen GROUP BY backend_type,wait_type,wait_event
)
SELECT json_build_object('transaction_read_only',current_setting('transaction_read_only'),
 'groups',coalesce((SELECT json_agg(grouped ORDER BY backend_type,wait_type,wait_event) FROM grouped),'[]'::json));
ROLLBACK;""" % tuple(','.join("'%s'"%x for x in seq) for seq in (BACKENDS[:-1],WAIT_TYPES[1:-1],EVENTS[:-2]))


def integer(value,nullable=False):
    need((nullable and value is None) or (type(value) is int and -10**9<value<10**9),'numeric_projection_shape')
    return value


def models(row):
    need(row.get('transaction_read_only')=='on','database_not_read_only')
    out=dict(transaction_read_only='on',models=[],global_setting=None)
    for key in ('max_connections','reserved_connections','client_backends','database_client_backends',
                'database_parallel_workers','idle_in_transaction'):
        out[key]=integer(row.get(key)); need(out[key]>=0,'negative_database_count')
    g=row.get('global_setting')
    if g is not None:
        need(g.get('present') is True and type(g.get('valid_numeric_nonsecret')) is bool,'global_setting_shape')
        out['global_setting']=dict(present=True,value=integer(g.get('value'),True),valid_numeric_nonsecret=g['valid_numeric_nonsecret'])
    need(isinstance(row.get('models'),list) and len(row['models'])<=4,'models_shape')
    seen=set()
    for v in row['models']:
        need(v.get('id') in MODELS and v['id'] not in seen and v.get('mode') in ('INHERIT_GLOBAL','EXPLICIT','INVALID'),'unknown_model_projection')
        seen.add(v['id']); out['models'].append(dict(id=v['id'],stored_override=integer(v.get('stored_override'),True),mode=v['mode']))
    out['missing_model_row_is_not_zero_limit']=True
    out['nonpositive_global_disables_default_governor_not_worker_limits']=True
    return out


def waits(row):
    need(row.get('transaction_read_only')=='on' and isinstance(row.get('groups'),list) and len(row['groups'])<=200,'wait_projection_shape')
    groups=[]
    for v in row['groups']:
        need(v.get('backend_type') in BACKENDS and v.get('wait_type') in WAIT_TYPES and v.get('wait_event') in EVENTS,'unknown_wait_projection')
        age=v.get('max_query_age_seconds')
        need(age is None or (type(age) in (int,float) and 0<=age<10**9),'query_age_shape')
        groups.append(dict(backend_type=v['backend_type'],wait_type=v['wait_type'],wait_event=v['wait_event'],
            records=integer(v.get('records')),parallel_follower_records=integer(v.get('parallel_follower_records')),max_query_age_seconds=age))
    return dict(groups=groups,raw_queries_read=False,parallel_workers_are_not_app_connections=True)


def stored_resources(old,summary):
    report=json.loads(safe_read(ROOT/'RESULT.json',maximum=8*1024**2))
    need(old.summarize(report)==summary,'report_summary_binding_changed')
    # The old report already contains only the allowlisted kubelet projection.
    return dict(source='PREVIOUS_CAPTURE_NOT_FRESH_NODE_METRICS',resources_start=report.get('resources_start'),
        resources_samples=[dict(utc=x['utc'],resources=x['resources']) for x in report['samples'] if 'resources' in x])


def main():
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    need(len(sys.argv)==1,'no_arguments')
    import resource
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    emit(dict(database_followup='RUNNING',phase='VERIFY_PREVIOUS_SUMMARY_AND_CURRENT_IDENTITIES',production_writes=0))
    summary=json.loads(safe_read(ROOT/'SUMMARY.json',SUMMARY_SHA))
    need(summary.get('baseline_stable') is True and summary.get('sample_count')==11
         and summary.get('diagnostic')=='COMPLETE_WITH_UNAVAILABLE_CHECKS','unexpected_previous_summary')
    old=initialize(); d,obs,act,h=old.initialize()
    pods,signature=old.inventory(d,obs); app=pods['weknora-app'][0]
    pg,identity=act.review.pg_binding(app)
    output=dict(database_followup='INCOMPLETE',production_writes=0,provider_requests=0,
        original_evidence_modified=False,automatic_retry=False,ready_to_expand=False,
        previous_summary_sha256=SUMMARY_SHA,utc_started=utc(),samples=[])
    output['previous_resources']=stored_resources(old,summary)
    for i in range(3):
        emit(dict(database_followup='RUNNING',sample=i+1,of=3))
        row=dict(sample=i+1,utc=utc(),configuration=models(h.sql_read(pg,MODEL_SQL)),waits=waits(h.sql_read(pg,WAIT_SQL)))
        output['samples'].append(row)
        if i<2: time.sleep(5)
    _,after=old.inventory(d,obs); _,pg_end=act.review.pg_binding(app)
    need(signature==after and identity==pg_end,'dependent_identity_changed')
    output.update(database_followup='COMPLETE',baseline_stable=True,utc_finished=utc())
    emit(output); return 0


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        emit(dict(database_followup='INCOMPLETE',failure=safe_error(exc),production_writes=0,provider_requests=0,automatic_retry=False));sys.exit(1)
