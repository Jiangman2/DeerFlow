#!/usr/bin/env python3
"""Read-only104 ->168/136 review. New private evidence and server dry-run only."""
import argparse
import copy
import datetime
from decimal import Decimal
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
import types

FOLLOWUP = Path('/var/lib/weknora-db-followup-tool-Em6tofN8/check.py')
FOLLOWUP_SHA = 'c2f088e3d28d4250dfc7806519e384786682ebe0d5c410a09f98cfa5f9b738e1'
FOLLOWUP_LOG = FOLLOWUP.with_name('check.log')
APP_UID = '2fb12206-2d91-4b12-806b-471e1ca19c61'
CURRENT = dict(core=16, enrichment=64, shared=4, postprocess=4, maintenance=4, wiki=12)
TARGETS = (128, 96)
ENV_KEYS = dict(core='WEKNORA_ASYNQ_CORE_CONCURRENCY', enrichment='WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY',
    shared='WEKNORA_ASYNQ_SHARED_CONCURRENCY', postprocess='WEKNORA_ASYNQ_POSTPROCESS_CONCURRENCY',
    maintenance='WEKNORA_ASYNQ_MAINTENANCE_CONCURRENCY', wiki='WEKNORA_WIKI_ASYNQ_CONCURRENCY',
    model='WEKNORA_MODEL_MAX_CONCURRENCY')
SETTING_KEYS = tuple('asynq.'+k+'_concurrency' for k in CURRENT)
PREFIX = 'weknora-glm168-review-'
SAMPLES, INTERVAL = 5, 15
GIB = 1024**3
BUDGETS = {128:dict(requests=dict(cpu='2', memory='12Gi'), limits=dict(memory='32Gi')),
           96:dict(requests=dict(cpu='2', memory='8Gi'), limits=dict(memory='24Gi'))}
TX = "BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; "
EXTRA_SQL = TX+"""
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'schema',(SELECT json_agg(json_build_object('version',version,'dirty',dirty)) FROM schema_migrations),
 'worker_settings',(SELECT coalesce(json_agg(key ORDER BY key),'[]'::json) FROM system_settings WHERE key IN (%s)),
 'lock_waiters',(SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='active' AND wait_event_type='Lock'));
ROLLBACK;""" % ','.join("'%s'"%k for k in SETTING_KEYS)


class Failure(RuntimeError): pass


def need(ok, label):
    if not ok: raise Failure(label)


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def emit(value): print(json.dumps(value,sort_keys=True,allow_nan=False),flush=True)


def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()


def error(exc):
    label=str(exc) if isinstance(exc,RuntimeError) and re.fullmatch('[a-z0-9_]{1,100}',str(exc)) else 'read_unavailable'
    return dict(error_type=type(exc).__name__,check=label,raw_error_printed=False)


def load_module(path, raw):
    module=types.ModuleType('frozen_'+path.stem);module.__file__=str(path)
    exec(compile(raw,str(path),'exec'),module.__dict__)
    return module


