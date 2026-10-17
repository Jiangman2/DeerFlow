"""Qualify the diagnostic with local containers only, never production."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import check as m

HERE=Path(__file__).resolve().parent
PREVIOUS=HERE.parent/'weknora-glm168-review-20261017-r1'
spec=importlib.util.spec_from_file_location('review_qa',PREVIOUS/'package.py')
qa=importlib.util.module_from_spec(spec);spec.loader.exec_module(qa)
NAME='weknora-glm168-gate-diagnostic-20261017-r1.py'


def sql_test():
    name='weknora-gate-qa-'+uuid.uuid4().hex[:12]
    container=qa.run(['docker','run','-d','--name',name,'--network','none','--memory=384m','--cpus=1',
        '--tmpfs','/var/lib/postgresql/data:rw,size=256m','-e','POSTGRES_HOST_AUTH_METHOD=trust',
        'postgres:16-alpine','-c','shared_buffers=16MB','-c','max_connections=20']).stdout.decode().strip()
    assert len(container)==64 and all(x in '0123456789abcdef' for x in container)
    command=['docker','exec','-i',container,'psql','-X','-qAt','-v','ON_ERROR_STOP=1','-U','postgres']
    children=[]
    def sql(query):return qa.run(command,input=query.encode()).stdout
    try:
        for _ in range(40):
            if qa.run(['docker','exec',container,'pg_isready','-q','-U','postgres'],check=False).returncode==0:break
            time.sleep(.25)
        else:raise RuntimeError('postgres_not_ready')
        assert m.gate(json.loads(sql(m.GATE_SQL)))['original_gate_would_pass_now']
        sql('CREATE TABLE gate_fixture(n int); INSERT INTO gate_fixture VALUES(1);')
        blocker=subprocess.Popen(command+['-c','BEGIN; LOCK TABLE gate_fixture IN ACCESS EXCLUSIVE MODE; SELECT pg_sleep(12); COMMIT;'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        children.append(blocker)
        for _ in range(20):
            count=sql("SELECT count(*) FROM pg_locks WHERE relation='gate_fixture'::regclass AND mode='AccessExclusiveLock' AND granted;")
            if int(count)==1:break
            time.sleep(.1)
        else:raise RuntimeError('fixture_blocker_not_started')
        children.append(subprocess.Popen(command+['-c',"SET lock_timeout='15s'; SELECT count(*) FROM gate_fixture;"],stdout=subprocess.PIPE,stderr=subprocess.PIPE))
        for _ in range(12):
            blocked=m.gate(json.loads(sql(m.GATE_SQL)))
            if blocked['lock_waiters']>=1:break
            time.sleep(.1)
        assert blocked['lock_waiters']>=1 and not blocked['original_gate_conditions']['no_lock_waiters']
        assert any(x['backend_type']=='client backend' for x in blocked['blocker_roles'])
        for child in children:
            child.communicate(timeout=20);assert child.returncode==0
        assert m.gate(json.loads(sql(m.GATE_SQL)))['lock_waiters']==0
        return dict(postgresql16_sql='PASS',real_isolated_lock_wait_and_blocker_roles='PASS',production_calls=0)
    finally:
        qa.run(['docker','rm','-f',container])  # Exact newly-created disposable fixture only.
        for child in children:
            if child.poll() is None:child.communicate(timeout=5)


def launcher(sha):return '''sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
SCRIPT=/home/cnooc/'''+NAME+'''
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-gate-diagnostic-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/check.py"
test "$(sha256sum "$CONTROL/check.py" | awk '{print $1}')" = '''+sha+'''
echo "diagnostic_log=$CONTROL/check.log"
set +e
python3 -I -B -u "$CONTROL/check.py" 2>&1 | tee "$CONTROL/check.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "diagnostic_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "diagnostic_log=$CONTROL/check.log"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
'''


def main():
    dest=Path(sys.argv[1]);assert not dest.exists(),'new_delivery_directory_required'
    raw=(HERE/'check.py').read_bytes();ast.parse(raw,feature_version=(3,7));sha=hashlib.sha256(raw).hexdigest()
    proof=sql_test();dest.mkdir(parents=True)
    (dest/NAME).write_bytes(raw);(dest/'RUN-DATAX01.txt').write_bytes(launcher(sha).encode())
    (dest/'RUNBOOK.txt').write_bytes((HERE/'RUNBOOK.txt').read_bytes())
    rows=[]
    for image in qa.IMAGES:
        base=['docker','run','--rm','--network','none','-v',str(HERE)+':/qa:ro','-v',str(dest)+':/delivery:ro',
            '-e','CHECK=/delivery/'+NAME,'-w','/qa']
        tests=qa.run(base+[image,'python','-B','-m','unittest','-v','test_check']).stdout
        assert b'Ran 20 tests' in tests and b'\nOK\n' in tests and b'skipped' not in tests
        tag=image.split(':')[1].split('-')[0];(dest/('TESTS-'+tag+'.txt')).write_bytes(tests)
        fixture='''import sys,hashlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/previous')
import qualify_fixture
qualify_fixture.main()
import test_check
m=test_check.m
raw=Path('/downloads/weknora-glm168-review-20261017-r1-verified/weknora-glm168-review-20261017-r1.py').read_bytes()
assert hashlib.sha256(raw).hexdigest()==m.TOOL_SHA
m.TOOL.parent.mkdir(mode=0o700,parents=True);m.TOOL.write_bytes(raw);m.TOOL.chmod(0o600)
with patch('subprocess.Popen',side_effect=AssertionError('unexpected process during import')):
 old,c=m.load()
assert old.CURRENT==c.sw.u.TARGET
print('diagnostic_frozen_loader=PASS production_calls=0')
'''
        frozen=qa.run(base+['-v',str(PREVIOUS)+':/previous:ro','-v',str(qa.DOWNLOADS)+':/downloads:ro','-v',str(qa.LEGACY)+':/legacy:ro',
            '-e','REVIEW=/downloads/weknora-glm168-review-20261017-r1-verified/weknora-glm168-review-20261017-r1.py',image,'python','-B','-c',fixture]).stdout
        (dest/('FROZEN-'+tag+'.txt')).write_bytes(frozen)
        rows.append(dict(runtime=image,tests=20,skips=0,actual_memory_probe='PASS_LOCAL_CONTAINER',frozen_loader='PASS'))
        print(json.dumps(rows[-1]),flush=True)
        wrong=qa.run(base+[image,'python','-I','-B','/delivery/'+NAME],check=False)
        assert wrong.returncode==1 and b'root_datax01_required' in wrong.stdout
    qa.run(['docker','run','--rm','-i','--network','none',qa.IMAGES[0],'bash','-n'],input=launcher(sha).encode())
    proof.update(script_sha256=sha,tests=rows,python37_grammar='PASS',shell_syntax='PASS',
        production_execution='NOT_EXECUTED_LOCALLY',production_writes=0,provider_requests=0,
        original_evidence_modified=False,expansion_retried=False,ready_to_expand=False)
    (dest/'VERIFICATION.json').write_text(json.dumps(proof,indent=2)+'\n',encoding='utf-8')
    (dest/'SHA256SUMS').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
        for p in sorted(dest.iterdir()) if p.is_file() and p.name!='SHA256SUMS'),encoding='ascii')
    print(json.dumps(proof))


if __name__=='__main__':main()
