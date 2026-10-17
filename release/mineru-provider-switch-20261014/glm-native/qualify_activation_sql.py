"""Exercise the exact readonly policy query on a fresh local PostgreSQL16."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid
from review_activation import POLICY_SQL, clean_policies

SCHEMA="""
CREATE TABLE public.models(id text PRIMARY KEY);
CREATE TABLE public.knowledge_bases(image_processing_config jsonb,deleted_at timestamptz);
CREATE TABLE public.custom_agents(id text PRIMARY KEY,config jsonb,deleted_at timestamptz);
INSERT INTO knowledge_bases VALUES('{"model_id":"deerflow-dashscope-vlm"}',NULL);
INSERT INTO custom_agents VALUES('builtin-synthetic','{"vlm_model_id":"deerflow-dashscope-vlm","image_upload_enabled":true}',NULL);
CREATE FUNCTION public.deerflow_apply_enterprise_knowledge_base_defaults() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.image_processing_config=jsonb_build_object('model_id','deerflow-dashscope-vlm');
RETURN NEW; END $$;
CREATE TRIGGER deerflow_enterprise_defaults_before_insert BEFORE INSERT ON public.knowledge_bases
FOR EACH ROW EXECUTE FUNCTION public.deerflow_apply_enterprise_knowledge_base_defaults();
CREATE ROLE release_reader NOLOGIN;
GRANT SELECT ON public.models,public.knowledge_bases,public.custom_agents TO release_reader;
"""


def main():
    p=argparse.ArgumentParser(); p.add_argument('--output',required=True,type=Path); args=p.parse_args()
    if args.output.exists(): raise RuntimeError('qualification_exists')
    cid=None
    def cmd(argv,data=None,check=True):
        r=subprocess.run(argv,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if check and r.returncode: raise RuntimeError('local_sql_command_failed')
        return r
    try:
        cid=cmd(['docker','run','-d','--rm','--pull=never','--network=none','--cpus=1','--memory=256m',
            '--pids-limit=128','--tmpfs','/var/lib/postgresql/data:rw,size=128m',
            '--label','codex.scope=weknora-activation-sql','--name','codex-activation-sql-'+uuid.uuid4().hex[:12],
            '-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:16-alpine']).stdout.decode().strip()
        assert re.fullmatch('[a-f0-9]{64}',cid)
        for _ in range(30):
            if cmd(['docker','exec',cid,'pg_isready','-U','postgres'],check=False).returncode==0: break
            time.sleep(1)
        else: raise RuntimeError('local_pg_timeout')
        argv=['docker','exec','-i',cid,'psql','-X','-qAt','-U','postgres','-v','ON_ERROR_STOP=1']
        cmd(argv,SCHEMA.encode())
        raw=cmd(argv,('SET ROLE release_reader;\n'+POLICY_SQL).encode()).stdout
        safe=clean_policies(json.loads(raw))
        assert len(safe['policies'])==1 and safe['policies'][0]['legacy_literal_count']==1
        assert safe['agent_refs']==[dict(model_class='LEGACY',total=1,builtin=1,image_enabled=1)]
        assert safe['kb_multimodal_refs']==dict(total=1,legacy=1,proposed=0,other=0)
        assert 'CREATE FUNCTION' not in json.dumps(safe) and safe['proposed_id_rows']==0
        denied=cmd(argv,b'SET ROLE release_reader; BEGIN READ ONLY; DELETE FROM public.models;',check=False)
        assert denied.returncode != 0
        report=dict(activation_postgres16_qualification='PASS',minimal_select_role='PASS',write_rejected=True,
            exact_sql_sha256=hashlib.sha256(POLICY_SQL.encode()).hexdigest(),network='NONE',
            production_connections=0,provider_requests=0,credentials_used='NONE_SYNTHETIC_ONLY')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report))
    finally:
        if cid and re.fullmatch('[a-f0-9]{64}',cid):
            cmd(['docker','rm','-f',cid]); print('disposable_sql_fixture_removed=true')


if __name__=='__main__': main()
