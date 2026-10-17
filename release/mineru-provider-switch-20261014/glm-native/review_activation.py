"""Protect one rollback tag on datax-03; read-only activation review on datax-01.

No live Kubernetes patch, signal, DB write, model request or image import.
The source review is deliberately NOT authorization to stop consumers.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import sys
import time
import traceback

import export_weknora_base as io
import preload_native_candidate as cli
import production_node_preload as node
import recover_production_preload as recovery
import production_release_review as review

ROOT = None
PHASE = 'PREFLIGHT'
SOURCE = Path('/var/lib/weknora-glm-activation-review-20261017-r1')
TARGET = Path('/var/lib/weknora-glm-rollback-protection-20261017-r1')
SOURCE_PASS = '4ffabd974b5e51a1f4bce91479b5adab3b3ab1ad900ab15bc425241e07fa181d'
OLD_MANIFEST = 'sha256:579198308537bd130e9daa79173a3ae56897cdd520abc58d88df1efe043095ef'
LEGACY = 'deerflow-dashscope-vlm'
PROPOSED = 'deerflow-cnooc-glm-5.3-flash'
need = io.require

# Only the configured loopback DB connection, fixed queries and a read-only
# transaction. Function definitions stay in a root-private receipt, never stdout.
# Model credentials and document/task content are not selected.
POLICY_SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
WITH policies AS (
 SELECT c.relname AS table_name,t.tgname AS trigger_name,t.tgenabled AS enabled,
 t.tgtype::int AS trigger_type,pn.nspname AS function_schema,p.proname AS function_name,
 pg_get_function_identity_arguments(p.oid) AS identity_arguments,
 pg_get_functiondef(p.oid) AS definition,p.prosrc AS body,
 p.prosecdef AS security_definer,p.proconfig AS settings
 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
 JOIN pg_namespace pn ON pn.oid=p.pronamespace
 WHERE n.nspname='public' AND c.relname IN ('models','knowledge_bases','custom_agents')
 AND NOT t.tgisinternal ORDER BY c.relname,t.tgname LIMIT 65
), agent_refs AS (
 SELECT CASE WHEN config->>'vlm_model_id'='deerflow-dashscope-vlm' THEN 'LEGACY'
 WHEN config->>'vlm_model_id'='deerflow-cnooc-glm-5.3-flash' THEN 'PROPOSED'
 WHEN coalesce(config->>'vlm_model_id','')='' THEN 'EMPTY' ELSE 'OTHER' END AS model_class,
 count(*) AS total,count(*) FILTER (WHERE id LIKE 'builtin-%') AS builtin,
 count(*) FILTER (WHERE config->>'image_upload_enabled'='true') AS image_enabled
 FROM public.custom_agents WHERE deleted_at IS NULL GROUP BY 1
)
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'policies',coalesce((SELECT json_agg(policies) FROM policies),'[]'::json),
 'agent_refs',coalesce((SELECT json_agg(agent_refs) FROM agent_refs),'[]'::json),
 'kb_multimodal_refs',(SELECT json_build_object('total',count(*),
 'legacy',count(*) FILTER (WHERE image_processing_config->>'model_id'='deerflow-dashscope-vlm'),
 'proposed',count(*) FILTER (WHERE image_processing_config->>'model_id'='deerflow-cnooc-glm-5.3-flash'),
 'other',count(*) FILTER (WHERE coalesce(image_processing_config->>'model_id','') NOT IN
 ('','deerflow-dashscope-vlm','deerflow-cnooc-glm-5.3-flash'))) FROM public.knowledge_bases WHERE deleted_at IS NULL),
 'proposed_id_rows',(SELECT count(*) FROM public.models WHERE id='deerflow-cnooc-glm-5.3-flash'));
ROLLBACK;"""

