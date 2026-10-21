"""Bounded, content-free document reference migration SQL and recovery checks.

Only the exact legacy VLM model-id leaf is changed. A narrow write normalizer
closes stale-upload races. No parse states, queue entries or vectors are touched.
This module never connects to a database itself.
"""
import json
import re

OLD='deerflow-dashscope-vlm'
NEW='deerflow-cnooc-glm-5.3-flash'
PATH='{process_overrides,vlm_config,model_id}'
FUNCTION='deerflow_document_glm_reference_20261017_r1'
TRIGGER='deerflow_document_glm_reference_before_write'
BATCH_SIZE=500
MAX_DOCUMENTS=50000


def need(value,code):
    if not value: raise RuntimeError(code)


def literal(value):
    need(isinstance(value,str) and '\x00' not in value,'invalid_sql_value')
    return "'"+value.replace("'","''")+"'"


def readonly(select):
    return "BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; SET LOCAL standard_conforming_strings=on;\n"+select+'; ROLLBACK;'


FUNCTION_SQL="""CREATE FUNCTION public.deerflow_document_glm_reference_20261017_r1()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path=pg_catalog AS $document_glm$
BEGIN
 IF NEW.deleted_at IS NULL
    AND NEW.metadata#>>'{process_overrides,vlm_config,model_id}'='deerflow-dashscope-vlm'
    AND coalesce(NEW.metadata#>>'{process_overrides,vlm_config,base_url}','')='' THEN
   NEW.metadata := jsonb_set(NEW.metadata,'{process_overrides,vlm_config,model_id}',
                            to_jsonb('deerflow-cnooc-glm-5.3-flash'::text),false);
 END IF;
 RETURN NEW;
END
$document_glm$;"""
TRIGGER_SQL="""CREATE TRIGGER deerflow_document_glm_reference_before_write
BEFORE INSERT OR UPDATE OF metadata,deleted_at ON public.knowledges
FOR EACH ROW EXECUTE FUNCTION public.deerflow_document_glm_reference_20261017_r1();"""
TRIGGER_DEFINITION=('CREATE TRIGGER '+TRIGGER+' BEFORE INSERT OR UPDATE OF metadata, deleted_at '
                    'ON public.knowledges FOR EACH ROW EXECUTE FUNCTION '+FUNCTION+'()')
FUNCTION_BODY=FUNCTION_SQL.split('$document_glm$')[1]

CATALOG_SELECT="""SELECT jsonb_build_object(
 'columns',(SELECT jsonb_object_agg(a.attname,t.typname) FROM pg_attribute a
 JOIN pg_type t ON t.oid=a.atttypid WHERE a.attrelid='public.knowledges'::regclass
 AND a.attnum>0 AND NOT a.attisdropped AND a.attname IN ('id','tenant_id','metadata','updated_at','deleted_at')),
 'ordinary_table',(SELECT relkind='r' AND NOT relrowsecurity AND NOT relforcerowsecurity
 FROM pg_class WHERE oid='public.knowledges'::regclass),
 'id_unique',(SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid
 AND a.attname='id' WHERE i.indrelid='public.knowledges'::regclass AND i.indisunique
 AND i.indisvalid AND i.indisready AND i.indnkeyatts=1 AND i.indpred IS NULL
 AND i.indexprs IS NULL AND a.attnum=ANY(i.indkey))),
 'triggers',coalesce((SELECT jsonb_agg(jsonb_build_object('name',t.tgname,'enabled',t.tgenabled,
 'definition',pg_get_triggerdef(t.oid),'function',p.proname,'namespace',pn.nspname,
 'body',p.prosrc,'security_definer',p.prosecdef,'settings',p.proconfig,
 'arguments',pg_get_function_identity_arguments(p.oid)) ORDER BY t.tgname)
 FROM pg_trigger t JOIN pg_proc p ON p.oid=t.tgfoid JOIN pg_namespace pn ON pn.oid=p.pronamespace
 WHERE t.tgrelid='public.knowledges'::regclass AND NOT t.tgisinternal),'[]'::jsonb),
 'function_count',(SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public' AND p.proname='deerflow_document_glm_reference_20261017_r1'))"""
CATALOG_SQL=readonly(CATALOG_SELECT)
COUNTS_SELECT="""SELECT jsonb_build_object('legacy',count(*) FILTER
 (WHERE metadata#>>'{process_overrides,vlm_config,model_id}'='deerflow-dashscope-vlm'),
 'glm',count(*) FILTER (WHERE metadata#>>'{process_overrides,vlm_config,model_id}'='deerflow-cnooc-glm-5.3-flash'),
 'inline',count(*) FILTER (WHERE coalesce(metadata#>>'{process_overrides,vlm_config,base_url}','')<>''))
 FROM public.knowledges WHERE deleted_at IS NULL"""
COUNTS_SQL=readonly(COUNTS_SELECT)


