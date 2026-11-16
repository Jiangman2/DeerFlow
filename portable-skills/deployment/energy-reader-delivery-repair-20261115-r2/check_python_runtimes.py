"""Run sealed asset/host guards and regressions in cached offline runtimes."""
import hashlib
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
DOCKER=r'C:/Users/15285/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
RELEASE=REPO/'release/energy-reader-work-repair-20261115-r2'
SCRIPT='/repo/release/energy-reader-work-repair-20261115-r2/repair-energy-reader-work-20261115-r2.py'
GUARD=r'''
import base64,contextlib,io,json,sys
source=open(sys.argv[1],'rb').read();scope={'__name__':'sealed_guard_check'};exec(compile(source,'<sealed>','exec'),scope)
assets=scope['load_assets']()
assert len(assets)==4
for key in ('reader','startup','gateway_child'):compile(assets[key],'<sealed-helper>','exec')
scope['sys'].argv=['sealed','--repair-once']
with contextlib.redirect_stdout(io.StringIO()) as capture:status=scope['main']()
row=json.loads(capture.getvalue().splitlines()[-1])
assert status==1 and row['error_code']=='DATAX01_ROOT_REQUIRED' and row['commands']==0
assert row['nginx_patch_requests']==row['gateway_patch_requests']==0
print(json.dumps({'status':'PYTHON37_SEALED_ASSETS_AND_WRONG_HOST_GUARD_PASSED','sealed_assets':len(assets),'commands':0,'network_requests':0,'work_messages_sent':0}))
'''
AUDIT=r'''
import base64,io,json,os,sys,unittest
from pathlib import Path
source=open(sys.argv[1],'rb').read();host={'__name__':'sealed_audit_check'};exec(compile(source,'<sealed>','exec'),host)
child={'__name__':'sealed_child_check'};exec(compile(host['load_assets']()['gateway_child'],'<child>','exec'),child)
root=Path('/etc/deerflow-openssl');root.mkdir();version=root/'..2026_fixture';version.mkdir()
(version/'openssl.cnf').write_text('synthetic approved openssl config')
(root/'..data').symlink_to(version.name,target_is_directory=True)
(root/'openssl.cnf').symlink_to('..data/openssl.cnf')
assert child['read'](root/'openssl.cnf')==b'synthetic approved openssl config'
Path('/tmp/openssl.cnf').write_text('outside synthetic config')
(root/'..data').unlink();(root/'..data').symlink_to('/tmp',target_is_directory=True)
try:child['read'](root/'openssl.cnf')
except child['Refused'] as exc:assert str(exc)=='OPENSSL_SOURCE_ESCAPED_MOUNT'
else:raise AssertionError('outside mount accepted')
candidates={n:base64.b64decode(row['base64']).decode() for n,row in child['CANDIDATES'].items()}
tests={'__name__':'sealed_tests','CANDIDATES':candidates}
exec(compile(child['TEST_SOURCE'],'<sealed-tests>','exec'),tests)
sys.addaudithook(child['audit'])
suite=unittest.defaultTestLoader.loadTestsFromTestCase(tests['NotificationLinkTests'])
stream=io.StringIO();result=unittest.TextTestRunner(stream=stream).run(suite)
assert result.testsRun==12 and result.wasSuccessful() and not result.skipped,stream.getvalue()
print(json.dumps({'status':'SEALED_NOTIFICATION_TESTS_UNDER_PRODUCTION_AUDIT_PASSED','notification_checks':12,'projected_configmap_checks':2,'network_requests':0,'messages_sent':0}))
'''

def run(image,args,body=None,user=None):
    command=[DOCKER,'run','--rm','-i','--pull','never','--network','none','--mount','type=bind,source='+str(REPO)+',target=/repo,readonly']
    if user:command+=['--user',user]
    command+=[image,'python','-I','-B']+args
    result=subprocess.run(command,input=body,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90)
    if result.returncode:raise AssertionError(result.stdout.decode('utf-8','replace')+result.stderr.decode('utf-8','replace'))
    return {'exitcode':result.returncode,'stdout':result.stdout.decode('utf-8','replace').strip(),'stderr':result.stderr.decode('utf-8','replace').strip()}

def main():
    rows={}
    rows['linux_nonroot_regressions']=run('python:3.10-slim',['-m','unittest','discover','-s','/repo/portable-skills/deployment/energy-reader-delivery-repair-20261115-r2','-p','test*.py'],user='1000:1000')
    rows['python37_entry_and_assets']=run('python:3.7-slim',['-c',GUARD,SCRIPT])
    rows['sealed_child_audit_and_configmap']=run('python:3.10-slim',['-c',AUDIT,SCRIPT])
    out={'schema':'energy-reader-work-repair-offline-validation.v1','status':'OFFLINE_RUNTIME_CHECKS_PASSED',
         'script_sha256':hashlib.sha256((RELEASE/Path(SCRIPT).name).read_bytes()).hexdigest(),'rows':rows,
         'production_access':False,'provider_requests':0,'work_messages_sent':0}
    raw=(json.dumps(out,sort_keys=True,indent=2)+'\n').encode();path=RELEASE/'OFFLINE-RUNTIMES.json'
    if path.exists():assert path.read_bytes()==raw,'RECORDED_RUNTIME_CHECKS_ALREADY_EXIST'
    else:path.write_bytes(raw)
    print(json.dumps(out,ensure_ascii=True))

if __name__=='__main__':main()
