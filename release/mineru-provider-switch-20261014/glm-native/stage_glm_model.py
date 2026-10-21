"""Stage exactly one encrypted, inactive, nondefault GLM model. No route switch.

No migration, policy/config/reference update, restart, signal or provider call.
An uncertain INSERT is reconciled read-only; never repeated automatically.
"""
import argparse
import copy
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import traceback
import warnings

import switch_image56 as sw
import native_credential_transport as crypto

ROOT=Path('/var/lib/weknora-glm-model-stage-20261017-r1')
SWITCH_REPORT=sw.ROOT/'RESULT-d470edf484194c90b5b707e7eb8c22f7.json'
SWITCH_SHA='a696583a3ef24f896c33f8d9b7450b0203d57f7e6941cc16bc1d869da53822b7'
POD='weknora-app-5d7f445654-hsklf'
POD_UID='4968798e-aceb-4ab5-a00c-df5dee478d04'
MODEL=sw.activation.PROPOSED
PARAMETERS=dict(base_url='https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1',
    provider='openai',interface_type='openai',supports_vision=True,
    extra_config={'wire_profile':'enterprise_glm_single_image_sse_v1'},max_concurrency=0)
DESCRIPTION='Operator staged GLM native SSE model; activation pending (20261017-r1)'
need=sw.need
PHASE='READ_ONLY_PREFLIGHT'

READ_SQL="""BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'schema',(SELECT json_agg(json_build_object('version',version,'dirty',dirty)) FROM public.schema_migrations),
 'model',(SELECT json_build_object('id',id,'tenant_id',tenant_id,'name',name,'display_name',display_name,
  'type',type,'source',source,'description',description,'parameters',parameters::jsonb-'api_key',
  'is_default',is_default,'is_builtin',is_builtin,'managed_by',managed_by,'status',status,
  'deleted_at',deleted_at,'cipher_sha256',encode(sha256(convert_to(coalesce(parameters->>'api_key',''),'UTF8')),'hex'),
  'native_encrypted',(coalesce(parameters->>'api_key','') ~ '^enc:v1:[A-Za-z0-9_-]+$'
   AND length(parameters->>'api_key') BETWEEN 46 AND 5507))
  FROM public.models WHERE id='deerflow-cnooc-glm-5.3-flash'),
 'new_references',
  (SELECT count(*) FROM public.knowledge_bases WHERE deleted_at IS NULL AND
   (vlm_config->>'model_id'='deerflow-cnooc-glm-5.3-flash' OR image_processing_config->>'model_id'='deerflow-cnooc-glm-5.3-flash'))+
  (SELECT count(*) FROM public.custom_agents WHERE deleted_at IS NULL AND config->>'vlm_model_id'='deerflow-cnooc-glm-5.3-flash'));
ROLLBACK;"""

READ_CIPHER_SQL="""BEGIN READ ONLY; SET LOCAL statement_timeout='5s';
SELECT json_build_object('ciphertext',parameters->>'api_key') FROM public.models
WHERE id='deerflow-cnooc-glm-5.3-flash'; ROLLBACK;"""

# Input SQL is on stdin; DB credentials remain in the existing PG process env.
# No plaintext GLM credential enters SQL. Server logs, if configured by the
# operator, can at most contain the native encrypted ciphertext.
WRITE_SHELL="""set -eu
test -n "${POSTGRES_PASSWORD:-}" && test -n "${POSTGRES_USER:-}" && test -n "${POSTGRES_DB:-}"
export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=5
export PGOPTIONS='-c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000'
exec psql -X -qAt --no-password -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"
"""


def save(name,value):
    sw.activation.ROOT=ROOT; sw.activation.save(name,value)


def read(name): return sw.recovery.read(ROOT/name)[0]


def phase(value):
    global PHASE
    PHASE=value
    sw.a.native.PHASE=value
    sw.obs.emit(dict(glm_model_stage='RUNNING',phase=value,production_route_changed=False))


def expected(cipher):
    need(crypto.valid_cipher(cipher),'ciphertext_format')
    return dict(id=MODEL,tenant_id=10000,name='GLM-5.3-Flash',display_name='GLM-5.3-Flash',
        type='VLLM',source='remote',description=DESCRIPTION,parameters=copy.deepcopy(PARAMETERS),
        is_default=False,is_builtin=True,managed_by='',status='inactive',deleted_at=None,
        cipher_sha256=hashlib.sha256(cipher.encode()).hexdigest(),native_encrypted=True)


