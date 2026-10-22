"""Receipt-bound GLM activation; bounded reference batches/config-only Recreate.

No image/slot/embedding changes. No automatic signal, SQL or patch replay.
Status is read-only; reconciliation may continue only a verified prior boundary.

Only exact legacy document model IDs are normalized; pending work is not
resubmitted. This source is not a release artifact without packaged qualification.
"""
import argparse
import contextlib
import copy
import json
import os
from pathlib import Path
import signal
import socket
import sys
import time
import traceback
import tempfile
import uuid
import glm_activation_plan as rules
import glm_document_migration as docs

stage=rules.stage
sw=rules.sw
need=rules.need
ROOT=Path('/var/lib/weknora-glm-activate56-20261017-r1')
PHASE='READ_ONLY_PREFLIGHT'
OVERRIDE_SQL="""BEGIN READ ONLY; SET LOCAL statement_timeout='5s';
SELECT json_build_object('legacy_qwen_override',count(*) FILTER
 (WHERE metadata::jsonb#>>'{process_overrides,vlm_config,model_id}'='deerflow-dashscope-vlm'),
 'inline_vlm_override',count(*) FILTER
 (WHERE coalesce(metadata::jsonb#>>'{process_overrides,vlm_config,base_url}','')<>''))
 FROM public.knowledges WHERE deleted_at IS NULL; ROLLBACK;"""
LEGACY_SQL="""BEGIN READ ONLY; SET LOCAL statement_timeout='5s';
SELECT json_build_object('legacy_active',exists(SELECT 1 FROM public.models WHERE id='deerflow-dashscope-vlm'
 AND status='active' AND deleted_at IS NULL),'default_vision_ids',coalesce((SELECT json_agg(id ORDER BY id)
 FROM public.models WHERE type='VLLM' AND is_default AND deleted_at IS NULL),'[]'::json)); ROLLBACK;"""


