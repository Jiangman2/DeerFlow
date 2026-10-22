#!/usr/bin/env python3
"""Receipt-bound GLM88 to GLM104 enrichment-only controlled restart.

No build, SQL writes, model submissions, automatic rollback, or mutation retry.
An explicit reconcile can finish an already signalled drain and issue the first
CAS; a recorded uncertain CAS is never replayed. Status is strictly read-only.
"""
import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import time
import tempfile
import traceback
import types
import uuid

REVIEW_TOOL=Path('/var/lib/weknora-glm104-review-tool-Fl7IvtXM/review.py')
REVIEW_TOOL_SHA='ecb124290028380637ae9bca1d6d348e58c0463901b960039ea62e10660ff410'
REVIEW_ROOT=Path('/var/lib/weknora-glm104-review-tnt5zssn')
REVIEW_SHA='28575a95773de99aed5466981af4d152cc64fc101d11972e41d9a9c3cd2179c1'
PROPOSAL_SHA='b073bf1a00c6312002447c19df2296396246a93a1299e7ee557746f13a2417ba'
ROOT=Path('/var/lib/weknora-glm104-switch-20261017-r1')
r=u=None
PHASE='READ_ONLY_PREFLIGHT'


def need(value,code):
    if not value: raise RuntimeError(code)


