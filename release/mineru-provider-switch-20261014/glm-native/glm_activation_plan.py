"""Pure, bounded GLM reference/default transition. No credentials or model calls."""
import copy
import hashlib
import json
import re
import stage_glm_model as stage
import glm_document_migration as documents

sw=stage.sw
need=sw.need
OLD=sw.activation.LEGACY
NEW=stage.MODEL
CM='weknora-config-glm-20261017-r1'
STAGE_SHA='4a7087a49947a5925c488db42f0dc90508c7954b5dcade24d25ba93befebfcff'

# Complete trigger definitions from the operator's read-only 2026-10-17 receipt.
# WHEN predicates, UPDATE OF restrictions, arguments and deferral are material.
TRIGGERS={
 ('models','deerflow_apply_enterprise_model_defaults'):'deerflow_enterprise_model_defaults_before_write',
 ('knowledge_bases','deerflow_apply_enterprise_knowledge_base_defaults'):'deerflow_enterprise_defaults_before_insert',
 ('custom_agents','deerflow_apply_enterprise_custom_agent_defaults'):'deerflow_enterprise_defaults_before_insert',
 ('knowledge_bases','deerflow_ensure_mineru_parser_rule'):'deerflow_mineru_parser_defaults',
 ('knowledge_bases','weknora_document_stability_r2_spreadsheet_default'):'weknora_document_stability_r2_spreadsheet_default',
}
POLICY_SQL=sw.activation.POLICY_SQL.replace('p.prosrc AS body,',
    'p.prosrc AS body,pg_get_triggerdef(t.oid) AS trigger_definition,')


def check_triggers(rows):
    need(len(rows)==5,'trigger_count_changed')
    for r in rows:
        table,fn=r['table_name'],r['function_name']
        name=TRIGGERS.get((table,fn)); need(name is not None,'unknown_trigger')
        definition='CREATE TRIGGER '+name+' BEFORE INSERT'+(' OR UPDATE' if r['trigger_type']==23 else '')
        definition+=' ON public.'+table+' FOR EACH ROW EXECUTE FUNCTION '+fn+'()'
        need(r.get('trigger_name')==name and r.get('trigger_definition')==definition,'complete_trigger_definition_changed')


def sha(value): return hashlib.sha256(value.encode()).hexdigest()
def literal(value): return stage.sql_literal(value)


def policy_plan(rows):
    sw.policy_check(rows)
    check_triggers(rows)
    out=[]
    for row in rows:
        r=copy.deepcopy(row); name=r['function_name']; body=r['body']
        if name=='deerflow_apply_enterprise_model_defaults':
            need(body.count("'"+OLD+"'")==1,'model_policy_shape')
            body=body.replace("'"+OLD+"'","'"+NEW+"'")
            marker="    ELSIF NEW.type = 'KnowledgeQA' THEN"
            need(body.count(marker)==1,'model_policy_branch')
            body=body.replace(marker,"    ELSIF NEW.id = '"+OLD+"' THEN\n"
                "        NEW.is_default := false;\n"+marker)
        elif name=='deerflow_apply_enterprise_knowledge_base_defaults':
            need(body.count("'"+OLD+"'")==2,'kb_policy_shape')
            body=body.replace("'"+OLD+"'","'"+NEW+"'")
        elif name=='deerflow_apply_enterprise_custom_agent_defaults':
            need(body.count("'"+OLD+"'")==2 and body.startswith('\nBEGIN\n'),'agent_policy_shape')
            body=body.replace("'"+OLD+"'","'"+NEW+"'")
            # A VLM-only migration must not apply unrelated legacy builtin
            # defaults (chat/ASR/feature flags) as a side effect of this UPDATE.
            early="""
    IF TG_OP = 'UPDATE'
       AND OLD.config->>'vlm_model_id' = '%s'
       AND NEW.config->>'vlm_model_id' = '%s'
       AND (NEW.config - 'vlm_model_id') IS NOT DISTINCT FROM (OLD.config - 'vlm_model_id') THEN
        RETURN NEW;
    END IF;
""" % (OLD,NEW)
            body=body.replace('\nBEGIN\n','\nBEGIN\n'+early,1)
            marker='    RETURN NEW;\nEND\n'
            need(body.count(marker)==1,'agent_policy_return')
            body=body.replace(marker,"    IF NEW.config->>'vlm_model_id' = '"+OLD+"' THEN\n"
                "        NEW.config := jsonb_set(NEW.config, '{vlm_model_id}', to_jsonb('"+NEW+"'::text));\n"
                "    END IF;\n\n"+marker)
        if body!=r['body']:
            need(r['definition'].count(r['body'])==1,'function_body_not_unique')
            r['definition']=r['definition'].replace(r['body'],body,1); r['body']=body
        out.append(r)
    need(sum(a['definition']!=b['definition'] for a,b in zip(rows,out))==3,'three_policy_changes_only')
    return out


