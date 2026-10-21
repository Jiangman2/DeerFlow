#!/usr/bin/env python3
"""Read-only GLM88 -> GLM104 preflight: enrichment48 ->64 ONLY.

Writes new private host evidence and sends one server-only dry run. No signals,
live patch, queue mutation, model requests, image import or schema change.
"""
import argparse
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
import traceback
import observe_glm88_load as o

CURRENT={'core':16,'postprocess':4,'enrichment':48,'maintenance':4,'shared':4,'wiki':12}
TARGET=dict(CURRENT,enrichment=64)
DIAGNOSTIC=Path('/var/lib/weknora-glm88-diagnostic-jm5h5t55/SUMMARY.json')
DIAGNOSTIC_SHA='7499ba73cc046497c2cd311d74edf8849c8b92535842fb80fa5ce8bf44736fb1'
PREFIX='weknora-glm104-review-'
SAMPLES=5
INTERVAL=15


def need(value,code):
    if not value: raise RuntimeError(code)


def emit(row): print(json.dumps(row,sort_keys=True),flush=True)


def initialize():
    o.initialize()
    need(o.r.TARGET==CURRENT and o.r.e.TARGET==CURRENT and o.r.e.TARGET_MODEL==128,
         'accepted_capacity_contract_changed')


def bind_inputs():
    receipt=json.loads(o.safe_read(o.PASS,o.PASS_SHA))
    need(receipt.get('glm88_switch')=='PASS' and receipt.get('effective_pools')==CURRENT
         and receipt.get('model_governor_startup_limit')==128 and receipt.get('pod_uid')==o.POD_UID
         and receipt.get('database')=='GLM' and receipt.get('mineru_slots')==16,
         'accepted_glm88_receipt_required')
    diagnosis=json.loads(o.safe_read(DIAGNOSTIC,DIAGNOSTIC_SHA))
    need(diagnosis.get('diagnostic')=='COMPLETE' and diagnosis.get('baseline_stable') is True
         and diagnosis.get('warnings')==[] and len(diagnosis.get('samples',[]))==5
         and diagnosis.get('production_writes')==0 and diagnosis.get('ready_to_expand') is False,
         'diagnostic_receipt_changed')
    # Historical diagnostic is rationale only; all eligibility below is fresh.
    return o.s.stored_plan()


def startup_proof(pod):
    records=sorted(o.s.ROOT.glob('MODEL-STARTUP-*.json'))
    need(len(records)<=100,'startup_record_bound')
    for path in records:
        row=json.loads(o.safe_read(path))
        if o.r.sw.same(row.get('pod_identity'),o.r.obs.identity(pod)):
            need(row.get('limit')==128 and row.get('distributed_via_redis') is True
                 and row.get('raw_logs_saved') is False,'startup_limit_proof_changed')
            return dict(limit=128,distributed_via_redis=True,source='EXACT_GLM88_POD_STARTUP_RECEIPT')
    # Do not invoke the switcher's startup() fallback: that writes old evidence.
    raise RuntimeError('exact_glm88_startup_receipt_missing')


def proposal(spec):
    e=o.r.e
    cs=spec['template']['spec']['containers']
    need(len(cs)==1 and cs[0]['name']=='app' and not cs[0].get('envFrom'),'app_container_shape')
    env=cs[0].get('env',[])
    need(len({v['name'] for v in env})==len(env),'duplicate_environment_key')
    mapping={v['name']:v for v in env}
    expected={e.review.SETTINGS[k][1]:str(v) for k,v in dict(CURRENT,model=128).items()}
    need(expected==e.TARGET_ENV,'current_capacity_contract_changed')
    need(all(mapping.get(k)==dict(name=k,value=v) for k,v in expected.items()),'current_literal_concurrency_drift')
    key=e.review.SETTINGS['enrichment'][1]
    target=e.change_env(spec,{key:'64'})
    restored=copy.deepcopy(target)
    result=restored['template']['spec']['containers'][0]['env']
    indexes=[i for i,v in enumerate(result) if v.get('name')==key]
    need(len(indexes)==1 and result[indexes[0]]==dict(name=key,value='64'),'only_enrichment_delta_required')
    result[indexes[0]]=dict(name=key,value='48')
    need(restored==spec,'only_enrichment_delta_required')
    return target


def trends(samples):
    result=o.r.queue_trends(samples)
    for name,row in result.items():
        values=[s['queue']['queues'].get(name) for s in samples]
        reset=any(a is None or b is None or any(b[k]<a[k] for k in ('processed_total','failed_total'))
                  for a,b in zip(values,values[1:]))
        if reset:
            row.update(processed_counter_delta=None,failed_counter_delta=None,counter_reset_or_rollover=True)
    return result