def load_review():
    # Bootstrap trust without importing a mutable sibling or invoking its CLI.
    for p in (REVIEW_TOOL.parent,)+tuple(REVIEW_TOOL.parent.parents):
        s=p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'unsafe_helper_parent')
    fd=os.open(str(REVIEW_TOOL),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as stream:
        s=os.fstat(stream.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid==0 and s.st_nlink==1 and not s.st_mode&0o022
             and s.st_size<=1048576,'unsafe_review_helper')
        raw=stream.read(1048577)
    need(hashlib.sha256(raw).hexdigest()==REVIEW_TOOL_SHA,'review_helper_hash_changed')
    module=types.ModuleType('glm104_frozen_review'); module.__file__=str(REVIEW_TOOL)
    exec(compile(raw,str(REVIEW_TOOL),'exec'),module.__dict__)
    module.initialize()
    return module


def read(name): return json.loads(r.safe_bytes(ROOT/name))


def save(name,row):
    # New-state atomic/no-replace receipt; never repoint a frozen module's ROOT.
    r.sw.old.safe_dir(ROOT)
    need(Path(name).name==name and name not in ('.','..'),'unsafe_journal_name')
    raw=(json.dumps(row,sort_keys=True,indent=2)+'\n').encode()
    fd,tmp=tempfile.mkstemp(prefix='.receipt-',dir=str(ROOT))
    try:
        with os.fdopen(fd,'wb') as stream:
            os.fchmod(stream.fileno(),0o600); stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.link(tmp,str(ROOT/name))
    finally: os.unlink(tmp)
    directory=os.open(str(ROOT),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)


def phase(value):
    global PHASE
    PHASE=value
    r.obs.emit(dict(glm104_switch='RUNNING',phase=value,mineru_slots_changed=False))


def pinned_inputs():
    review=json.loads(r.safe_bytes(REVIEW_ROOT/'RESULT.json',REVIEW_SHA))
    plan=json.loads(r.safe_bytes(REVIEW_ROOT/'PROPOSAL.private.json',PROPOSAL_SHA))
    need(review.get('glm104_review')=='PASS' and review.get('blockers')==[]
         and review.get('ready_for_controlled_upgrade_review') is True
         and review.get('server_dry_run')=='PASS' and review.get('proposal_sha256')==PROPOSAL_SHA,
         'review_not_exact_pass')
    accepted,activation=u.bind_inputs()
    need(plan['original']==accepted['candidate'] and plan['candidate']==u.proposal(plan['original'])
         and plan['current']==u.CURRENT and plan['target']==u.TARGET
         and plan['target_model_default']==128 and plan['original_model_default']==128
         and plan['apply_permitted'] is False and plan['fresh_apply_preflight_required'] is True
         and plan['accepted_glm88_sha256']==u.o.PASS_SHA and plan['diagnostic_sha256']==u.DIAGNOSTIC_SHA
         and plan['only_change']=='ENRICHMENT_48_TO_64' and plan['queue_empty_required'] is False
         and plan['pending_queue_preserved'] is True,'proposal_scope_changed')
    need(plan['pod_identity'][0]==u.o.POD_UID and plan['deployment_uid']==r.e.process.DEP_UID
         and plan['configuration']==activation['configmap'] and plan['placement']==activation['placement']
         and plan['candidate_binary']==activation['candidate_binary']
         and plan['document_policy_catalog']==activation['document_catalog_after'],'proposal_binding_changed')
    return plan,activation


def current(plan):
    dep,pod=u.o.current(dict(candidate=plan['original']),plan['pod_identity'])
    need(dep['metadata']['uid']==plan['deployment_uid'] and dep['spec']==plan['original'],'original_environment_changed')
    return dep,pod


def target_env():
    return dict(r.e.TARGET_ENV,**{r.e.review.SETTINGS['enrichment'][1]:'64'})


def postgres_identity(pod,plan):
    _,identity=r.e.review.pg_binding(pod)
    need(r.sw.same(identity,plan['postgres_identity']),'postgres_identity_changed')


def other_signature(signature):
    result=copy.deepcopy(signature)
    for key in ('deployments','pods'): result[key].pop('weknora-app',None)
    return result


def health(plan,app_identity):
    _,pods,signature,_=r.obs.baseline()
    need(r.sw.same(other_signature(signature),other_signature(plan['dependency_signature'])),
         'dependent_workload_changed')
    need(r.sw.same(r.obs.identity(pods['weknora-app'][0]),app_identity),'health_app_identity_changed')
    workers=[p for n in r.obs.WORKERS for p in pods[n]]
    with r.obs.ThreadPoolExecutor(max_workers=4) as pool:
        states=list(pool.map(r.obs.health,workers+pods[r.obs.DISPATCHER]))
    return r.mineru_check(states,workers)


def fresh(plan,activation,stopped=False):
    dep,pod=current(plan)
    need(dep['spec']==plan['original'],'original_environment_changed')
    r.e.runtime_env(pod,r.e.TARGET_ENV); u.startup_proof(pod)
    counts=r.check_glm(pod,activation); r.app_endpoint(pod)
    need(r.sw.placement(dep,pod,candidate=True)==plan['placement'],'placement_changed')
    postgres_identity(pod,plan)
    db=r.e.database(pod); rows=r.obs.resource_snapshot({'app':[pod]})
    r.e.check_resource_rows(pod,db,rows,expanding=True); u.app_metric_freshness(pod,rows)
    h=health(plan,plan['pod_identity'])
    snapshot=r.e.snapshot(r.e.redis_binding(pod))
    r.e.check_capacities(snapshot,u.CURRENT,'stopped' if stopped else 'active')
    need(snapshot['server_identity_hashes']==plan['server_ids'],'consumer_identity_changed')
    if stopped: need(r.e.drain_complete(snapshot),'claimed_work_not_finished')
    # Baseline recheck is after the slower resource/DB/worker reads.
    dep,pod=current(plan)
    return dep,pod,dict(queues=r.queue_projection(snapshot),resources=rows,database=db['connections'],
                        mineru=h,document_counts=counts)


def prepare():
    phase('BIND_REVIEW_AND_FRESH_GLM88_PREFLIGHT')
    plan,activation=pinned_inputs()
    dep,pod,evidence=fresh(plan,activation)
    need(r.sw.binary(pod,plan['candidate_binary'])==plan['process'],'reviewed_process_changed')
    r.dry_run(dep,plan['candidate'])
    save('PLAN.private.json',plan)
    save('PREPARED.json',dict(plan_sha256=hashlib.sha256(r.safe_bytes(ROOT/'PLAN.private.json')).hexdigest(),
                             review_sha256=REVIEW_SHA,proposal_sha256=PROPOSAL_SHA))
    save('PREFLIGHT.json',evidence)
    return plan,activation


def stored_plan():
    expected,activation=pinned_inputs()
    prepared=read('PREPARED.json')
    plan=json.loads(r.safe_bytes(ROOT/'PLAN.private.json',prepared['plan_sha256']))
    need(r.sw.same(plan,expected) and prepared['review_sha256']==REVIEW_SHA and prepared['proposal_sha256']==PROPOSAL_SHA,
         'stored_plan_binding_changed')
    return plan,activation


def signal_once(plan,activation):
    need(not os.path.lexists(str(ROOT/'SIGNAL-REQUESTED.json')),'signal_already_requested_NO_REPEAT')
    phase('FINAL_PREFLIGHT_AND_DRYRUN_BEFORE_STOP')
    dep,pod,evidence=fresh(plan,activation); r.dry_run(dep,plan['candidate'])
    need(r.sw.binary(pod,plan['candidate_binary'])==plan['process'],'process_before_signal_changed')
    current(plan)
    save('FINAL-PREFLIGHT.json',evidence)
    save('SIGNAL-REQUESTED.json',dict(pod_identity=plan['pod_identity'],process=plan['process']))
    phase('STOP_CLAIMING_ONCE_PENDING_QUEUES_PRESERVED')
    response=r.obs.call(['exec',pod['metadata']['name'],'-c','app','--','gosu','1000:1000',
        'python3','-I','-B','-c',r.e.SIGNAL_CODE,json.dumps(dict(start_ticks=plan['process']['start_ticks'],
        binary_sha256=plan['candidate_binary']))],timeout=40,limit=4096)
    ack=json.loads(response)
    need(ack==dict(tstp_sent_once=True,pid=1,start_ticks=plan['process']['start_ticks']),
         'signal_ack_uncertain_USE_STATUS')
    save('SIGNAL-ACK.json',ack)


def drain(plan,report):
    phase('WAIT_CLAIMED_WORK_ONLY_NO_FORCE_TERMINATION')
    need(r.sw.same(read('SIGNAL-REQUESTED.json'),dict(pod_identity=plan['pod_identity'],process=plan['process'])),
         'signal_journal_binding_changed')
    began=time.monotonic(); stable=0; samples=[]
    # The old image56 waiter has a56 capacity contract; never call or modify it.
    while time.monotonic()-began<1800:
        _,pod=current(plan)
        snap=r.e.snapshot(r.e.redis_binding(pod)); r.e.check_capacities(snap,u.CURRENT)
        need(snap['server_identity_hashes']==plan['server_ids'],'consumer_identity_changed')
        states={p['pool']:p['status'] for p in snap['effective_servers']}
        stable=stable+1 if r.e.drain_complete(snap) else 0
        row=dict(utc=r.obs.utc(),pool_states=states,
            active_handlers=sum(p['active'] for p in snap['effective_servers']),
            redis_active=sum(q['active'] for q in snap['queues'].values()),
            pending_retained=sum(q['pending'] for q in snap['queues'].values()),
            stable_zero_samples=stable,elapsed_seconds=round(time.monotonic()-began))
        samples.append(row); samples=samples[-3:]
        save('drain-'+uuid.uuid4().hex+'.json',row); r.obs.emit(dict(weknora_drain='RUNNING',**row))
        if stable>=3:
            save('DRAIN-VERIFIED-'+uuid.uuid4().hex+'.json',dict(samples=samples,six_stopped_pools=True))
            report['live_stop_behavior']='SIX_POOLS_STOPPED_AND_ZERO_ACTIVE_CONFIRMED'
            return
        need(time.monotonic()-began<90 or all(v=='stopped' for v in states.values()),
             'not_all_pools_stopped_NO_FORCE_TERMINATION')
        time.sleep(10)
    raise RuntimeError('claimed_work_deadline_USE_STATUS_NO_FORCE_TERMINATION')


def apply_after_drain(plan,activation,report):
    need(not os.path.lexists(str(ROOT/'PATCH-REQUESTED.json')),'patch_already_requested_NO_REPEAT')
    drain(plan,report)
    phase('FRESH_GLM_MINERU_RESOURCE_RECHECK_AFTER_DRAIN')
    dep,pod,evidence=fresh(plan,activation,stopped=True)
    r.dry_run(dep,plan['candidate'])
    # Close any last-moment spec/process or claimed-work change after dry-run.
    dep,pod=current(plan)
    snapshot=r.e.snapshot(r.e.redis_binding(pod)); r.e.check_capacities(snapshot,u.CURRENT,'stopped')
    need(snapshot['server_identity_hashes']==plan['server_ids'] and r.e.drain_complete(snapshot),
         'claimed_work_changed_before_patch')
    ops=r.e.patch_plan(dep,plan['candidate'])
    save('POST-DRAIN-CHECK.json',evidence); save('PATCH.private.json',ops)
    save('PATCH-REQUESTED.json',dict(candidate_sha256=r.obs.digest(plan['candidate']),
        patch_sha256=r.obs.digest(ops),uid=dep['metadata']['uid'],resource_version=dep['metadata']['resourceVersion']))
    phase('CAS_ENRICHMENT_ONLY_RECREATE_API_BRIEFLY_UNAVAILABLE')
    # No retry wrapper. A lost response is reconciled by reads, never replayed.
    reply=json.loads(r.sw.a.native.call(['patch','deployment','weknora-app','-n','deer-flow','--type=json',
        '--patch-file=/dev/stdin','-o','json'],data=json.dumps(ops).encode(),timeout=40,limit=1048576)[1])
    need(reply['metadata']['uid']==dep['metadata']['uid'] and reply['spec']==plan['candidate'],
         'environment_patch_ack_uncertain_USE_STATUS')
    save('PATCH-ACK.json',dict(uid=reply['metadata']['uid'],generation=reply['metadata']['generation']))


def startup(pod):
    records=sorted(ROOT.glob('MODEL-STARTUP-*.json'))
    need(len(records)<=100,'startup_record_bound')
    for p in records:
        proof=read(p.name)
        if r.sw.same(proof.get('pod_identity'),r.obs.identity(pod)):
            need(proof.get('limit')==128 and proof.get('distributed_via_redis') is True
                 and proof.get('raw_logs_saved') is False,'startup_evidence_changed')
            return proof
    return r.e.capture_startup_limit(ROOT,pod,128)


def new_pod(plan):
    dep=r.e.deployment()
    pod=r.sw.validate_new_pod(dep,dict(candidate=plan['candidate'],old_pod_uid=u.o.POD_UID))
    return dep,pod


def accept(plan,activation,report):
    phase('WAIT_SAME_IMAGE_GLM104_MODEL128_AND_SERVICE_READINESS')
    began=time.monotonic(); stable=0; identity=None; last=None
    transient={'one_ready_app_required','app_terminating','original_pod_still_serving','controller_not_converged',
        'incomplete_queue_snapshot','six_pool_process_required','six_unique_server_ids_required',
        'pool_state_mismatch','other_active_asynq_host','model_governor_startup_not_confirmed'}
    while time.monotonic()-began<600:
        try:
            dep,pod=new_pod(plan); current=r.obs.identity(pod)
            if not r.sw.same(current,identity): identity=current; stable=0
            startup(pod); r.e.runtime_env(pod,target_env())
            snap=r.e.snapshot(r.e.redis_binding(pod)); r.e.check_capacities(snap,u.TARGET,'active')
            stable+=1; last=None
            if stable>=3: break
        except Exception as exc:
            stable=0; last=r.sw.recovery.safe_error(exc)
            if str(exc) not in transient: raise
        r.obs.emit(dict(glm104_rollout='WAITING',elapsed_seconds=round(time.monotonic()-began),
                        stable_samples=stable,last_check=last)); time.sleep(10)
    need(stable>=3,'rollout_deadline_USE_STATUS_NO_AUTOMATIC_ROLLBACK')
    r.sw.binary(pod,plan['candidate_binary'])
    counts=r.check_glm(pod,activation); r.app_endpoint(pod)
    need(r.sw.placement(dep,pod,candidate=True)==plan['placement'],'post_switch_placement_changed')
    postgres_identity(pod,plan)
    db=r.e.database(pod); resources=r.obs.resource_snapshot({'app':[pod]})
    r.e.check_resource_rows(pod,db,resources,expanding=True); u.app_metric_freshness(pod,resources)
    h=health(plan,identity)
    _,end=new_pod(plan); need(r.sw.same(r.obs.identity(end),identity),'post_switch_process_changed')
    save('ACCEPTANCE-'+uuid.uuid4().hex+'.json',dict(pod_identity=identity,resources=resources,
        database=db['connections'],mineru=h,queues=r.queue_projection(snap),document_counts=counts))
    result=dict(glm104_switch='PASS',effective_worker_slots=104,effective_pools=u.TARGET,
        model_governor_startup_limit=128,model_id=r.a.rules.NEW,configuration='GLM',database='GLM',
        pod=pod['metadata']['name'],pod_uid=pod['metadata']['uid'],schema_version=90,
        pending_queue_preserved=True,mineru_slots=16,mineru_quarantined=0,
        throughput_acceptance='PENDING_REAL_BACKLOG_AND_RESOURCE_OBSERVATION',
        new_slot_count_is_not_throughput_proof=True,only_change='ENRICHMENT_48_TO_64')
    if not (ROOT/'SUCCESS.json').exists(): save('SUCCESS.json',result)
    report.update(result)


def advance(plan,activation,report,applying):
    if (ROOT/'PATCH-REQUESTED.json').exists():
        requested=read('PATCH-REQUESTED.json'); ops=read('PATCH.private.json')
        need(requested['candidate_sha256']==r.obs.digest(plan['candidate']) and requested['patch_sha256']==r.obs.digest(ops),
             'patch_journal_binding_changed')
        expected=r.e.patch_plan(dict(metadata=dict(uid=r.e.process.DEP_UID,
            resourceVersion=requested['resource_version']),spec=plan['original']),plan['candidate'])
        need(requested['uid']==r.e.process.DEP_UID and ops==expected,'patch_journal_scope_changed')
        live=r.e.deployment()
        need(live['metadata']['uid']==plan['deployment_uid'] and live['spec']==plan['candidate'],
             'uncertain_patch_NOT_REPLAYED_USE_STATUS')
    else:
        if not (ROOT/'SIGNAL-REQUESTED.json').exists():
            need(applying,'no_signal_to_reconcile_NO_AUTOMATIC_START'); signal_once(plan,activation)
        apply_after_drain(plan,activation,report)
    accept(plan,activation,report)


def status():
    out=dict(glm104_status='READ_ONLY',state=str(ROOT),production_writes=0,signals_sent=0,
             provider_probe_requests=0,files_modified=False,automatic_retry=False)
    if ROOT.exists():
        r.sw.old.safe_dir(ROOT)
        out['records']={k:(ROOT/(k+'.json')).exists() for k in
            ('STARTED','PREPARED','SIGNAL-REQUESTED','SIGNAL-ACK','PATCH-REQUESTED','PATCH-ACK','SUCCESS')}
        if out['records']['PREPARED']:
            plan,activation=stored_plan(); dep=r.e.deployment()
            out['live_configuration']='GLM104' if dep['spec']==plan['candidate'] else (
                'GLM88' if dep['spec']==plan['original'] else 'DRIFT')
            pods=r.obs.owned(dep)
            out['pods']=[dict(name=p['metadata']['name'],uid=p['metadata']['uid'],ready=r.obs.ready(p),
                             phase=p.get('status',{}).get('phase')) for p in pods]
            if len(pods)==1 and r.obs.ready(pods[0]):
                s=r.e.snapshot(r.e.redis_binding(pods[0])); out['pools']=s['effective_servers']
                out['consumers_stopped_and_claimed_empty']=r.e.drain_complete(s)
                out['queues']={n:{k:v[k] for k in ('pending','active','retry')} for n,v in s['queues'].items()}
                out['database']=r.a.database_state(pods[0],activation)
    r.obs.emit(out); return 0


def main(argv=None):
    global r,u
    p=argparse.ArgumentParser(description=__doc__); g=p.add_mutually_exclusive_group(required=True)
    for mode in ('apply-once','status','reconcile'): g.add_argument('--'+mode,action='store_true')
    args=p.parse_args(argv)
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    import fcntl,resource
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0)); u=load_review(); r=u.o.r
    if args.status: return status()
    def interrupted(sig,frame): raise RuntimeError('interrupted_USE_STATUS')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupted)
    descriptors=[]
    try:
        fd=r.sw.frozen_consumers.open_safe(r.e.ROOT/'controller.lock'); descriptors.append(fd)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); descriptors.append(r.e.acquire_previous_lock())
        with contextlib.ExitStack() as locks:
            for path in (r.sw.ROOT,r.a.stage.ROOT,r.ACTIVATION,u.o.s.ROOT,ROOT): locks.enter_context(r.sw.recovery.lock(path))
            if args.apply_once:
                need(not os.path.lexists(str(ROOT)),'previous_attempt_USE_STATUS_NO_REPEAT_APPLY')
                ROOT.mkdir(mode=0o700); save('STARTED.json',dict(review_sha256=REVIEW_SHA,proposal_sha256=PROPOSAL_SHA))
            else: r.sw.old.safe_dir(ROOT)
            report=dict(glm104_switch='UNCONFIRMED',state=str(ROOT),utc_started=r.obs.utc(),
                image_changed=False,model_changed=False,embedding_changed=False,mineru_slots_changed=False,
                database_writes=0,database_migration_requested=False,credentials_requested=False,
                provider_probe_requests=0,build_started=False,images_imported=False,queue_empty_required=False,
                automatic_retry=False,automatic_rollback=False)
            code=1
            try:
                plan,activation=prepare() if args.apply_once else stored_plan()
                advance(plan,activation,report,args.apply_once); code=0
            except BaseException as exc:
                report.update(failed_stage=PHASE,failed_check=r.sw.recovery.safe_error(exc),error_type=type(exc).__name__,
                    raw_error_printed=False,code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                    for x in traceback.extract_tb(exc.__traceback__)[-8:]])
            report.update(utc_finished=r.obs.utc(),signal_attempted=(ROOT/'SIGNAL-REQUESTED.json').exists(),
                live_patch_attempted=(ROOT/'PATCH-REQUESTED.json').exists(),live_patch_acknowledged=(ROOT/'PATCH-ACK.json').exists(),
                instruction='USE_STATUS_NO_REPEAT_APPLY_NO_FORCE_TERMINATION_NO_OLD88_OR_IMAGE_ONLY_ROLLBACK')
            name='RESULT-'+uuid.uuid4().hex+'.json'; save(name,report); r.obs.emit(report)
            r.obs.emit(dict(report=str(ROOT/name),report_sha256=hashlib.sha256(r.safe_bytes(ROOT/name)).hexdigest()))
            return code
    finally:
        for fd in descriptors: os.close(fd)


if __name__=='__main__':
    try: sys.exit(main())
    except Exception as exc:
        print(json.dumps(dict(glm104_switch='UNCONFIRMED',error_type=type(exc).__name__,raw_error_printed=False,
            failed_check=str(exc) if re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}',str(exc)) else 'SAFE_DIAGNOSTIC_REQUIRED',
            instruction='USE_STATUS_NO_REPEAT_APPLY')),flush=True); sys.exit(1)