def config_plan(cm):
    need(cm.get('metadata',{}).get('name')=='weknora-config' and
        cm['metadata'].get('uid')=='9e1b3b93-4a53-49f2-8ec1-177761df46b3','config_identity')
    data=copy.deepcopy(cm.get('data',{}))
    for name,digest in sw.CONFIG_HASHES.items():
        need(isinstance(data.get(name),str) and sha(data[name])==digest,'config_bytes_changed')
    text=data['builtin_models.yaml']
    # Exact block edit under a hash-bound complete config; no YAML parser or
    # dependency installation on production, and no credential copied to YAML.
    pattern=r'(?m)^  - id: '+re.escape(OLD)+r'\n(?:(?!  - id: ).*\n)*'
    matches=list(re.finditer(pattern,text)); need(len(matches)==1,'legacy_yaml_block')
    block=matches[0].group(); need(block.count('    is_default: true\n')==1,'legacy_default_yaml')
    data['builtin_models.yaml']=text[:matches[0].start()]+block.replace(
        '    is_default: true\n','    is_default: false\n')+text[matches[0].end():]
    # GLM is deliberately DB-managed, not a new credential-less YAML entry.
    text=data['builtin_agents.yaml']
    need(text.count(OLD)==5 and len(re.findall(r'(?m)^      vlm_model_id: "'+re.escape(OLD)+r'"$',text))==5,
         'agent_yaml_references')
    data['builtin_agents.yaml']=text.replace('      vlm_model_id: "'+OLD+'"','      vlm_model_id: "'+NEW+'"')
    return dict(apiVersion='v1',kind='ConfigMap',metadata={'name':CM,'namespace':'deer-flow',
        'labels':{'deerflow-release':'weknora-glm-20261017-r1'}},immutable=True,data=data,
        **({'binaryData':copy.deepcopy(cm['binaryData'])} if cm.get('binaryData') else {}))


def spec_plan(spec):
    result=copy.deepcopy(spec); ps=result['template']['spec']
    need(result.get('replicas')==1 and result.get('strategy',{}).get('type')=='Recreate','single_recreate_required')
    need(len(ps['containers'])==1 and ps['containers'][0]['name']=='app' and
         ps['containers'][0]['image']==sw.image.IMAGE,'accepted_image_required')
    refs=[v for v in ps.get('volumes',[]) if v.get('configMap',{}).get('name')=='weknora-config']
    need(len(refs)==1,'one_config_volume_required')
    mounts=[m for m in ps['containers'][0].get('volumeMounts',[]) if m['name']==refs[0]['name']]
    need(len(mounts)==3 and {m.get('subPath') for m in mounts}==set(sw.CONFIG_HASHES)
        and all(m.get('readOnly') is True and m.get('mountPath')=='/app/config/'+m['subPath']
                and not m.get('subPathExpr') for m in mounts),'three_exact_subpath_mounts')
    refs[0]['configMap']['name']=CM
    return result


def patch_ops(dep,original,target):
    need(dep['metadata']['uid']==sw.e.process.DEP_UID and dep['spec']==original and spec_plan(original)==target,
         'config_only_cas_shape')
    return [{'op':'test','path':'/metadata/uid','value':dep['metadata']['uid']},
        {'op':'test','path':'/metadata/resourceVersion','value':dep['metadata']['resourceVersion']},
        {'op':'test','path':'/spec','value':original},
        {'op':'replace','path':'/spec/template/spec/volumes','value':target['template']['spec']['volumes']}]


# IDs and selected model references only. The hash protects all non-VLM fields
# without exporting KB names, prompts, documents, inline configs or credentials.
REFS_SELECT="""SELECT coalesce(jsonb_agg(x ORDER BY x->>'kind',x->>'id',x->>'tenant'),'[]'::jsonb) FROM (
 SELECT jsonb_build_object('kind','kb','id',id,'tenant',tenant_id,
  'vlm',vlm_config->'model_id','image',image_processing_config->'model_id',
  'other_sha',encode(sha256(convert_to((to_jsonb(k)-'vlm_config'-'image_processing_config'-'updated_at' ||
    jsonb_build_object('vlm_config',vlm_config-'model_id','image_processing_config',image_processing_config-'model_id'))::text,'UTF8')),'hex')) x
 FROM public.knowledge_bases k WHERE deleted_at IS NULL AND
   (vlm_config->>'model_id'='deerflow-dashscope-vlm' OR image_processing_config->>'model_id'='deerflow-dashscope-vlm')
 UNION ALL
 SELECT jsonb_build_object('kind','agent','id',id,'tenant',tenant_id,'vlm',config->'vlm_model_id',
  'other_sha',encode(sha256(convert_to((to_jsonb(a)-'config'-'updated_at' ||
    jsonb_build_object('config',config-'vlm_model_id'))::text,'UTF8')),'hex'))
 FROM public.custom_agents a WHERE deleted_at IS NULL AND config->>'vlm_model_id'='deerflow-dashscope-vlm'
) projected"""
REFS_SQL="BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';\n"+REFS_SELECT+'; ROLLBACK;'


