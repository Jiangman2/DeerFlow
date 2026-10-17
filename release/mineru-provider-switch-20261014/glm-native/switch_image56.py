"""One journaled WeKnora image-only Recreate; keep56 and model configuration.

Reuse frozen, previously delivered stop/drain readers, not the old88 mutator.
Never replay an uncertain signal or patch; --reconcile only reads an already
patched rollout or continues draining the already-signalled exact process.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import time
import traceback
import uuid

import review_activation as activation
import production_release_review as release
import recover_production_preload as recovery
import preload_native_candidate as image
import export_weknora_base as old
import diagnose_mineru_quarantine as frozen_consumers

ROOT=Path('/var/lib/weknora-glm-image56-switch-20261017-r1')
REVIEW_SHA='3821dd58f6bf3334b6b93cd75bef82e6659d3b52daf101cbcb8bc1a870f43fc2'
PIN_SHA='b3063bb997386ff2e6961f17022ba618d83e5295e35bf70052fcdaa5ecde3f49'
CAPS={'core':8,'postprocess':4,'enrichment':24,'maintenance':4,'shared':4,'wiki':12}
POLICIES={
 '2e7ee4bf2b9fea9437b10ba1262a404d22c32b61c6929b1b7cb767a9902fdbb8':('custom_agents','deerflow_apply_enterprise_custom_agent_defaults','390b5806f8befd67a9198acae227663994452a637dff1ab8f5fd77dd05b587ad',23),
 '7e2ec349640dcacffc3ec607609c9c58ff7f49b9fa135d3c3c49b5e1cc38fa13':('knowledge_bases','deerflow_apply_enterprise_knowledge_base_defaults','e0009edaf0f5c3790a8920d39c2d81d232ea66c7aa84e656dfd85f56b6180944',7),
 '89183b6ba79d1ccf1118392d765946e8d90d9eda279bc2855250e4c46d724c97':('knowledge_bases','deerflow_ensure_mineru_parser_rule','4bfa10b123374678fd1232008d9317aaf2ca46d447dd6b6abcca953f4c7b1df6',7),
 'd8cd829fe597e29c113b1c9f5a617a85c8e577956593c7fc9b700260d9a81783':('knowledge_bases','weknora_document_stability_r2_spreadsheet_default','bd058cb0bb72f9af6e6cae5f99285f7d41e7bd0a86ba9273f7f696db8b33b488',7),
 '8506b46c5819d7cba92815aefd00306f1d4d3e6cec26ef9c2997744f1bf05335':('models','deerflow_apply_enterprise_model_defaults','dbaa86abe52449ea0d59262d5e00f015a4a97b0f10975f553d823bf7fee92ef7',23),
}
CONFIG_HASHES={
 'config.yaml':'56158a544e26a361fd00ae721a5bd2552b61864c8f69bb7fc735fd3b766e7fe6',
 'builtin_models.yaml':'db1fc802c744d4a409278dbe6f41a273dcd6c39cf90b20e55f3be98a3383421f',
 'builtin_agents.yaml':'9077fbba6710ccce552f67a0d1aab375e019a1412893378427ad09e8aaddd15c'}
MODEL_SQL="""BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'schema',(SELECT json_agg(json_build_object('version',version,'dirty',dirty))
 FROM public.schema_migrations),'vision_signature',(SELECT md5(json_build_array(id,name,type,source,
 parameters->>'base_url',parameters->>'provider',parameters->>'interface_type',
 parameters->>'max_concurrency',parameters->>'supports_vision',
 parameters->'extra_config'->>'wire_profile',is_default,status,is_builtin,managed_by,deleted_at)::text) FROM public.models
 WHERE id='deerflow-dashscope-vlm'));
