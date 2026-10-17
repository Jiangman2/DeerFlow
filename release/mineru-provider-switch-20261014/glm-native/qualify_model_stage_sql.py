"""Run the exact INSERT against five real pinned policy definitions on local PG16."""
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
import stage_glm_model as r

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
CIPHER='enc:v1:'+'A'*39
SCHEMA="""
CREATE TABLE schema_migrations(version bigint,dirty boolean); INSERT INTO schema_migrations VALUES(90,false);
CREATE TABLE tenants(id bigint PRIMARY KEY); INSERT INTO tenants VALUES(10000);
CREATE TABLE models(id varchar(64) PRIMARY KEY,tenant_id bigint,name text,display_name text,type text,source text,
 description text,parameters jsonb,is_default boolean,is_builtin boolean,managed_by text,status text,deleted_at timestamptz);
CREATE TABLE knowledge_bases(id text PRIMARY KEY,vlm_config jsonb,image_processing_config jsonb,deleted_at timestamptz);
CREATE TABLE custom_agents(id text PRIMARY KEY,config jsonb,deleted_at timestamptz);
INSERT INTO models VALUES('deerflow-dashscope-vlm',10000,'qwen3-vl-plus','Qwen','VLLM','remote','synthetic',
 '{"api_key":"SYNTHETIC-LEGACY-NOT-A-KEY"}',true,true,'','active',NULL);
INSERT INTO knowledge_bases VALUES('kb','{"model_id":"deerflow-dashscope-vlm"}','{}',NULL);
INSERT INTO custom_agents VALUES('agent','{"vlm_model_id":"deerflow-dashscope-vlm"}',NULL);
CREATE ROLE stage_writer NOLOGIN;
GRANT SELECT ON models,knowledge_bases,custom_agents,schema_migrations,tenants TO stage_writer;
GRANT INSERT ON models TO stage_writer;
"""


def policy_source():
    source=(REPO/'k8s/weknora/enterprise-model-feature-defaults.sql').read_text(encoding='utf-8')
    replacements={'CHAT':'deerflow-cnooc-deepseek-v4-flash','EMBEDDING':'deerflow-cnooc-qwen3-embedding-8b',
                  'RERANK':'deerflow-cnooc-qwen3-reranker-8b'}
    for key,value in replacements.items(): source=source.replace('__DEERFLOW_DEFAULT_'+key+'_MODEL_ID__',value)
    originals=[source,(REPO/'k8s/enterprise/weknora-mineru-defaults.sql').read_text(encoding='utf-8'),
        (REPO/'release/production-build-alignment-20260928/external/manage-weknora-spreadsheet-policy-datax-01.sh').read_text(encoding='utf-8')]
    output=[]
    for _,(table,name,body_hash,tgtype) in r.sw.POLICIES.items():
        found=[]
        for text in originals:
            text=text.replace('${FUNCTION_NAME}','weknora_document_stability_r2_spreadsheet_default').replace('\\$','$')
            m=re.search(r'CREATE OR REPLACE FUNCTION (?:public\.)?'+name+r'\(\).*?AS (\$[A-Za-z_]*\$)(.*?)\1;',text,re.S|re.I)
            if m: found.append(m)
        assert len(found)==1,name
        assert hashlib.sha256(found[0][2].encode()).hexdigest()==body_hash,name
        output.append(found[0][0])
        output.append('CREATE TRIGGER test_'+name+' BEFORE INSERT'+(' OR UPDATE' if tgtype==23 else '')+
            ' ON public.'+table+' FOR EACH ROW EXECUTE FUNCTION public.'+name+'();')
    return '\n'.join(output)


def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',required=True,type=Path); args=p.parse_args()
    if args.output.exists(): raise RuntimeError('qualification_exists')
    cid=None
    def cmd(argv,data=None,check=True):
        value=subprocess.run(argv,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if check and value.returncode: raise RuntimeError('synthetic_sql_failure:'+value.stderr.decode()[-1500:])
        return value
    try:
        cid=cmd(['docker','run','-d','--rm','--pull=never','--network=none','--cpus=1','--memory=256m',
            '--pids-limit=128','--tmpfs','/var/lib/postgresql/data:rw,size=128m','--name','codex-modelstage-'+uuid.uuid4().hex[:12],
            '-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:16-alpine']).stdout.decode().strip()
        assert re.fullmatch('[a-f0-9]{64}',cid)
        for _ in range(30):
            if cmd(['docker','exec',cid,'pg_isready','-U','postgres'],check=False).returncode==0: break
            time.sleep(1)
        else: raise RuntimeError('pg_not_ready')
        argv=['docker','exec','-i',cid,'psql','-X','-qAt','-U','postgres','-v','ON_ERROR_STOP=1']
        def sql(text,check=True): return cmd(argv,text.encode(),check)
        sql(SCHEMA+policy_source())
        initial=json.loads(sql(r.sw.activation.POLICY_SQL).stdout)
        r.sw.policy_check(initial['policies'])
        def snapshot(): return json.loads(sql('SET ROLE stage_writer;'+r.READ_SQL).stdout)
        assert r.projection(snapshot()) is None
        statement=r.insert_sql(CIPHER)
        assert json.loads(sql('SET ROLE stage_writer;'+statement).stdout)=={'model_insert_committed':True}
        assert r.projection(snapshot())==r.expected(CIPHER)
        assert CIPHER not in json.dumps(snapshot())
        assert sql('SET ROLE stage_writer;'+statement,False).returncode!=0
        assert r.projection(snapshot())==r.expected(CIPHER)
        final=json.loads(sql(r.sw.activation.POLICY_SQL).stdout)
        assert final['policies']==initial['policies'] and final['agent_refs']==initial['agent_refs']
        assert final['kb_multimodal_refs']==initial['kb_multimodal_refs']
        assert sql("SELECT is_default AND status='active' FROM models WHERE id='deerflow-dashscope-vlm'").stdout.strip()==b't'
        assert sql("SET ROLE stage_writer; UPDATE models SET status='active';",False).returncode!=0
        sql("DELETE FROM models WHERE id='deerflow-cnooc-glm-5.3-flash'; UPDATE schema_migrations SET version=91;")
        assert sql('SET ROLE stage_writer;'+statement,False).returncode!=0
        sql('UPDATE schema_migrations SET version=90; ALTER TABLE models DISABLE TRIGGER test_deerflow_apply_enterprise_model_defaults;')
        assert sql('SET ROLE stage_writer;'+statement,False).returncode!=0
        assert sql('SELECT count(*) FROM models;').stdout.strip()==b'1'
        report=dict(model_stage_postgres16='PASS',five_exact_policy_definitions='PASS',nondefault_inactive_insert='PASS',
            native_ciphertext_not_returned_by_status=True,duplicate_insert_refused=True,policy_drift_refused=True,
            schema_drift_refused=True,minimal_select_insert_role='PASS',production_connections=0,provider_requests=0,
            network='NONE',sql_sha256=hashlib.sha256(statement.encode()).hexdigest(),
            read_sql_sha256=hashlib.sha256(r.READ_SQL.encode()).hexdigest())
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8'); print(json.dumps(report))
    finally:
        if cid and re.fullmatch('[a-f0-9]{64}',cid):
            cmd(['docker','rm','-f',cid]); print('disposable_synthetic_sql_fixture_removed=true')


if __name__=='__main__': main()