def ref_target(rows):
    need(isinstance(rows,list) and len(rows)<=4000,'reference_count_bound')
    seen=set(); result=copy.deepcopy(rows)
    for r in result:
        need(r.get('kind') in ('kb','agent') and type(r.get('tenant')) is int and r['tenant']>0
            and isinstance(r.get('id'),str) and re.fullmatch('[A-Za-z0-9_.:-]{1,64}',r['id']) and
            re.fullmatch('[a-f0-9]{64}',r.get('other_sha','')),'reference_shape')
        key=(r['kind'],r['id'],r['tenant']); need(key not in seen,'duplicate_reference'); seen.add(key)
        need(set(r)==({'kind','id','tenant','other_sha','vlm','image'} if r['kind']=='kb' else
                     {'kind','id','tenant','other_sha','vlm'}),'reference_keys')
        need(r['vlm']==OLD or r.get('image')==OLD,'nonlegacy_reference')
        for field in ('vlm','image'):
            if r.get(field)==OLD: r[field]=NEW
    return result


def policies_guard(rows):
    check_triggers(rows)
    entries=[]
    for r in rows:
        entries.append('('+','.join(literal(str(v)) for v in
            (r['table_name'],r['trigger_name'],r['function_name'],str(r['trigger_type']),sha(r['definition']),sha(r['trigger_definition'])))+')')
    return """IF (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public'
 AND c.relname IN ('models','knowledge_bases','custom_agents') AND NOT t.tgisinternal)<>5 OR
 (SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
 JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_proc p ON p.oid=t.tgfoid
 JOIN pg_namespace pn ON pn.oid=p.pronamespace
 JOIN (VALUES %s) AS v(tbl,trg,fn,typ,digest,trgdigest) ON c.relname=v.tbl AND t.tgname=v.trg
 AND p.proname=v.fn AND t.tgtype::text=v.typ
 AND encode(sha256(convert_to(pg_get_functiondef(p.oid),'UTF8')),'hex')=v.digest
 AND encode(sha256(convert_to(pg_get_triggerdef(t.oid),'UTF8')),'hex')=v.trgdigest
 WHERE n.nspname='public' AND pn.nspname='public' AND t.tgenabled='O'
 AND NOT t.tgisinternal AND NOT p.prosecdef AND p.proconfig IS NULL
 AND pg_get_function_identity_arguments(p.oid)='')<>5
 THEN RAISE EXCEPTION 'policies_changed'; END IF;""" % ','.join(entries)


def model_guard(exp,active):
    expected=copy.deepcopy(exp); expected.update(status='active' if active else 'inactive',is_default=active)
    # Reuse the already qualified projection, not plaintext/cipher values.
    select=stage.READ_SQL.split("SELECT json_build_object",1)[1].rsplit(';',2)[0]
    select='SELECT json_build_object'+select
    return "IF (SELECT s.r->'model' FROM ("+select+") AS s(r))::jsonb IS DISTINCT FROM "+literal(
        json.dumps(expected,separators=(',',':')))+"::jsonb THEN RAISE EXCEPTION 'staged_model_changed'; END IF;"