def projection(row):
    need(row.get('read_only')=='on' and row.get('schema')==[{'version':90,'dirty':False}], 'schema_changed')
    need(type(row.get('new_references')) is int and row['new_references']==0,'glm_references_already_active')
    value=row.get('model')
    if value is not None:
        need(isinstance(value,dict) and set(value)==set(expected('enc:v1:'+('A'*39))), 'model_projection_shape')
    return value


def current():
    receipt,_=sw.recovery.read(SWITCH_REPORT,SWITCH_SHA)
    need(receipt.get('image_switch')=='PASS' and receipt.get('effective_worker_slots')==56
        and receipt.get('model_configuration')=='UNCHANGED_QWEN_STILL_ACTIVE'
        and receipt.get('pod_uid')==POD_UID,'completed_switch_receipt')
    plan=sw.read_plan()
    dep=sw.e.deployment(); pod=sw.validate_new_pod(dep,plan)
    need(pod['metadata']['name']==POD and pod['metadata']['uid']==POD_UID,'reviewed_current_pod_changed')
    return dep,pod,plan


def baseline(binary=False):
    dep,pod,plan=current()
    sw.e.runtime_env(pod,sw.e.CURRENT_ENV); sw.configs(pod)
    if binary: sw.binary(pod,plan['candidate_binary'])
    need(sw.placement(dep,pod,candidate=True)==plan['placement'],'placement_changed')
    need(sw.model_state(pod)==plan['model_state'],'original_model_state_changed')
    policies=sw.db_sql(pod,sw.activation.POLICY_SQL)
    sw.policy_check(policies['policies'])
    need(policies['proposed_id_rows'] in (0,1),'model_count')
    snap=sw.e.snapshot(sw.e.redis_binding(pod)); sw.e.check_capacities(snap,sw.CAPS,'active')
    db=sw.e.database(pod); sw.e.resources(pod,db,expanding=False)
    return pod,dict(deployment_spec_sha256=sw.obs.digest(dep['spec']),pod_identity=sw.obs.identity(pod),
        model_state=plan['model_state'],policy_hashes=sw.policy_check(policies['policies']),
        server_ids=snap['server_identity_hashes'],mineru_health=sw.health()['mineru'])


def sql_literal(value):
    need(isinstance(value,str) and '\x00' not in value,'sql_value')
    return "'"+value.replace("'","''")+"'"


def insert_sql(cipher):
    exp=expected(cipher)
    params=json.dumps(dict(PARAMETERS,api_key=cipher),sort_keys=True,separators=(',',':'))
    hashes=','.join(sql_literal(v) for v in sorted(sw.POLICIES))
    # Triggers run normally; verify their reviewed definitions within the same
    # short transaction and require the exact inserted values before COMMIT.
    return """BEGIN;
SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; SET LOCAL idle_in_transaction_session_timeout='10s';
SET LOCAL standard_conforming_strings=on;
DO $stage$
BEGIN
 IF NOT pg_try_advisory_xact_lock(20261017,5601) THEN RAISE EXCEPTION 'release_busy'; END IF;
 IF (SELECT count(*) FROM public.schema_migrations)<>1 OR NOT EXISTS
   (SELECT 1 FROM public.schema_migrations WHERE version=90 AND dirty=false) THEN RAISE EXCEPTION 'schema_changed'; END IF;
 IF NOT EXISTS (SELECT 1 FROM public.tenants WHERE id=10000) THEN RAISE EXCEPTION 'tenant_missing'; END IF;
 IF (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN ('models','knowledge_bases','custom_agents') AND NOT t.tgisinternal)<>5
 OR (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='public' AND c.relname IN ('models','knowledge_bases','custom_agents') AND NOT t.tgisinternal
   AND t.tgenabled='O' AND encode(sha256(convert_to(pg_get_functiondef(t.tgfoid),'UTF8')),'hex') IN (%s))<>5
 THEN RAISE EXCEPTION 'policies_changed'; END IF;
 IF EXISTS (SELECT 1 FROM public.models WHERE id=%s) THEN RAISE EXCEPTION 'model_already_exists'; END IF;
 INSERT INTO public.models (id,tenant_id,name,display_name,type,source,description,parameters,
   is_default,is_builtin,managed_by,status,deleted_at)
 VALUES (%s,10000,'GLM-5.3-Flash','GLM-5.3-Flash','VLLM','remote',%s,%s::jsonb,false,true,'','inactive',NULL);
 IF NOT EXISTS (SELECT 1 FROM public.models WHERE id=%s AND tenant_id=10000 AND name='GLM-5.3-Flash'
   AND display_name='GLM-5.3-Flash' AND type='VLLM' AND source='remote' AND description=%s
   AND parameters::jsonb=%s::jsonb AND NOT is_default AND is_builtin AND managed_by='' AND status='inactive' AND deleted_at IS NULL)
 THEN RAISE EXCEPTION 'inserted_model_changed_by_policy'; END IF;
END
$stage$;
COMMIT;
SELECT json_build_object('model_insert_committed',true);
""" % (hashes,sql_literal(MODEL),sql_literal(MODEL),sql_literal(DESCRIPTION),sql_literal(params),
        sql_literal(MODEL),sql_literal(DESCRIPTION),sql_literal(params))


