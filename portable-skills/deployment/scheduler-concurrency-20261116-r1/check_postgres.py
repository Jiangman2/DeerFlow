"""Run the real queue budget race over an isolated offline PostgreSQL socket."""
import json
from pathlib import Path
import subprocess
import time
import uuid

ROOT=Path(__file__).resolve().parents[3]
DOCKER='C:/Users/15285/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
IMAGE='deerflow.local/deer-flow-gateway@sha256:0255237caedba428d7ceafd584e171a6d23739f07fc7a566bce016f28d543c5e'
OP=uuid.uuid4().hex
NAME='codex-scheduler32-pg-'+OP[:12]
VOLUME=NAME+'-socket'
LABEL='codex.scheduler32-local-check'


def run(args,timeout=30):
    result=subprocess.run([DOCKER]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
    if result.returncode:
        raise RuntimeError('Isolated Docker check failed: '+result.stderr.decode('utf-8','replace')[-2500:])
    return result.stdout.decode('utf-8')


def main():
    volume=False
    container=False
    try:
        run(['volume','create','--label',LABEL+'='+OP,VOLUME]);volume=True
        run(['run','-d','--name',NAME,'--label',LABEL+'='+OP,'--pull','never','--network','none',
             '--tmpfs','/var/lib/postgresql/data:rw,size=384m','--mount','type=volume,source='+VOLUME+',target=/var/run/postgresql',
             '-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:16-alpine']);container=True
        deadline=time.monotonic()+30
        while True:
            ready=subprocess.run([DOCKER,'exec',NAME,'pg_isready','-q','-U','postgres'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
            if ready.returncode==0:break
            assert time.monotonic()<deadline,'Isolated local PostgreSQL startup deadline'
            time.sleep(.3)
        print(json.dumps({'phase':'OFFLINE_POSTGRESQL_40_CLAIMANTS_GLOBAL_LIMIT_32'}),flush=True)
        output=run(['run','--rm','--pull','never','--platform','linux/arm64','--network','none','--read-only',
                    '--tmpfs','/tmp:rw,size=128m','--mount','type=volume,source='+VOLUME+',target=/scheduler-pg',
                    '--mount','type=bind,source='+str(ROOT)+',target=/repo,readonly','--workdir','/repo',
                    '--entrypoint','/app/backend/.venv/bin/python',IMAGE,'-I','-B',
                    '/repo/portable-skills/deployment/scheduler-concurrency-20261116-r1/check_global_budget.py',
                    '--postgres-socket','/scheduler-pg'],timeout=90)
        row=json.loads(output.splitlines()[-1])
        assert row['postgresql_race_exercised'] and row['global_executing_total']==32
        print(json.dumps(row,sort_keys=True))
    finally:
        if container:
            labels=json.loads(run(['inspect',NAME,'--format','{{json .Config.Labels}}']))
            assert labels.get(LABEL)==OP,'Refuse to remove a container with a different owner label'
            run(['rm','-f',NAME])
        if volume:
            labels=json.loads(run(['volume','inspect',VOLUME,'--format','{{json .Labels}}']))
            assert labels.get(LABEL)==OP,'Refuse to remove a volume with a different owner label'
            run(['volume','rm',VOLUME])


if __name__=='__main__':main()
