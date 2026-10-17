#!/usr/bin/env python3
"""Read-only GLM88 bottleneck diagnosis; never changes or resubmits work."""
import argparse
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import types

import observe_glm88_load as o

PRIOR=Path('/var/lib/weknora-glm88-load-8dc4h31h/SUMMARY.json')
PRIOR_SHA='a638d73e8c25e16a083fd0ba59cb76e8d7b8377f581608cfceab333a85548a00'
SINCE='2026-10-17T06:50:00Z'
PREFIX='weknora-glm88-diagnostic-'
SAMPLES=5
INTERVAL=15
MODELS=('deerflow-cnooc-deepseek-v4-flash','deerflow-cnooc-qwen3-embedding-8b',
        'deerflow-dashscope-vlm','deerflow-cnooc-glm-5.3-flash')
TIMEOUT_KEYS=('MINERU_TIMEOUT_SECONDS','MINERU_DISPATCH_WAIT_TIMEOUT_SECONDS',
 'MINERU_DISPATCH_REQUEST_TIMEOUT_SECONDS','MINERU_DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS',
 'MINERU_DISPATCH_FAILURE_COOLDOWN_SECONDS','MINERU_DISPATCH_PROBE_TIMEOUT_SECONDS',
 'DEERFLOW_DOCREADER_WORD_TIMEOUT_SECONDS','DEERFLOW_WORD_MINERU_FALLBACK_TIMEOUT_SECONDS')
TIMEOUT_PROBE='''import json,os
names=%r
out={}
for k in names:
 v=os.environ.get(k)
 try:
  n=float(v) if v is not None else None
  out[k]=n if n is None or 0<=n<=86400 else "INVALID"
 except ValueError: out[k]="INVALID"
print(json.dumps(out,allow_nan=False))
''' % (TIMEOUT_KEYS,)
MODEL_SHELL=r'''set +x
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
for model in '''+' '.join(MODELS)+r'''; do
 count=$(r ZCOUNT "weknora:modelsem:$model" "($millis" +inf)
 integer "$count"
 printf 'M\t%s\t%s\n' "$model" "$count"
done
printf 'END\n'
'''
d=q=None
need=o.need


def emit(value): print(json.dumps(value,sort_keys=True,ensure_ascii=True,allow_nan=False),flush=True)


def initialize():
    global d,q
    o.initialize()
    sources=globals().get('EMBEDDED_HELPERS')
    if sources is None:
        sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
        import enrichment_diagnostic
        import diagnose_mineru_quarantine
        d,q=enrichment_diagnostic,diagnose_mineru_quarantine
    else:
        modules=[]
        for name in ('enrichment_diagnostic','diagnose_mineru_quarantine'):
            raw=sources[name].encode(); mod=types.ModuleType('glm88_readonly_'+name); mod.__file__=name+'.py'
            exec(compile(raw,mod.__file__,'exec'),mod.__dict__); modules.append(mod)
        d,q=modules
    d.obs=o.r.obs; d.review=o.r.e.review


def timestamp(line):
    match=re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d))\s',line)
    if not match: return None
    try: return datetime.datetime.fromisoformat(match[1].replace('Z','+00:00')).astimezone(datetime.timezone.utc).isoformat()
    except ValueError: return None


def quarantine_counts(raw,mapping):
    groups={}; matched=unmapped=0
    reasons={'ReadTimeout','ConnectTimeout','ConnectionError','Timeout','HTTP 429 busy admission race'}
    reasons.update('HTTP %d'%n for n in range(500,600))
    pattern=r'MinerU dispatcher quarantined backend=([0-9.]+) cooldown_seconds=[0-9.]+ reason=(.*)$'
    for line in raw.decode('utf-8','replace').splitlines():
        m=re.search(pattern,line)
        if not m: continue
        matched+=1
        if m[1] not in mapping: unmapped+=1; continue
        row=groups.setdefault(mapping[m[1]],dict(pod=mapping[m[1]],reason_counts={},last_event_utc=None))
        reason=m[2] if m[2] in reasons else 'UNCLASSIFIED'
        row['reason_counts'][reason]=row['reason_counts'].get(reason,0)+1
        row['last_event_utc']=timestamp(line)
    return dict(workers=list(groups.values()),matched_events=matched,unmapped_events=unmapped,
                proves_current_membership=False)


