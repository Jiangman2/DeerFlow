"""Local disposable PostgreSQL16 qualification; no ports, network or host data."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import time
import uuid

from production_release_review import SQL,clean_models

SCHEMA="""
CREATE TABLE public.models(id varchar(64) PRIMARY KEY,type text,is_builtin boolean,managed_by text,name text,parameters jsonb,deleted_at timestamptz);
CREATE TABLE public.knowledge_bases(id text PRIMARY KEY,vlm_config jsonb,deleted_at timestamptz);
CREATE TABLE public.schema_migrations(version bigint,dirty boolean);
INSERT INTO public.schema_migrations VALUES(90,false);
INSERT INTO public.models VALUES('deerflow-dashscope-vlm','VLLM',true,'','qwen3-vl-plus','{"api_key":"SYNTHETIC_NOT_A_KEY","max_concurrency":64}',NULL);
INSERT INTO public.knowledge_bases VALUES('synthetic-only','{"enabled":true,"model_id":"deerflow-dashscope-vlm"}',NULL);
CREATE ROLE release_reader NOLOGIN;
GRANT SELECT ON public.models,public.knowledge_bases,public.schema_migrations TO release_reader;
"""


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',type=Path,required=True); args=parser.parse_args()
    if args.output.exists(): raise RuntimeError('qualification_exists')
    name='codex-weknora-release-sql-'+uuid.uuid4().hex[:12]; cid=None
    def command(argv,data=None,checked=True):
        r=subprocess.run(argv,input=data,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
        if checked and r.returncode: raise RuntimeError('local_postgres_command_failed')
        return r
    try:
        cid=command(['docker','run','-d','--rm','--pull=never','--network=none','--cpus=1','--memory=256m',
            '--pids-limit=128','--tmpfs','/var/lib/postgresql/data:rw,size=128m',
            '--label','codex.scope=weknora-release-sql-qualification','--name',name,
            '-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:16-alpine']).stdout.decode().strip()
        if not re.fullmatch('[a-f0-9]{64}',cid): raise RuntimeError('container_identity')
        for _ in range(30):
            if command(['docker','exec',cid,'pg_isready','-U','postgres'],checked=False).returncode==0: break
            time.sleep(1)
        else: raise RuntimeError('local_pg_readiness_timeout')
        argv=['docker','exec','-i',cid,'psql','-X','-qAt','-U','postgres','-v','ON_ERROR_STOP=1']
        command(argv,SCHEMA.encode())
        raw=command(argv,('SET ROLE release_reader;\n'+SQL).encode()).stdout
        if b'SYNTHETIC_NOT_A_KEY' in raw: raise RuntimeError('query_returned_secret_column')
        result=clean_models(json.loads(raw))
        if result['models'][0]['kb_count']!=1 or result['models'][0]['max_concurrency']!=64: raise RuntimeError('sql_projection_mismatch')
        denied=command(argv,b"SET ROLE release_reader; BEGIN READ ONLY; UPDATE public.models SET name='x';",checked=False)
        if denied.returncode==0: raise RuntimeError('sql_write_not_denied')
        receipt=dict(local_postgres16_sql='PASS',sql_read_only=True,minimal_select_role='PASS',write_rejected=True,
            network='NONE',production_connections=0,provider_requests=0,credentials_used='NONE_SYNTHETIC_ONLY')
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8'); print(json.dumps(receipt))
    finally:
        if cid and re.fullmatch('[a-f0-9]{64}',cid):
            # Only the new exact container created above; no image/volume prune.
            command(['docker','rm','-f',cid]); print('disposable_sql_fixture_removed=true')


if __name__=='__main__': main()
