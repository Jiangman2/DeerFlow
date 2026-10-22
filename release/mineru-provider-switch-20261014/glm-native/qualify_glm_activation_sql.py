"""Exercise exact activation and five pinned triggers in disposable PG16."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import glm_activation_plan as r
import activate_glm56 as app
import qualify_model_stage_sql as prior
import glm_document_migration as docs

SCHEMA=prior.SCHEMA+"""
ALTER TABLE models ADD COLUMN updated_at timestamptz;
ALTER TABLE knowledge_bases ADD COLUMN tenant_id bigint DEFAULT 10000;
ALTER TABLE knowledge_bases ADD COLUMN updated_at timestamptz;
ALTER TABLE knowledge_bases ADD COLUMN summary_model_id text;
ALTER TABLE knowledge_bases ADD COLUMN embedding_model_id text;
ALTER TABLE knowledge_bases ADD COLUMN asr_config jsonb;
ALTER TABLE knowledge_bases ADD COLUMN wiki_config jsonb;
ALTER TABLE knowledge_bases ADD COLUMN extract_config jsonb;
ALTER TABLE knowledge_bases ADD COLUMN chunking_config jsonb;
ALTER TABLE knowledge_bases ADD COLUMN is_temporary boolean DEFAULT false;
ALTER TABLE custom_agents ADD COLUMN tenant_id bigint DEFAULT 10000;
ALTER TABLE custom_agents ADD COLUMN updated_at timestamptz;
ALTER TABLE custom_agents DROP CONSTRAINT custom_agents_pkey;
ALTER TABLE custom_agents ADD PRIMARY KEY(id,tenant_id);
CREATE TABLE knowledges(id varchar(36) PRIMARY KEY,tenant_id integer NOT NULL DEFAULT 10000,
 metadata jsonb,updated_at timestamptz,deleted_at timestamptz,parse_status text DEFAULT 'completed',payload text);
INSERT INTO custom_agents(id,tenant_id,config) VALUES
 ('builtin-quick-answer',10000,'{"vlm_model_id":"deerflow-dashscope-vlm","image_upload_enabled":false,"model_id":"CUSTOM-KEEP"}'),
 ('builtin-quick-answer',10001,'{"vlm_model_id":"deerflow-dashscope-vlm","temperature":0.2}'),
 ('unrelated',10000,'{"vlm_model_id":"OTHER-MODEL","flag":false}');
INSERT INTO knowledge_bases(id,vlm_config,image_processing_config) VALUES
 ('other','{"model_id":"OTHER-MODEL","enabled":false}','{}'),
 ('disabled','{"model_id":"deerflow-dashscope-vlm","enabled":false,"temperature":0.7}','{"model_id":"OTHER-MODEL"}');
