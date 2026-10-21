"""Read-only production model scope and receipt binding. Never submits work."""
import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor

PASS_PATH = '/var/lib/weknora-full-enrichment-20261017-r1-fix2/RESULT.json'
PASS_SHA = '9c7706df6ca46499ee3bd8ce9274ef690488e6ff1e12f419341acb31c655a421'
RECOVERY_PATH = '/var/lib/weknora-enrichment-config-recovery-20261017-r1/RESULT.json'
RECOVERY_SHA = 'c00c641ba00a065fbc47623dd962ea76c19bd27c659a9509c00e17cfcfd58b98'
EXPECTED_POOLS = {'CORE':8,'POSTPROCESS':4,'ENRICHMENT':24,'MAINTENANCE':4,'SHARED':4}

# Only fixed SQL, executed with the already configured DB identity. No key,
# descriptions, KB titles, document/chunk contents or arbitrary JSON returned.
SQL = """BEGIN READ ONLY;
SET LOCAL statement_timeout='5s';
SET LOCAL lock_timeout='1s';
WITH refs AS (
 SELECT coalesce(vlm_config->>'model_id','') AS model_id,
 count(*) AS kb_count,
 count(*) FILTER (WHERE vlm_config->>'enabled'='true') AS enabled_count
 FROM public.knowledge_bases WHERE deleted_at IS NULL
 GROUP BY coalesce(vlm_config->>'model_id','')
), selected AS (
 SELECT m.id, m.type, m.is_builtin, m.managed_by,
 CASE WHEN m.name='qwen3-vl-plus' THEN 'QWEN3_VL_PLUS'
      WHEN m.name='GLM-5.3-Flash' THEN 'GLM_5_3_FLASH' ELSE 'OTHER' END AS name_class,
 coalesce(m.parameters->'extra_config'->>'wire_profile' = 'enterprise_glm_single_image_sse_v1',false) AS glm_profile,
 CASE WHEN (m.parameters->>'max_concurrency') ~ '^[0-9]{1,6}$'
      THEN (m.parameters->>'max_concurrency')::int ELSE NULL END AS max_concurrency,
 coalesce(r.kb_count,0) AS kb_count, coalesce(r.enabled_count,0) AS enabled_count
 FROM public.models m LEFT JOIN refs r ON r.model_id=m.id
 WHERE m.deleted_at IS NULL AND (m.type='VLLM' OR coalesce(r.kb_count,0)>0)
 ORDER BY m.id LIMIT 257
)
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'schema', (SELECT json_agg(json_build_object('version',version,'dirty',dirty)) FROM public.schema_migrations),
 'models',coalesce((SELECT json_agg(selected) FROM selected),'[]'::json),
 'legacy_inline_kbs',(SELECT count(*) FROM public.knowledge_bases WHERE deleted_at IS NULL
   AND coalesce(vlm_config->>'model_name','')<>'' AND coalesce(vlm_config->>'base_url','')<>''),
 'unresolved_model_refs',(SELECT count(*) FROM refs r WHERE r.model_id<>'' AND NOT EXISTS
   (SELECT 1 FROM public.models m WHERE m.id=r.model_id AND m.deleted_at IS NULL)));
ROLLBACK;"""
DB_SHELL = """set -eu
test -n "${POSTGRES_PASSWORD:-}" && test -n "${POSTGRES_USER:-}" && test -n "${POSTGRES_DB:-}"
export PGPASSWORD="$POSTGRES_PASSWORD" PGCONNECT_TIMEOUT=5
export PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000'
exec psql -X -qAt --no-password -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1"
"""
VISION_PROBE = r'''
import ast,hashlib,json,os
from pathlib import Path
out={'vlm_name_class':'GLM' if os.environ.get('VLM_MODEL_NAME')=='GLM-5.3-Flash' else ('QWEN_DEFAULT' if os.environ.get('VLM_MODEL_NAME','qwen3-vl-plus')=='qwen3-vl-plus' else 'OTHER'),
 'vision_id_is_legacy':os.environ.get('WEKNORA_DEFAULT_VISION_MODEL_ID','deerflow-dashscope-vlm')=='deerflow-dashscope-vlm',
 'vlm_base_url_configured':bool(os.environ.get('VLM_BASE_URL')), 'source_files':[]}
for root in ('/app','/app/backend'):
 p=Path(root)/'app/gateway/weknora_identity.py'
 if not p.is_file(): continue
 b=p.read_bytes(); assert len(b)<1048576
 t=ast.parse(b); funcs=[x for x in ast.walk(t) if isinstance(x,(ast.FunctionDef,ast.AsyncFunctionDef))]
 out['source_files'].append({'sha256':hashlib.sha256(b).hexdigest(),
   'defines_builtin_model_provisioning':any(x.name=='_weknora_default_models' for x in funcs),
   'mentions_glm_wire_profile':b'enterprise_glm_single_image_sse_v1' in b,
   'has_conflict_update_sql':b'ON CONFLICT' in b and b'DO UPDATE' in b})
print(json.dumps(out))
'''


