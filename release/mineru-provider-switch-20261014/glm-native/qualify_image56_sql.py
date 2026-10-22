"""Real PostgreSQL16, isolated local synthetic fixture, no production connection."""
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
from switch_image56 import MODEL_SQL

SCHEMA="""
CREATE TABLE public.schema_migrations(version bigint,dirty boolean);
INSERT INTO schema_migrations VALUES(90,false);
CREATE TABLE public.models(id text PRIMARY KEY,name text,type text,source text,
 parameters json,is_default boolean,status text,is_builtin boolean,managed_by text,deleted_at timestamptz);
INSERT INTO models VALUES('deerflow-dashscope-vlm','qwen3-vl-plus','VLLM','remote',
 '{"base_url":"https://synthetic.invalid/v1","api_key":"NOT_A_REAL_KEY","max_concurrency":null}',
 false,'active',true,'synthetic',NULL);
CREATE ROLE release_reader NOLOGIN;
GRANT SELECT ON public.models,public.schema_migrations TO release_reader;
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
            '--name','codex-image56-sql-'+uuid.uuid4().hex[:12],
            '-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:16-alpine']).stdout.decode().strip()
        assert re.fullmatch('[a-f0-9]{64}',cid)
        for _ in range(30):
            if cmd(['docker','exec',cid,'pg_isready','-U','postgres'],check=False).returncode==0: break
            time.sleep(1)
        else: raise RuntimeError('local_pg_timeout')
        argv=['docker','exec','-i',cid,'psql','-X','-qAt','-U','postgres','-v','ON_ERROR_STOP=1']
        cmd(argv,SCHEMA.encode())
        def query(): return json.loads(cmd(argv,('SET ROLE release_reader;\n'+MODEL_SQL).encode()).stdout)
        initial=query()
        assert initial['read_only']=='on' and initial['schema']==[{'version':90,'dirty':False}]
        assert re.fullmatch('[a-f0-9]{32}',initial['vision_signature'])
        assert 'NOT_A_REAL_KEY' not in json.dumps(initial)
        cmd(argv,b"UPDATE models SET parameters=jsonb_set(parameters::jsonb,'{api_key}','\"ROTATED_SYNTHETIC\"'::jsonb);")
        assert query()==initial
        cmd(argv,b"UPDATE models SET parameters=jsonb_set(parameters::jsonb,'{base_url}','\"https://changed.invalid/v1\"'::jsonb);")
        assert query()['vision_signature']!=initial['vision_signature']
        assert cmd(argv,b'SET ROLE release_reader; BEGIN READ ONLY; DELETE FROM models;',check=False).returncode!=0
        result=dict(image56_postgres16_qualification='PASS',minimal_select_role='PASS',
            credentials_not_selected_or_hashed=True,contract_change_detected=True,write_rejected=True,
            exact_sql_sha256=hashlib.sha256(MODEL_SQL.encode()).hexdigest(),production_connections=0,network='NONE')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8'); print(json.dumps(result))
    finally:
        if cid and re.fullmatch('[a-f0-9]{64}',cid):
            cmd(['docker','rm','-f',cid]); print('disposable_synthetic_sql_fixture_removed=true')


if __name__=='__main__': main()