def bootstrap(chain=True):
    # Only the bootstrap needs its own safe reader. Frozen helpers are never CLI-executed.
    import stat
    for p in (FOLLOWUP.parent,)+tuple(FOLLOWUP.parent.parents):
        s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'unsafe_helper_parent')
    fd=os.open(str(FOLLOWUP),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        s=os.fstat(stream.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022 and s.st_size<=1048576,'unsafe_helper_file')
        raw=stream.read(1048577)
    need(hashlib.sha256(raw).hexdigest()==FOLLOWUP_SHA,'helper_hash_changed')
    f=load_module(FOLLOWUP,raw)
    if not chain:return types.SimpleNamespace(f=f)
    deep=f.initialize()
    d=load_module(deep.PRIOR,deep.safe_read(deep.PRIOR,deep.PRIOR_SHA))
    sw=load_module(d.TOOL,d.safe_read(d.TOOL,d.TOOL_SHA))
    sw.u=sw.load_review();sw.r=sw.u.o.r
    return types.SimpleNamespace(f=f,deep=deep,d=d,obs=sw.r.obs,e=sw.r.e,sw=sw,r=sw.r)


def proposal(spec, enrichment):
    need(enrichment in TARGETS,'unknown_candidate')
    cs=spec['template']['spec']['containers']
    need(len(cs)==1 and cs[0]['name']=='app' and not cs[0].get('envFrom'),'app_container_shape')
    env=cs[0].get('env',[]); names=[v['name'] for v in env]
    need(len(names)==len(set(names)),'duplicate_environment')
    mapping={v['name']:v for v in env}
    expected={ENV_KEYS[k]:str(v) for k,v in dict(CURRENT,model=128).items()}
    need(all(mapping.get(k)==dict(name=k,value=v) for k,v in expected.items()),'current_literal_environment_changed')
    out=copy.deepcopy(spec)
    index=names.index(ENV_KEYS['enrichment'])
    out['template']['spec']['containers'][0]['env'][index]['value']=str(enrichment)
    restored=copy.deepcopy(out);restored['template']['spec']['containers'][0]['env'][index]['value']='64'
    need(restored==spec,'candidate_changed_outside_enrichment')
    return out


def bind_inputs(c):
    c.d.bind_receipt()
    summary=json.loads(c.f.safe_read(c.f.ROOT/'SUMMARY.json',c.f.SUMMARY_SHA))
    need(summary.get('baseline_stable') is True and summary.get('sample_count')==11
         and summary.get('diagnostic')=='COMPLETE_WITH_UNAVAILABLE_CHECKS','unexpected_deep_receipt')
    raw=c.f.safe_read(FOLLOWUP_LOG,maximum=1048576)
    rows=[json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    completed=[x for x in rows if x.get('database_followup')=='COMPLETE']
    need(len(completed)==1,'corrected_followup_missing')
    receipt=completed[0]
    need(receipt.get('baseline_stable') is True and receipt.get('production_writes')==0
         and receipt.get('provider_requests')==0 and receipt.get('previous_summary_sha256')==c.f.SUMMARY_SHA
         and len(receipt.get('samples',[]))==3,'corrected_followup_not_complete')
    previous=[c.f.models(x['configuration']) for x in receipt['samples']]
    need(all(x['global_setting'] is None and all(m['mode']=='INHERIT_GLOBAL' for m in x['models'])
         and len(x['models'])==4 for x in previous),'historical_model_configuration_changed')
    plan,activation=c.sw.stored_plan()  # pure receipt reads, no old current/apply path
    ack=c.sw.read('PATCH-ACK.json');requested=c.sw.read('PATCH-REQUESTED.json')
    ops=c.sw.read('PATCH.private.json')
    need(ack['uid']==plan['deployment_uid'] and requested['uid']==plan['deployment_uid']
         and requested['candidate_sha256']==c.obs.digest(plan['candidate'])
         and requested['patch_sha256']==c.obs.digest(ops),'acknowledged104_binding_changed')
    need(c.sw.u.TARGET==CURRENT,'old104_target_changed')
    for n in TARGETS: proposal(plan['candidate'],n)
    return plan,activation,dict(deep_summary_sha256=c.f.SUMMARY_SHA,followup_log_sha256=hashlib.sha256(raw).hexdigest(),
        acknowledged104_receipt_sha256=c.d.RECEIPT_SHA,old104_receipt_not_rewritten=True)


def current(c,plan,identity=None):
    dep,pod=c.sw.new_pod(plan)
    need(pod['metadata']['uid']==APP_UID and c.obs.ready(pod),'expected_ready104_pod_required')
    need(dep['spec']==plan['candidate'] and dep['metadata']['uid']==plan['deployment_uid'],'live104_spec_changed')
    if identity is not None:need(c.r.sw.same(c.obs.identity(pod),identity),'app_process_changed')
    return dep,pod


def startup(c,pod):
    records=sorted(c.sw.ROOT.glob('MODEL-STARTUP-*.json'));need(len(records)<=100,'startup_record_bound')
    for p in records:
        row=json.loads(c.f.safe_read(p))
        if c.r.sw.same(row.get('pod_identity'),c.obs.identity(pod)):
            need(row.get('limit')==128 and row.get('distributed_via_redis') is True and row.get('raw_logs_saved') is False,'startup_limit_changed')
            return dict(limit=128,distributed_via_redis=True,evidence='EXACT_PROCESS_STARTUP_PLUS_FRESH_DB_ENV',
                live_process_local_waiters='NOT_MEASURED')
    raise Failure('exact104_startup_receipt_missing_no_capture_retry')


def database(c,pod,pg_identity):
    pg,identity=c.e.review.pg_binding(pod)
    need(c.r.sw.same(identity,pg_identity),'postgres_identity_changed')
    def sql(query):
        return json.loads(c.obs.call(['exec',pg['metadata']['name'],'-c',c.obs.container(pg)['name'],
            '--','sh','-c',c.e.review.DB_SHELL,'review',query],timeout=20,limit=65536))
    config=c.f.models(sql(c.f.MODEL_SQL))
    waits=c.f.waits(sql(c.f.WAIT_SQL))
    extra=sql(EXTRA_SQL)
    need(extra.get('read_only')=='on' and extra.get('schema')==[dict(version=90,dirty=False)],'database_schema_changed')
    need(extra.get('worker_settings')==[],'persisted_worker_override_requires_review')
    need(config['global_setting'] is None,'persisted_model_global_override_requires_review')
    need({x['id'] for x in config['models']}==set(c.f.MODELS) and all(x['mode']=='INHERIT_GLOBAL' for x in config['models']),'model_override_requires_review')
    need(type(extra.get('lock_waiters')) is int and extra['lock_waiters']>=0,'lock_count_shape')
    connections=dict(all=config['client_backends'],max_connections=config['max_connections'],
        reserved_connections=config['reserved_connections'],idle_in_transaction=config['idle_in_transaction'],lock_waiters=extra['lock_waiters'])
    return dict(configuration=config,waits=waits,connections=connections,
        resolved_default=128,default_resolution='DB_ABSENT_ENV128',parallel_workers_excluded_from_connection_count=True)


def quantity(value,kind):
    value=str(value)
    match=re.fullmatch(r'([0-9]+(?:\.[0-9]+)?)([A-Za-z]*)',value)
    need(match is not None,'unsupported_resource_quantity')
    amount=Decimal(match[1]); unit=match[2]
    scales={'':Decimal(1),'m':Decimal('0.001'),'u':Decimal('0.000001'),'n':Decimal('0.000000001')}
    if kind=='memory':
        scales.update({k:Decimal(v) for k,v in dict(Ki=1024,Mi=1024**2,Gi=1024**3,Ti=1024**4,k=1000,K=1000,M=1000**2,G=1000**3,T=1000**4).items()})
    need(unit in scales,'unsupported_resource_unit')
    return amount*scales[unit]


def pod_requests(pod):
    spec=pod['spec'];need(not spec.get('resources'),'pod_level_resources_require_review')
    def request(container,kind):
        resources=container.get('resources',{})
        value=resources.get('requests',{}).get(kind,resources.get('limits',{}).get(kind,'0'))
        return quantity(value,kind)
    total={}
    for kind in ('cpu','memory'):
        apps=sum((request(x,kind) for x in spec.get('containers',[])),Decimal(0))
        sidecars=Decimal(0); init_peak=Decimal(0)
        for x in spec.get('initContainers',[]):
            n=request(x,kind)
            if x.get('restartPolicy')=='Always':sidecars+=n;init_peak=max(init_peak,sidecars)
            else:init_peak=max(init_peak,sidecars+n)
        total[kind]=max(apps+sidecars,init_peak)+quantity(spec.get('overhead',{}).get(kind,'0'),kind)
    return total


def reservations(c,app,node):
    name=app['spec']['nodeName'];need(name=='datax-03','app_node_changed')
    # All namespaces, but only the reviewed node. Nothing except numeric totals leaves memory.
    result=json.loads(c.obs.call(['get','pods','--all-namespaces','--field-selector=spec.nodeName='+name,'-o','json'],timeout=25,limit=8388608))
    items=result.get('items',[])
    need(isinstance(items,list) and len(items)<=500 and not result.get('metadata',{}).get('continue'),'node_pod_inventory_incomplete')
    total=dict(cpu=Decimal(0),memory=Decimal(0)); own=None; seen=set()
    for p in items:
        uid=p['metadata']['uid'];need(uid not in seen and p['spec'].get('nodeName')==name,'node_pod_identity_or_duplicate');seen.add(uid)
        if p.get('status',{}).get('phase') in ('Succeeded','Failed'):continue
        value=pod_requests(p)
        for k in total:total[k]+=value[k]
        if uid==app['metadata']['uid']:own=p
    need(own is not None and own['spec']==app['spec'],'node_pod_app_binding_changed')
    return total,own


def budget(enrichment,total,app,node,cgroup,row):
    planned=copy.deepcopy(app);cs=planned['spec']['containers']
    need(len(cs)==1 and cs[0]['name']=='app','budget_app_container_shape')
    cs[0]['resources']=copy.deepcopy(BUDGETS[enrichment])
    before=pod_requests(app);after=pod_requests(planned)
    projected={k:total[k]-before[k]+after[k] for k in total}
    alloc={k:quantity(node['status']['allocatable'][k],k) for k in total}
    need(all(projected[k]<=alloc[k]*Decimal('.9') for k in total),'candidate_reservation_headroom_insufficient')
    values=cgroup['values']
    v2=values.get('v2_memory_current');v1=values.get('v1_memory_usage');peak=values.get('v2_memory_peak',values.get('v1_memory_peak'))
    current_memory=(v2 or v1 or [None])[0]
    need(type(current_memory) is int and peak and type(peak[0]) is int,'memory_planning_cgroup_unavailable')
    limit=int(quantity(BUDGETS[enrichment]['limits']['memory'],'memory'))
    observed=max(current_memory,peak[0],row['pods'][0]['memory']['workingSetBytes'])
    factor=Decimal(enrichment)/64
    need(Decimal(observed)*factor+4*GIB<=limit,'candidate_memory_scenario_insufficient')
    remaining=row['memory']['availableBytes']-max(0,limit-row['pods'][0]['memory']['workingSetBytes'])
    need(remaining>=8*GIB,'candidate_full_limit_node_reserve_below_8GiB')
    return dict(resources=BUDGETS[enrichment],status='FEASIBLE_PLANNING_SCENARIO_NOT_APPROVED_OR_APPLIED',
        projected_node_cpu_requests=float(projected['cpu']),projected_node_memory_requests_bytes=int(projected['memory']),
        node_cpu_allocatable=float(alloc['cpu']),node_memory_allocatable_bytes=int(alloc['memory']),
        available_memory_if_app_reaches_limit_bytes=remaining,observed_cgroup_peak_bytes=peak[0],
        linear_memory_scenario_is_not_load_acceptance=True,cpu_limit_proposed=False)


def save(c,root,name,row):return c.obs.write_new(root/name,row)


def check_pressure(delta):
    need(delta.get('available') is True and isinstance(delta.get('counters'),dict),'cgroup_delta_unavailable')
    counters=delta['counters'];need(counters,'cgroup_counters_missing')
    for key,value in counters.items():
        need(not value.get('counter_reset'),'cgroup_counter_reset')
        if 'failcnt' in key:need(value.get('delta')==0,'new_memory_limit_event')
        if key in ('v2_memory_events','v1_memory_oom_control'):
            need(all(value.get(k,0)==0 for k in ('max','oom','oom_kill','under_oom')),'new_memory_oom_event')
        if 'cpu' in key:
            need(all(value.get(k,0)==0 for k in ('nr_throttled','throttled_time','throttled_usec')),'new_cpu_throttling')


def collect(c,root,report):
    report['phase']='BIND_RECEIPTS_AND_EXACT_LIVE104'
    plan,activation,inputs=bind_inputs(c);report['inputs']=inputs
    dep,app=current(c,plan);identity=c.obs.identity(app)
    report['current_pod']=dict(name=app['metadata']['name'],uid=app['metadata']['uid'],node=app['spec']['nodeName'])
    report['current_app_resources']=c.obs.container(app).get('resources',{})
    c.e.runtime_env(app,{ENV_KEYS[k]:str(v) for k,v in dict(CURRENT,model=128).items()})
    report['model_default']=startup(c,app);report['document_counts']=c.r.check_glm(app,activation)
    c.r.app_endpoint(app)
    need(c.r.sw.placement(dep,app,candidate=True)==plan['placement'],'storage_placement_changed')
    process=c.r.sw.binary(app,plan['candidate_binary'])
    _,pods,signature,_=c.obs.baseline()
    need(c.r.sw.same(c.obs.identity(pods['weknora-app'][0]),identity),'baseline_app_changed')
    workers=[p for n in c.obs.WORKERS for p in pods[n]];targets=workers+pods[c.obs.DISPATCHER]
    _,pg_identity=c.e.review.pg_binding(app);binding=c.e.redis_binding(app)
    before=c.deep.cgroup(c.obs,app);report['cgroup_before']=before;server_ids=None
    node=c.obs.get('nodes',app['spec']['nodeName']);reserved,accounted_app=reservations(c,app,node)
    report['node_reservation_digest']=digest([str(reserved[k]) for k in ('cpu','memory')])
    report['candidate_budgets']={str(n):[] for n in TARGETS}
    for i in range(SAMPLES):
        report['phase']='FRESH_SAMPLE_'+str(i+1)
        began=time.monotonic();current(c,plan,identity)
        emit(dict(glm168_review='RUNNING',phase='FRESH_QUEUE_HEALTH_RESOURCE_DB',sample=i+1,of=SAMPLES,production_writes=0))
        with c.obs.ThreadPoolExecutor(max_workers=4) as pool:
            future=pool.submit(c.e.snapshot,binding);states=list(pool.map(c.obs.health,targets));snapshot=future.result()
        c.e.check_capacities(snapshot,CURRENT,'active')
        if server_ids is None:server_ids=snapshot['server_identity_hashes']
        need(server_ids==snapshot['server_identity_hashes'],'consumer_identity_changed')
        row=dict(sample=i+1,utc=utc(),queue=c.r.queue_projection(snapshot),health=states)
        try:row.update(c.r.mineru_check(states,workers))
        except Exception as exc:report['blockers'].append(dict(scope='mineru_sample_'+str(i+1),**error(exc)))
        if i in (0,2,4):
            db=database(c,app,pg_identity);resources=c.obs.resource_snapshot({'app':[app]})
            c.e.check_resource_rows(app,db,resources,expanding=True);c.sw.u.app_metric_freshness(app,resources)
            row.update(database=db,resources=resources)
            for n in TARGETS:
                try:report['candidate_budgets'][str(n)].append(budget(n,reserved,accounted_app,node,before,resources[0]))
                except Exception as exc:report['candidate_budgets'][str(n)].append(dict(status='BLOCKED',failure=error(exc)))
        report['samples'].append(row);save(c,root,'SAMPLE-%02d.json'%(i+1),row)
        emit(dict(sample=i+1,pools=row['queue']['pools'],mineru=row.get('dispatcher'),
            queues={n:{k:v[k] for k in ('pending','active','retry')} for n,v in row['queue']['queues'].items()}))
        if i<SAMPLES-1:time.sleep(max(0,INTERVAL-(time.monotonic()-began)))
    report['phase']='FINAL_RESOURCE_AND_BASELINE_RECHECK'
    dep,app=current(c,plan,identity);c.r.check_glm(app,activation);c.r.app_endpoint(app)
    c.e.runtime_env(app,{ENV_KEYS[k]:str(v) for k,v in dict(CURRENT,model=128).items()});startup(c,app)
    _,_,after,_=c.obs.baseline();need(c.r.sw.same(signature,after),'dependency_changed')
    total2,own2=reservations(c,app,node)
    need(total2==reserved and own2['spec']==accounted_app['spec'],'node_resource_reservations_changed')
    final_cgroup=c.deep.cgroup(c.obs,app)
    delta=c.deep.cgroup_delta(before,final_cgroup);report['cgroup_delta']=delta;check_pressure(delta)
    final_db=database(c,app,pg_identity);report['final_database']=final_db
    final_resources=c.obs.resource_snapshot({'app':[app]})
    c.e.check_resource_rows(app,final_db,final_resources,expanding=True);c.sw.u.app_metric_freshness(app,final_resources)
    report['final_resources']=final_resources
    node=c.obs.get('nodes',app['spec']['nodeName'])
    for n in TARGETS:
        try:report['candidate_budgets'][str(n)].append(budget(n,total2,own2,node,final_cgroup,final_resources[0]))
        except Exception as exc:report['candidate_budgets'][str(n)].append(dict(status='BLOCKED',failure=error(exc)))
    report['queue_trends']=c.sw.u.trends(report['samples'])
    db_rows=[x['database'] for x in report['samples'] if 'database' in x]
    persistent=[any(g['backend_type']=='client backend' and g['wait_type'] in ('IO','IPC','LWLock','Lock')
        and (g['max_query_age_seconds'] or 0)>30 for g in x['waits']['groups']) for x in db_rows]
    if all(persistent):report['blockers'].append(dict(scope='database',check='persistent_long_client_waits'))
    if report['blockers']:report['glm168_review']='BLOCKED';return
    report['phase']='ENV_ONLY_SERVER_DRY_RUN_NO_LIVE_PATCH'
    for n in TARGETS:
        rows=report['candidate_budgets'][str(n)]
        candidate=dict(enrichment=n,total_slots=sum(CURRENT.values())-64+n,ready_for_controlled_upgrade_review=False)
        report['candidates'].append(candidate)
        if len(rows)!=4 or any(x['status']=='BLOCKED' for x in rows):candidate['review']='BLOCKED_RESOURCE_SCENARIO';continue
        target=proposal(dep['spec'],n)
        patch=c.r.dry_run(dep,target)  # this helper has no live branch
        check_dep,_=current(c,plan,identity);need(check_dep['spec']==dep['spec'],'spec_changed_after_dry_run')
        prepared=dict(original=dep['spec'],candidate=target,pod_identity=identity,process=process,
            deployment_uid=dep['metadata']['uid'],current=CURRENT,target=dict(CURRENT,enrichment=n),
            model_default=128,mineru_slots=16,inputs=inputs,server_ids=server_ids,postgres_identity=pg_identity,
            dependency_signature=signature,placement=plan['placement'],candidate_binary=plan['candidate_binary'],
            configuration=activation['configmap'],document_policy_catalog=activation['document_catalog_after'],
            resource_budget_proposal=BUDGETS[n],resource_budget_included_in_candidate=False,
            apply_permitted=False,fresh_apply_preflight_required=True,maintenance_window_required=True,
            resource_budget_approval_required=True,pending_queue_preserved=True,queue_empty_required=False,
            only_dry_run_change='ENRICHMENT_64_TO_'+str(n))
        raw=save(c,root,'PROPOSAL-%d.private.json'%n,prepared);save(c,root,'DRY-RUN-%d.private.json'%n,patch)
        candidate.update(review='PASS',server_dry_run='PASS',ready_for_controlled_upgrade_review=True,
            proposal_sha256=hashlib.sha256(raw).hexdigest(),resource_budget_proposal=BUDGETS[n])
    current(c,plan,identity)
    _,_,end,_=c.obs.baseline();need(c.r.sw.same(signature,end),'dependency_changed_after_dry_run')
    report['glm168_review']='PASS' if any(x['review']=='PASS' for x in report['candidates']) else 'BLOCKED'


def public(report):
    keys=('glm168_review','phase','utc_started','utc_finished','current_pod','current_app_resources','inputs','model_default',
        'document_counts','blockers','candidates','candidate_budgets','queue_trends','cgroup_delta','failure','final_database','final_resources')
    out={k:report[k] for k in keys if k in report}
    out.update(current_slots=104,preferred_enrichment=128,preferred_total_slots=168,alternative_total_slots=136,
        mineru_slots=16,model_default_kept=128,production_writes=0,signals_sent=0,provider_submissions=0,
        model_changed=False,consumer_slots_changed=False,storage_changed=False,original_evidence_modified=False,
        ready_to_apply=False,resource_budget_is_unapproved_separate_proposal=True,
        remaining_release_gates=['RESOURCE_BUDGET_DECISION','CONTROLLED_RESTART_WINDOW','FRESH_MUTATION_PREFLIGHT','LOAD_AND_CONTENT_ACCEPTANCE'],
        glm128_plus_shared4_can_wait_at_default128=True,optional_glm192_override='ADVICE_ONLY_NOT_PREPARED_OR_APPLIED',
        resource_scope='APP_NODE_RESERVATIONS_AND_METRICS_PG_CONNECTION_WAIT_METADATA_NOT_DB_IOPS',
        health_may_trigger_existing_due_readiness_reprobe=True,task_counts_are_not_document_counts=True)
    out['preferred168_ready_for_controlled_review']=any(x.get('enrichment')==128 and x.get('review')=='PASS' for x in report.get('candidates',[]))
    out['resource_samples']=[dict(sample=x['sample'],resources=x['resources'],database=x['database']) for x in report.get('samples',[]) if 'resources' in x]
    return out


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);modes=p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--review',action='store_true');modes.add_argument('--status',type=Path)
    args=p.parse_args(argv)
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    import resource
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    c=bootstrap(chain=not bool(args.status))
    if args.status:
        need(args.status.parent==Path('/var/lib') and re.fullmatch(PREFIX+'[a-z0-9_]{8}',args.status.name),'exact_review_directory_required')
        emit(dict(public(json.loads(c.f.safe_read(args.status/'RESULT.json'))),status_reads_stored_report_not_live_cluster=True));return 0
    c.obs.safe_parent('/var/lib');root=Path(tempfile.mkdtemp(prefix=PREFIX,dir='/var/lib'));root.chmod(0o700)
    report=dict(glm168_review='INCOMPLETE',utc_started=utc(),samples=[],blockers=[],candidates=[])
    emit(dict(glm168_review_evidence=str(root),production_writes=0,samples=SAMPLES,interval_seconds=INTERVAL))
    try:collect(c,root,report)
    except BaseException as exc:
        report['failure']=dict(error(exc),code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
            for x in traceback.extract_tb(exc.__traceback__)[-5:]])
    report['utc_finished']=utc();raw=save(c,root,'RESULT.json',report);emit(public(report))
    emit(dict(report=str(root/'RESULT.json'),report_sha256=hashlib.sha256(raw).hexdigest(),private_manifests_exported=False,
        status_command='sudo python3 -I -B '+str(Path(__file__).resolve())+' --status '+str(root),ready_to_apply=False))
    return 0 if report['glm168_review']=='PASS' else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:emit(dict(glm168_review='INCOMPLETE',failure=error(exc),production_writes=0,ready_to_apply=False));sys.exit(1)