def require(ok,label):
    if not ok: raise RuntimeError(label)


def accepted(a):
    from pathlib import Path
    r=json.loads(a.audit.read(Path(PASS_PATH),PASS_SHA))
    s=json.loads(a.audit.read(Path(RECOVERY_PATH),RECOVERY_SHA))
    require(all(r.get(k)=='PASS' for k in ('native_enrichment_acceptance','native_schema_migration',
        'real_asynq_glm_enrichment','real_embedding_1024d_index','vector_only_retrieval')),'full_acceptance_not_passed')
    require(r.get('cleanup')=='NAMESPACE_ABSENT_VERIFIED' and r.get('production_baseline')=='UNCHANGED'
        and r.get('production_writes')==0 and r.get('document_submission_attempted') is True,'acceptance_boundary')
    require(s.get('fixture_config_recovery')=='PASS' and s.get('acceptance_exit_code')==0
        and s.get('fresh_acceptance_state')==str(Path(PASS_PATH).parent),'recovery_not_passed')
    return {'full_acceptance_sha256':PASS_SHA,'recovery_sha256':RECOVERY_SHA}


def container(p,name):
    rows=[x for x in p['spec']['containers'] if x['name']==name]
    require(len(rows)==1,'container_not_unique'); return rows[0]


def literals(c):
    rows=c.get('env',[]); require(len({x['name'] for x in rows})==len(rows),'duplicate_env')
    return {x['name']:x.get('value') for x in rows if 'value' in x}


def credential_ref(c,name):
    rows=[x for x in c.get('env',[]) if x['name']==name]
    require(len(rows)==1,'db_credential_ref_missing')
    r=rows[0].get('valueFrom',{}).get('secretKeyRef')
    require(r and r.get('name') and r.get('key') and not r.get('optional'),'db_credential_ref_shape')
    return r