ERROR_PATTERNS={
 'mineru_async_deadline':r'Enterprise MinerU (?:task|result for task|task status for task|task result for task) .* (?:exceeded|was not ready within)',
 'mineru_async_failed':r'Enterprise MinerU asynchronous task failed:',
 'mineru_business_error':r'\bMinerUBusinessError\b',
 'mineru_http_error':r'\bMinerUHTTPError\b',
 'transport_read_timeout':r'\bReadTimeout(?:Error)?\b',
 'transport_connect_timeout':r'\bConnectTimeout(?:Error)?\b',
 'libreoffice_deadline':r'LibreOffice timed out after',
 'native_parser_deadline':r'(?:DocReader|AnyDoc|docreader|anydoc).*timed out|subprocess.TimeoutExpired',
 'visual_evidence_missing':r'Visual evidence (?:rendering omitted|required|rendering returned no)',
 'client_disconnected':r'Parser response client disconnected before body delivery',
 'glm_deadline':r'enterprise GLM: (?:context deadline exceeded|stream deadline exceeded)',
 'glm_transport_failed':r'enterprise GLM: transport failed',
 'glm_stream_incomplete':r'enterprise GLM: (?:incomplete or empty completion|stream incomplete or limit exceeded)',
 'glm_http_429':r'enterprise GLM: HTTP status 429\b',
 'glm_http_503':r'enterprise GLM: HTTP status 503\b',
 'glm_http_other_5xx':r'enterprise GLM: HTTP status 5(?!03\b)[0-9]{2}\b',
 'image_ocr_error':r'\[ImageMultimodal\] OCR failed',
 'image_caption_error':r'\[ImageMultimodal\] Caption failed',
 'image_unreadable':r'\[ImageMultimodal\] Skip unreadable image',
 'image_index_error':r'\[ImageMultimodal\] Failed to index multimodal chunks',
 'limiter_fail_open':r'\[ModelLimiter\] acquire failed.*failing open',
}


def error_counts(raw):
    lines=raw.decode('utf-8','replace').splitlines()
    return {name:sum(bool(re.search(pattern,line)) for line in lines) for name,pattern in ERROR_PATTERNS.items()}


def coverage(raw,line_limit,byte_limit):
    lines=raw.decode('utf-8','replace').splitlines(); stamps=[timestamp(x) for x in lines]
    stamps=[s for s in stamps if s]
    return dict(since_utc=SINCE,byte_limit_reached=len(raw)>=byte_limit,line_limit_reached=len(lines)>=line_limit,
        bytes_read=len(raw),lines_read=len(lines),first_retained_utc=stamps[0] if stamps else None,
        last_retained_utc=stamps[-1] if stamps else None,complete_history_proven=False,raw_logs_saved=False)


def logs(pod,mapping):
    limit=2097152 if pod['metadata']['name'].startswith('weknora-app-') else 1048576
    raw=o.r.obs.call(['logs',pod['metadata']['name'],'-c',o.r.obs.container(pod)['name'],
        '--since-time='+SINCE,'--timestamps=true','--tail=10000','--limit-bytes='+str(limit)],timeout=25,limit=limit+4096)
    series,transport=o.r.obs.log_stats(raw)
    return dict(pod=pod['metadata']['name'],coverage=coverage(raw,10000,limit),
        error_marker_lines=error_counts(raw),marker_counts_are_not_unique_failures=True,
        timings={k:o.r.obs.summary_stats(v) for k,v in series.items()},transport_failure_lines=transport,
        quarantine=quarantine_counts(raw,mapping))


def parse_models(raw):
    rows=raw.decode('ascii').splitlines(); need(len(rows)==len(MODELS)+2 and rows[-1]=='END','model_count_shape')
    h=rows[0].split('\t'); need(len(h)==2 and h[0]=='T' and h[1].isdigit(),'model_clock_invalid')
    holders={}
    for line in rows[1:-1]:
        v=line.split('\t'); need(len(v)==3 and v[0]=='M' and v[1] in MODELS and v[1] not in holders
                               and v[2].isdigit() and len(v[2])<=8,'model_counter_invalid')
        holders[v[1]]=int(v[2])
    return dict(epoch=int(h[1]),holders=holders,scope='UNEXPIRED_REDIS_LEASES_NOT_PROVIDER_JOB_COUNT')


