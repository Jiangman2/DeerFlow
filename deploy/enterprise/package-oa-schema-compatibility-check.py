"""Prepare an isolated synthetic SQLite restart check for existing DB consumers."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import ast

ROOT = Path(__file__).resolve().parents[2]
BOOT = 'backend/packages/harness/deerflow/persistence/bootstrap.py'
HELPER = 'backend/packages/harness/deerflow/persistence/oa_schema_compatibility.py'

CHILD = r'''
import asyncio,base64,contextlib,hashlib,io,json,pathlib,sys,tempfile,types
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine
from deerflow.persistence import bootstrap as original
DIAGNOSTIC={}

async def check():
 raw=pathlib.Path(original.__file__).read_bytes()
 observed=hashlib.sha256(raw).hexdigest()
 DIAGNOSTIC.update(phase='source_binding',baseline_bootstrap_sha256=observed)
 assert observed in BOOT_VARIANTS,'BOOTSTRAP_SOURCE_CHANGED'
 boot_code=BOOT_VARIANTS[observed]['candidate']
 DIAGNOSTIC['phase']='schema_head'
 assert original._get_head_revision()=='0020_ppt_confirmation','MIGRATION_HEAD_CHANGED'
 checks=[]
 with tempfile.TemporaryDirectory(prefix='oa-schema-check-') as directory:
  engine=create_async_engine('sqlite+aiosqlite:///'+str(pathlib.Path(directory)/'synthetic.db'))
  try:
   async with engine.begin() as connection:
    await connection.execute(sa.text('CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)'))
    await connection.execute(sa.text("INSERT INTO alembic_version VALUES ('0021_oa_result_sets')"))
    await connection.execute(sa.text("CREATE TABLE oa_policy_flow_jobs (id VARCHAR(64) PRIMARY KEY,result_protocol VARCHAR(8) NOT NULL DEFAULT 'v1', result_files JSON)"))
    await connection.execute(sa.text("INSERT INTO oa_policy_flow_jobs(id) VALUES ('synthetic')"))
   DIAGNOSTIC['phase']='baseline_unknown_revision'
   try:
    await original.bootstrap_schema(engine,backend='sqlite')
   except Exception as exc:
    assert '0021_oa_result_sets' in str(exc),'UNEXPECTED_BASELINE_ERROR'
   else:
    raise AssertionError('BASELINE_UNEXPECTEDLY_ACCEPTED')
   checks.append('baseline_unknown_revision_reproduced')
   helper=types.ModuleType('deerflow.persistence.oa_schema_compatibility')
   exec(compile(base64.b64decode(HELPER_CODE),'<candidate-helper>','exec'),helper.__dict__)
   sys.modules[helper.__name__]=helper
   candidate=types.ModuleType('_oa_candidate_bootstrap'); candidate.__file__=original.__file__
   exec(compile(base64.b64decode(boot_code),'<candidate-bootstrap>','exec'),candidate.__dict__)
   DIAGNOSTIC['phase']='compatible_startup'
   await candidate.bootstrap_schema(engine,backend='sqlite')
   checks.append('compatible_0020_consumer_restarts_on_0021')
   async with engine.begin() as connection:
    assert (await connection.execute(sa.text('SELECT version_num FROM alembic_version'))).scalar()=='0021_oa_result_sets'
    assert (await connection.execute(sa.text("SELECT result_protocol FROM oa_policy_flow_jobs WHERE id='synthetic'"))).scalar()=='v1'
    await connection.execute(sa.text("UPDATE oa_policy_flow_jobs SET result_files='[]' WHERE id='synthetic'"))
   checks.append('revision_and_legacy_row_preserved')
   async with engine.begin() as connection:
    await connection.execute(sa.text('ALTER TABLE oa_policy_flow_jobs RENAME COLUMN result_files TO missing_files'))
   DIAGNOSTIC['phase']='incomplete_expansion'
   try:
    await candidate.bootstrap_schema(engine,backend='sqlite')
   except RuntimeError as exc:
    assert str(exc)=='oa_additive_schema_shape_mismatch'
   else:
    raise AssertionError('MALFORMED_SCHEMA_ACCEPTED')
   checks.append('incomplete_expansion_rejected')
   async with engine.begin() as connection:
    await connection.execute(sa.text('ALTER TABLE oa_policy_flow_jobs RENAME COLUMN missing_files TO result_files'))
    await connection.execute(sa.text("UPDATE alembic_version SET version_num='0022_unknown'"))
   DIAGNOSTIC['phase']='unknown_successor'
   try:
    await candidate.bootstrap_schema(engine,backend='sqlite')
   except Exception as exc:
    assert '0022_unknown' in str(exc),'UNEXPECTED_UNKNOWN_ERROR'
   else:
    raise AssertionError('UNKNOWN_REVISION_ACCEPTED')
   checks.append('unknown_successor_rejected')
   async with engine.begin() as connection:
    await connection.execute(sa.text("UPDATE alembic_version SET version_num='0020_ppt_confirmation'"))
   DIAGNOSTIC['phase']='existing_0020_startup'
   await candidate.bootstrap_schema(engine,backend='sqlite')
   async with engine.connect() as connection:
    assert (await connection.execute(sa.text('SELECT version_num FROM alembic_version'))).scalar()=='0020_ppt_confirmation'
   checks.append('existing_0020_startup_preserved')
  finally:
   await engine.dispose()
 return {'status':'SYNTHETIC_SCHEMA_COMPATIBILITY_ACCEPTED','checks':checks,'baseline_bootstrap_sha256':observed,'source_commit':BOOT_VARIANTS[observed]['commit'],'candidate_bootstrap_sha256':hashlib.sha256(base64.b64decode(boot_code)).hexdigest(),'candidate_helper_sha256':hashlib.sha256(base64.b64decode(HELPER_CODE)).hexdigest(),'production_database_access':False,'synthetic_database':'TEMPORARY_SQLITE','temporary_files_cleaned':True,'candidate_only_in_child':True}

try:
 sink=io.StringIO()
 with contextlib.redirect_stdout(sink),contextlib.redirect_stderr(sink):
  result=asyncio.run(check())
except Exception as exc:
 code=str(exc) if isinstance(exc,AssertionError) and str(exc) in ('BOOTSTRAP_SOURCE_CHANGED','MIGRATION_HEAD_CHANGED','UNEXPECTED_BASELINE_ERROR','BASELINE_UNEXPECTEDLY_ACCEPTED','MALFORMED_SCHEMA_ACCEPTED','UNEXPECTED_UNKNOWN_ERROR','UNKNOWN_REVISION_ACCEPTED') else 'CANDIDATE_CHECK_FAILED'
 result={'status':'INCOMPLETE','error_class':type(exc).__name__,'error_code':code,'diagnostic':DIAGNOSTIC,'production_database_access':False,'raw_errors_exported':False}
print(json.dumps(result,sort_keys=True))
'''

HOST = r'''
import argparse,hashlib,json,subprocess
def digest(v):
 return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def main():
 parser=argparse.ArgumentParser(); parser.add_argument('--verify-compatibility-offline',action='store_true',required=True); parser.parse_args()
 base=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
 def query(args,timeout=75):
  p=subprocess.run(base+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
  assert p.returncode==0 and len(p.stdout)<2097152,'READ_QUERY_FAILED'
  return json.loads(p.stdout)
 rows=[]
 for name,expected in EXPECTED.items():
  before=query(['get','deployment',name,'-o','json'])
  meta,spec,status=before['metadata'],before['spec'],before['status']
  assert meta['uid']==expected['uid'] and meta['generation']==expected['generation'],'DEPLOYMENT_CHANGED'
  assert [c['image'] for c in spec['template']['spec']['containers']]==[expected['image']],'IMAGE_CHANGED'
  assert status.get('observedGeneration')==meta['generation'] and all(status.get(k,0)==spec['replicas'] for k in ('updatedReplicas','readyReplicas','replicas')) and not status.get('unavailableReplicas') and not status.get('terminatingReplicas'),'ROLLOUT_IN_PROGRESS'
  observation=query(['exec','deployment/'+name,'--','/app/backend/.venv/bin/python','-I','-B','-c',CHILD])
  after=query(['get','deployment',name,'-o','json'])
  assert before['metadata']['uid']==after['metadata']['uid'] and spec==after['spec'],'DEPLOYMENT_CHANGED'
  scheduling={k:spec['template']['spec'].get(k) for k in ('nodeSelector','affinity','tolerations','schedulerName')}
  rows.append({'deployment':name,'generation':meta['generation'],'uid':meta['uid'],'spec_sha256':digest(spec),'scheduling':scheduling,'observation':observation})
  print(json.dumps({'phase':'SCHEMA_COMPATIBILITY_SYNTHETIC','checked':len(rows),'total':2}),flush=True)
 accepted=all(r['observation']['status']=='SYNTHETIC_SCHEMA_COMPATIBILITY_ACCEPTED' for r in rows)
 print(json.dumps({'schema':'portal-oa-schema-compatibility-candidate.v1','status':'SCHEMA_COMPATIBILITY_CHECKED_NOT_DEPLOYED' if accepted else 'INCOMPLETE','consumers':rows,'production_database_access':False,'workload_modified':False,'provider_requests':0,'ready_for_rollout':False},sort_keys=True))
 return 0 if accepted else 1
if __name__=='__main__':
 try: code=main()
 except Exception as exc:
  print(json.dumps({'status':'INCOMPLETE','error_class':type(exc).__name__,'raw_errors_exported':False,'workload_modified':False})); code=1
 raise SystemExit(code)
'''


def build():
    original = subprocess.check_output(['git', 'show', 'a161e84e0:' + BOOT], cwd=ROOT)
    bootstrap = (ROOT / BOOT).read_text(encoding='utf-8').encode()
    helper = (ROOT / HELPER).read_text(encoding='utf-8').encode()
    marker = b'        elif decision == "versioned":\n'
    start = bootstrap.index(marker) + len(marker)
    end = bootstrap.index(b'            logger.info("bootstrap: branch=versioned', start)
    insertion = bootstrap[start:end]
    assert bootstrap.replace(insertion,b'',1)==original, 'UNRELATED_BOOTSTRAP_CHANGE'
    variants={}
    for commit in ('a161e84e0','9df7b5d41cf367e09be3bffce325079d918220c3','debb0fd16184f7270d66de70307a158a4e75d4a7'):
        source=subprocess.check_output(['git','show',commit+':'+BOOT],cwd=ROOT)
        assert source.count(marker)==1, 'BOOTSTRAP_BRANCH_SHAPE'
        candidate=source.replace(marker,marker+insertion,1)
        compile(candidate,'<candidate-variant>','exec')
        variants[hashlib.sha256(source).hexdigest()]={'commit':commit,'candidate':base64.b64encode(candidate).decode()}
    # The forward candidate remains unchanged: this is an old-consumer backport.
    child = ('BOOT_VARIANTS=' + repr(variants) + '\nHELPER_CODE=' + repr(base64.b64encode(helper).decode()) + '\n' + CHILD)
    compile(child, '<child>', 'exec')
    expected = {
        'gateway': {'uid':'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1','generation':382,'image':'deerflow.local/deer-flow-gateway@sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5'},
        'ppt-master-runner': {'uid':'4115f178-0599-445e-89c3-9d9452ddc26a','generation':54,'image':'deerflow.local/deer-flow-ppt-master-runner@sha256:8a853325c46a215c06d0ab77720aa3a8e48b06c98c60f2837c663867e704b0f0'},
    }
    host = 'EXPECTED=' + repr(expected) + '\nCHILD=' + repr(child) + '\n' + HOST
    ast.parse(host, feature_version=(3,7))
    return host, child


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--child-output',type=Path,required=True)
    args=parser.parse_args()
    host,child=build()
    with args.output.open('x',encoding='utf-8',newline='\n') as stream: stream.write(host)
    with args.child_output.open('x',encoding='utf-8',newline='\n') as stream: stream.write(child)
    print(json.dumps({'bytes':len(host.encode()),'sha256':hashlib.sha256(host.encode()).hexdigest(),'ready_for_rollout':False}))