ROLLBACK;"""
a=e=obs=None
need=old.require


def initialize():
    global a,e,obs
    import recover_enrichment_gateway as native
    a=native.load_original()
    obs=frozen_consumers.load_helpers()
    e=sys.modules['tune_weknora88']
    need(e.CURRENT==CAPS and e.CURRENT_MODEL==64,'frozen_reader_capacity_contract')


def save(name,row):
    activation.ROOT=ROOT
    activation.save(name,row)


def read(name):
    return recovery.read(ROOT/name)[0]


def same(x,y):
    return obs.digest(x)==obs.digest(y)


def policy_check(rows):
    need(len(rows)==len(POLICIES),'policy_count_changed')
    seen=set()
    for p in rows:
        h=activation.sha(p['definition'].encode()); expected=POLICIES.get(h)
        need(expected is not None and h not in seen,'unreviewed_policy_definition')
        seen.add(h)
        table,name,body,tgtype=expected
        need((p.get('table_name'),p.get('function_name'),activation.sha(p['body'].encode()),p.get('trigger_type'))
             ==(table,name,body,tgtype) and p.get('function_schema')=='public' and p.get('enabled')=='O'
             and p.get('identity_arguments')=='' and p.get('security_definer') is False
             and not p.get('settings'),'policy_contract_changed')
    return sorted(seen)


def db_sql(pod,sql):
    pg,container=release.db_binding(a,pod)
    row=json.loads(a.native.call(['exec','-n','deer-flow',pg['metadata']['name'],'-c',container,
        '--','sh','-c',release.DB_SHELL,'image-switch-readonly',sql],limit=1048576)[1])
    need(a.native.get('pod',pg['metadata']['name'],'deer-flow')['metadata']['uid']==pg['metadata']['uid'],'db_pod_changed')
    return row


def model_state(pod):
    state=db_sql(pod,MODEL_SQL)
    need(state.get('read_only')=='on' and state.get('schema')==[{'version':90,'dirty':False}]
         and isinstance(state.get('vision_signature'),str)
         and re.fullmatch('[a-f0-9]{32}',state['vision_signature']),'schema_or_vision_state')
    return state


def configs(pod):
    cm=a.native.get('configmap','weknora-config','deer-flow')
    need(cm['metadata']['uid']=='9e1b3b93-4a53-49f2-8ec1-177761df46b3','configmap_identity_changed')
    for name,digest in CONFIG_HASHES.items():
        text=cm.get('data',{}).get(name)
        need(isinstance(text,str) and activation.sha(text.encode())==digest,'mounted_defaults_changed')
    actual=json.loads(a.native.call(['exec','-n','deer-flow',pod['metadata']['name'],'-c','app',
        '--','python3','-I','-B','-c',activation.CONFIG_PROBE],limit=8192)[1])
    need(set(actual)=={'/app/config/'+n for n in CONFIG_HASHES},'runtime_config_paths')
    need(all(actual['/app/config/'+n].get('sha256')==h for n,h in CONFIG_HASHES.items()),'runtime_defaults_changed')


def placement(dep,pod,candidate=False):
    module=e.placement
    prior=(module.IMAGE,module.IMAGE_ID)
    try:
        if candidate: module.IMAGE,module.IMAGE_ID=image.IMAGE,image.CONFIG
        result=module.check(dep,pod)
    finally: module.IMAGE,module.IMAGE_ID=prior
    # Node metadata is an additional current presence check, not a rehash or
    # fresh CRI pin check. Pin authority is the operator-provided verified receipt.
    n=obs.get('nodes','datax-03')
    names={s for v in n.get('status',{}).get('images',[]) for s in v.get('names',[])}
    need(old.IMAGE in names and image.IMAGE in names,'both_preloaded_image_tags_required')
    for key in ('image','current_image_id'): result.pop(key,None)
    return result


def binary(pod,digest):
    before=e.BINARY
    try:
        e.BINARY=digest
        return e.binary_probe(pod)
    finally: e.BINARY=before


def health():
    _,pods,signature,_=obs.baseline()
    h=obs.health(pods[obs.DISPATCHER][0]); c=h.get('counters',{})
    # This is not expansion: preserve the previously reviewed56 envelope.
    need(h.get('role')=='dispatcher' and c.get('live')==16 and c.get('discovered')==16
         and c.get('ready') is True and type(c.get('quarantined')) is int
         and 0<=c['quarantined']<=2,'mineru_outside_existing56_envelope')
    for k in ('deployments','pods'): signature[k].pop('weknora-app',None)
    return dict(mineru=h,other_workloads=signature,
        health_scope='EXISTING56_NOT_EXPANSION_APPROVAL',degraded=c['quarantined']!=0)


def fresh(plan,stopped=False):
    d=e.deployment(); pod=e.app_for(d,plan['original'])
    need(same(obs.identity(pod),plan['old_identity']),'original_process_changed')
    e.runtime_env(pod,e.CURRENT_ENV)
    db=e.database(pod); e.resources(pod,db,expanding=False)
    need(placement(d,pod)==plan['placement'],'placement_changed')
    configs(pod)
    need(model_state(pod)==plan['model_state'],'model_or_schema_changed')
    policy_check(db_sql(pod,activation.POLICY_SQL)['policies'])
    deps=health(); need(same(deps['other_workloads'],plan['other_workloads']),'dependent_workload_changed')
    s=e.snapshot(e.redis_binding(pod)); e.check_capacities(s,CAPS,'stopped' if stopped else 'active')
    need(s['server_identity_hashes']==plan['server_ids'],'consumer_identity_changed')
    if stopped: need(e.drain_complete(s),'claimed_work_not_finished')
    return d,pod,s


def check_plan_shape(plan):
    fake={'metadata':{'uid':e.process.DEP_UID,'resourceVersion':'unused'},'spec':plan['original']}
    _,expected=activation.image_patch(fake)
    need(plan['candidate']==expected and plan['old_pod_uid']==old.POD_UID
         and re.fullmatch('[a-f0-9]{64}',plan['candidate_binary']), 'image_only_plan_changed')


def prepare(report):
    obs.emit(dict(image_switch='PREFLIGHT',phase='BIND_REVIEW_AND_VERIFIED_IMAGE',production_workload_writes=0))
    r,_=recovery.read(activation.SOURCE/'RESULT.json',REVIEW_SHA)
    need(r.get('activation_review')=='PASS' and r.get('blockers')==['UNKNOWN_DB_TRIGGER']
         and r.get('image_only_server_dryrun')=='PASS' and r.get('current_worker_slots')==56,'activation_review_boundary')
    p,q=a.native.initialize(); release.accepted(a)
    _,guard,build_plan=q.package(); guard.live(build_plan,binary=True)
    raw=q.read(image.SOURCE/'BUILD-RESULT.json')
    need(activation.sha(raw)==image.REPORT_SHA,'build_receipt_changed')
    build=json.loads(raw); component=build['component']
    need(component['image']==image.IMAGE and component['manifest_digest']==image.MANIFEST
         and component['config_digest']==image.CONFIG and build['database_migrations']==[], 'candidate_identity_or_migrations')
    digest=component['runtime_files']['app/WeKnora']['sha256']
    need(re.fullmatch('[a-f0-9]{64}',digest),'candidate_binary_digest')
    archived,_=recovery.read(activation.SOURCE/'POLICIES.private.json')
    policy_check(archived['policies'])
    report['previous_unknown_triggers']='MATCHED_EXACT_MINERU_AND_SPREADSHEET_POLICY_BODIES'
    d=e.deployment(); pod=e.app_for(d,d['spec'])
    need(pod['metadata']['uid']==old.POD_UID,'reviewed_old_pod_changed')
    pr=binary(pod,e.BINARY); s=e.snapshot(e.redis_binding(pod)); e.check_capacities(s,CAPS,'active')
    _,target=activation.image_patch(d)
    deps=health()
    obs.emit(dict(image_switch='PREFLIGHT',phase='CURRENT_56_POOLS_STORAGE_AND_MODEL_DEFAULTS',production_workload_writes=0))
    plan=dict(original=d['spec'],candidate=target,old_identity=obs.identity(pod),old_pod_uid=old.POD_UID,
        old_binary=e.BINARY,candidate_binary=digest,process=pr,server_ids=s['server_identity_hashes'],
        placement=placement(d,pod),model_state=model_state(pod),other_workloads=deps['other_workloads'],
        operator_supplied_rollback_pin_receipt_sha256=PIN_SHA)
    check_plan_shape(plan)
    fresh(plan)
    projected=patch(d,plan,dry=True)
    need(projected['spec']==target,'admission_changed_image_only_patch')
    save('PLAN.private.json',plan)
    save('PREPARED.json',dict(plan_sha256=recovery.read(ROOT/'PLAN.private.json')[1]))
    report['rollback_node_metadata_rechecked']=True
    report['preflight']='PASS'
    return plan


def read_plan():
    plan,digest=recovery.read(ROOT/'PLAN.private.json')
    need(read('PREPARED.json')['plan_sha256']==digest,'plan_receipt_changed')
    check_plan_shape(plan)
    return plan


def patch(dep,plan,dry):
    ops,target=activation.image_patch(dep)
    need(target==plan['candidate'],'patch_target_changed')
    return json.loads(a.native.call(['patch','deployment','weknora-app','-n','deer-flow','--type=json',
        '--patch-file=/dev/stdin','-o','json']+(['--dry-run=server'] if dry else []),
        data=json.dumps(ops).encode(),timeout=40,limit=1048576)[1])


def signal_once(plan,report):
    need(not (ROOT/'SIGNAL-REQUESTED.json').exists(),'signal_already_requested_NO_REPEAT')
    report['phase']='FINAL_PREFLIGHT_BEFORE_STOP_CLAIMING'
    dep,pod,s=fresh(plan)
    need(patch(dep,plan,dry=True)['spec']==plan['candidate'],'final_dryrun_changed')
    pr=binary(pod,plan['old_binary'])
    need(pr==plan['process'],'original_process_changed')
    # The journal precedes the single signal; continuation cannot send another.
    save('SIGNAL-REQUESTED.json',dict(pod_identity=plan['old_identity'],start_ticks=pr['start_ticks']))
    report['signal_attempted']=True; report['phase']='STOP_CLAIMING_ONCE'
    obs.emit(dict(image_switch='STOP_CLAIMING_ONCE',queue_empty_required=False,pending_queue_preserved=True))
    out=obs.call(['exec',pod['metadata']['name'],'-c','app','--','gosu','1000:1000','python3','-I','-B',
        '-c',e.SIGNAL_CODE,json.dumps({'start_ticks':pr['start_ticks'],'binary_sha256':plan['old_binary']})],timeout=40,limit=4096)
    ack=json.loads(out)
    need(ack=={'tstp_sent_once':True,'pid':1,'start_ticks':pr['start_ticks']},'signal_response_uncertain_USE_STATUS')
    save('SIGNAL-ACK.json',ack)


def wait_drain(plan,report):
    start=time.monotonic(); stable=0; samples=[]
    while time.monotonic()-start<1800:
        pod=e.app_for(e.deployment(),plan['original'])
        # JSON journals turn tuples into lists; compare canonical JSON without
        # weakening the actual UID/containerID/restart identity.
        need(same(obs.identity(pod),plan['old_identity']),'original_process_changed')
        s=e.snapshot(e.redis_binding(pod)); e.check_capacities(s,CAPS)
        need(s['server_identity_hashes']==plan['server_ids'],'consumer_identity_changed')
        states={x['pool']:x['status'] for x in s['effective_servers']}
        stable=stable+1 if e.drain_complete(s) else 0
        row=dict(utc=obs.utc(),pool_states=states,active_handlers=sum(x['active'] for x in s['effective_servers']),
            redis_active=sum(q['active'] for q in s['queues'].values()),
            pending_retained=sum(q['pending'] for q in s['queues'].values()),stable_zero_samples=stable,
            elapsed_seconds=round(time.monotonic()-start))
        samples.append(row); samples=samples[-3:]
        save('drain-'+uuid.uuid4().hex+'.json',row); obs.emit(dict(weknora_drain='RUNNING',**row))
        if stable>=3:
            save('DRAIN-VERIFIED-'+uuid.uuid4().hex+'.json',dict(samples=samples,six_stopped_pools=True))
            report['live_stop_behavior']='SIX_POOLS_STOPPED_AND_ZERO_ACTIVE_CONFIRMED'
            return
        need(time.monotonic()-start<90 or all(v=='stopped' for v in states.values()),
             'not_all_pools_stopped_NO_FORCE_TERMINATION')
        time.sleep(10)
    need(False,'claimed_work_deadline_USE_STATUS_NO_FORCE_TERMINATION')


def apply_after_drain(plan,report):
    need(not (ROOT/'PATCH-REQUESTED.json').exists(),'patch_already_requested_NO_REPEAT')
    report['phase']='WAIT_CLAIMED_WORK_ONLY'
    wait_drain(plan,report)
    report['phase']='FINAL_READONLY_CHECK_BEFORE_IMAGE_CAS'
    dep,pod,s=fresh(plan,stopped=True)
    ops,_=activation.image_patch(dep)
    save('PATCH.private.json',ops)
    save('PATCH-REQUESTED.json',dict(target_sha256=obs.digest(plan['candidate']),utc=obs.utc()))
    report['live_patch_attempted']=True; report['phase']='CAS_IMAGE_ONLY'
    obs.emit(dict(image_switch='CAS_IMAGE_ONLY',single_replica_api_interruption_expected=True,
        worker_slots=56,model_changed=False,queue_empty_required=False))
    reply=patch(dep,plan,dry=False)
    need(reply['metadata']['uid']==e.process.DEP_UID and reply['spec']==plan['candidate'],
         'image_patch_ack_uncertain_USE_STATUS')
    save('PATCH-ACK.json',dict(generation=reply['metadata']['generation'],uid=reply['metadata']['uid']))
    report['live_patch_acknowledged']=True


def validate_new_pod(dep,plan):
    need(dep['spec']==plan['candidate'],'live_spec_drift')
    pod=e.app_for(dep,plan['candidate'])
    need(pod['metadata']['uid']!=plan['old_pod_uid'],'original_pod_still_serving')
    statuses=pod.get('status',{}).get('containerStatuses',[])
    need(len(statuses)==1 and statuses[0].get('name')=='app'
         and statuses[0].get('imageID')==image.CONFIG and statuses[0].get('restartCount')==0,
         'candidate_image_or_restart_changed')
    st=dep.get('status',{})
    need(st.get('observedGeneration',0)>=dep['metadata']['generation'] and
         all(st.get(k)==1 for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')),
         'controller_not_converged')
    return pod


def capture_startup(pod):
    previous=sorted(ROOT.glob('MODEL-STARTUP-*.json'))
    need(len(previous)<=100,'too_many_startup_records')
    for path in previous:
        proof=read(path.name)
        if same(proof.get('pod_identity'),obs.identity(pod)):
            need(proof.get('limit')==64 and proof.get('distributed_via_redis') is True
                 and proof.get('raw_logs_saved') is False,'startup_receipt_changed')
            return
    e.capture_startup_limit(ROOT,pod,64)


def accept(plan,report):
    report['phase']='WAIT_CANDIDATE_STARTUP_AND_56_ACTIVE_SLOTS'
    start=time.monotonic(); stable=0; identity=None; last=None
    transient={'one_ready_app_required','app_terminating','original_pod_still_serving','controller_not_converged',
        'incomplete_queue_snapshot','six_pool_process_required','six_unique_server_ids_required',
        'pool_state_mismatch','other_active_asynq_host','model_governor_startup_not_confirmed'}
    while time.monotonic()-start<600:
        try:
            dep=e.deployment(); pod=validate_new_pod(dep,plan)
            current=obs.identity(pod)
            if not same(current,identity): identity=current; stable=0
            capture_startup(pod)
            snap=e.snapshot(e.redis_binding(pod)); e.check_capacities(snap,CAPS,'active')
            stable+=1
            if stable>=3: break
        except Exception as exc:
            last=str(exc); stable=0
            if last not in transient: raise
        obs.emit(dict(image_rollout='RUNNING',elapsed_seconds=round(time.monotonic()-start),
                      stable_samples=stable,last_check=last))
        time.sleep(10)
    need(stable>=3,'rollout_deadline_USE_STATUS_NO_AUTOMATIC_ROLLBACK')
    binary(pod,plan['candidate_binary']); e.runtime_env(pod,e.CURRENT_ENV)
    db=e.database(pod); e.resources(pod,db,expanding=False)
    need(placement(dep,pod,candidate=True)==plan['placement'],'placement_or_storage_changed')
    configs(pod); need(model_state(pod)==plan['model_state'],'model_or_schema_changed_after_startup')
    policy_check(db_sql(pod,activation.POLICY_SQL)['policies'])
    deps=health(); need(same(deps['other_workloads'],plan['other_workloads']),'other_workload_changed')
    # Service must route exclusively to this exact new Pod, not just report a
    # healthy Deployment. No document/model request is used for this check.
    endpoints=a.native.get('endpoints','weknora-app','deer-flow'); addresses=[]
    for s in endpoints.get('subsets',[]):
        need(not s.get('notReadyAddresses'),'unready_app_endpoint'); addresses+=s.get('addresses',[])
    need(addresses and all(x.get('targetRef',{}).get('uid')==pod['metadata']['uid'] for x in addresses),
         'app_service_not_exact_candidate')
    need(same(obs.identity(validate_new_pod(e.deployment(),plan)),identity),'candidate_process_changed')
    result=dict(image_switch='PASS',image=image.IMAGE,pod=pod['metadata']['name'],pod_uid=pod['metadata']['uid'],
        effective_worker_slots=56,effective_pools=CAPS,model_governor_startup_limit=64,
        pending_queue_preserved=True,model_configuration='UNCHANGED_QWEN_STILL_ACTIVE',
        production_schema_version=90,database_migration_requested=False,
        mineru_health=deps['mineru'],glm_activation='PENDING_SEPARATE_CONTROLLED_STEP',
        release_acceptance='PENDING_MODEL_ACTIVATION_AND_USER_VALIDATION')
    if not (ROOT/'SUCCESS.json').exists(): save('SUCCESS.json',result)
    report.update(result)


def advance(plan,report,applying):
    if (ROOT/'PATCH-REQUESTED.json').exists():
        need(e.deployment()['spec']==plan['candidate'],'uncertain_patch_NOT_REPLAYED_USE_STATUS')
    else:
        if applying: signal_once(plan,report)
        else:
            sig=read('SIGNAL-REQUESTED.json')
            need(same(sig['pod_identity'],plan['old_identity'])
                 and sig['start_ticks']==plan['process']['start_ticks'],'signalled_identity_changed')
        apply_after_drain(plan,report)
    accept(plan,report)


def status():
    out=dict(image_switch_status='READ_ONLY',production_writes=0,signals_sent=0,files_modified=False,state=str(ROOT))
    out['records']={n:(ROOT/n).exists() for n in ('PREPARED.json','SIGNAL-REQUESTED.json','SIGNAL-ACK.json',
        'PATCH-REQUESTED.json','PATCH-ACK.json','SUCCESS.json')}
    if not ROOT.exists(): obs.emit(out); return 0
    old.safe_dir(ROOT)
    if out['records']['PREPARED.json']:
        plan=read_plan(); d=e.deployment()
        out['live_configuration']='candidate' if d['spec']==plan['candidate'] else (
            'original' if d['spec']==plan['original'] else 'DRIFT_REVIEW_REQUIRED')
        pods=obs.owned(d)
        out['pods']=[dict(name=p['metadata']['name'],uid=p['metadata']['uid'],ready=obs.ready(p),
                         phase=p.get('status',{}).get('phase')) for p in pods]
        if len(pods)==1 and obs.ready(pods[0]):
            try:
                s=e.snapshot(e.redis_binding(pods[0])); out['pools']=s['effective_servers']
                out['claimed_work_finished']=e.drain_complete(s)
            except Exception as exc: out['snapshot_check']=recovery.safe_error(exc)
    obs.emit(out); return 0


def main(argv=None):
    parser=argparse.ArgumentParser(); g=parser.add_mutually_exclusive_group(required=True)
    for mode in ('apply-once','status','reconcile'): g.add_argument('--'+mode,action='store_true')
    args=parser.parse_args(argv)
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    os.umask(0o077)
    import resource,fcntl
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    initialize()
    if args.status: return status()
    def interrupt(sig,frame): raise old.CheckFailure('interrupted_USE_STATUS')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupt)
    descriptors=[]
    try:
        # Exclude the obsolete88 mutator and56 recovery paths; do not change or
        # unlock their journals. This new state never calls their main/mutate.
        fd=frozen_consumers.open_safe(e.ROOT/'controller.lock'); descriptors.append(fd)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        descriptors.append(e.acquire_previous_lock())
        with recovery.lock(ROOT):
            if args.apply_once:
                need(not os.path.lexists(str(ROOT)),'previous_attempt_USE_STATUS')
                ROOT.mkdir(mode=0o700)
            else: old.safe_dir(ROOT)
            report=dict(image_switch='UNCONFIRMED',phase='READ_ONLY_PREFLIGHT',automatic_retry=False,
                automatic_rollback=False,provider_probe_submissions=0,signal_attempted=False,
                live_patch_attempted=False,live_patch_acknowledged=False,model_changed=False,
                consumer_slots_changed=False,mineru_slots_changed=False,queue_empty_required=False,
                operator_supplied_rollback_pin_receipt_sha256=PIN_SHA,
                rollback_pin_fresh_cri_recheck=False,rollback_node_metadata_rechecked=False,
                database_writes=0,images_imported=0,build_started=False,
                utc_started=obs.utc(),state=str(ROOT))
            code=1
            try:
                plan=prepare(report) if args.apply_once else read_plan()
                advance(plan,report,args.apply_once); code=0
            except BaseException as exc:
                report.update(error_type=type(exc).__name__,failed_check=recovery.safe_error(exc),raw_error_printed=False,
                    code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                                 for x in traceback.extract_tb(exc.__traceback__)[-8:]])
            report.update(utc_finished=obs.utc(),instruction='USE_STATUS_NO_REPEAT_APPLY_NO_FORCE_TERMINATION',
                signal_request_recorded=(ROOT/'SIGNAL-REQUESTED.json').exists(),
                patch_request_recorded=(ROOT/'PATCH-REQUESTED.json').exists())
            name='RESULT-'+uuid.uuid4().hex+'.json'; save(name,report); obs.emit(report)
            obs.emit(dict(report=str(ROOT/name),report_sha256=recovery.read(ROOT/name)[1]))
            return code
    finally:
        for fd in descriptors: os.close(fd)


if __name__=='__main__': sys.exit(main())