"""


def policy_source():
    value=prior.policy_source()
    for (_,fn),name in r.TRIGGERS.items(): value=value.replace('CREATE TRIGGER test_'+fn+' ', 'CREATE TRIGGER '+name+' ')
    return value


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True,type=Path); args=parser.parse_args()
    if args.output.exists(): raise RuntimeError('proof_exists')
    cid=None
    def cmd(argv,data=None,check=True):
        out=subprocess.run(argv,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=35)
        if check and out.returncode: raise RuntimeError('synthetic_test_sql_error:'+out.stderr.decode()[-1800:])
        return out
    try:
        cid=cmd(['docker','run','-d','--rm','--pull=never','--network=none','--cpus=1','--memory=256m',
            '--pids-limit=128','--tmpfs','/var/lib/postgresql/data:rw,size=128m',
            '--name','codex-glm-activate-'+uuid.uuid4().hex[:12],'-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:16-alpine']).stdout.decode().strip()
        assert re.fullmatch('[a-f0-9]{64}',cid)
        for _ in range(30):
            if cmd(['docker','exec',cid,'pg_isready','-U','postgres'],check=False).returncode==0: break
            time.sleep(1)
        else: raise RuntimeError('pg_not_ready')
        argv=['docker','exec','-i',cid,'psql','-X','-qAt','-U','postgres','-v','ON_ERROR_STOP=1']
        def sql(text,check=True): return cmd(argv,text.encode(),check)
        sql(SCHEMA+policy_source())
        sql(r.stage.insert_sql(prior.CIPHER))
        rows=json.loads(sql(r.POLICY_SQL).stdout)['policies']
        expected=r.stage.expected(prior.CIPHER)
        refs=json.loads(sql(r.REFS_SQL).stdout)
        catalog=docs.check_catalog(json.loads(sql(docs.CATALOG_SQL).stdout),False)
        assert len(refs)==5
        assert json.loads(sql(app.OVERRIDE_SQL).stdout)=={'legacy_qwen_override':0,'inline_vlm_override':0}
        statement=r.activation_sql(rows,refs,expected,catalog)
        # Compare-and-swap catches an otherwise unrelated user edit and rolls
        # back every function/default/reference update.
        sql("UPDATE custom_agents SET config=config||'{\"temperature\":0.4}' WHERE id='agent';")
        assert sql(statement,False).returncode!=0
        assert json.loads(sql(r.POLICY_SQL).stdout)['policies']==rows
        assert r.stage.projection(json.loads(sql(r.stage.READ_SQL).stdout))==expected
        refs=json.loads(sql(r.REFS_SQL).stdout)
        # A hidden WHEN clause must fail even if the function bytes match.
        trigger=rows[0]
        sql('DROP TRIGGER '+trigger['trigger_name']+' ON public.'+trigger['table_name']+';'+
            trigger['trigger_definition'].replace(' EXECUTE FUNCTION',' WHEN (false) EXECUTE FUNCTION')+';')
        statement=r.activation_sql(rows,refs,expected,catalog)
        assert sql(statement,False).returncode!=0
        sql('DROP TRIGGER '+trigger['trigger_name']+' ON public.'+trigger['table_name']+';'+trigger['trigger_definition']+';')
        # 23,964 existing overrides including data outside the model-id leaf.
        sql("INSERT INTO knowledges(id,metadata,updated_at,payload) SELECT 'doc-'||lpad(i::text,8,'0'),"
            "jsonb_build_object('keep',i,'process_overrides',jsonb_build_object('vlm_config',"
            "jsonb_build_object('model_id','deerflow-dashscope-vlm','enabled',false,'prompt','SYNTHETIC-PRESERVE'))),"
            "'2026-10-17T00:00:00Z'::timestamptz,'SYNTHETIC-DOCUMENT' FROM generate_series(1,23964) i;")
        assert json.loads(sql(statement).stdout)=={'glm_default_commit':True}
        assert json.loads(sql(r.REFS_SQL).stdout)==[]
        actual=json.loads(sql('SELECT ('+r.target_select(refs)+');').stdout)
        assert actual==r.ref_target(refs)
        after=json.loads(sql(r.POLICY_SQL).stdout)['policies']
        assert after==r.policy_plan(rows)
        assert json.loads(sql(app.LEGACY_SQL).stdout)=={'legacy_active':True,'default_vision_ids':[r.NEW]}
        assert sql(statement,False).returncode!=0  # no duplicate activation
        # Gateway-style old-row upsert must not restore a second default.
        sql("UPDATE models SET is_default=true,status='active' WHERE id='deerflow-dashscope-vlm';")
        assert json.loads(sql(app.LEGACY_SQL).stdout)['default_vision_ids']==[r.NEW]
        sql("INSERT INTO custom_agents(id,tenant_id,config) VALUES('late-agent',10000,'{\"vlm_model_id\":\"deerflow-dashscope-vlm\",\"image_upload_enabled\":false}');")
        assert sql("SELECT config->>'vlm_model_id' FROM custom_agents WHERE id='late-agent';").stdout.strip()==r.NEW.encode()
        assert sql("SELECT config->>'image_upload_enabled' FROM custom_agents WHERE id='late-agent';").stdout.strip()==b'false'
        assert sql("SELECT config->>'vlm_model_id' FROM custom_agents WHERE id='unrelated';").stdout.strip()==b'OTHER-MODEL'
        # Exercise actual INSERT defaults including the two unchanged parser policies.
        sql("INSERT INTO knowledge_bases(id) VALUES('new-kb');")
        assert sql("SELECT vlm_config->>'model_id' FROM knowledge_bases WHERE id='new-kb';").stdout.strip()==r.NEW.encode()
        assert sql("SELECT image_processing_config->>'model_id' FROM knowledge_bases WHERE id='new-kb';").stdout.strip()==r.NEW.encode()
        sql("INSERT INTO knowledges(id,metadata) VALUES('late','{\"process_overrides\":{\"vlm_config\":{\"model_id\":\"deerflow-dashscope-vlm\",\"enabled\":false}},\"keep\":7}');")
        assert sql("SELECT metadata#>>'{process_overrides,vlm_config,model_id}' FROM knowledges WHERE id='late';").stdout.strip()==r.NEW.encode()
        installed=docs.check_catalog(json.loads(sql(docs.CATALOG_SQL).stdout),True)
        assert installed==docs.installed_catalog(catalog)
        # Batches use real PG transactions. Simulate missing client commit ACK by
        # ignoring one successful response and reconciling only its hashes.
        migrated=0; batches=[]
        while True:
            batch=json.loads(sql(docs.NEXT_SQL).stdout)
            if not batch: break
            assert len(batch)<=500
            assert docs.classify_state(json.loads(sql(docs.state_sql(batch)).stdout),batch)=='BEFORE'
            result=json.loads(sql(docs.batch_sql(batch,installed)).stdout)
            assert result=={'document_batch_committed':len(batch)}
            assert docs.classify_state(json.loads(sql(docs.state_sql(batch)).stdout),batch)=='AFTER'
            if not batches: assert sql(docs.batch_sql(batch,installed),False).returncode!=0
            migrated+=len(batch); batches.append(batch)
        assert migrated==23964 and len(batches)==48
        assert json.loads(sql(docs.COUNTS_SQL).stdout)=={'legacy':0,'glm':23965,'inline':0}
        assert sql("SELECT count(*) FROM knowledges WHERE id LIKE 'doc-%' AND parse_status='completed' AND payload='SYNTHETIC-DOCUMENT' AND updated_at='2026-10-17T00:00:00Z' AND metadata#>>'{process_overrides,vlm_config,enabled}'='false';").stdout.strip()==b'23964'
        # Unrelated custom / inline models, deleted documents and null metadata
        # must not be normalized. A deletion undone with legacy config is.
        sql("INSERT INTO knowledges(id,metadata,deleted_at) VALUES ('custom','{\"process_overrides\":{\"vlm_config\":{\"model_id\":\"CUSTOM\"}}}',NULL),('inline','{\"process_overrides\":{\"vlm_config\":{\"model_id\":\"deerflow-dashscope-vlm\",\"base_url\":\"https://example.invalid\"}}}',NULL),('deleted','{\"process_overrides\":{\"vlm_config\":{\"model_id\":\"deerflow-dashscope-vlm\"}}}',CURRENT_TIMESTAMP),('null',NULL,NULL);")
        assert sql("SELECT metadata#>>'{process_overrides,vlm_config,model_id}' FROM knowledges WHERE id='custom';").stdout.strip()==b'CUSTOM'
        assert json.loads(sql(docs.COUNTS_SQL).stdout)['inline']==1
        sql("UPDATE knowledges SET deleted_at=NULL WHERE id='deleted';")
        assert sql("SELECT metadata#>>'{process_overrides,vlm_config,model_id}' FROM knowledges WHERE id='deleted';").stdout.strip()==r.NEW.encode()
        # Recovery SQL cannot overwrite a user's later metadata edit. No broad
        # rollback is executed: only these synthetic batches with exact hashes.
        sql('DROP TRIGGER '+docs.TRIGGER+' ON public.knowledges; DROP FUNCTION public.'+docs.FUNCTION+'();')
        sql("UPDATE knowledges SET metadata=metadata||'{\"user_edit\":true}' WHERE id="+r.literal(batches[0][0]['id'])+';')
        assert sql(docs.rollback_batch_sql(batches[0],catalog),False).returncode!=0
        assert json.loads(sql(docs.rollback_batch_sql(batches[1],catalog)).stdout)=={'document_batch_restored':500}
        assert docs.classify_state(json.loads(sql(docs.state_sql(batches[1])).stdout),batches[1])=='BEFORE'
        sql(docs.FUNCTION_SQL+docs.TRIGGER_SQL)
        assert json.loads(sql(docs.batch_sql(batches[1],installed)).stdout)=={'document_batch_committed':500}
        report=dict(glm_activation_postgres16='PASS',five_exact_predecessor_policies='PASS',three_policy_changes_only=True,
            nonvision_fields_preserved=True,composite_agent_key_test=True,late_qwen_upsert_demoted=True,
            concurrent_reference_edit_aborts=True,duplicate_activation_refused=True,
            original_parser_policies_preserved=True,new_kb_and_agent_defaults='PASS',document_override_inventory='PASS',
            document_migration=dict(rows=migrated,batches=len(batches),max_batch=500,nonvision_data_preserved=True,
                stale_upload_normalized=True,unknown_commit_reconciled_readonly=True,duplicate_batch_refused=True,
                drift_rollback_refused=True,exact_batch_rollback_and_reapply=True),
            complete_trigger_when_guard=True,
            no_production_connections=True,network='NONE',provider_requests=0,
            source_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                [Path(r.__file__),Path(app.__file__),Path(docs.__file__)]},
            policy_plan_sha256=hashlib.sha256(json.dumps(r.policy_plan(rows),sort_keys=True).encode()).hexdigest())
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report))
    finally:
        if cid and re.fullmatch('[a-f0-9]{64}',cid): cmd(['docker','rm','-f',cid]); print('synthetic_fixture_removed=true')


if __name__=='__main__': main()