def once(report):
    phase('BIND_IMAGE56_AND_CURRENT_MODEL_DEFAULTS')
    sw.release.accepted(sw.a)
    pod,before=baseline(binary=True)
    need(projection(sw.db_sql(pod,READ_SQL)) is None,'model_already_exists_USE_STATUS')
    save('BASELINE.private.json',before)
    phase('NATIVE_CRYPTO_SELFTEST_NO_MODEL_REQUEST')
    crypto.run(sw.a.native,pod,{'mode':'self-test'})
    save('CRYPTO-SELFTEST.json',dict(native_roundtrip='PASS',helper_sha256=crypto.HELPER_SHA,
        process_uid=1000,helper_storage='PRIVATE_TEMP_EXECUTABLE_CLEANED',credential_files_created=0,provider_requests=0))
    phase('HIDDEN_INPUT_ENCRYPT_ONLY_NOT_MODEL_ACTIVATION')
    with warnings.catch_warnings():
        warnings.simplefilter('error',getpass.GetPassWarning)
        key=getpass.getpass('GLM API key (hidden; saved ONLY as native encrypted model credential): ')
    need(crypto.valid_key(key),'api_key_format')
    cipher=crypto.run(sw.a.native,pod,{'mode':'encrypt','api_key':key})['ciphertext']
    exp=expected(cipher)
    save('EXPECTED.private.json',exp)
    pod,check=baseline()
    need(sw.same(check['pod_identity'],before['pod_identity']) and check['server_ids']==before['server_ids'],
        'baseline_changed_before_insert')
    need(projection(sw.db_sql(pod,READ_SQL)) is None,'model_appeared_before_insert')
    pg,container=sw.release.db_binding(sw.a,pod)
    phase('REGISTER_ONE_INACTIVE_NONDEFAULT_ENCRYPTED_MODEL')
    save('INSERT-REQUESTED.json',dict(id=MODEL,expected_sha256=sw.recovery.read(ROOT/'EXPECTED.private.json')[1],
        pg_uid=pg['metadata']['uid'],provider_requests=0,defaults_changed=False,references_changed=False))
    report['database_insert_attempted']=True
    result=json.loads(sw.a.native.call(['exec','-i','-n','deer-flow',pg['metadata']['name'],'-c',container,
        '--','sh','-c',WRITE_SHELL],data=insert_sql(cipher).encode(),timeout=30,limit=4096)[1])
    need(result=={'model_insert_committed':True},'insert_ack_uncertain_USE_STATUS')
    save('INSERT-ACK.json',result)
    need(sw.a.native.get('pod',pg['metadata']['name'],'deer-flow')['metadata']['uid']==pg['metadata']['uid'],'db_pod_changed')
    phase('READBACK_NATIVE_DECRYPT_AND_UNCHANGED_56_POOLS')
    need(projection(sw.db_sql(pod,READ_SQL))==exp,'insert_readback_mismatch_USE_STATUS')
    actual=sw.db_sql(pod,READ_CIPHER_SQL)['ciphertext']
    need(actual==cipher,'credential_readback_changed')
    crypto.run(sw.a.native,pod,{'mode':'verify','api_key':key,'ciphertext':actual})
    key=None; actual=None; cipher=None
    _,after=baseline()
    need(sw.same(after['pod_identity'],before['pod_identity']) and after['server_ids']==before['server_ids'],
        'live_process_changed_after_insert')
    save('AFTER.json',after)
    report.update(glm_model_stage='PASS',model_id=MODEL,model_status='inactive',is_default=False,
        native_encrypted_storage='PASS',native_decrypt_readback='PASS',database_rows_inserted=1,
        credentials_storage='WEKNORA_NATIVE_AES256_GCM_DATABASE_ONLY',
        next_gate='CONTROLLED_DEFAULT_REFERENCE_AND_RESTART_CONFIGURATION_SWITCH')
    save('SUCCESS.json',report)