def check_catalog(value,installed):
    need(value.get('columns')==dict(id='varchar',tenant_id='int4',metadata='jsonb',
        updated_at='timestamptz',deleted_at='timestamptz'),'document_schema_changed')
    need(value.get('ordinary_table') is True and value.get('id_unique') is True,'document_table_contract')
    if not installed:
        need(value.get('triggers')==[] and value.get('function_count')==0,'document_policy_already_present_or_unknown')
    else:
        need(value.get('function_count')==1 and value.get('triggers')==[dict(name=TRIGGER,enabled='O',
            definition=TRIGGER_DEFINITION,function=FUNCTION,namespace='public',body=FUNCTION_BODY,
            security_definer=False,settings=['search_path=pg_catalog'],arguments='')],'document_guard_drift')
    return value


def catalog_guard(expected):
    return "IF ("+CATALOG_SELECT+") IS DISTINCT FROM "+literal(json.dumps(expected))+"::jsonb THEN RAISE EXCEPTION 'document_catalog_changed'; END IF;"


def installed_catalog(before):
    check_catalog(before,False)
    result=dict(before,function_count=1,triggers=[dict(name=TRIGGER,enabled='O',definition=TRIGGER_DEFINITION,
        function=FUNCTION,namespace='public',body=FUNCTION_BODY,security_definer=False,
        settings=['search_path=pg_catalog'],arguments='')])
    return check_catalog(result,True)


def install_sql(before):
    check_catalog(before,False)
    return 'DO $document_before$ BEGIN '+catalog_guard(before)+"\nIF ("+COUNTS_SELECT+")->>'inline'<>'0' THEN RAISE EXCEPTION 'inline_document_override'; END IF; END $document_before$;\n"+FUNCTION_SQL+'\n'+TRIGGER_SQL+'\n'


META_SHA="encode(sha256(convert_to(metadata::text,'UTF8')),'hex')"
TARGET_SHA="encode(sha256(convert_to(jsonb_set(metadata,'"+PATH+"',to_jsonb('"+NEW+"'::text),false)::text,'UTF8')),'hex')"
LEAF="metadata#>>'"+PATH+"'"
PROJECTION="jsonb_build_object('id',id,'tenant',tenant_id,'before_sha',"+META_SHA+",'after_sha',"+TARGET_SHA+")"
NEXT_SELECT="SELECT coalesce(jsonb_agg(x ORDER BY x->>'id'),'[]'::jsonb) FROM (SELECT "+PROJECTION+" x FROM public.knowledges WHERE deleted_at IS NULL AND "+LEAF+"='"+OLD+"' AND coalesce(metadata#>>'{process_overrides,vlm_config,base_url}','')='' ORDER BY id LIMIT "+str(BATCH_SIZE)+") selected"
NEXT_SQL=readonly(NEXT_SELECT)


def validate_batch(rows):
    need(isinstance(rows,list) and 0<len(rows)<=BATCH_SIZE,'document_batch_bound')
    seen=set()
    for row in rows:
        need(set(row)=={'id','tenant','before_sha','after_sha'},'document_batch_fields')
        need(isinstance(row['id'],str) and re.fullmatch('[A-Za-z0-9_.:-]{1,64}',row['id'])
             and type(row['tenant']) is int and row['tenant']>0,'document_batch_identity')
        need(row['id'] not in seen,'duplicate_document'); seen.add(row['id'])
        need(all(re.fullmatch('[a-f0-9]{64}',row[k]) for k in ('before_sha','after_sha'))
             and row['before_sha']!=row['after_sha'],'document_batch_hash')
    return rows


def values(rows):
    validate_batch(rows)
    return ','.join('('+','.join((literal(x['id']),str(x['tenant']),literal(x['before_sha']),literal(x['after_sha'])))+')' for x in rows)


def state_sql(rows):
    pairs=values(rows)
    return readonly("SELECT jsonb_build_object('rows',count(*),'before',count(*) FILTER (WHERE k.deleted_at IS NULL AND "+LEAF+"='"+OLD+"' AND "+META_SHA+"=v.before_sha),'after',count(*) FILTER (WHERE k.deleted_at IS NULL AND "+LEAF+"='"+NEW+"' AND "+META_SHA+"=v.after_sha)) FROM (VALUES "+pairs+") AS v(id,tenant,before_sha,after_sha) LEFT JOIN public.knowledges k ON k.id=v.id AND k.tenant_id=v.tenant")


def classify_state(value,rows):
    validate_batch(rows); n=len(rows)
    need(value.get('rows')==n,'document_reconciliation_count')
    if value==dict(rows=n,before=n,after=0): return 'BEFORE'
    if value==dict(rows=n,before=0,after=n): return 'AFTER'
    raise RuntimeError('document_batch_mixed_or_concurrent_edit_REVIEW')


