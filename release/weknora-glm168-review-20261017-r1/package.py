"""Qualify the exact single-file candidate review without production access."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

HERE=Path(__file__).resolve().parent
DOWNLOADS=Path('C:/Users/15285/Downloads')
LEGACY=Path('C:/Users/15285/.codex/visualizations/2026/09/24/01a0d2d7-0015-7a73-93c3-791b713a47f6/weknora-tune88-20261016-r1')
NAME='weknora-glm168-review-20261017-r1.py'
IMAGES=('python:3.7.17-slim-bullseye','python:3.12-slim-bookworm')


def run(command,input=None,check=True):
    result=subprocess.run(command,input=input,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=100)
    if check and result.returncode:
        sys.stdout.buffer.write(result.stdout);raise RuntimeError('local_qualification_failed')
    return result


def sql_test():
    import review as m
    spec=importlib.util.spec_from_file_location('frozen_sql_followup',DOWNLOADS/'weknora-pipeline-db-followup-20261017-r1-verified/weknora-pipeline-db-followup-20261017-r1.py')
    f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
    name='weknora168-sql-'+uuid.uuid4().hex[:12]
    container=run(['docker','run','-d','--name',name,'--network','none','--memory=384m','--cpus=1',
        '--tmpfs','/var/lib/postgresql/data:rw,size=256m','-e','POSTGRES_HOST_AUTH_METHOD=trust',
        'postgres:16-alpine','-c','shared_buffers=16MB','-c','max_connections=20']).stdout.decode().strip()
    if len(container)!=64 or any(x not in '0123456789abcdef' for x in container):raise RuntimeError('container_identity_invalid')
    def sql(query):
        return run(['docker','exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres'],input=query.encode()).stdout
    try:
        for _ in range(40):
            if run(['docker','exec',container,'pg_isready','-q','-U','postgres'],check=False).returncode==0:break
            time.sleep(.25)
        else:raise RuntimeError('postgres_not_ready')
        sql('''CREATE TABLE models(id text PRIMARY KEY,parameters jsonb,deleted_at timestamptz);
CREATE TABLE system_settings(key text PRIMARY KEY,value jsonb,is_secret boolean);
CREATE TABLE schema_migrations(version bigint,dirty boolean);
INSERT INTO schema_migrations VALUES(90,false);
INSERT INTO models VALUES('deerflow-cnooc-glm-5.3-flash','{"max_concurrency":0,"api_key":"SYNTHETIC_SECRET"}',NULL);
''')
        model=f.models(json.loads(sql(f.MODEL_SQL)));assert model['global_setting'] is None
        assert model['models'][0]['mode']=='INHERIT_GLOBAL'
        f.waits(json.loads(sql(f.WAIT_SQL)))
        extra=json.loads(sql(m.EXTRA_SQL));assert extra['read_only']=='on' and extra['schema']==[dict(version=90,dirty=False)] and extra['worker_settings']==[]
        sql("INSERT INTO system_settings VALUES('asynq.enrichment_concurrency','64',false);")
        extra=sql(m.EXTRA_SQL);assert b'SYNTHETIC_SECRET' not in extra
        assert json.loads(extra)['worker_settings']==['asynq.enrichment_concurrency']
        return dict(postgresql16_queries='PASS_REAL_ISOLATED_DATABASE',inherited_zero_and_absent_global='PASS',
            persisted_worker_override_detection='PASS',secret_projection='PASS')
    finally:
        run(['docker','rm','-f',container])  # Only the newly-created disposable test container.


def launcher(sha):
    return '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
SCRIPT=/home/cnooc/'''+NAME+'''
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-glm168-review-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/review.py"
test "$(sha256sum "$CONTROL/review.py" | awk '{print $1}')" = '''+sha+'''
echo "review_log=$CONTROL/review.log"
set +e
python3 -I -B -u "$CONTROL/review.py" --review 2>&1 | tee "$CONTROL/review.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "review_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "review_log=$CONTROL/review.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''


def main():
    dest=Path(sys.argv[1]);assert not dest.exists(),'fresh_delivery_directory_required'
    raw=(HERE/'review.py').read_bytes();ast.parse(raw,feature_version=(3,7))
    sha=hashlib.sha256(raw).hexdigest();proof=sql_test();dest.mkdir(parents=True)
    (dest/NAME).write_bytes(raw);(dest/'RUN-DATAX01.txt').write_bytes(launcher(sha).encode())
    (dest/'RUNBOOK.txt').write_bytes((HERE/'RUNBOOK.txt').read_bytes())
    rows=[]
    for image in IMAGES:
        base=['docker','run','--rm','--network','none','-v',str(HERE)+':/qa:ro','-v',str(dest)+':/delivery:ro',
            '-e','REVIEW=/delivery/'+NAME,'-w','/qa']
        tests=run(base+[image,'python','-B','-m','unittest','-v','test_review']).stdout
        assert b'Ran 32 tests' in tests and b'\nOK\n' in tests and b'skipped' not in tests
        filename='TESTS-'+image.split(':')[1].split('-')[0]+'.txt';(dest/filename).write_bytes(tests)
        fixture=run(base+['-v',str(DOWNLOADS)+':/downloads:ro','-v',str(LEGACY)+':/legacy:ro',
            image,'python','-B','qualify_fixture.py']).stdout
        (dest/('FROZEN-'+image.split(':')[1].split('-')[0]+'.txt')).write_bytes(fixture)
        rows.append(dict(runtime=image,unit_tests=32,skips=0,frozen_helper_bootstrap='PASS',env_only_dryrun_helper='PASS_MOCKED_API'))
        print(json.dumps(rows[-1]),flush=True)
        wrong=run(base+[image,'python','-I','-B','/delivery/'+NAME,'--review'],check=False)
        assert wrong.returncode==1 and b'root_datax01_required' in wrong.stdout
    run(['docker','run','--rm','-i','--network','none',IMAGES[0],'bash','-n'],input=launcher(sha).encode())
    proof.update(script=NAME,script_sha256=sha,tests=rows,python37_grammar='PASS',shell_syntax='PASS',
        production_execution='NOT_EXECUTED_LOCALLY',provider_submissions=0,production_writes=0,
        preferred_enrichment=128,preferred_total=168,alternative_total=136,ready_to_apply=False)
    (dest/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    (dest/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
        for p in sorted(dest.iterdir()) if p.is_file() and p.name!='SHA256SUMS'),encoding='ascii')
    print(json.dumps(proof))


if __name__=='__main__':main()