def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def save(name, value):
    old = io.ROOT
    try:
        io.ROOT = ROOT
        io.write_new(name, value)
        fd = os.open(str(ROOT), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally: io.ROOT = old


def phase(name):
    global PHASE
    PHASE = name
    io.emit(dict(activation_preparation='RUNNING', phase=name, production_workload_writes=0))


def common():
    return dict(production_workloads_mutated=False, production_configuration_changed=False,
                database_writes=0, provider_requests=0, signals_sent=0, images_imported=0,
                consumer_slots_changed=False, mineru_slots_changed=False, model_changed=False,
                automatic_retry=False, ready_for_rollout=False, credentials_requested=False,
                raw_configuration_printed=False)


def check_candidate_receipt(r):
    need(r.get('production_node_preload') == 'PASS' and r.get('production_writes') == 0
         and r.get('production_workloads_mutated') is False, 'preload_boundary_changed')
    c = r.get('candidate', {})
    need(c.get('image') == cli.IMAGE and c.get('manifest_digest') == cli.MANIFEST
         and c.get('config_id') == cli.CONFIG and c.get('content_and_unpack') == 'PASS'
         and c.get('pinned') is True, 'candidate_receipt_identity')
    return c


def exact_old():
    old = node.rollback()
    need(old.get('image') == io.IMAGE and old.get('config_digest') == io.CONFIG
         and old.get('manifest_digest') == OLD_MANIFEST, 'rollback_identity_changed')
    return old


def pin_once():
    phase('BIND_TARGET_PRELOAD_AND_RUNNING_ROLLBACK_IMAGE')
    r, digest = recovery.read(recovery.TARGET/'RESULT.json')
    check_candidate_receipt(r)
    candidate = cli.image_check(require_pin=True)
    old = exact_old()
    save('BEFORE.json', dict(preload_sha256=digest, rollback_image=old, candidate=candidate))
    if not old['pinned']:
        phase('PROTECT_EXACT_OLD_IMAGE_TAG_ONLY')
        need(exact_old() == old, 'rollback_changed_before_pin')
        # Journal first. An uncertain label response is reconciled by --status,
        # never by repeating this mutation or by pulling/importing an image.
        save('PIN-REQUESTED.json', dict(image=io.IMAGE, manifest_digest=OLD_MANIFEST, label=cli.PIN))
        cli.command(cli.CTR+['images', 'label', io.IMAGE, cli.PIN], timeout=30, limit=65536)
        save('PIN-ACK.json', dict(exit_code=0))
    for _ in range(10):
        after = exact_old()
        need(dict(after, pinned=old['pinned']) == old, 'rollback_identity_changed_after_pin')
        if after['pinned']: break
        time.sleep(1)
    need(after['pinned'], 'pin_not_observed_USE_STATUS')
    need(cli.image_check(require_pin=True) == candidate, 'candidate_changed')
    return dict(rollback_image_protection='PASS', old_image=after,
                candidate=candidate, already_pinned=old['pinned'],
                image_label_write_attempted=not old['pinned'],
                rollback_ready_scope='IMAGE_CONTENT_ONLY_NOT_FULL_ROLLOUT_APPROVAL')


def clean_policies(row):
    need(row.get('read_only') == 'on', 'policy_query_not_readonly')
    policies = row.get('policies')
    need(isinstance(policies, list) and len(policies) <= 64, 'policy_count_bound')
    known = {'deerflow_apply_enterprise_model_defaults',
             'deerflow_apply_enterprise_knowledge_base_defaults',
             'deerflow_apply_enterprise_custom_agent_defaults'}
    summaries = []
    for p in policies:
        need(p.get('table_name') in ('models', 'knowledge_bases', 'custom_agents')
             and p.get('enabled') in ('O', 'D', 'R', 'A') and type(p.get('trigger_type')) is int
             and isinstance(p.get('body'), str) and len(p['body'].encode()) < 131072
             and isinstance(p.get('definition'), str) and len(p['definition'].encode()) < 262144,
             'policy_shape')
        recognized = p.get('function_schema') == 'public' and p.get('function_name') in known
        summaries.append(dict(table=p['table_name'], enabled=p['enabled'], trigger_type=p['trigger_type'],
            function=p['function_name'] if recognized else 'OTHER_REQUIRES_REVIEW',
            trigger_name_sha256=sha(p.get('trigger_name','').encode()),
            definition_sha256=sha(p['definition'].encode()), body_sha256=sha(p['body'].encode()),
            legacy_literal_count=p['body'].count("'"+LEGACY+"'"),
            proposed_literal_count=p['body'].count("'"+PROPOSED+"'"),
            security_definer=p.get('security_definer') is True,
            identity_arguments_empty=p.get('identity_arguments') == '',
            settings_present=bool(p.get('settings'))))
    agents = row.get('agent_refs')
    need(isinstance(agents,list) and len(agents) <= 4, 'agent_count_bound')
    safe_agents=[]
    for r in agents:
        need(r.get('model_class') in ('LEGACY','PROPOSED','EMPTY','OTHER'), 'agent_model_class')
        need(all(type(r.get(k)) is int and r[k] >= 0 for k in ('total','builtin','image_enabled')), 'agent_counts')
        safe_agents.append({k:r[k] for k in ('model_class','total','builtin','image_enabled')})
    kb = row.get('kb_multimodal_refs', {})
    need(all(type(kb.get(k)) is int and kb[k] >= 0 for k in ('total','legacy','proposed','other')), 'kb_counts')
    need(type(row.get('proposed_id_rows')) is int and row['proposed_id_rows'] in (0,1), 'proposed_model_count')
    return dict(policies=summaries, agent_refs=safe_agents,
                kb_multimodal_refs={k:kb[k] for k in ('total','legacy','proposed','other')},
                proposed_id_rows=row['proposed_id_rows'], credentials_selected=False,
                policy_definitions_exported=False)


# No import of application modules and no YAML dump. Bound files and hash their
# actual bytes to reconcile mounted subPath snapshots with current ConfigMaps.
CONFIG_PROBE = r'''
import hashlib,json,os,stat
paths=['/app/config/config.yaml','/app/config/builtin_models.yaml','/app/config/builtin_agents.yaml']
out={}
for path in paths:
 fd=os.open(path,os.O_RDONLY|os.O_NONBLOCK)
 with os.fdopen(fd,'rb') as f:
  s=os.fstat(f.fileno()); assert stat.S_ISREG(s.st_mode) and s.st_size<=1048576
  raw=f.read(1048577); assert len(raw)==s.st_size
  assert s.st_mtime_ns==os.fstat(f.fileno()).st_mtime_ns
 out[path]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
 'legacy_id_count':raw.count(b'deerflow-dashscope-vlm'),
 'proposed_id_count':raw.count(b'deerflow-cnooc-glm-5.3-flash')}
print(json.dumps(out))
'''


def mounted_configs(a, pod):
    ac = review.container(pod,'app')
    paths = ('/app/config/config.yaml','/app/config/builtin_models.yaml','/app/config/builtin_agents.yaml')
    maps, summaries = {}, []
    for path in paths:
        mounts = [m for m in ac.get('volumeMounts',[]) if m.get('mountPath') == path]
        need(len(mounts)==1 and mounts[0].get('readOnly') is True and
             mounts[0].get('subPath') == Path(path).name and not mounts[0].get('subPathExpr'), 'config_mount_shape')
        m = mounts[0]
        volumes = [v for v in pod['spec'].get('volumes',[]) if v['name']==m['name']]
        need(len(volumes)==1 and set(volumes[0])=={'name','configMap'}, 'config_volume_shape')
        v = volumes[0]['configMap']; name = v.get('name'); key = Path(path).name
        need(isinstance(name,str) and not v.get('optional'), 'config_map_identity')
        if 'items' in v:
            entries=[i for i in v['items'] if i.get('path')==key]
            need(len(entries)==1, 'config_item_mapping'); key=entries[0]['key']
        if name not in maps:
            maps[name]=a.native.get('configmap',name,'deer-flow')
        cm=maps[name]; text=cm.get('data',{}).get(key)
        need(isinstance(text,str) and len(text.encode())<=1048576, 'config_bytes_bound')
        raw=text.encode()
        summaries.append(dict(path=path, configmap=name, configmap_uid=cm['metadata']['uid'],
            key=key, sha256=sha(raw), bytes=len(raw), legacy_id_count=raw.count(LEGACY.encode()),
            proposed_id_count=raw.count(PROPOSED.encode()), immutable=cm.get('immutable') is True))
    runtime=json.loads(a.native.call(['exec','-n','deer-flow',pod['metadata']['name'],'-c','app',
        '--','python3','-I','-B','-c',CONFIG_PROBE],limit=8192)[1])
    for r in summaries:
        r['mounted_matches_configmap']=runtime.get(r['path'])=={k:r[k] for k in
            ('sha256','bytes','legacy_id_count','proposed_id_count')}
    save('CONFIGMAPS.private.json',maps)
    save('MOUNTED-CONFIGS.json',summaries)
    return summaries,maps


def image_patch(dep):
    need(dep['metadata']['uid']=='44aca5b9-8fc1-44d6-af61-37c6d62723d5'
         and dep['spec'].get('replicas')==1 and dep['spec'].get('strategy',{}).get('type')=='Recreate', 'deployment_boundary')
    cs=dep['spec']['template']['spec']['containers']
    matches=[i for i,c in enumerate(cs) if c.get('name')=='app']
    need(len(matches)==1 and cs[matches[0]].get('image')==io.IMAGE, 'original_app_image_changed')
    index=matches[0]; expected=copy.deepcopy(dep['spec'])
    expected['template']['spec']['containers'][index]['image']=cli.IMAGE
    patch=[{'op':'test','path':'/metadata/uid','value':dep['metadata']['uid']},
           {'op':'test','path':'/metadata/resourceVersion','value':dep['metadata']['resourceVersion']},
           {'op':'test','path':'/spec','value':dep['spec']},
           {'op':'replace','path':'/spec/template/spec/containers/'+str(index)+'/image','value':cli.IMAGE}]
    return patch,expected


def source_review():
    import recover_enrichment_gateway as frozen
    phase('BIND_ACCEPTANCE_AND_COMPLETED_PRODUCTION_PRELOAD')
    r,_=recovery.read(recovery.SOURCE/'RESULT.json',SOURCE_PASS)
    need(r.get('production_preload_recovery')=='PASS' and r.get('production_writes')==0, 'source_preload_not_passed')
    remote,remote_sha=recovery.read(recovery.SOURCE/'REMOTE-RESULT.json')
    check_candidate_receipt(remote)
    a=frozen.load_original(); binding=review.accepted(a)
    p,q=a.native.initialize(); _,guard,plan=q.package()
    phase('FRESH_SINGLE_POD_56_SLOT_BASELINE')
    before=guard.live(plan,binary=True)
    need(before.get('node')=='datax-03', 'live_node_changed')
    pod=a.native.get('pod',before['pod'],'deer-flow')
    need(pod['metadata']['uid']==io.POD_UID, 'live_pod_changed')
    dep=a.native.get('deployment','weknora-app','deer-flow')
    env=review.literals(review.container(pod,'app'))
    expected={'WEKNORA_ASYNQ_'+k+'_CONCURRENCY':str(v) for k,v in review.EXPECTED_POOLS.items()}
    expected.update(WEKNORA_WIKI_ASYNQ_CONCURRENCY='12',WEKNORA_MODEL_MAX_CONCURRENCY='64')
    need(all(env.get(k)==v for k,v in expected.items()), 'current_56_slot_settings_changed')
    save('BASELINE.private.json',dict(deployment=dep,pod=pod,guard=before))
    phase('READ_MODEL_REFERENCES_AND_DEFAULT_POLICIES')
    pg,pc=review.db_binding(a,pod)
    def sql(query):
        return json.loads(a.native.call(['exec','-n','deer-flow',pg['metadata']['name'],'-c',pc,
            '--','sh','-c',review.DB_SHELL,'read-only-review',query],limit=1048576)[1])
    models=review.clean_models(sql(review.SQL)); save('MODELS.json',models)
    need(models['schema']==[{'version':90,'dirty':False}], 'schema_changed')
    policies=sql(POLICY_SQL); policy_summary=clean_policies(policies)
    save('POLICIES.private.json',policies); save('POLICY-SUMMARY.json',policy_summary)
    io.emit(dict(model_policy_review=policy_summary,model_scope=models))
    phase('BIND_CONFIGMAP_AND_ACTUAL_MOUNTED_DEFAULTS')
    configs,maps=mounted_configs(a,pod)
    io.emit(dict(mounted_configuration_review=configs))
    phase('SERVER_DRY_RUN_IMAGE_ONLY_NO_LIVE_PATCH')
    patch,expected_spec=image_patch(dep)
    save('IMAGE-PATCH-DRYRUN.private.json',patch)
    output=json.loads(a.native.call(['patch','deployment','weknora-app','-n','deer-flow',
        '--type=json','--patch-file=/dev/stdin','--dry-run=server','-o','json'],
        data=json.dumps(patch).encode(),limit=1048576)[1])
    need(output.get('spec')==expected_spec, 'dryrun_changed_other_spec_fields')
    save('DRYRUN.private.json',output)
    phase('FINAL_BASELINE_RECHECK')
    need(guard.live(plan,binary=True)==before, 'production_baseline_changed')
    need(a.native.get('deployment','weknora-app','deer-flow')['spec']==dep['spec'], 'deployment_spec_changed')
    need(a.native.get('pod',pg['metadata']['name'],'deer-flow')['metadata']['uid']==pg['metadata']['uid'], 'db_pod_changed')
    for name,cm in maps.items():
        current=a.native.get('configmap',name,'deer-flow')
        need(current['metadata']['uid']==cm['metadata']['uid'] and current.get('data')==cm.get('data')
             and current.get('binaryData')==cm.get('binaryData'), 'configmap_changed')
    # Counts may change with live traffic; policy definitions must not.
    final=sql(POLICY_SQL)
    need(final['policies']==policies['policies'], 'policies_changed_during_review')
    blockers=[]
    if policy_summary['proposed_id_rows']: blockers.append('PROPOSED_MODEL_ID_ALREADY_EXISTS_REQUIRES_REVIEW')
    if any(x['function']=='OTHER_REQUIRES_REVIEW' for x in policy_summary['policies']): blockers.append('UNKNOWN_DB_TRIGGER')
    if not all(x['mounted_matches_configmap'] for x in configs): blockers.append('MOUNTED_DEFAULTS_DIFFER_FROM_CONFIGMAP')
    if models['legacy_inline_kbs'] or models['unresolved_model_refs']: blockers.append('MODEL_REFERENCE_REQUIRES_REVIEW')
    return dict(activation_review='PASS', receipt_binding=binding, production_baseline='UNCHANGED',
        source_preload_sha256=SOURCE_PASS, remote_receipt_sha256=remote_sha,
        image_only_server_dryrun='PASS', current_worker_slots=56, model_default_limit=64,
        blockers=blockers, proposed_strategy='SEPARATE_OPERATOR_GLM_ID_REQUIRES_REVIEW',
        gateway_source_recheck='NOT_REPEATED_NO_GATEWAY_CHANGE',
        next_gates=['REVIEW_ROLLBACK_PIN_RECEIPT_AND_DEFAULT_REFERENCES',
            'CONTROLLED_IMAGE_SWITCH_KEEP_56_SLOTS', 'HIDDEN_CREDENTIAL_AND_MODEL_DEFAULT_ACTIVATION',
            'PRODUCTION_BUSINESS_VALIDATION'], queue_empty_required=False)


def status(host):
    out=dict(common(),activation_status='READ_ONLY',node=host,state=str(ROOT),files_modified=False,records={})
    if ROOT.exists():
        io.safe_dir(ROOT)
        for name in ('STARTED.json','BEFORE.json','PIN-REQUESTED.json','PIN-ACK.json','RESULT.json'):
            if (ROOT/name).exists():
                r,digest=recovery.read(ROOT/name)
                out['records'][name]=dict(sha256=digest,fields={k:r.get(k) for k in
                    ('rollback_image_protection','activation_review','failed_check','failed_stage') if k in r})
    if host=='datax-03':
        out['old_image']=exact_old(); out['candidate']=cli.image_check(require_pin=True)
    io.emit(out)
    return 0


def main(argv=None):
    global ROOT
    parser=argparse.ArgumentParser()
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--prepare-once',action='store_true'); group.add_argument('--status',action='store_true')
    args=parser.parse_args(argv); host=socket.gethostname().split('.')[0]
    need(os.geteuid()==0 and host in ('datax-01','datax-03') and platform.machine()=='aarch64','root_reviewed_arm64_node_required')
    ROOT=SOURCE if host=='datax-01' else TARGET
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    if args.status: return status(host)
    def interrupted(sig,frame): raise io.CheckFailure('interrupted_USE_STATUS')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGALRM): signal.signal(sig,interrupted)
    signal.alarm(600)
    try:
        with recovery.lock(ROOT):
            need(not os.path.lexists(str(ROOT)), 'previous_attempt_USE_STATUS')
            ROOT.mkdir(mode=0o700); save('STARTED.json',dict(node=host,scope='NO_LIVE_WORKLOAD_SWITCH'))
            result=dict(common(),node=host,state=str(ROOT))
            try:
                result.update(source_review() if host=='datax-01' else pin_once())
                result['preparation']='PASS'
            except BaseException as exc:
                result.update(preparation='UNCONFIRMED',error_type=type(exc).__name__,
                    failed_check=recovery.safe_error(exc),failed_stage=PHASE,
                    image_label_write_attempted=(ROOT/'PIN-REQUESTED.json').exists(),raw_error_printed=False,
                    code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                                 for x in traceback.extract_tb(exc.__traceback__)[-8:]])
            result['instruction']='USE_STATUS_ON_UNCERTAINTY_NO_REPEAT_PREPARE'
            save('RESULT.json',result)
            io.emit(result)
            io.emit(dict(report=str(ROOT/'RESULT.json'),report_sha256=recovery.read(ROOT/'RESULT.json')[1]))
            return 0 if result['preparation']=='PASS' else 1
    finally: signal.alarm(0)


if __name__=='__main__': sys.exit(main())
