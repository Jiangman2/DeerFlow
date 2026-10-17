#!/usr/bin/env python3
"""Bounded read-only observation of the accepted GLM88 production load.

No task/model submission, configuration/queue/SQL writes, signals or restarts.
Only fresh private host evidence is written. No old mutation CLI is invoked.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile
import time
import traceback
import types

TOOL=Path('/var/lib/weknora-glm88-switch-tool-3IXKnz6L/switch.py')
TOOL_SHA='ebdf30fefd7e23038ad12fabe6adbcca96ccf3e3af3db388aa67f2ab2e69ec51'
PASS=Path('/var/lib/weknora-glm88-switch-20261017-r1/RESULT-7588d5438056436bab9eba48007b36a3.json')
PASS_SHA='00291105b7491a556ab58cdf8e8e21659cf9ed27fab14d310b072a396ce28f4c'
POD_UID='cdd90c71-8027-4e5c-997e-67808128ba0c'
SAMPLES=11
INTERVAL=30
PREFIX='weknora-glm88-load-'
s=r=None


def need(value,code):
    if not value: raise RuntimeError(code)


def safe_read(path,expected=None):
    path=Path(path)
    for parent in (path.parent,)+tuple(path.parent.parents):
        st=parent.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not st.st_mode&0o022,'unsafe_private_parent')
    fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
             and not before.st_mode&0o022 and before.st_size<=4194304,'unsafe_private_file')
        raw=stream.read(4194305); after=os.fstat(stream.fileno()); named=path.lstat()
    identity=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_mode,v.st_uid,v.st_nlink)
    need(identity(before)==identity(after)==identity(named) and len(raw)==before.st_size,'private_input_changed')
    need(expected is None or hashlib.sha256(raw).hexdigest()==expected,'input_hash_changed')
    return raw


def initialize():
    global s,r
    raw=safe_read(TOOL,TOOL_SHA)
    s=types.ModuleType('load_frozen_glm88_switch'); s.__file__=str(TOOL)
    exec(compile(raw,str(TOOL),'exec'),s.__dict__)
    s.r=s.load_review(); r=s.r


def current(plan,identity=None):
    dep,pod=s.new_pod(plan)
    need(pod['metadata']['uid']==POD_UID,'accepted_glm88_pod_changed')
    if identity is not None: need(r.sw.same(r.obs.identity(pod),identity),'app_process_changed')
    return dep,pod


def compact_resources(records):
    nodes={}
    for record in records:
        for row in record.get('resources',[]):
            entry=nodes.setdefault(row['node'],dict(samples=0,unavailable_samples=0,cpu_cores=[],
                memory_available_GiB=[],disk_available_GiB=[],pod_memory_MiB_max={},pod_cpu_cores_max={}))
            entry['samples']+=1
            if row.get('unavailable'): entry['unavailable_samples']+=1
            for target,section,key,divisor in (('cpu_cores','cpu','usageNanoCores',1e9),
                ('memory_available_GiB','memory','availableBytes',1024**3),
                ('disk_available_GiB','filesystem','availableBytes',1024**3)):
                value=row.get(section,{}).get(key)
                if isinstance(value,(int,float)): entry[target].append(value/divisor)
            for pod in row.get('pods',[]):
                value=pod.get('memory',{}).get('workingSetBytes')
                if isinstance(value,(int,float)):
                    name=pod['pod']; entry['pod_memory_MiB_max'][name]=round(max(value/1024**2,
                        entry['pod_memory_MiB_max'].get(name,0)),2)
                cpu=pod.get('cpu',{}).get('usageNanoCores')
                if isinstance(cpu,(int,float)):
                    name=pod['pod']; entry['pod_cpu_cores_max'][name]=round(max(cpu/1e9,
                        entry['pod_cpu_cores_max'].get(name,0)),4)
    for row in nodes.values():
        for key in ('cpu_cores','memory_available_GiB','disk_available_GiB'):
            values=row.pop(key); row[key+'_min']=round(min(values),3) if values else None
            row[key+'_max']=round(max(values),3) if values else None
    return nodes


def summary(report):
    samples=report.get('samples',[])
    valid=[x for x in samples if x.get('worker_reads_complete') and x.get('dispatcher')]
    queues=r.obs.interpret(samples)['all_queue_trends'] if r else report.get('queue_trends',{})
    queue_details={}
    for name,trend in queues.items():
        rows=[x['redis']['queues'][name] for x in samples if name in x.get('redis',{}).get('queues',{})]
        oldest=[v.get('oldest_pending_seconds') for v in rows if isinstance(v.get('oldest_pending_seconds'),(int,float))]
        queue_details[name]=dict(trend,oldest_pending_seconds_max=max(oldest) if oldest else None,
                                 oldest_pending_seconds_last=rows[-1].get('oldest_pending_seconds') if rows else None)
    pools={}
    for name in ('core','postprocess','enrichment','maintenance','shared','wiki'):
        rows=[p for x in samples for p in x.get('redis',{}).get('effective_servers',[]) if p['pool']==name]
        pools[name]=dict(samples=len(rows),capacity_values=sorted({p['concurrency'] for p in rows}),
            active_max=max((p['active'] for p in rows),default=None),
            active_mean=round(sum(p['active'] for p in rows)/len(rows),2) if rows else None,
            full_samples=sum(p['active']>=p['concurrency'] for p in rows))
    windows=[x['redis']['redis_sample_epoch'] for x in samples if 'redis' in x]
    out=dict(observation=report.get('observation','INCOMPLETE'),sample_count=len(samples),
        baseline_stable=report.get('baseline_stable',False),failure=report.get('failure'),
        sample_window_seconds=windows[-1]-windows[0] if len(windows)>1 else None,
        queue_trends=queue_details,pools=pools,
        mineru=dict(valid_samples=len(valid),sampled_active_min=min((x['active_workers'] for x in valid),default=None),
            sampled_active_max=max((x['active_workers'] for x in valid),default=None),
            sampled_active_mean=round(sum(x['active_workers'] for x in valid)/len(valid),2) if valid else None,
            sampled_full_count=sum(x['active_workers']==16 for x in valid),
            dispatcher_waiting_max=max((x['dispatcher'].get('waiting',0) for x in valid),default=None),
            quarantined_max=max((x['dispatcher'].get('quarantined',0) for x in valid),default=None),
            statistics_are_sampled_not_time_weighted=True),
        node_resource_ranges=compact_resources(report.get('resource_samples',[])),
        database_samples=[{k:v for k,v in x.items() if k!='resources'} for x in report.get('resource_samples',[])],
        app_configured_resources=report.get('baseline',{}).get('weknora-app',{}).get('resources'),
        warnings=report.get('warnings',[]),latency_last_10m_bounded_tail=report.get('latency',{}),
        log_coverage=report.get('log_coverage',[]),
        production_writes=0,signals_sent=0,provider_probe_requests=0,automatic_tuning=False,
        task_counters_are_not_document_counts=True,model_actual_occupancy='NOT_MEASURED',
        per_document_stage_timings='NOT_MEASURED',performance_acceptance='PENDING_REVIEW')
    return out


def warning(report,sample,check,exc):
    report['warnings'].append(dict(sample=sample,scope=check,error_type=type(exc).__name__,
        check=r.sw.recovery.safe_error(exc),raw_error_printed=False))


def resource_sample(pod,pods,report,number,pg_identity):
    record=dict(sample=number,utc=r.obs.utc())
    try: record['database_connections']=r.e.database(pod)['connections']
    except Exception as exc: warning(report,number,'DATABASE_COUNTERS',exc)
    try:
        pg,binding=r.e.review.pg_binding(pod)
        need(r.sw.same(binding,pg_identity),'postgres_identity_changed')
        targets=dict(pods); targets['postgres']=[pg]
        record['resources']=r.obs.resource_snapshot(targets)
    except Exception as exc:
        warning(report,number,'RESOURCE_COLLECTION',exc); record['resources']=[]
    if 'database_connections' in record:
        try:
            rows=[dict(x,pods=[p for p in x.get('pods',[]) if p.get('pod')==pod['metadata']['name']])
                  for x in record['resources'] if x.get('node')==pod['spec']['nodeName']]
            r.e.check_resource_rows(pod,dict(connections=record['database_connections']),rows,expanding=True)
        except Exception as exc: warning(report,number,'REVIEWED_RESOURCE_ENVELOPE_NOT_AUTOMATIC_ROLLBACK',exc)
    for node in record['resources']:
        if node.get('unavailable'): warning(report,number,'NODE_METRICS',RuntimeError('node_metrics_unavailable'))
        for key in ('cpu','memory','filesystem'):
            stamp=node.get(key,{}).get('time')
            try:
                sampled=r.obs.datetime.datetime.strptime(stamp[:19],'%Y-%m-%dT%H:%M:%S').replace(tzinfo=r.obs.datetime.timezone.utc)
                need(abs((r.obs.datetime.datetime.now(r.obs.datetime.timezone.utc)-sampled).total_seconds())<120,
                     'stale_node_resource_sample')
            except Exception as exc: warning(report,number,'METRIC_FRESHNESS_'+key,exc)
    report['resource_samples'].append(record)
    return record


def collect(root,report):
    receipt=json.loads(safe_read(PASS,PASS_SHA))
    need(receipt.get('glm88_switch')=='PASS' and receipt.get('effective_pools')==r.TARGET
         and receipt.get('model_governor_startup_limit')==128 and receipt.get('pod_uid')==POD_UID
         and receipt.get('database')=='GLM' and receipt.get('mineru_slots')==16,'accepted_glm88_receipt_required')
    plan,activation=s.stored_plan(); _,pod=current(plan); identity=r.obs.identity(pod)
    r.e.runtime_env(pod,r.e.TARGET_ENV); r.check_glm(pod,activation); r.app_endpoint(pod)
    _,pods,signature,report['baseline']=r.obs.baseline()
    need(r.sw.same(signature['pods']['weknora-app'],[identity]),'initial_app_changed')
    _,pg_identity=r.e.review.pg_binding(pod)
    workers=[p for n in r.obs.WORKERS for p in pods[n]]; targets=workers+pods[r.obs.DISPATCHER]
    binding=r.e.redis_binding(pod)
    for i in range(SAMPLES):
        began=time.monotonic(); _,pod=current(plan,identity)
        row=dict(number=i+1,utc=r.obs.utc())
        with r.obs.ThreadPoolExecutor(max_workers=4) as pool:
            future=pool.submit(r.e.snapshot,binding); states=list(pool.map(r.obs.health,targets))
            try:
                snapshot=future.result(); r.e.check_capacities(snapshot,r.TARGET,'active'); row['redis']=snapshot
            except Exception as exc: warning(report,i+1,'QUEUE_COLLECTION',exc)
        row['workers']=[v for v in states if v.get('role')=='worker']
        expected={p['metadata']['name'] for p in workers}
        row['worker_reads_complete']=(len(row['workers'])==16 and {x.get('pod') for x in row['workers']}==expected
            and all(type(x.get('counters',{}).get('active')) is int and x['counters']['active'] in (0,1)
                    for x in row['workers']))
        row['active_workers']=sum(x['counters']['active'] for x in row['workers']) if row['worker_reads_complete'] else None
        row['dispatcher']=next((x['counters'] for x in states if x.get('role')=='dispatcher'),None)
        row['unavailable_reads']=[v for v in states if v.get('unavailable')]
        try: r.mineru_check(states,workers)
        except Exception as exc: warning(report,i+1,'MINERU_HEALTH',exc)
        row['collection_seconds']=round(time.monotonic()-began,2); report['samples'].append(row)
        r.obs.write_new(root/('SAMPLE-%02d.json'%(i+1)),row)
        r.obs.emit(dict(glm88_load_sample=i+1,of=SAMPLES,mineru_active=row['active_workers'],dispatcher=row['dispatcher'],
            pools=row.get('redis',{}).get('effective_servers'),
            queues={n:{k:v[k] for k in ('pending','active','retry','oldest_pending_seconds','processed_total','failed_total')}
                    for n,v in row.get('redis',{}).get('queues',{}).items()},warning_count=len(report['warnings'])))
        if i%3==0 or i==SAMPLES-1:
            record=resource_sample(pod,pods,report,i+1,pg_identity)
            r.obs.write_new(root/('RESOURCES-%02d.json'%(i+1)),record)
            r.obs.emit(dict(glm88_load_resources=compact_resources([record]),
                database_connections=record.get('database_connections'),warning_count=len(report['warnings'])))
        if i<SAMPLES-1: time.sleep(max(0,INTERVAL-(time.monotonic()-began)))
    r.obs.emit(dict(glm88_load='BOUNDED_LOG_AGGREGATES',raw_logs_saved=False))
    with r.obs.ThreadPoolExecutor(max_workers=4) as pool:
        logs=list(pool.map(r.obs.logs,targets+pods['weknora-app']+pods['weknora-docreader']))
    values={}
    for row in logs:
        for key,series in row.get('values',{}).items(): values.setdefault(key,[]).extend(series)
    report['latency']={k:r.obs.summary_stats(v) for k,v in values.items()}
    report['log_coverage']=[{k:v for k,v in x.items() if k!='values'} for x in logs]
    _,_,end,_=r.obs.baseline(); need(r.sw.same(end,signature),'dependent_workload_changed')
    _,pod=current(plan,identity); r.e.runtime_env(pod,r.e.TARGET_ENV); r.check_glm(pod,activation)
    _,pg_end=r.e.review.pg_binding(pod); need(r.sw.same(pg_end,pg_identity),'postgres_identity_changed')
    report['baseline_stable']=True
    report['observation']='COMPLETE_WITH_WARNINGS' if report['warnings'] else 'COMPLETE'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__); g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--observe',action='store_true'); g.add_argument('--status',type=Path)
    args=p.parse_args(argv)
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    import resource
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if args.status:
        need(args.status.parent==Path('/var/lib') and re.fullmatch(PREFIX+r'[a-z0-9_]{8}',args.status.name),
             'exact_evidence_directory_required')
        row=json.loads(safe_read(args.status/'SUMMARY.json'))
        print(json.dumps(row,sort_keys=True),flush=True); return 0
    print(json.dumps(dict(glm88_load='LOADING_PINNED_READERS',production_writes=0)),flush=True)
    initialize(); r.obs.safe_parent('/var/lib')
    root=Path(tempfile.mkdtemp(prefix=PREFIX,dir='/var/lib')); root.chmod(0o700)
    report=dict(observation='INCOMPLETE',utc_started=r.obs.utc(),samples=[],resource_samples=[],warnings=[])
    r.obs.emit(dict(glm88_load_evidence=str(root),samples=SAMPLES,interval_seconds=INTERVAL,
                    production_writes=0,provider_probe_requests=0,signals_sent=0))
    try: collect(root,report)
    except BaseException as exc:
        report['failure']=dict(check=r.sw.recovery.safe_error(exc),error_type=type(exc).__name__,raw_error_printed=False,
            code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
    report['utc_finished']=r.obs.utc(); result=summary(report)
    r.obs.write_new(root/'RESULT.json',report); raw=r.obs.write_new(root/'SUMMARY.json',result)
    r.obs.emit(dict(glm88_load_summary=result))
    r.obs.emit(dict(summary=str(root/'SUMMARY.json'),summary_sha256=hashlib.sha256(raw).hexdigest(),
        status_command='sudo python3 -I -B '+str(Path(__file__).resolve())+' --status '+str(root),
        status_reads_stored_summary_not_live_cluster=True,private_files_exported=False,production_writes=0))
    return 0 if report['observation'].startswith('COMPLETE') else 1


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(json.dumps(dict(glm88_load='INCOMPLETE',error_type=type(exc).__name__,raw_error_printed=False,
            failed_check=str(exc) if re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',str(exc)) else 'SAFE_DIAGNOSTIC_REQUIRED',
            production_writes=0,signals_sent=0,provider_probe_requests=0)),flush=True); sys.exit(1)