def app_metric_freshness(pod,rows):
    need(len(rows)==1 and rows[0].get('node')==pod['spec']['nodeName'],'app_resource_node_changed')
    pods=rows[0].get('pods',[])
    need(len(pods)==1 and pods[0].get('pod')==pod['metadata']['name'],'app_resource_pod_changed')
    for key in ('cpu','memory'):
        stamp=pods[0].get(key,{}).get('time')
        need(isinstance(stamp,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z',stamp),
             'app_resource_time_format')
        sampled=datetime.datetime.strptime(stamp[:19],'%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
        need(abs((datetime.datetime.now(datetime.timezone.utc)-sampled).total_seconds())<120,'stale_app_resource_sample')


def resources(pod,pg_identity,report,label):
    r=o.r
    _,identity=r.e.review.pg_binding(pod)
    need(r.sw.same(identity,pg_identity),'postgres_identity_changed')
    db=r.e.database(pod); rows=r.obs.resource_snapshot({'app':[pod]})
    try:
        r.e.check_resource_rows(pod,db,rows,expanding=True)
        app_metric_freshness(pod,rows)
    except Exception as exc: r.blocker(report,label,exc)
    return dict(database=db['connections'],resources=rows)


def stage(report,value):
    report['stage']=value
    emit(dict(glm104_review='RUNNING',stage=value,production_writes=0,signals_sent=0))


def collect(root,report):
    r=o.r
    stage(report,'BIND_ACCEPTED_GLM88_AND_LOAD_DIAGNOSTIC')
    plan,activation=bind_inputs(); dep,pod=o.current(plan); identity=r.obs.identity(pod)
    need(dep['spec']==plan['candidate'],'original_environment_changed')
    proposal(dep['spec'])
    report['startup_model_governor']=startup_proof(pod)
    report['document_counts']=r.check_glm(pod,activation)
    r.e.runtime_env(pod,r.e.TARGET_ENV); r.app_endpoint(pod)
    need(r.sw.placement(dep,pod,candidate=True)==plan['placement'],'storage_placement_changed')
    process=r.sw.binary(pod,plan['candidate_binary'])
    r.obs.read_expansion()
    _,pods,signature,_=r.obs.baseline()
    need(r.sw.same(r.obs.identity(pods['weknora-app'][0]),identity),'initial_dependency_app_changed')
    _,pg_identity=r.e.review.pg_binding(pod)
    workers=[p for name in r.obs.WORKERS for p in pods[name]]
    targets=workers+pods[r.obs.DISPATCHER]
    binding=r.e.redis_binding(pod); server_ids=None
    report['current_pod']=dict(name=pod['metadata']['name'],uid=pod['metadata']['uid'],node=pod['spec']['nodeName'])
    report['app_resources']=r.obs.container(pod).get('resources',{})
    stage(report,'FIVE_QUEUE_HEALTH_AND_THREE_RESOURCE_SAMPLES')
    for i in range(SAMPLES):
        began=time.monotonic(); d,pod=o.current(plan,identity)
        need(d['spec']==dep['spec'],'original_environment_changed')
        with r.obs.ThreadPoolExecutor(max_workers=4) as pool:
            future=pool.submit(r.e.snapshot,binding)
            states=list(pool.map(r.obs.health,targets)); snapshot=future.result()
        r.e.check_capacities(snapshot,CURRENT,'active')
        if server_ids is None: server_ids=snapshot['server_identity_hashes']
        need(snapshot['server_identity_hashes']==server_ids,'consumer_identity_changed')
        row=dict(sample=i+1,utc=r.obs.utc(),queue=r.queue_projection(snapshot),health=states)
        try: row.update(r.mineru_check(states,workers))
        except Exception as exc: r.blocker(report,'MINERU_SAMPLE_'+str(i+1),exc)
        if i in (0,2,4): row.update(resources(pod,pg_identity,report,'RESOURCE_SAMPLE_'+str(i+1)))
        report['samples'].append(row); r.obs.write_new(root/('SAMPLE-%02d.json'%(i+1)),row)
        emit(dict(glm104_sample=i+1,of=SAMPLES,pools=row['queue']['pools'],mineru=row.get('dispatcher'),
                  queue_counts={n:{k:v[k] for k in ('pending','active','retry')} for n,v in row['queue']['queues'].items()},
                  blocker_count=len(report['blockers'])))
        if i<SAMPLES-1: time.sleep(max(0,INTERVAL-(time.monotonic()-began)))
    stage(report,'FINAL_RESOURCE_GLM_DEPENDENCY_AND_ENVIRONMENT_RECHECK')
    dep2,pod=o.current(plan,identity)
    need(dep2['spec']==dep['spec'],'original_environment_changed')
    report['document_counts']=r.check_glm(pod,activation)
    _,_,after,_=r.obs.baseline()
    need(r.sw.same(signature,after),'dependencies_changed_during_review')
    r.e.runtime_env(pod,r.e.TARGET_ENV); r.app_endpoint(pod); startup_proof(pod)
    need(r.sw.placement(dep2,pod,candidate=True)==plan['placement'],'placement_changed_during_review')
    report['final_resources']=resources(pod,pg_identity,report,'FINAL_RESOURCE_REVIEW')
    report['queue_trends']=trends(report['samples'])
    report['backlog_observed']=any(v['pending']>0 for s in report['samples'] for v in s['queue']['queues'].values())
    if report['blockers']:
        report['glm104_review']='BLOCKED'; return
    stage(report,'ENRICHMENT_ONLY_SERVER_DRY_RUN_NO_LIVE_PATCH')
    target=proposal(dep2['spec']); patch=r.dry_run(dep2,target)
    dep3,_=o.current(plan,identity)
    need(dep3['spec']==dep2['spec'] and dep3['metadata']['uid']==dep2['metadata']['uid'],'original_environment_changed')
    prepared=dict(original=dep2['spec'],candidate=target,pod_identity=identity,process=process,
        deployment_uid=dep2['metadata']['uid'],server_ids=server_ids,placement=plan['placement'],
        candidate_binary=plan['candidate_binary'],configuration=activation['configmap'],
        document_policy_catalog=activation['document_catalog_after'],dependency_signature=signature,
        postgres_identity=pg_identity,current=CURRENT,target=TARGET,original_model_default=128,target_model_default=128,
        accepted_glm88_sha256=o.PASS_SHA,diagnostic_sha256=DIAGNOSTIC_SHA,
        only_change='ENRICHMENT_48_TO_64',apply_permitted=False,fresh_apply_preflight_required=True,
        pending_queue_preserved=True,queue_empty_required=False)
    raw=r.obs.write_new(root/'PROPOSAL.private.json',prepared)
    r.obs.write_new(root/'SERVER-DRY-RUN.private.json',patch)
    report.update(glm104_review='PASS',ready_for_controlled_upgrade_review=True,server_dry_run='PASS',
                  proposal_sha256=hashlib.sha256(raw).hexdigest())


def public_summary(report):
    keys=('glm104_review','stage','blockers','current_pod','document_counts','app_resources',
          'startup_model_governor','queue_trends','backlog_observed','ready_for_controlled_upgrade_review',
          'server_dry_run','proposal_sha256','failure','final_resources','utc_started','utc_finished')
    result={k:report[k] for k in keys if k in report}
    result.update(current_worker_slots=88,proposed_worker_slots=104,current_pools=CURRENT,proposed_pools=TARGET,
        proposed_model_default=128,mineru_slots=16,max_proposed_parse_handlers=20,interactive_slots_reserved=0,
        only_change='ENRICHMENT_48_TO_64',production_writes=0,signals_sent=0,provider_probe_requests=0,
        consumer_slots_changed=False,mineru_slots_changed=False,model_changed=False,image_changed=False,
        database_migration_requested=False,queue_empty_required=False,ready_to_apply=False,
        throughput_benefit='NOT_MEASURED_BY_PREFLIGHT',historical_diagnostic_is_not_current_approval=True,
        task_counters_are_not_document_counts=True,health_may_trigger_existing_due_readiness_reprobe=True,
        resource_scope='APP_DATAX03_AND_POSTGRES_CONNECTIONS',
        further_mineru_expansion_requires_separate_shared_node_review=True,
        resource_samples=[dict(sample=s['sample'],database=s['database'],resources=s['resources'])
                          for s in report.get('samples',[]) if 'resources' in s])
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--review',action='store_true'); modes.add_argument('--status',type=Path)
    args=parser.parse_args(argv)
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if args.status:
        need(args.status.is_absolute() and args.status.parent==Path('/var/lib')
             and re.fullmatch(PREFIX+r'[a-z0-9_]{8}',args.status.name),'exact_review_directory_required')
        report=json.loads(o.safe_read(args.status/'RESULT.json'))
        emit(dict(public_summary(report),status_reads_stored_summary_not_live_cluster=True)); return 0
    emit(dict(glm104_review='LOADING_PINNED_READERS',production_writes=0,signals_sent=0)); initialize()
    o.r.obs.safe_parent('/var/lib')
    root=Path(tempfile.mkdtemp(prefix=PREFIX,dir='/var/lib')); root.chmod(0o700)
    report=dict(glm104_review='INCOMPLETE',utc_started=o.r.obs.utc(),stage='START',samples=[],blockers=[],
        accepted_glm88_sha256=o.PASS_SHA,diagnostic_sha256=DIAGNOSTIC_SHA,ready_for_controlled_upgrade_review=False)
    emit(dict(glm104_review_evidence=str(root),samples=SAMPLES,interval_seconds=INTERVAL,production_writes=0))
    try: collect(root,report)
    except BaseException as exc:
        report['failure']=dict(check=o.r.sw.recovery.safe_error(exc),error_type=type(exc).__name__,raw_error_printed=False,
            code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
    report['utc_finished']=o.r.obs.utc()
    raw=o.r.obs.write_new(root/'RESULT.json',report)
    emit(public_summary(report))
    emit(dict(report=str(root/'RESULT.json'),report_sha256=hashlib.sha256(raw).hexdigest(),private_snapshots_exported=False,
        production_writes=0,ready_to_apply=False,status_command='sudo python3 -I -B '+str(Path(__file__).resolve())+' --status '+str(root)))
    return 0 if report['glm104_review']=='PASS' else 1


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        emit(dict(glm104_review='INCOMPLETE',error_type=type(exc).__name__,
            failed_check=str(exc) if re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',str(exc)) else 'SAFE_DIAGNOSTIC_REQUIRED',
            production_writes=0,signals_sent=0,raw_error_printed=False,ready_to_apply=False))
        sys.exit(1)