def save(name,row):
    # Atomic, no-replace publication. Interruptions cannot expose half JSON.
    sw.old.safe_dir(ROOT)
    need(Path(name).name==name and name not in ('.','..'),'unsafe_journal_name')
    raw=(json.dumps(row,sort_keys=True,indent=2)+'\n').encode()
    fd,tmp=tempfile.mkstemp(prefix='.receipt-',dir=str(ROOT))
    try:
        with os.fdopen(fd,'wb') as out:
            os.fchmod(out.fileno(),0o600); out.write(raw); out.flush(); os.fsync(out.fileno())
        os.link(tmp,str(ROOT/name))
    finally:
        os.unlink(tmp)
    directory=os.open(str(ROOT),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: os.fsync(directory)
    finally: os.close(directory)


def read(name): return sw.recovery.read(ROOT/name)[0]


def save_same(name,value):
    if (ROOT/name).exists(): need(sw.same(read(name),value),'preexisting_receipt_changed')
    else: save(name,value)


def sql_write(pod,statement):
    pg,container=sw.release.db_binding(sw.a,pod)
    reply=sw.a.native.call(['exec','-i','-n','deer-flow',pg['metadata']['name'],'-c',container,
        '--','sh','-c',stage.WRITE_SHELL],data=statement.encode(),timeout=30,limit=4096)[1]
    need(sw.a.native.get('pod',pg['metadata']['name'],'deer-flow')['metadata']['uid']==pg['metadata']['uid'],'db_pod_changed')
    return json.loads(reply)


def phase(name):
    global PHASE
    PHASE=name; sw.a.native.PHASE=name
    sw.obs.emit(dict(glm_activation='RUNNING',phase=name,worker_slots=56,mineru_slots_changed=False))


def sql_read(pod,query):
    need(query.startswith('BEGIN READ ONLY;') and query.endswith('ROLLBACK;'),'readonly_query_contract')
    pg,container=sw.release.db_binding(sw.a,pod)
    shell=stage.WRITE_SHELL.replace("PGOPTIONS='-c statement_timeout", "PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout")
    out=sw.a.native.call(['exec','-i','-n','deer-flow',pg['metadata']['name'],'-c',container,
        '--','sh','-c',shell],data=query.encode(),timeout=30,limit=2097152)[1]
    need(sw.a.native.get('pod',pg['metadata']['name'],'deer-flow')['metadata']['uid']==pg['metadata']['uid'],'db_pod_changed')
    return json.loads(out)


def checked_staging():
    receipt,_=sw.recovery.read(stage.ROOT/'RESULT.json',rules.STAGE_SHA)
    need(receipt.get('glm_model_stage')=='PASS' and receipt.get('model_id')==rules.NEW
         and receipt.get('native_encrypted_storage')=='PASS' and receipt.get('native_decrypt_readback')=='PASS'
         and receipt.get('model_status')=='inactive' and receipt.get('is_default') is False,'stage_receipt_not_passed')
    exp,digest=sw.recovery.read(stage.ROOT/'EXPECTED.private.json')
    request,_=sw.recovery.read(stage.ROOT/'INSERT-REQUESTED.json')
    need(request.get('expected_sha256')==digest,'staged_projection_hash')
    return exp


def policies_equal(actual,expected):
    # Exact definitions, trigger identities and safety attributes, excluding no
    # fields. Sorted query order is pinned by the predecessor SQL.
    need(sw.same(actual,expected),'default_policy_drift')


def database_state(pod,plan):
    value=sql_read(pod,stage.READ_SQL)
    need(value.get('schema')==[{'version':90,'dirty':False}] and value.get('read_only')=='on','schema_changed')
    policies=sql_read(pod,rules.POLICY_SQL)['policies']
    catalog=sql_read(pod,docs.CATALOG_SQL)
    old=plan['model']; new=dict(old,status='active',is_default=True)
    legacy=sql_read(pod,LEGACY_SQL)
    if value['model']==old and sw.same(policies,plan['policies_before']) and legacy=={
        'legacy_active':True,'default_vision_ids':[rules.OLD]}:
        need(value['new_references']==0,'inactive_glm_has_references')
        need(catalog==plan['document_catalog_before'],'document_catalog_drift')
        return 'ORIGINAL'
    if value['model']==new and sw.same(policies,plan['policies_after']) and legacy=={
        'legacy_active':True,'default_vision_ids':[rules.NEW]}:
        need(sql_read(pod,rules.REFS_SQL)==[],'legacy_references_reappeared')
        need(catalog==plan['document_catalog_after'],'document_catalog_drift')
        return 'GLM'
    return 'DRIFT_REVIEW_REQUIRED'


def config_object(value,expected,uid=None):
    need(value.get('apiVersion')=='v1' and value.get('kind')=='ConfigMap'
         and value['metadata'].get('name')==rules.CM and value['metadata'].get('namespace')=='deer-flow'
         and value.get('immutable') is True and value.get('data')==expected['data']
         and value.get('binaryData',{})==expected.get('binaryData',{})
         and not value['metadata'].get('deletionTimestamp'),'candidate_config_changed')
    if uid is not None: need(value['metadata']['uid']==uid,'candidate_config_recreated')


def mounted(pod,expected):
    data=json.loads(sw.a.native.call(['exec','-n','deer-flow',pod['metadata']['name'],'-c','app',
        '--','python3','-I','-B','-c',sw.activation.CONFIG_PROBE],limit=8192)[1])
    need(set(data)=={'/app/config/'+x for x in sw.CONFIG_HASHES},'mounted_config_paths')
    for name in sw.CONFIG_HASHES:
        need(data['/app/config/'+name]['sha256']==rules.sha(expected['data'][name]),'mounted_config_digest')


def deployment_patch(dep,plan,dry):
    ops=rules.patch_ops(dep,plan['original'],plan['candidate'])
    result=json.loads(sw.a.native.call(['patch','deployment','weknora-app','-n','deer-flow','--type=json',
        '--patch-file=/dev/stdin','-o','json']+(['--dry-run=server'] if dry else []),
        data=json.dumps(ops).encode(),timeout=40,limit=1048576)[1])
    need(result['metadata']['uid']==sw.e.process.DEP_UID and result['spec']==plan['candidate'],
         'config_cas_ack_uncertain_USE_STATUS')
    return result


def prepare():
    phase('BIND_STAGED_GLM_AND_CURRENT_56')
    exp=checked_staging(); sw.release.accepted(sw.a)
    dep,pod,image_plan=stage.current(); _,bound=stage.baseline(binary=True)
    need(stage.projection(sql_read(pod,stage.READ_SQL))==exp,'staged_model_drift')
    overrides=sql_read(pod,OVERRIDE_SQL)
    need(type(overrides.get('legacy_qwen_override')) is int and 0<=overrides['legacy_qwen_override']<=docs.MAX_DOCUMENTS
         and overrides.get('inline_vlm_override')==0,'document_vlm_overrides_require_review')
    catalog=docs.check_catalog(sql_read(pod,docs.CATALOG_SQL),False)
    need(sql_read(pod,docs.COUNTS_SQL)['glm']==0,'staged_glm_document_references_already_present')
    models=sw.release.clean_models(sql_read(pod,sw.release.SQL))
    need(models['legacy_inline_kbs']==0 and models['unresolved_model_refs']==0,'kb_model_reference_review')
    policies=sql_read(pod,rules.POLICY_SQL)['policies']
    cm=sw.a.native.get('configmap','weknora-config','deer-flow')
    manifest=rules.config_plan(cm)
    snapshot=sw.e.snapshot(sw.e.redis_binding(pod)); sw.e.check_capacities(snapshot,sw.CAPS,'active')
    proc=sw.binary(pod,image_plan['candidate_binary']); deps=sw.health()
    plan=dict(original=dep['spec'],candidate=rules.spec_plan(dep['spec']),old_pod_uid=pod['metadata']['uid'],
        old_identity=sw.obs.identity(pod),old_pod=pod,process=proc,candidate_binary=image_plan['candidate_binary'],
        server_ids=snapshot['server_identity_hashes'],placement=image_plan['placement'],other_workloads=deps['other_workloads'],
        original_cm=cm,configmap=manifest,model=exp,policies_before=policies,policies_after=rules.policy_plan(policies),
        stage_receipt_sha256=rules.STAGE_SHA,document_override_review=overrides,
        document_catalog_before=catalog,document_catalog_after=docs.installed_catalog(catalog))
    phase('SERVER_DRY_RUN_CONFIG_AND_DEPLOYMENT')
    existing=sw.a.native.call(['get','configmap',rules.CM,'-n','deer-flow','--ignore-not-found','-o','json'],limit=1048576)[1]
    need(not existing.strip(),'candidate_config_name_exists_USE_STATUS')
    dry=json.loads(sw.a.native.call(['create','--dry-run=server','-f','-','-o','json'],data=json.dumps(manifest).encode(),limit=1048576)[1])
    config_object(dry,manifest); deployment_patch(dep,plan,True)
    fresh_original(plan,'ORIGINAL','active')
    save('PLAN.private.json',plan); save('PREPARED.json',dict(plan_sha256=sw.recovery.read(ROOT/'PLAN.private.json')[1]))
    return plan


def read_plan():
    plan,digest=sw.recovery.read(ROOT/'PLAN.private.json')
    need(read('PREPARED.json')['plan_sha256']==digest and plan['stage_receipt_sha256']==rules.STAGE_SHA,'plan_hash_changed')
    need(plan['candidate']==rules.spec_plan(plan['original']) and plan['configmap']==rules.config_plan(plan['original_cm'])
        and plan['policies_after']==rules.policy_plan(plan['policies_before']) and plan['model']==checked_staging(),
        'plan_scope_changed')
    need(plan['document_catalog_after']==docs.installed_catalog(plan['document_catalog_before']),'document_plan_changed')
    return plan


def fresh_original(plan,db_state='ORIGINAL',pool_state='stopped'):
    d=sw.e.deployment(); pod=sw.e.app_for(d,plan['original'])
    need(sw.same(sw.obs.identity(pod),plan['old_identity']),'original_process_changed')
    sw.e.runtime_env(pod,sw.e.CURRENT_ENV); mounted(pod,plan['original_cm'])
    # Restart-required database overrides can change without changing live env.
    db=sw.e.database(pod); sw.e.resources(pod,db,expanding=False)
    need(sw.placement(d,pod,candidate=True)==plan['placement'],'storage_or_placement_changed')
    need(database_state(pod,plan)==db_state,'database_transition_not_exact')
    cm=sw.a.native.get('configmap','weknora-config','deer-flow')
    need(cm['metadata']['uid']==plan['original_cm']['metadata']['uid'] and cm.get('data')==plan['original_cm'].get('data')
        and cm.get('binaryData')==plan['original_cm'].get('binaryData'),'original_config_changed')
    snapshot=sw.e.snapshot(sw.e.redis_binding(pod)); sw.e.check_capacities(snapshot,sw.CAPS,pool_state)
    need(snapshot['server_identity_hashes']==plan['server_ids'],'consumer_identity_changed')
    if pool_state=='stopped': need(sw.e.drain_complete(snapshot),'claimed_work_not_finished')
    deps=sw.health(); need(sw.same(deps['other_workloads'],plan['other_workloads']),'dependent_workload_changed')
    return d,pod


def ensure_config(plan,allow_create):
    if (ROOT/'CONFIG-REQUESTED.json').exists():
        value=sw.a.native.get('configmap',rules.CM,'deer-flow'); config_object(value,plan['configmap'])
        if (ROOT/'CONFIG-ACK.json').exists(): config_object(value,plan['configmap'],read('CONFIG-ACK.json')['uid'])
        else:
            # Exact immutable content reconciles a lost create response. Never
            # re-create an absent/unknown object or infer ownership from a name.
            need(value['metadata'].get('labels',{}).get('deerflow-release')=='weknora-glm-20261017-r1',
                 'unacknowledged_config_identity')
            save('CONFIG-ACK.json',dict(uid=value['metadata']['uid'],reconciled_exact_content=True))
        return
    need(allow_create,'config_not_requested_NO_AUTOMATIC_REPLAY')
    save('CONFIG-REQUESTED.json',dict(name=rules.CM,sha256=sw.obs.digest(plan['configmap'])))
    value=json.loads(sw.a.native.call(['create','-f','-','-o','json'],data=json.dumps(plan['configmap']).encode(),limit=1048576)[1])
    config_object(value,plan['configmap']); save('CONFIG-ACK.json',dict(uid=value['metadata']['uid']))


@contextlib.contextmanager
def scoped_journal():
    original=sw.ROOT
    try:
        sw.ROOT=ROOT
        yield
    finally: sw.ROOT=original


def signal_once(plan):
    need(not (ROOT/'SIGNAL-REQUESTED.json').exists(),'signal_not_replayed')
    d,pod=fresh_original(plan,pool_state='active'); deployment_patch(d,plan,True)
    need(sw.binary(pod,plan['candidate_binary'])==plan['process'],'process_before_signal_changed')
    save('SIGNAL-REQUESTED.json',dict(pod_identity=plan['old_identity'],process=plan['process']))
    result=json.loads(sw.obs.call(['exec',pod['metadata']['name'],'-c','app','--','gosu','1000:1000',
        'python3','-I','-B','-c',sw.e.SIGNAL_CODE,json.dumps({'start_ticks':plan['process']['start_ticks'],
        'binary_sha256':plan['candidate_binary']})],timeout=40,limit=4096))
    need(result=={'tstp_sent_once':True,'pid':1,'start_ticks':plan['process']['start_ticks']},'signal_ack_uncertain_USE_STATUS')
    save('SIGNAL-ACK.json',result)


def commit_defaults(plan):
    phase('SHORT_ATOMIC_DEFAULT_AND_REFERENCE_TRANSACTION')
    _,pod=fresh_original(plan)
    counts=sql_read(pod,docs.COUNTS_SQL)
    need(counts['inline']==0 and counts['legacy']<=docs.MAX_DOCUMENTS,'document_scope_changed')
    refs=sql_read(pod,rules.REFS_SQL); rules.ref_target(refs)
    save_same('REFERENCES-BEFORE.private.json',refs)
    statement=rules.activation_sql(plan['policies_before'],refs,plan['model'],plan['document_catalog_before'])
    save('DB-REQUESTED.json',dict(sql_sha256=rules.sha(statement),references_sha256=sw.recovery.read(ROOT/'REFERENCES-BEFORE.private.json')[1]))
    ack=sql_write(pod,statement)
    need(ack=={'glm_default_commit':True},'database_ack_uncertain_USE_STATUS')
    save('DB-ACK.json',ack)
    need(database_state(pod,plan)=='GLM','database_commit_not_verified')


def migrate_documents(plan,report,applying):
    phase('MIGRATE_LEGACY_DOCUMENT_MODEL_IDS_IN_500_ROW_TRANSACTIONS')
    _,pod=fresh_original(plan,db_state='GLM')
    total=0; batches=0
    for index in range(1,docs.MAX_DOCUMENTS//docs.BATCH_SIZE+2):
        prefix='DOC-'+str(index).zfill(4)
        saved=ROOT/(prefix+'-PLAN.private.json')
        if saved.exists(): batch=read(saved.name); docs.validate_batch(batch)
        else:
            batch=sql_read(pod,docs.NEXT_SQL)
            if not batch: break
            docs.validate_batch(batch)
            need(total+len(batch)<=docs.MAX_DOCUMENTS,'document_migration_limit')
            save(saved.name,batch)
        statement=docs.batch_sql(batch,plan['document_catalog_after'])
        binding=dict(rows=len(batch),plan_sha256=sw.recovery.read(saved)[1],sql_sha256=rules.sha(statement))
        ackname=prefix+'-ACK.json'; requested=prefix+'-REQUESTED.json'
        if (ROOT/ackname).exists():
            need(read(ackname).get('binding')==binding,'document_batch_receipt_drift')
        else:
            state=docs.classify_state(sql_read(pod,docs.state_sql(batch)),batch)
            if (ROOT/requested).exists():
                need(read(requested)==binding,'document_request_binding')
                # Never repeat an uncertain mutation in the apply call. A user-
                # invoked reconcile may retry once after exact BEFORE proof.
                if state=='BEFORE':
                    need(not applying and not (ROOT/(prefix+'-MANUAL-RETRY.json')).exists(),
                         'document_batch_uncertain_USE_STATUS')
                    save(prefix+'-MANUAL-RETRY.json',dict(binding=binding,verified_state='BEFORE'))
                    ack=sql_write(pod,statement)
                    need(ack=={'document_batch_committed':len(batch)},'document_batch_ack_uncertain')
            else:
                need(state=='BEFORE','unrequested_document_batch_changed')
                save(requested,binding)
                ack=sql_write(pod,statement)
                need(ack=={'document_batch_committed':len(batch)},'document_batch_ack_uncertain')
            need(docs.classify_state(sql_read(pod,docs.state_sql(batch)),batch)=='AFTER','document_batch_not_verified')
            save(ackname,dict(binding=binding,verified_state='AFTER'))
        total+=len(batch); batches+=1
        report.update(document_rows_migrated=total,document_batches=batches)
        sw.obs.emit(dict(document_reference_migration='RUNNING',rows_verified=total,batches=batches,max_batch=docs.BATCH_SIZE))
        if index%10==0: _,pod=fresh_original(plan,db_state='GLM')
    else: raise RuntimeError('document_batch_loop_limit')
    counts=sql_read(pod,docs.COUNTS_SQL)
    need(counts['legacy']==0 and counts['inline']==0,'legacy_or_inline_documents_remain')
    save_same('DOCUMENTS-COMPLETE.json',dict(rows_migrated=total,batches=batches,legacy_remaining=0,inline_remaining=0))
    report.update(document_rows_migrated=total,document_batches=batches,document_write_guard='PASS')


def accept(plan,report):
    phase('WAIT_SAME_IMAGE_NEW_CONFIG_AND_56_CONSUMERS')
    start=time.monotonic(); stable=0; identity=None
    transient={'one_ready_app_required','app_terminating','original_pod_still_serving','controller_not_converged',
        'incomplete_queue_snapshot','six_pool_process_required','six_unique_server_ids_required',
        'pool_state_mismatch','other_active_asynq_host','model_governor_startup_not_confirmed'}
    while time.monotonic()-start<600:
        try:
            d=sw.e.deployment(); pod=sw.validate_new_pod(d,plan)
            current=sw.obs.identity(pod)
            if not sw.same(identity,current): stable=0; identity=current
            with scoped_journal(): sw.capture_startup(pod)
            s=sw.e.snapshot(sw.e.redis_binding(pod)); sw.e.check_capacities(s,sw.CAPS,'active')
            stable+=1
            if stable>=3: break
        except Exception as exc:
            stable=0
            if str(exc) not in transient: raise
        sw.obs.emit(dict(glm_rollout='WAITING',elapsed_seconds=round(time.monotonic()-start),stable_samples=stable))
        time.sleep(10)
    need(stable>=3,'rollout_deadline_USE_STATUS')
    sw.binary(pod,plan['candidate_binary']); sw.e.runtime_env(pod,sw.e.CURRENT_ENV)
    need(sw.placement(d,pod,candidate=True)==plan['placement'],'placement_changed')
    db=sw.e.database(pod); sw.e.resources(pod,db,expanding=False)
    mounted(pod,plan['configmap'])
    value=sw.a.native.get('configmap',rules.CM,'deer-flow'); config_object(value,plan['configmap'],read('CONFIG-ACK.json')['uid'])
    need(database_state(pod,plan)=='GLM','post_restart_model_state')
    counts=sql_read(pod,docs.COUNTS_SQL)
    need(counts['legacy']==0 and counts['inline']==0,'post_restart_legacy_or_inline_documents')
    deps=sw.health(); need(sw.same(deps['other_workloads'],plan['other_workloads']),'dependency_changed')
    eps=sw.a.native.get('endpoints','weknora-app','deer-flow'); addresses=[]
    for sub in eps.get('subsets',[]):
        need(not sub.get('notReadyAddresses'),'unready_endpoint'); addresses+=sub.get('addresses',[])
    need(addresses and all(x.get('targetRef',{}).get('uid')==pod['metadata']['uid'] for x in addresses),'endpoint_not_exact_candidate')
    need(sw.same(sw.obs.identity(sw.validate_new_pod(sw.e.deployment(),plan)),identity),'candidate_process_changed')
    report.update(glm_activation='PASS',model_id=rules.NEW,model_status='active',is_default=True,
        effective_worker_slots=56,effective_pools=sw.CAPS,model_governor_startup_limit=64,
        legacy_qwen='RETAINED_ACTIVE_NONDEFAULT',pod=pod['metadata']['name'],pod_uid=pod['metadata']['uid'],
        schema_version=90,pending_queue_preserved=True,production_business_acceptance='PENDING_USER_VALIDATION',
        native_enrichment_prior_acceptance_sha256=sw.release.PASS_SHA,ready_for_88=False)
    report.update(document_rows_migrated=read('DOCUMENTS-COMPLETE.json')['rows_migrated'],
        document_batches=read('DOCUMENTS-COMPLETE.json')['batches'],document_write_guard='PASS',legacy_document_overrides=0)
    if not (ROOT/'SUCCESS.json').exists(): save('SUCCESS.json',report)


def advance(plan,report,applying):
    ensure_config(plan,applying)
    if (ROOT/'PATCH-REQUESTED.json').exists():
        need(sw.e.deployment()['spec']==plan['candidate'],'uncertain_patch_NOT_REPLAYED')
    else:
        if not (ROOT/'SIGNAL-REQUESTED.json').exists():
            need(applying,'signal_not_requested_NO_REPLAY'); phase('STOP_CLAIMING_ONCE'); signal_once(plan)
        else:
            need(read('SIGNAL-REQUESTED.json')=={'pod_identity':plan['old_identity'],'process':plan['process']},'signal_binding_changed')
        phase('WAIT_CLAIMED_WORK_PENDING_QUEUE_PRESERVED')
        with scoped_journal(): sw.wait_drain(plan,report)
        if not (ROOT/'DB-REQUESTED.json').exists(): commit_defaults(plan)
        else:
            need(database_state(plan['old_pod'],plan)=='GLM','uncertain_database_transaction_NOT_REPLAYED')
        migrate_documents(plan,report,applying)
        d,_=fresh_original(plan,db_state='GLM')
        config_object(sw.a.native.get('configmap',rules.CM,'deer-flow'),plan['configmap'],read('CONFIG-ACK.json')['uid'])
        deployment_patch(d,plan,True)
        phase('CAS_CONFIG_ONLY_RECREATE_API_BRIEFLY_UNAVAILABLE')
        save('PATCH-REQUESTED.json',dict(candidate_sha256=sw.obs.digest(plan['candidate'])))
        reply=deployment_patch(d,plan,False)
        save('PATCH-ACK.json',dict(uid=reply['metadata']['uid'],generation=reply['metadata']['generation']))
    accept(plan,report)


def status():
    out=dict(glm_activation_status='READ_ONLY',files_modified=False,production_writes=0,signals_sent=0,provider_requests=0,state=str(ROOT))
    if ROOT.exists():
        sw.old.safe_dir(ROOT)
        out['records']={name:(ROOT/(name+'.json')).exists() for name in
            ('PREPARED','CONFIG-REQUESTED','CONFIG-ACK','SIGNAL-REQUESTED','SIGNAL-ACK','DB-REQUESTED','DB-ACK','PATCH-REQUESTED','PATCH-ACK','SUCCESS')}
        if out['records']['PREPARED']:
            plan=read_plan(); dep=sw.e.deployment()
            out['configuration']='GLM' if dep['spec']==plan['candidate'] else ('ORIGINAL' if dep['spec']==plan['original'] else 'DRIFT')
            out['database']=database_state(plan['old_pod'],plan)
            out['document_counts']=sql_read(plan['old_pod'],docs.COUNTS_SQL)
            out['document_batches_acknowledged']=len(list(ROOT.glob('DOC-*-ACK.json')))
            pods=sw.obs.owned(dep)
            out['pods']=[dict(name=p['metadata']['name'],uid=p['metadata']['uid'],ready=sw.obs.ready(p),phase=p.get('status',{}).get('phase')) for p in pods]
            if len(pods)==1 and sw.obs.ready(pods[0]):
                s=sw.e.snapshot(sw.e.redis_binding(pods[0])); out['pools']=s['effective_servers']
                out['claimed_work_finished']=sw.e.drain_complete(s)
    sw.obs.emit(out); return 0


def main(argv=None):
    parser=argparse.ArgumentParser(); g=parser.add_mutually_exclusive_group(required=True)
    for name in ('apply-once','status','reconcile'): g.add_argument('--'+name,action='store_true')
    args=parser.parse_args(argv)
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    import fcntl,resource
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0)); sw.initialize()
    if args.status: return status()
    def interrupted(sig,frame): raise RuntimeError('interrupted_USE_STATUS')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupted)
    descriptors=[]
    try:
        fd=sw.frozen_consumers.open_safe(sw.e.ROOT/'controller.lock'); descriptors.append(fd)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); descriptors.append(sw.e.acquire_previous_lock())
        with sw.recovery.lock(sw.ROOT),sw.recovery.lock(stage.ROOT),sw.recovery.lock(ROOT):
            if args.apply_once:
                need(not os.path.lexists(str(ROOT)),'previous_attempt_USE_STATUS'); ROOT.mkdir(mode=0o700)
                save('STARTED.json',dict(stage_receipt_sha256=rules.STAGE_SHA))
            else: sw.old.safe_dir(ROOT)
            report=dict(glm_activation='UNCONFIRMED',state=str(ROOT),consumer_slots_changed=False,mineru_slots_changed=False,
                image_changed=False,embedding_changed=False,database_migration_requested=False,provider_probe_requests=0,
                credentials_requested=False,credentials_exported=False,queue_empty_required=False,automatic_retry=False,
                automatic_rollback=False,build_started=False,utc_started=sw.obs.utc())
            code=1
            try:
                plan=prepare() if args.apply_once else read_plan(); advance(plan,report,args.apply_once); code=0
            except BaseException as exc:
                report.update(failed_stage=PHASE,failed_check=sw.recovery.safe_error(exc),error_type=type(exc).__name__,raw_error_printed=False,
                    code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno) for x in traceback.extract_tb(exc.__traceback__)[-8:]])
            report.update(utc_finished=sw.obs.utc(),instruction='USE_STATUS_NO_REPEAT_APPLY_NO_FORCE_TERMINATION_NO_IMAGE_ONLY_ROLLBACK',
                signal_attempted=(ROOT/'SIGNAL-REQUESTED.json').exists(),database_write_attempted=(ROOT/'DB-REQUESTED.json').exists(),
                live_patch_attempted=(ROOT/'PATCH-REQUESTED.json').exists(),live_patch_acknowledged=(ROOT/'PATCH-ACK.json').exists())
            name='RESULT-'+uuid.uuid4().hex+'.json'; save(name,report); sw.obs.emit(report)
            sw.obs.emit(dict(report=str(ROOT/name),report_sha256=sw.recovery.read(ROOT/name)[1]))
            return code
    finally:
        for fd in descriptors: os.close(fd)


if __name__=='__main__': sys.exit(main())