def activation_sql(before,refs,exp,document_catalog):
    after=policy_plan(before); target=ref_target(refs)
    need(exp.get('id')==NEW and exp.get('status')=='inactive' and exp.get('is_default') is False
        and exp.get('parameters')==stage.PARAMETERS and exp.get('native_encrypted') is True,'staged_projection')
    # Snapshot all target non-VLM fields under table write locks and compare to
    # the just-saved host preimage. A concurrent user edit aborts the transaction.
    # No connection or model call is performed while these locks are held.
    sql="""BEGIN;
SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
SET LOCAL idle_in_transaction_session_timeout='10s'; SET LOCAL standard_conforming_strings=on;
LOCK TABLE public.models,public.knowledge_bases,public.custom_agents,public.knowledges IN SHARE ROW EXCLUSIVE MODE;
DO $guard$ BEGIN
 IF NOT pg_try_advisory_xact_lock(20261017,5601) THEN RAISE EXCEPTION 'release_busy'; END IF;
 IF (SELECT count(*) FROM public.schema_migrations)<>1 OR NOT EXISTS
 (SELECT 1 FROM public.schema_migrations WHERE version=90 AND dirty=false) THEN RAISE EXCEPTION 'schema_changed'; END IF;
"""+policies_guard(before)+'\n'+model_guard(exp,False)+"\n IF ("+REFS_SELECT+") IS DISTINCT FROM "+literal(json.dumps(refs))+"::jsonb THEN RAISE EXCEPTION 'references_changed'; END IF;\n"
    sql+=""" IF (SELECT count(*) FROM public.models WHERE type='VLLM' AND is_default AND deleted_at IS NULL)<>1
 OR NOT EXISTS (SELECT 1 FROM public.models WHERE id='deerflow-dashscope-vlm'
 AND type='VLLM' AND is_default AND status='active' AND deleted_at IS NULL)
 THEN RAISE EXCEPTION 'legacy_default_changed'; END IF;
END $guard$;
CREATE TEMP TABLE release_models ON COMMIT DROP AS SELECT id,to_jsonb(m)-'is_default'-'status'-'updated_at' AS untouched
 FROM public.models m WHERE id IN ('deerflow-dashscope-vlm','deerflow-cnooc-glm-5.3-flash');
"""
    for a,b in zip(before,after):
        if a['definition']!=b['definition']: sql+=b['definition']+';\n'
    # Close stale uploads in the same COMMIT as activating GLM, before batching
    # existing documents. Only the old model-id leaf is normalized on writes.
    sql+=documents.install_sql(document_catalog)
    sql+="""UPDATE public.models SET is_default=false,updated_at=CURRENT_TIMESTAMP WHERE id='deerflow-dashscope-vlm';
UPDATE public.models SET is_default=true,status='active',updated_at=CURRENT_TIMESTAMP WHERE id='deerflow-cnooc-glm-5.3-flash';
UPDATE public.knowledge_bases SET
 vlm_config=CASE WHEN vlm_config->>'model_id'='deerflow-dashscope-vlm' THEN jsonb_set(vlm_config,'{model_id}',to_jsonb('deerflow-cnooc-glm-5.3-flash'::text)) ELSE vlm_config END,
 image_processing_config=CASE WHEN image_processing_config->>'model_id'='deerflow-dashscope-vlm' THEN jsonb_set(image_processing_config,'{model_id}',to_jsonb('deerflow-cnooc-glm-5.3-flash'::text)) ELSE image_processing_config END,
 updated_at=CURRENT_TIMESTAMP WHERE deleted_at IS NULL AND
 (vlm_config->>'model_id'='deerflow-dashscope-vlm' OR image_processing_config->>'model_id'='deerflow-dashscope-vlm');
UPDATE public.custom_agents SET config=jsonb_set(config,'{vlm_model_id}',to_jsonb('deerflow-cnooc-glm-5.3-flash'::text)),
 updated_at=CURRENT_TIMESTAMP WHERE deleted_at IS NULL AND config->>'vlm_model_id'='deerflow-dashscope-vlm';
DO $after$ BEGIN
"""+policies_guard(after)+'\n'+model_guard(exp,True)+"\n IF ("+REFS_SELECT+")<>'[]'::jsonb THEN RAISE EXCEPTION 'legacy_references_remain'; END IF;\n"
    # Replace only the WHERE selection (not the projection) for exact targeted
    # rows, retaining original composite keys and other-field digests.
    selected=target_select(refs)
    sql+=" IF ("+selected+") IS DISTINCT FROM "+literal(json.dumps(target))+"::jsonb THEN RAISE EXCEPTION 'nonvision_fields_changed'; END IF;\n"
    sql+=documents.catalog_guard(documents.installed_catalog(document_catalog))+'\n'
    sql+=""" IF EXISTS (SELECT 1 FROM release_models b JOIN public.models m USING(id)
 WHERE to_jsonb(m)-'is_default'-'status'-'updated_at' IS DISTINCT FROM b.untouched)
 OR NOT EXISTS (SELECT 1 FROM public.models WHERE id='deerflow-dashscope-vlm' AND NOT is_default AND status='active' AND deleted_at IS NULL)
 THEN RAISE EXCEPTION 'legacy_model_or_credentials_changed'; END IF;
END $after$;
COMMIT;
SELECT json_build_object('glm_default_commit',true);
"""
    return sql


def target_select(refs):
    ref_target(refs)
    query=REFS_SELECT
    for kind,where in [('kb',"deleted_at IS NULL AND\n   (vlm_config->>'model_id'='deerflow-dashscope-vlm' OR image_processing_config->>'model_id'='deerflow-dashscope-vlm')"),
                       ('agent',"deleted_at IS NULL AND config->>'vlm_model_id'='deerflow-dashscope-vlm'")]:
        keys=[r for r in refs if r['kind']==kind]
        condition='false' if not keys else '(id,tenant_id) IN ('+','.join('('+literal(r['id'])+','+str(r['tenant'])+')' for r in keys)+')'
        need(query.count(where)==1,'reference_query_shape')
        query=query.replace(where,condition,1)
    return query
