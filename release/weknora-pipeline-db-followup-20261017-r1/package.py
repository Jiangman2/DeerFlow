"""Package and qualify the follow-up; local ephemeral PostgreSQL16 only."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import check as c

HERE=Path(__file__).resolve().parent
NAME='weknora-pipeline-db-followup-20261017-r1.py'


def run(command,input=None,check=True):
    result=subprocess.run(command,input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    if check and result.returncode:
        raise RuntimeError('local_qualification_failed_'+command[0])
    return result


def sql_test():
    name='weknora-db-followup-qa-'+uuid.uuid4().hex[:12]
    container=run(['docker','run','-d','--name',name,'--network','none','--memory=384m','--cpus=1',
        '--tmpfs','/var/lib/postgresql/data:rw,size=256m','-e','POSTGRES_HOST_AUTH_METHOD=trust',
        'postgres:16-alpine','-c','shared_buffers=16MB','-c','max_connections=20','-c','max_parallel_workers=2']).stdout.decode().strip()
    if len(container)!=64 or any(x not in '0123456789abcdef' for x in container):raise RuntimeError('container_identity_invalid')
    def sql(text,check=True):
        return run(['docker','exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-v','VERBOSITY=sqlstate','-U','postgres'],input=text.encode(),check=check)
    try:
        for _ in range(40):
            ready=run(['docker','exec',container,'pg_isready','-q','-U','postgres'],check=False)
            if ready.returncode==0:break
            time.sleep(.25)
        else:raise RuntimeError('isolated_postgres_not_ready')
        sql('''CREATE TABLE models(id text PRIMARY KEY,parameters json,deleted_at timestamptz);
CREATE TABLE system_settings(key text PRIMARY KEY,value jsonb,is_secret boolean);
INSERT INTO system_settings VALUES('model.max_concurrency','128',false);
INSERT INTO models VALUES
('deerflow-cnooc-glm-5.3-flash','{"api_key":"SYNTHETIC_SECRET","max_concurrency":0}',NULL),
('deerflow-cnooc-deepseek-v4-flash','{"max_concurrency":256}',NULL),
('deerflow-cnooc-qwen3-embedding-8b','{"max_concurrency":200}',NULL),
('deerflow-dashscope-vlm','{"max_concurrency":"SYNTHETIC_SECRET"}',NULL);
''')
        regression=sql('SELECT max_concurrency FROM public.models;',check=False)
        if regression.returncode==0 or b'42703' not in regression.stderr:raise RuntimeError('missing_column_regression_not_reproduced')
        count=0
        for kind in ('json','jsonb'):
            if kind=='jsonb':sql('ALTER TABLE models ALTER COLUMN parameters TYPE jsonb USING parameters::jsonb;')
            value=sql(c.MODEL_SQL).stdout
            if b'SYNTHETIC_SECRET' in value:raise RuntimeError('secret_projection')
            row=c.models(json.loads(value)); c.waits(json.loads(sql(c.WAIT_SQL).stdout))
            mapped={v['id']:v for v in row['models']}
            if row['global_setting']['value']!=128 or mapped[c.MODELS[2]]['mode']!='INHERIT_GLOBAL':raise RuntimeError('override_semantics')
            if mapped[c.MODELS[0]]['stored_override']!=256 or mapped[c.MODELS[3]]['mode']!='INVALID':raise RuntimeError('override_projection')
            count+=2
        sql("UPDATE system_settings SET value='0';")
        if c.models(json.loads(sql(c.MODEL_SQL).stdout))['global_setting']['value']!=0:raise RuntimeError('zero_global_lost')
        sql("UPDATE system_settings SET value='\"SYNTHETIC_SECRET\"',is_secret=true;")
        redacted=sql(c.MODEL_SQL).stdout
        if b'SYNTHETIC_SECRET' in redacted or c.models(json.loads(redacted))['global_setting']['value'] is not None:raise RuntimeError('secret_setting_leak')
        return dict(postgresql16='PASS',synthetic_json_and_jsonb_sql_queries=count,
            missing_column_regression='REPRODUCED_42703',secret_redaction='PASS',isolated_no_ports_no_network=True)
    finally:
        # Only the exact newly-created disposable test container; no user volumes.
        run(['docker','rm','-f',container])


def main():
    dest=Path(sys.argv[1]); need_new=not dest.exists()
    if not need_new:raise RuntimeError('new_delivery_directory_required')
    raw=(HERE/'check.py').read_bytes(); ast.parse(raw,feature_version=(3,7))
    proof=sql_test(); dest.mkdir(parents=True)
    (dest/NAME).write_bytes(raw)
    tests=[]
    for image in ('python:3.7.17-slim-bullseye','python:3.12-slim-bookworm'):
        result=run(['docker','run','--rm','--network','none','-v',str(HERE)+':/qa:ro',
            '-v',str(dest)+':/delivery:ro','-e','FOLLOWUP=/delivery/'+NAME,'-w','/qa',image,
            'python','-B','-m','unittest','-v','test_check'])
        data=result.stdout+result.stderr
        if b'Ran 16 tests' not in data or b'\nOK\n' not in data:raise RuntimeError('unit_suite_incomplete')
        logfile='TESTS-'+image.split(':')[1].split('-')[0]+'.txt';(dest/logfile).write_bytes(data)
        tests.append(dict(runtime=image,tests=16,result='PASS',log=logfile))
    sha=hashlib.sha256(raw).hexdigest()
    launcher='''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
SCRIPT=/home/cnooc/'''+NAME+'''
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-db-followup-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/check.py"
test "$(sha256sum "$CONTROL/check.py" | awk '{print $1}')" = '''+sha+'''
set +e
python3 -I -B -u "$CONTROL/check.py" 2>&1 | tee "$CONTROL/check.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "check_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "check_log=$CONTROL/check.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''
    run(['docker','run','--rm','-i','--network','none','python:3.7.17-slim-bullseye','bash','-n'],input=launcher.encode())
    (dest/'RUN-DATAX01.txt').write_bytes(launcher.encode())
    proof.update(script_sha256=sha,unit_tests=tests,production_execution='NOT_EXECUTED_LOCALLY',production_writes=0,
        provider_requests=0,original_evidence_modified=False,sampling='THREE_SHORT_METADATA_READS_NO_FIVE_MINUTE_REPLAY')
    (dest/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    (dest/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
        for p in sorted(dest.iterdir()) if p.is_file() and p.name!='SHA256SUMS'),encoding='ascii')
    print(json.dumps(proof))


if __name__=='__main__':main()
