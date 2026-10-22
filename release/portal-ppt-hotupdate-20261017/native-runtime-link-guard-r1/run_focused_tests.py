import hashlib
import json
from pathlib import Path
import subprocess
import sys
import uuid

HERE=Path(__file__).resolve().parent
IMAGE='sha256:1353c9a1723ad9a8f05be830d951311623e153488500b1e22b3bcb059e676630'
OWNER=uuid.uuid4().hex
LABEL='portal-ppt-link-guard-unit-owner'
D=['docker','--context','desktop-linux']
cid=None;result=None;cleanup=False
def run(args,timeout=30):
    return subprocess.run(D+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
try:
    args=['container','create','--pull','never','--name','portal-link-unit-'+OWNER,'--label',LABEL+'='+OWNER,
          '--network','none','--read-only','--cap-drop','ALL','--security-opt','no-new-privileges','--user','0:0',
          '--cpus','1','--memory','256m','--pids-limit','64',
          '--mount','type=bind,src='+str(HERE).replace('\\','/')+',dst=/work,readonly',
          '--entrypoint','python',IMAGE,'-I','-B','/work/test_guard.py']
    created=run(args);assert created.returncode==0,created.stderr.decode()
    cid=created.stdout.decode().strip()
    inspected=run(['container','inspect',cid]);assert inspected.returncode==0
    obj=json.loads(inspected.stdout)[0]
    assert obj['Id']==cid and obj['Image']==IMAGE and obj['Config']['Labels'][LABEL]==OWNER and obj['HostConfig']['NetworkMode']=='none'
    result=run(['start','--attach',cid],60)
finally:
    if cid:
        inspected=run(['container','inspect',cid]);assert inspected.returncode==0
        obj=json.loads(inspected.stdout)[0]
        assert obj['Id']==cid and obj['Config']['Labels'][LABEL]==OWNER
        cleanup=run(['rm','--force',cid]).returncode==0
    if result:
        (HERE/'FOCUSED-TEST.stdout.txt').write_bytes(result.stdout)
        (HERE/'FOCUSED-TEST.stderr.txt').write_bytes(result.stderr)
        value=dict(status='PASS' if result.returncode==0 and cleanup else 'INCOMPLETE',returncode=result.returncode,
                   image=IMAGE,owner=OWNER,container_id=cid,cleanup_exact_owned_id=cleanup,
                   scope='FOCUSED_LOCAL_LINUX_RENDERER_AND_KERNEL_API_MOCK_FIXTURES',
                   files={name:dict(bytes=(HERE/name).stat().st_size,sha256=hashlib.sha256((HERE/name).read_bytes()).hexdigest()) for name in ['test_guard.py','link_guard.py','guard_kernel.py','preexec.sh','run_focused_tests.py']},
                   stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
                   remote_execution=False,host_network=False,production_mutation=False,native_arm64_qualified=False,ready_for_rollout=False)
        (HERE/'FOCUSED-QUALIFICATION.json').write_text(json.dumps(value,sort_keys=True,indent=2)+'\n',encoding='utf-8')
        print(result.stderr.decode(),end='');print(json.dumps(value,sort_keys=True))
sys.exit(0 if result and result.returncode==0 and cleanup else 1)