def db_binding(a,app):
    n=a.native; sts=n.get('statefulset','weknora-postgres','deer-flow')
    require(sts['spec'].get('replicas')==1,'db_replica_review')
    sel=sts['spec'].get('selector',{})
    require(sel.get('matchLabels') and not sel.get('matchExpressions'),'db_selector_review')
    selector=','.join(k+'='+v for k,v in sorted(sel['matchLabels'].items()))
    pods=json.loads(n.call(['get','pods','-n','deer-flow','-l',selector,'-o','json'])[1])['items']
    pods=[p for p in pods if any(x.get('uid')==sts['metadata']['uid'] and x.get('controller') is True
        for x in p['metadata'].get('ownerReferences',[])) and not p['metadata'].get('deletionTimestamp')]
    require(len(pods)==1,'db_owned_pod_not_unique'); pg=pods[0]
    cs=pg['status'].get('containerStatuses',[])
    require(len(cs)==1 and cs[0].get('ready') and cs[0].get('state',{}).get('running'),'db_not_ready')
    ac=container(app,'app'); pc=container(pg,cs[0]['name']); ae=literals(ac); pe=literals(pc)
    require(ae.get('DB_DRIVER','postgres')=='postgres' and ae.get('DB_HOST') in
        ('weknora-postgres','weknora-postgres.deer-flow','weknora-postgres.deer-flow.svc','weknora-postgres.deer-flow.svc.cluster.local')
        and ae.get('DB_PORT','5432')=='5432','db_address_review')
    require(ae.get('DB_NAME') and ae['DB_NAME']==pe.get('POSTGRES_DB') and ae.get('DB_USER')
        and ae['DB_USER']==pe.get('POSTGRES_USER'),'db_identity_review')
    require(credential_ref(ac,'DB_PASSWORD')==credential_ref(pc,'POSTGRES_PASSWORD'),'db_reference_mismatch')
    eps=n.get('endpoints','weknora-postgres','deer-flow'); addresses=[]
    for s in eps.get('subsets',[]):
        require(not s.get('notReadyAddresses') and any(p['port']==5432 for p in s.get('ports',[])),'db_endpoint_not_ready')
        addresses+=s.get('addresses',[])
    require(addresses and all(x.get('targetRef',{}).get('uid')==pg['metadata']['uid'] for x in addresses),'db_endpoint_identity')
    return pg,pc['name']


def clean_models(row):
    require(row.get('read_only')=='on','db_not_read_only')
    models=row.get('models'); require(isinstance(models,list) and len(models)<=256,'model_scope_exceeds_review_bound')
    out=[]
    for m in models:
        require(isinstance(m.get('id'),str) and 0<len(m['id'])<=64,'model_id_shape')
        mid=m['id'] if m['id'] in ('deerflow-dashscope-vlm','deerflow-cnooc-glm-5.3-flash') else 'sha256:'+hashlib.sha256(m['id'].encode()).hexdigest()
        require(m.get('name_class') in ('QWEN3_VL_PLUS','GLM_5_3_FLASH','OTHER') and type(m.get('is_builtin')) is bool
            and type(m.get('glm_profile')) is bool,'model_projection')
        for k in ('kb_count','enabled_count'):
            require(type(m.get(k)) is int and m[k]>=0,'model_count')
        limit=m.get('max_concurrency'); require(limit is None or type(limit) is int and 0<=limit<=999999,'model_limit')
        out.append(dict(id=mid,name_class=m['name_class'],is_builtin=m['is_builtin'],glm_profile=m['glm_profile'],
            managed_by_class=m.get('managed_by') if m.get('managed_by') in ('','yaml','operator') else 'OTHER',
            max_concurrency=limit,kb_count=m['kb_count'],enabled_count=m['enabled_count']))
    for k in ('legacy_inline_kbs','unresolved_model_refs'):
        require(type(row.get(k)) is int and row[k]>=0,'model_aggregate')
    schema=row.get('schema'); require(isinstance(schema,list) and len(schema)==1 and
        type(schema[0].get('version')) is int and type(schema[0].get('dirty')) is bool,'schema_projection')
    return dict(read_only='on',models=out,schema=schema,legacy_inline_kbs=row['legacy_inline_kbs'],
        unresolved_model_refs=row['unresolved_model_refs'],credentials_selected=False,business_content_selected=False)