def model_snapshot(binding):
    redis,db,_=binding
    row=parse_models(o.r.obs.call(['exec',redis['metadata']['name'],'-c',o.r.obs.container(redis)['name'],
        '--','sh','-c',MODEL_SHELL,'diagnostic',db],timeout=20,limit=4096))
    need(abs(row['epoch']-time.time())<45,'model_clock_stale'); return row


def clean_timeouts(row):
    need(isinstance(row,dict) and set(row)==set(TIMEOUT_KEYS),'runtime_timeout_shape')
    need(all(v is None or v=='INVALID' or (type(v) in (float,int) and math.isfinite(v) and 0<=v<=86400)
             for v in row.values()),'runtime_timeout_value')
    return row


def runtime_timeouts(pod):
    raw=o.r.obs.call(['exec',pod['metadata']['name'],'-c',o.r.obs.container(pod)['name'],
        '--','python3','-I','-B','-c',TIMEOUT_PROBE],timeout=15,limit=8192)
    return dict(pod=pod['metadata']['name'],explicit_environment=clean_timeouts(json.loads(raw)),
                absent_value_means_code_default_not_zero=True)


def fresh(stamp):
    try:
        parsed=datetime.datetime.fromisoformat(stamp.replace('Z','+00:00'))
        return parsed.tzinfo is not None and abs((datetime.datetime.now(datetime.timezone.utc)-parsed).total_seconds())<120
    except (ValueError,AttributeError,TypeError): return False


