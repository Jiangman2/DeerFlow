#!/usr/bin/env python3
"""Fresh read-only 88-slot review after the accepted GLM56 cutover.

Only local private evidence and Kubernetes server dry-run are written. Never
call an old mutator, stop consumers, alter queues, or submit a document/model.
The reviewed proposal is not an apply tool or a performance acceptance receipt.
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

TOOL=Path('/var/lib/weknora-glm-activate56-tool-sYzAva0R/activate.py')
TOOL_SHA='cf76e0dc1b1a9179ee3234562332847bfae3201be8070a4f0f042e981ee04580'
ACTIVATION=Path('/var/lib/weknora-glm-activate56-20261017-r1')
RECEIPT=ACTIVATION/'RESULT-46744846381e492a92e1e93791327ee3.json'
RECEIPT_SHA='85cb2a512e407a913d82702f00412cd41c7e256f9cf4798253c160049b32b05c'
POD='weknora-app-555f64bd5-zlsrv'
POD_UID='6b05f3d1-931b-48f3-83f3-02b3c724fbd8'
CURRENT={'core':8,'postprocess':4,'enrichment':24,'maintenance':4,'shared':4,'wiki':12}
TARGET={'core':16,'postprocess':4,'enrichment':48,'maintenance':4,'shared':4,'wiki':12}
SAMPLES=5
INTERVAL=15
PREFIX='weknora-glm88-review-'
a=sw=obs=e=None


def need(value,code):
    if not value: raise RuntimeError(code)


def safe_bytes(path,expected=None):
    path=Path(path)
    for parent in (path.parent,)+tuple(path.parent.parents):
        s=parent.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'unsafe_private_ancestor')
    fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1
             and not before.st_mode&0o022 and before.st_size<=4194304,'unsafe_private_file')
        raw=stream.read(4194305); after=os.fstat(stream.fileno()); named=path.lstat()
    identity=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
    need(identity(before)==identity(after)==identity(named) and len(raw)==before.st_size,'private_file_changed')
    need(expected is None or hashlib.sha256(raw).hexdigest()==expected,'pinned_file_hash_changed')
    return raw


def load_activation():
    raw=safe_bytes(TOOL,TOOL_SHA)
    exec(compile(raw,str(TOOL),'exec'),{'__name__':'glm88_review_frozen_readers','__file__':str(TOOL)})
    return sys.modules['activate_glm56']


def load_helpers():
    global a,sw,obs,e
    a=load_activation(); sw=a.sw
    sw.initialize(); obs=sw.obs; e=sw.e
    need(e.CURRENT==CURRENT and e.CURRENT_MODEL==64 and e.TARGET==TARGET and e.TARGET_MODEL==128,
         'frozen_capacity_contract_changed')


def baseline_receipt():
    r=json.loads(safe_bytes(RECEIPT,RECEIPT_SHA))
    need(r.get('glm_activation')=='PASS' and r.get('model_id')==a.rules.NEW
         and r.get('model_status')=='active' and r.get('is_default') is True
         and r.get('effective_pools')==CURRENT and r.get('effective_worker_slots')==56
         and r.get('model_governor_startup_limit')==64 and r.get('document_rows_migrated')==23964
         and r.get('document_batches')==48 and r.get('legacy_document_overrides')==0
         and r.get('document_write_guard')=='PASS' and r.get('schema_version')==90
         and r.get('pending_queue_preserved') is True and r.get('image_changed') is False
         and r.get('mineru_slots_changed') is False and r.get('live_patch_acknowledged') is True
         and r.get('pod')==POD and r.get('pod_uid')==POD_UID,'activation_receipt_not_exact_pass')
    plan=a.read_plan()
    success,_=sw.recovery.read(ACTIVATION/'SUCCESS.json')
    need(all(success.get(k)==r.get(k) for k in ('glm_activation','pod','pod_uid','effective_pools','model_id')),
         'activation_success_marker_changed')
    return plan


def current(plan,identity=None):
    dep=e.deployment(); pod=sw.validate_new_pod(dep,plan)
    need(pod['metadata']['name']==POD and pod['metadata']['uid']==POD_UID,'post_glm_pod_changed_requires_review')
    if identity is not None: need(sw.same(obs.identity(pod),identity),'process_changed_during_review')
    return dep,pod


def startup_proof(pod):
    records=sorted(ACTIVATION.glob('MODEL-STARTUP-*.json'))
    need(len(records)<=100,'startup_record_bound')
    for path in records:
        row,_=sw.recovery.read(path)
        if sw.same(row.get('pod_identity'),obs.identity(pod)):
            need(row.get('limit')==64 and row.get('distributed_via_redis') is True
                 and row.get('raw_logs_saved') is False,'startup_limit_proof_changed')
            return dict(limit=64,distributed_via_redis=True,source='EXACT_POD_STARTUP_RECEIPT')
    raise RuntimeError('exact_pod_startup_receipt_missing')


def check_glm(pod,plan):
    need(a.database_state(pod,plan)=='GLM','glm_model_or_reference_policy_drift')
    counts=a.sql_read(pod,a.docs.COUNTS_SQL)
    need(counts.get('legacy')==0 and counts.get('inline')==0,'unreviewed_document_override')
    cm=sw.a.native.get('configmap',a.rules.CM,'deer-flow')
    ack,_=sw.recovery.read(ACTIVATION/'CONFIG-ACK.json')
    a.config_object(cm,plan['configmap'],ack['uid']); a.mounted(pod,plan['configmap'])
    return counts


def app_endpoint(pod):
    data=sw.a.native.get('endpoints','weknora-app','deer-flow'); rows=[]
    for sub in data.get('subsets',[]):
        need(not sub.get('notReadyAddresses'),'app_endpoint_unready')
        rows+=sub.get('addresses',[])
    need(rows and all(r.get('targetRef',{}).get('uid')==pod['metadata']['uid'] for r in rows),
         'app_endpoint_not_exact_current_pod')


def proposal(spec):
    target_env={e.review.SETTINGS[k][1]:str(v) for k,v in dict(TARGET,model=128).items()}
    need(target_env==e.TARGET_ENV,'target_env_contract')
    before={v['name']:v.get('value') for v in spec['template']['spec']['containers'][0].get('env',[])}
    need(all(before.get(k)==v for k,v in e.CURRENT_ENV.items()),'current_literal_concurrency_drift')
    target=e.change_env(spec,target_env)
    changed=[k for k,v in target_env.items() if before.get(k)!=v]
    need(set(changed)=={e.review.SETTINGS[k][1] for k in ('core','enrichment','model')},'three_environment_changes_only')
    return target


def dry_run(dep,target):
    # Fixed verb/name, no live branch and no externally supplied operation.
    ops=e.patch_plan(dep,target)
    raw=sw.a.native.call(['patch','deployment','weknora-app','-n','deer-flow','--type=json',
        '--patch-file=/dev/stdin','--dry-run=server','-o','json'],data=json.dumps(ops).encode(),
        timeout=40,limit=1048576)[1]
    projected=json.loads(raw)
    need(projected['metadata']['uid']==dep['metadata']['uid'] and projected['spec']==target,
         'server_admission_changed_candidate')
    return ops


def queue_projection(snapshot):
    fields=('pending','active','scheduled','retry','archived','completed','processed_total','failed_total','paused','oldest_pending_seconds')
    queues={}
    for name,value in snapshot['queues'].items():
        need(name in obs.QUEUES,'unrecognized_queue')
        queues[name]={k:value[k] for k in fields}
    return dict(epoch=snapshot['redis_sample_epoch'],queues=queues,pools=snapshot['effective_servers'],
        server_ids=snapshot['server_identity_hashes'],non_atomic_snapshot=True,task_payloads_read=False)


def queue_trends(samples):
    if len(samples)<2: return {}
    first,last=samples[0]['queue'],samples[-1]['queue']
    out={}
    for name in sorted(set(first['queues'])|set(last['queues'])):
        if name not in first['queues'] or name not in last['queues']:
            out[name]={'coverage':'INCOMPLETE'}; continue
        x,y=first['queues'][name],last['queues'][name]
        processed=y['processed_total']-x['processed_total']; failed=y['failed_total']-x['failed_total']
        reset=processed<0 or failed<0
        out[name]=dict(pending_before=x['pending'],pending_after=y['pending'],pending_delta=y['pending']-x['pending'],
            active_after=y['active'],retry_after=y['retry'],oldest_pending_seconds=y['oldest_pending_seconds'],
            processed_counter_delta=None if reset else processed,failed_counter_delta=None if reset else failed,
            counter_reset_or_rollover=reset,window_seconds=last['epoch']-first['epoch'])
    return out


def mineru_check(states,workers):
    need(len(workers)==16 and len(states)==17,'mineru_16_workers_required')
    mapping={s.get('pod'):s for s in states}
    need(len(mapping)==17,'duplicate_health_pod')
    ds=[s for s in states if s.get('role')=='dispatcher']
    need(len(ds)==1,'dispatcher_health_unavailable')
    c=ds[0]['counters']
    need(c.get('discovered')==16 and c.get('live')==16 and c.get('quarantined')==0
         and c.get('ready') is True and c.get('capacity_target_met') is True,'mineru_16_not_healthy_for_expansion')
    ws=[mapping.get(p['metadata']['name'],{}) for p in workers]
    need(all(x.get('role')=='worker' and type(x.get('counters',{}).get('active')) is int
         and x['counters']['active'] in (0,1) and x['counters'].get('waiting')==0
         and not x.get('unavailable') for x in ws),'mineru_worker_read_or_serialization_failed')
    return dict(dispatcher=c,worker_reads_complete=True,active_workers=sum(x['counters']['active'] for x in ws))


def mark(report,stage):
    report['stage']=stage
    obs.emit(dict(glm88_review='RUNNING',stage=stage,production_writes=0,signals_sent=0))


def blocker(report,phase,exc):
    report['blockers'].append(dict(phase=phase,check=sw.recovery.safe_error(exc),error_type=type(exc).__name__))


def collect(root,report):
    mark(report,'BIND_ACCEPTED_GLM56_AND_NEW_POD')
    plan=baseline_receipt(); dep,pod=current(plan); identity=obs.identity(pod)
    report['startup_model_governor']=startup_proof(pod)
    report['document_counts']=check_glm(pod,plan)
    e.runtime_env(pod,e.CURRENT_ENV)
    need(sw.placement(dep,pod,candidate=True)==plan['placement'],'post_glm_storage_placement_changed')
    process=sw.binary(pod,plan['candidate_binary']); app_endpoint(pod)
    obs.read_expansion()
    _,pods,signature,_=obs.baseline()
    need(sw.same(obs.identity(pods['weknora-app'][0]),identity),'initial_dependency_app_changed')
    workers=[p for n in obs.WORKERS for p in pods[n]]
    targets=workers+pods[obs.DISPATCHER]
    binding=e.redis_binding(pod); server_ids=None
    report['app_resources']=obs.container(pod).get('resources',{})
    report['current_pod']=dict(name=POD,uid=POD_UID,node=pod['spec']['nodeName'])
    mark(report,'FIVE_QUEUE_HEALTH_SAMPLES_AND_THREE_RESOURCE_REVIEWS')
    for i in range(SAMPLES):
        began=time.monotonic(); current(plan,identity)
        with obs.ThreadPoolExecutor(max_workers=4) as pool:
            future=pool.submit(e.snapshot,binding)
            states=list(pool.map(obs.health,targets)); snapshot=future.result()
        e.check_capacities(snapshot,CURRENT,'active')
        if server_ids is None: server_ids=snapshot['server_identity_hashes']
        need(snapshot['server_identity_hashes']==server_ids,'consumer_identity_changed')
        row=dict(sample=i+1,utc=obs.utc(),queue=queue_projection(snapshot),health=states)
        try: row.update(mineru_check(states,workers))
        except Exception as exc: blocker(report,'MINERU_SAMPLE_'+str(i+1),exc)
        if i in (0,2,4):
            db=e.database(pod); resources=obs.resource_snapshot({'app':[pod]})
            row['database']=db; row['resources']=resources
            try: e.check_resource_rows(pod,db,resources,expanding=True)
            except Exception as exc: blocker(report,'RESOURCE_SAMPLE_'+str(i+1),exc)
        report['samples'].append(row); obs.write_new(root/('SAMPLE-%02d.json'%(i+1)),row)
        obs.emit(dict(glm88_sample=i+1,of=SAMPLES,pools=snapshot['effective_servers'],
            queue_counts={n:{k:v[k] for k in ('pending','active','retry')} for n,v in row['queue']['queues'].items()},
            mineru=row.get('dispatcher'),blocker_count=len(report['blockers'])))
        if i<SAMPLES-1: time.sleep(max(0,INTERVAL-(time.monotonic()-began)))
    mark(report,'FINAL_BASELINE_AND_GLM_CONFIGURATION_RECHECK')
    dep2,pod2=current(plan,identity); report['document_counts']=check_glm(pod2,plan)
    _,_,after,_=obs.baseline(); need(sw.same(signature,after),'dependencies_changed_during_review')
    e.runtime_env(pod2,e.CURRENT_ENV); app_endpoint(pod2)
    need(sw.placement(dep2,pod2,candidate=True)==plan['placement'],'placement_changed_during_review')
    db=e.database(pod2)
    report['final_database_connections']=db['connections']
    report['queue_trends']=queue_trends(report['samples'])
    report['backlog_observed']=any(v['pending']>0 for s in report['samples'] for v in s['queue']['queues'].values())
    report['throughput_benefit']='NOT_MEASURED_BY_PREFLIGHT'
    if report['blockers']:
        report['glm88_review']='BLOCKED'; return
    mark(report,'ENVIRONMENT_ONLY_SERVER_DRY_RUN')
    target=proposal(dep2['spec']); patch=dry_run(dep2,target)
    current(plan,identity)
    prepared=dict(original=dep2['spec'],candidate=target,pod_identity=identity,process=process,
        server_ids=server_ids,placement=plan['placement'],candidate_binary=plan['candidate_binary'],
        configuration=plan['configmap'],document_policy_catalog=plan['document_catalog_after'],
        activation_result_sha256=RECEIPT_SHA,activation_plan_sha256=sw.recovery.read(ACTIVATION/'PLAN.private.json')[1],
        dependency_signature=signature,target=TARGET,target_model_default=128,original_model_default=64,
        apply_permitted=False,fresh_apply_preflight_required=True)
    raw=obs.write_new(root/'PROPOSAL.private.json',prepared)
    obs.write_new(root/'SERVER-DRY-RUN.private.json',patch)
    report.update(glm88_review='PASS',ready_for_controlled_upgrade_review=True,server_dry_run='PASS',
        proposal_sha256=hashlib.sha256(raw).hexdigest())


def public_summary(report):
    keys=('glm88_review','stage','blockers','current_pod','document_counts','app_resources',
          'startup_model_governor','queue_trends','backlog_observed','throughput_benefit',
          'ready_for_controlled_upgrade_review','server_dry_run','proposal_sha256','failure')
    result={k:report[k] for k in keys if k in report}
    result.update(production_writes=0,signals_sent=0,provider_probe_requests=0,model_changed=False,
        image_changed=False,consumer_slots_changed=False,mineru_slots_changed=False,ready_to_apply=False,
        queue_empty_required=False,task_counters_are_not_document_counts=True,
        user_reported_glm_business_acceptance=True,business_acceptance_independently_replayed=False,
        current_worker_slots=56,proposed_worker_slots=88,proposed_pools=TARGET,
        proposed_model_default=128,mineru_slots=16,max_proposed_parse_handlers=20,interactive_slots_reserved=0,
        resource_samples=[dict(sample=s['sample'],database=s['database']['connections'],resources=s['resources'])
                          for s in report.get('samples',[]) if 'resources' in s])
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    g=p.add_mutually_exclusive_group(required=True); g.add_argument('--review',action='store_true'); g.add_argument('--status',type=Path)
    args=p.parse_args(argv)
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if args.status:
        need(args.status.is_absolute() and args.status.parent==Path('/var/lib')
             and re.fullmatch(PREFIX+r'[a-z0-9_]{8}',args.status.name),'exact_review_directory_required')
        report=json.loads(safe_bytes(args.status/'RESULT.json'))
        print(json.dumps(public_summary(report),sort_keys=True),flush=True); return 0
    load_helpers(); obs.safe_parent('/var/lib')
    root=Path(tempfile.mkdtemp(prefix=PREFIX,dir='/var/lib')); root.chmod(0o700)
    report=dict(glm88_review='INCOMPLETE',utc_started=obs.utc(),stage='START',samples=[],blockers=[],
        activation_result_sha256=RECEIPT_SHA,production_writes=0,signals_sent=0,provider_probe_requests=0,
        ready_for_controlled_upgrade_review=False)
    obs.emit(dict(glm88_review_evidence=str(root),production_writes=0,samples=SAMPLES,interval_seconds=INTERVAL))
    try: collect(root,report)
    except BaseException as exc:
        report['failure']=dict(check=sw.recovery.safe_error(exc),error_type=type(exc).__name__,raw_error_printed=False,
            code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                         for x in traceback.extract_tb(exc.__traceback__)[-6:]])
    report['utc_finished']=obs.utc()
    raw=obs.write_new(root/'RESULT.json',report)
    obs.emit(public_summary(report))
    obs.emit(dict(report=str(root/'RESULT.json'),report_sha256=hashlib.sha256(raw).hexdigest(),
        private_snapshots_exported=False,production_writes=0,ready_to_apply=False,
        status_command='sudo python3 -I -B '+str(Path(__file__).resolve())+' --status '+str(root)))
    return 0 if report['glm88_review']=='PASS' else 1


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(json.dumps(dict(glm88_review='INCOMPLETE',error_type=type(exc).__name__,
            failed_check=str(exc) if re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',str(exc)) else 'SAFE_DIAGNOSTIC_REQUIRED',
            production_writes=0,signals_sent=0,raw_error_printed=False,ready_to_apply=False)),flush=True)
        sys.exit(1)