def batch_sql(rows,catalog):
    check_catalog(catalog,True); pairs=values(rows); n=len(rows)
    # Locks only the current <=500 rows, never all 23,964 documents. NOWAIT and
    # short statement deadlines fail without waiting behind a live document edit.
    return ("""BEGIN; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
SET LOCAL idle_in_transaction_session_timeout='10s'; SET LOCAL standard_conforming_strings=on;
DO $g$ BEGIN
 IF NOT pg_try_advisory_xact_lock(20261017,5601) THEN RAISE EXCEPTION 'release_busy'; END IF;
"""+catalog_guard(catalog)+"""
 IF NOT EXISTS(SELECT 1 FROM public.models WHERE id='deerflow-cnooc-glm-5.3-flash'
 AND status='active' AND is_default AND deleted_at IS NULL)
 THEN RAISE EXCEPTION 'glm_not_active'; END IF;
END $g$;
CREATE TEMP TABLE release_doc_batch(id text PRIMARY KEY,tenant integer,before_sha text,after_sha text) ON COMMIT DROP;
INSERT INTO release_doc_batch VALUES """+pairs+""";
DO $batch$ DECLARE n integer; BEGIN
 PERFORM 1 FROM public.knowledges k JOIN release_doc_batch v ON k.id=v.id AND k.tenant_id=v.tenant
 ORDER BY k.id FOR UPDATE OF k NOWAIT;
 IF (SELECT count(*) FROM public.knowledges k JOIN release_doc_batch v ON k.id=v.id AND k.tenant_id=v.tenant
 WHERE k.deleted_at IS NULL AND """+LEAF+"='"+OLD+"' AND "+META_SHA+"=v.before_sha)<>"+str(n)+"""
 THEN RAISE EXCEPTION 'document_batch_preimage_changed'; END IF;
 UPDATE public.knowledges k SET metadata=jsonb_set(metadata,'"""+PATH+"',to_jsonb('"+NEW+"'::text),false) FROM release_doc_batch v WHERE k.id=v.id AND k.tenant_id=v.tenant;\n"
    +" GET DIAGNOSTICS n = ROW_COUNT; IF n<>"+str(n)+" THEN RAISE EXCEPTION 'document_batch_update_count'; END IF;\n"
    +" IF (SELECT count(*) FROM public.knowledges k JOIN release_doc_batch v ON k.id=v.id AND k.tenant_id=v.tenant WHERE "+META_SHA+"=v.after_sha)<>"+str(n)+" THEN RAISE EXCEPTION 'document_batch_postimage_changed'; END IF;\n"
    +"END $batch$; COMMIT; SELECT jsonb_build_object('document_batch_committed',"+str(n)+");\n")


def rollback_batch_sql(rows,uninstalled_catalog):
    """Offline recovery-plan SQL only; never called by the activation runner.

    The normalizer must already be removed through a separately reviewed
    default/config rollback. A changed metadata value is NOT overwritten.
    """
    check_catalog(uninstalled_catalog,False); pairs=values(rows); n=len(rows)
    return ("""BEGIN; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
SET LOCAL standard_conforming_strings=on; SET LOCAL idle_in_transaction_session_timeout='10s';
DO $g$ BEGIN IF NOT pg_try_advisory_xact_lock(20261017,5601) THEN RAISE EXCEPTION 'release_busy'; END IF;
"""+catalog_guard(uninstalled_catalog)+""" END $g$;
CREATE TEMP TABLE rollback_doc_batch(id text PRIMARY KEY,tenant integer,before_sha text,after_sha text) ON COMMIT DROP;
INSERT INTO rollback_doc_batch VALUES """+pairs+""";
DO $r$ BEGIN
 PERFORM 1 FROM public.knowledges k JOIN rollback_doc_batch v ON k.id=v.id AND k.tenant_id=v.tenant ORDER BY k.id FOR UPDATE OF k NOWAIT;
 IF (SELECT count(*) FROM public.knowledges k JOIN rollback_doc_batch v ON k.id=v.id AND k.tenant_id=v.tenant
 WHERE k.deleted_at IS NULL AND """+LEAF+"='"+NEW+"' AND "+META_SHA+"=v.after_sha)<>"+str(n)+" THEN RAISE EXCEPTION 'rollback_document_changed'; END IF;\n"
    +" UPDATE public.knowledges k SET metadata=jsonb_set(metadata,'"+PATH+"',to_jsonb('"+OLD+"'::text),false) FROM rollback_doc_batch v WHERE k.id=v.id AND k.tenant_id=v.tenant;\n"
    +" IF (SELECT count(*) FROM public.knowledges k JOIN rollback_doc_batch v ON k.id=v.id AND k.tenant_id=v.tenant WHERE "+META_SHA+"=v.before_sha)<>"+str(n)+" THEN RAISE EXCEPTION 'rollback_document_postimage'; END IF;\n"
    +" END $r$; COMMIT; SELECT jsonb_build_object('document_batch_restored',"+str(n)+");\n")