def collect(a,save):
    binding=accepted(a)
    p,q=a.native.initialize(); _,guard,plan=q.package(); before=guard.live(plan,binary=True)
    require(before['node']=='datax-03','production_node_changed')
    app=a.native.get('pod',before['pod'],'deer-flow'); dep=a.native.get('deployment','weknora-app','deer-flow')
    env=literals(container(app,'app'))
    expected={'WEKNORA_ASYNQ_'+k+'_CONCURRENCY':str(v) for k,v in EXPECTED_POOLS.items()}
    expected.update(WEKNORA_WIKI_ASYNQ_CONCURRENCY='12',WEKNORA_MODEL_MAX_CONCURRENCY='64')
    require(all(env.get(k)==v for k,v in expected.items()),'expected56_environment_changed')
    save('DEPLOYMENT.private.json',dep); save('POD.private.json',app)
    pg,pc=db_binding(a,app)
    raw=a.native.call(['exec','-n','deer-flow',pg['metadata']['name'],'-c',pc,'--','sh','-c',DB_SHELL,'db-review',SQL],limit=262144)[1]
    models=clean_models(json.loads(raw))
    require(a.native.get('pod',pg['metadata']['name'],'deer-flow')['metadata']['uid']==pg['metadata']['uid'],'db_pod_changed')
    save('MODEL-SCOPE.json',models)
    # Gateway may provision the old model again on SSO. Read every serving Pod,
    # not the first Pod, and never import/call its provisioning functions.
    d=a.native.get('deployment','gateway','deer-flow'); s=d.get('status',{}); count=d['spec']['replicas']
    require(1<=count<=128 and s.get('observedGeneration',0)>=d['metadata']['generation'] and
        all(s.get(k,0)==count for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')),'gateway_unstable')
    eps=a.native.get('endpoints','gateway','deer-flow'); add=[]
    for sub in eps.get('subsets',[]):
        require(not sub.get('notReadyAddresses'),'gateway_unready_endpoint'); add+=sub.get('addresses',[])
    require(len(add)==count and len({x.get('targetRef',{}).get('uid') for x in add})==count,'gateway_endpoint_coverage')
    def probe(x):
        ref=x.get('targetRef',{}); require(ref.get('kind')=='Pod','gateway_not_pod')
        pod=a.native.get('pod',ref['name'],'deer-flow'); require(pod['metadata']['uid']==ref['uid'],'gateway_pod_changed')
        cs=[c for c in pod['status'].get('containerStatuses',[]) if c['name']=='gateway']
        require(len(cs)==1 and cs[0].get('ready') and cs[0].get('state',{}).get('running') and
            not pod['metadata'].get('deletionTimestamp'),'gateway_not_ready')
        result=json.loads(a.native.call(['exec','-n','deer-flow',ref['name'],'-c','gateway','--','python3','-I','-B','-c',VISION_PROBE],limit=8192)[1])
        after=a.native.get('pod',ref['name'],'deer-flow')
        require(after['metadata']['uid']==pod['metadata']['uid'] and after['spec']==pod['spec'] and
            after['status'].get('containerStatuses')==pod['status'].get('containerStatuses'),'gateway_changed_during_probe')
        return result
    a.emit({'release_review':'GATEWAY_VISION_PROVISIONING','pods':count,'maximum_parallel_readonly_exec':4})
    with ThreadPoolExecutor(max_workers=4) as pool: contracts=list(pool.map(probe,add))
    require(a.native.get('deployment','gateway','deer-flow')['spec']==d['spec'] and
        a.native.get('endpoints','gateway','deer-flow').get('subsets')==eps.get('subsets'),'gateway_changed')
    grouped={}
    for row in contracts:
        key=json.dumps(row,sort_keys=True); grouped[key]=grouped.get(key,0)+1
    vision=[dict(pods=n,contract=json.loads(key)) for key,n in sorted(grouped.items())]
    save('GATEWAY-VISION.json',vision)
    require(guard.live(plan,binary=True)==before,'production_changed_during_review')
    return dict(binding,production_baseline=before,worker_environment=expected,model_scope=models,
        gateway_vision=vision,slots_changed=False,production_writes=0,production_database_read_only=True,
        rollout_ready=False,model_change_requires_gateway_provisioning_review=True),p,q,guard,plan