def status():
    result=dict(glm_model_stage_status='READ_ONLY',database_writes=0,signals_sent=0,provider_requests=0,
        files_modified=False,automatic_retry=False,state=str(ROOT))
    if not ROOT.exists(): result['state_exists']=False
    else:
        sw.old.safe_dir(ROOT)
        result['records']={n:(ROOT/n).exists() for n in ('STARTED.json','CRYPTO-SELFTEST.json',
            'EXPECTED.private.json','INSERT-REQUESTED.json','INSERT-ACK.json','SUCCESS.json','RESULT.json')}
        _,pod,_=current(); model=projection(sw.db_sql(pod,READ_SQL))
        if model is None: result['staged_model']='ABSENT_NO_REPLAY'
        elif result['records']['EXPECTED.private.json']:
            exp,digest=sw.recovery.read(ROOT/'EXPECTED.private.json')
            if result['records']['INSERT-REQUESTED.json']:
                need(read('INSERT-REQUESTED.json')['expected_sha256']==digest,'expected_receipt_changed')
            result['staged_model']='EXACT_INACTIVE_NONDEFAULT' if model==exp else 'DRIFT_REQUIRES_REVIEW'
        else: result['staged_model']='UNBOUND_REQUIRES_REVIEW'
        result.update(production_activation='NOT_PERFORMED_BY_THIS_TOOL',credential_values_printed=False)
    sw.obs.emit(result); return 0


def main(argv=None):
    p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True)
    g.add_argument('--stage-once',action='store_true'); g.add_argument('--status',action='store_true')
    args=p.parse_args(argv)
    need(os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01' and sys.platform=='linux','root_datax01_required')
    os.umask(0o077)
    import resource,fcntl
    resource.setrlimit(resource.RLIMIT_CORE,(0,0)); sw.initialize()
    if args.status: return status()
    def interrupt(sig,frame): raise RuntimeError('interrupted_USE_STATUS_NO_REPLAY')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP): signal.signal(sig,interrupt)
    fds=[]
    try:
        # Exclude image switch, obsolete88 and56 mutators; never call them.
        fd=sw.frozen_consumers.open_safe(sw.e.ROOT/'controller.lock'); fds.append(fd)
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB); fds.append(sw.e.acquire_previous_lock())
        with sw.recovery.lock(sw.ROOT),sw.recovery.lock(ROOT):
            need(not os.path.lexists(str(ROOT)),'previous_attempt_USE_STATUS_NO_REPLAY')
            ROOT.mkdir(mode=0o700)
            save('STARTED.json',dict(scope='ONE_ENCRYPTED_INACTIVE_MODEL_ONLY',switch_receipt_sha256=SWITCH_SHA))
            report=dict(glm_model_stage='UNCONFIRMED',state=str(ROOT),database_insert_attempted=False,
                production_workloads_mutated=False,production_route_changed=False,model_defaults_changed=False,
                knowledge_base_references_changed=False,agent_references_changed=False,
                consumer_slots_changed=False,mineru_slots_changed=False,signals_sent=0,provider_requests=0,
                plaintext_credentials_saved=False,credentials_exported=False,automatic_retry=False,
                database_migration_requested=False,build_started=False,images_imported=False,ready_for_rollout=False)
            code=1
            try: once(report); code=0
            except BaseException as exc:
                report.update(failed_stage=PHASE,error_type=type(exc).__name__,failed_check=sw.recovery.safe_error(exc),
                    raw_error_printed=False,code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                        for x in traceback.extract_tb(exc.__traceback__)[-8:]])
            report.update(utc_finished=sw.obs.utc(),instruction='USE_STATUS_NO_REPEAT_STAGE_NO_DELETE_STATE')
            save('RESULT.json',report); sw.obs.emit(report)
            sw.obs.emit(dict(report=str(ROOT/'RESULT.json'),report_sha256=sw.recovery.read(ROOT/'RESULT.json')[1]))
            return code
    finally:
        for fd in fds: os.close(fd)


if __name__=='__main__': sys.exit(main())