def node_projection(name,data,config):
    nc=data.get('node',{}).get('cpu',{}); need(fresh(nc.get('time')),'stale_node_cpu')
    n=nc.get('usageNanoCores'); need(type(n) in (int,float) and math.isfinite(n) and n>=0,'missing_node_cpu')
    capacity=config.get('status',{}).get('allocatable',{}).get('cpu','')
    need(isinstance(capacity,str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?m?',capacity),'node_cpu_capacity_shape')
    cores=float(capacity[:-1])/1000 if capacity.endswith('m') else float(capacity)
    need(cores>0,'node_capacity_zero'); rows=[]; missing=0
    for pod in data.get('pods',[]):
        cpu=pod.get('cpu',{}); value=cpu.get('usageNanoCores'); ref=pod.get('podRef',{})
        if not fresh(cpu.get('time')) or type(value) not in (int,float) or not math.isfinite(value) or value<0:
            missing+=1; continue
        need(all(isinstance(ref.get(k),str) and re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,252}',ref[k])
                 for k in ('name','namespace')),'pod_ref_not_kubernetes_name')
        rows.append(dict(pod=ref['name'],namespace=ref['namespace'],cpu_cores=round(value/1e9,4),sample_utc=cpu['time']))
    return dict(node=name,node_cpu_cores=round(n/1e9,3),allocatable_cpu_cores=cores,
        node_sample_utc=nc['time'],top_pods=sorted(rows,key=lambda x:x['cpu_cores'],reverse=True)[:5],
        unavailable_or_stale_pod_cpu=missing,instantaneous_samples_not_same_timestamp=True,
        host_process_attribution='NOT_MEASURED')


def node_attribution(node):
    need(re.fullmatch(r'ecs-zdz-pro-001[1-6]',node),'node_not_reviewed')
    data=json.loads(o.r.obs.call(['get','--raw','/api/v1/nodes/'+node+'/proxy/stats/summary'],limit=8388608))
    return node_projection(node,data,o.r.obs.get('nodes',node))


def prior_receipt():
    row=json.loads(o.safe_read(PRIOR,PRIOR_SHA))
    need(row.get('observation')=='COMPLETE_WITH_WARNINGS' and row.get('baseline_stable') is True
         and row.get('sample_count')==11 and row.get('mineru',{}).get('quarantined_max')==1,'prior_load_receipt_changed')
    return dict(summary_sha256=PRIOR_SHA,read_only=True)


def new_report():
    return dict(diagnostic='INCOMPLETE',samples=[],spans=[],database_waits=[],logs=[],timeouts=[],nodes=[],warnings=[],
        production_writes=0,provider_probe_requests=0,signals_sent=0,documents_submitted=0,
        raw_logs_saved=False,credentials_exported=False,automatic_retry=False,ready_to_expand=False,
        separate_glm_vs_embedding_wall_time='NOT_INSTRUMENTED',local_model_waiters='NOT_MEASURED',
        health_may_trigger_existing_due_readiness_reprobe=True,quarantine_explicitly_cleared=False)


def attempt(report,scope,fn):
    try: return fn()
    except Exception as exc:
        report['warnings'].append(dict(scope=scope,error_type=type(exc).__name__,
            check=o.r.sw.recovery.safe_error(exc),raw_error_printed=False))
        return None


def collect(root,report):
    report['prior']=prior_receipt()
    receipt=json.loads(o.safe_read(o.PASS,o.PASS_SHA))
    need(receipt.get('glm88_switch')=='PASS' and receipt.get('pod_uid')==o.POD_UID
         and receipt.get('effective_pools')==o.r.TARGET and receipt.get('model_governor_startup_limit')==128
         and receipt.get('database')=='GLM' and receipt.get('mineru_slots')==16,'accepted88_receipt_required')
    plan,activation=o.s.stored_plan(); _,app=o.current(plan); identity=o.r.obs.identity(app)
    o.r.e.runtime_env(app,o.r.e.TARGET_ENV); o.r.check_glm(app,activation); o.r.app_endpoint(app)
    _,pods,signature,_=o.r.obs.baseline()
    need(o.r.sw.same(signature['pods']['weknora-app'],[identity]),'initial_app_changed')
    pg,pg_identity=o.r.e.review.pg_binding(app); binding=o.r.e.redis_binding(app)
    workers=[p for n in o.r.obs.WORKERS for p in pods[n]]; dispatcher=pods[o.r.obs.DISPATCHER][0]
    targets=workers+[dispatcher]; mapping={p['status']['podIP']:p['metadata']['name'] for p in workers}
    cutoff=int(datetime.datetime.fromisoformat(SINCE.replace('Z','+00:00')).timestamp())
    # Capture historical logs first, before more high-volume traffic rotates them.
    emit(dict(glm88_diagnostic='BOUNDED_HISTORICAL_LOGS',since_utc=SINCE,raw_logs_saved=False))
    with o.r.obs.ThreadPoolExecutor(max_workers=4) as pool:
        report['logs']=list(pool.map(lambda p:attempt(report,'LOG_'+p['metadata']['name'],lambda:logs(p,mapping)),
                                    targets+[app]))
        report['timeouts']=list(pool.map(lambda p:attempt(report,'NUMERIC_TIMEOUTS',lambda:runtime_timeouts(p)),targets))
    o.r.obs.write_new(root/'LOG-AGGREGATES.json',report['logs'])
    for i in range(SAMPLES):
        start=time.monotonic(); _,app=o.current(plan,identity)
        row=dict(sample=i+1,utc=o.r.obs.utc())
        snap=attempt(report,'QUEUES',lambda:o.r.e.snapshot(binding))
        if snap is not None:
            o.r.e.check_capacities(snap,o.r.TARGET,'active'); row['redis']=snap
        row['models']=attempt(report,'MODEL_LEASES',lambda:model_snapshot(binding))
        row['dispatcher']=o.r.obs.health(dispatcher)
        row['worker_network']=attempt(report,'WORKER_HEALTH_READINESS',lambda:q.remote_health(o.r.obs,dispatcher,workers))
        row['readiness_503_while_active_is_expected']=True
        report['samples'].append(row); o.r.obs.write_new(root/('SAMPLE-%02d.json'%(i+1)),row)
        emit(dict(glm88_diagnostic_sample=row))
        if i in (0,SAMPLES-1):
            _,current_pg=o.r.e.review.pg_binding(app)
            need(o.r.sw.same(current_pg,pg_identity),'postgres_changed')
            report['spans'].append(dict(sample=i+1,statistics=attempt(report,'SPAN_AGGREGATES',lambda:d.span_snapshot(pg,cutoff))))
            report['database_waits'].append(dict(sample=i+1,statistics=attempt(report,'DATABASE_WAITS',lambda:d.database_waits(pg))))
        if i<SAMPLES-1: time.sleep(max(0,INTERVAL-(time.monotonic()-start)))
    emit(dict(glm88_diagnostic='SHARED_NODE_CPU_ATTRIBUTION'))
    for node in sorted({p['spec']['nodeName'] for p in workers}):
        report['nodes'].append(attempt(report,'NODE_'+node,lambda:node_attribution(node)))
    _,_,after,_=o.r.obs.baseline(); need(o.r.sw.same(signature,after),'dependent_workload_changed')
    _,app=o.current(plan,identity); o.r.e.runtime_env(app,o.r.e.TARGET_ENV); o.r.check_glm(app,activation)
    _,pg_end=o.r.e.review.pg_binding(app); need(o.r.sw.same(pg_identity,pg_end),'postgres_changed')
    report['baseline_stable']=True
    report['diagnostic']='COMPLETE_WITH_UNAVAILABLE_CHECKS' if report['warnings'] else 'COMPLETE'


def finish_summary(report):
    result=dict(report)
    model_rows=[x['models']['holders'] for x in report['samples'] if x.get('models')]
    result['model_holders']={k:dict(min=min(r[k] for r in model_rows),max=max(r[k] for r in model_rows),
        samples_at_or_above_128=sum(r[k]>=128 for r in model_rows)) for k in MODELS} if model_rows else None
    result['model_holder_scope']='UNEXPIRED_SHARED_REDIS_LEASES_CAN_INCLUDE_STALE_LEASES_NOT_GPU_JOBS'
    queue_rows=[x for x in report['samples'] if 'redis' in x]
    result['latest_queues']=queue_rows[-1]['redis']['queues'] if queue_rows else None
    result['latest_pools']=queue_rows[-1]['redis']['effective_servers'] if queue_rows else None
    result['queue_trends']=o.r.obs.interpret(queue_rows)['all_queue_trends'] if o.r and queue_rows else None
    result['samples']=[{k:v for k,v in x.items() if k!='redis'} for x in report['samples']]
    result['performance_acceptance']='NOT_GRANTED_BY_DIAGNOSTIC'
    return result


def host_gate():
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    import resource
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0))


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--diagnose',action='store_true'); g.add_argument('--status',type=Path); args=p.parse_args(argv)
    host_gate()
    if args.status:
        need(args.status.parent==Path('/var/lib') and re.fullmatch(PREFIX+r'[a-z0-9_]{8}',args.status.name),'exact_state_required')
        emit(json.loads(o.safe_read(args.status/'SUMMARY.json'))); return 0
    emit(dict(glm88_diagnostic='LOADING_PINNED_READERS',production_writes=0)); initialize()
    o.r.obs.safe_parent('/var/lib'); root=Path(tempfile.mkdtemp(prefix=PREFIX,dir='/var/lib')); root.chmod(0o700)
    report=new_report(); report['utc_started']=o.r.obs.utc()
    emit(dict(evidence=str(root),samples=SAMPLES,interval_seconds=INTERVAL,production_writes=0))
    try: collect(root,report)
    except BaseException as exc:
        report['failure']=dict(error_type=type(exc).__name__,check=o.r.sw.recovery.safe_error(exc),raw_error_printed=False)
    report['utc_finished']=o.r.obs.utc(); result=finish_summary(report)
    o.r.obs.write_new(root/'RESULT.json',report); raw=o.r.obs.write_new(root/'SUMMARY.json',result)
    emit(dict(glm88_diagnostic_summary=result))
    emit(dict(summary=str(root/'SUMMARY.json'),summary_sha256=hashlib.sha256(raw).hexdigest(),
        status_command='sudo python3 -I -B '+str(Path(__file__).resolve())+' --status '+str(root),
        status_reads_stored_summary_not_live_cluster=True,production_writes=0))
    return 0 if report['diagnostic'].startswith('COMPLETE') else 1


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        emit(dict(diagnostic='INCOMPLETE',error_type=type(exc).__name__,production_writes=0,
                  failed_check='SAFE_DIAGNOSTIC_REQUIRED',raw_error_printed=False)); sys.exit(1)
