"""Combine serving-edge, Skill visibility and one-task read-only diagnostics."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
THREAD='d934a54e-b894-4970-a32e-9b983c55d0df'
RELEASE=REPO/'release/energy-reader-serving-diagnostic-20261115-r1'

WRAPPER='''"""Read-only diagnostics for the operator-named daily report and active edge."""
import base64,contextlib,hashlib,io,json,os,sys,types
ASSETS={}
def main():
 out={'schema':'energy-reader-serving-and-task-diagnostic.v1','status':'INCOMPLETE','workload_modified':False,'persistent_files_modified':False,'database_modified':False,'provider_requests':0,'work_messages_sent':0,'credentials_exported':False,'tasks_retried':False}
 try:
  if sys.argv[1:]!=['--inspect-readonly']:raise ValueError('READONLY_MODE_REQUIRED')
  if os.name!='posix' or os.uname().nodename.split('.')[0]!='datax-01' or os.geteuid()!=0:raise ValueError('DATAX01_ROOT_REQUIRED')
  if set(ASSETS)!={'edge','views','task'}:raise ValueError('SEALED_DIAGNOSTICS_REQUIRED')
  for name in ('edge','views','task'):
   item=ASSETS[name];raw=base64.b64decode(item['base64'],validate=True)
   if len(raw)!=item['bytes'] or hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('SEALED_INTEGRITY_FAILED')
   module=types.ModuleType('_energy_readonly_'+name);module.__file__=__file__;sys.modules[module.__name__]=module
   exec(compile(raw,'<sealed-readonly-'+name+'>','exec'),module.__dict__)
   print(json.dumps({'phase':'READONLY_'+name.upper()}),flush=True)
   sys.argv=[__file__,'--verify-readonly' if name=='views' else '--inspect-readonly']
   with contextlib.redirect_stdout(io.StringIO()) as capture:code=module.main()
   rows=[json.loads(line) for line in capture.getvalue().splitlines() if line.startswith('{')]
   if not rows:raise ValueError('DIAGNOSTIC_RESULT_REQUIRED')
   out[name]=rows[-1]
  out['status']='READONLY_DIAGNOSTICS_COLLECTED'
 except Exception as exc:
  out.update(error_class=type(exc).__name__,error_code=str(exc) if type(exc) is ValueError else 'DIAGNOSTICS_FAILED_REDACTED')
 print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')
if __name__=='__main__':sys.exit(main())
'''


def sha(raw):return hashlib.sha256(raw).hexdigest()

def assignment(source,name,value):
    node=next(n for n in ast.parse(source).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets))
    lines=source.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[name+' = '+repr(value)+'\n'];return ''.join(lines)


def task_sources():
    path=HERE.parent/'energy-two-task-inspection-20261114-r1/seal.py'
    spec=importlib.util.spec_from_file_location('accepted_readonly_task_parent',path)
    parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
    child=parent.child_source().decode();host=parent.host_source().decode()
    child=assignment(child,'THREADS',(THREAD,));host=assignment(host,'THREADS',[THREAD])
    marker="\nif __name__=='__main__':sys.exit(main())"
    assert child.count(marker)==1
    child=child.replace(marker,'\n'+(HERE/'thread_detail.py').read_text(encoding='utf-8')+marker)
    child=child.replace('energy-two-task-child.v1','energy-reader-task-child.v1').replace('TWO_TASKS','REPORT_TASK')
    host=host.replace('energy-two-task-inspection.v1','energy-reader-task-inspection.v1').replace('TWO_TASKS','REPORT_TASK')
    assets={'child':{'base64':base64.b64encode(child.encode()).decode(),'sha256':sha(child.encode())}}
    host=assignment(host,'ASSETS',assets)
    return host.encode(),child.encode()


def build():
    task,child=task_sources()
    views=REPO/'release/energy-daily-reader-visibility-20261114-r1/inspect-energy-reader-skill-visibility-20261114-r1.py'
    assert sha(views.read_bytes())=='7290d284c8d4d8353aa61ffd96e592c3373aad5adbc815da90c06ad0a751ab1a'
    raw_assets={'edge':(HERE/'inspect_serving_edge.py').read_bytes(),'views':views.read_bytes(),'task':task}
    assets={n:{'base64':base64.b64encode(raw).decode(),'bytes':len(raw),'sha256':sha(raw)} for n,raw in raw_assets.items()}
    source=assignment(WRAPPER,'ASSETS',assets).encode()
    for name,raw in [('wrapper',source),('child',child)]+list(raw_assets.items()):
        compile(raw,'<readonly-'+name+'>','exec');ast.parse(raw.decode(),feature_version=(3,7))
    return source,raw_assets,child


def main():
    source,assets,child=build();name='inspect-energy-serving-reader-20261115-r1.py'
    meta={'schema':'energy-serving-reader-diagnostic-package.v1','filename':name,'bytes':len(source),'sha256':sha(source),
          'fixed_thread':THREAD,'database_readonly':True,'application_config_may_resolve_database_credentials':True,
          'raw_messages_exported':False,'raw_configuration_exported':False,'credentials_exported':False,
          'mutations_supported':False,'provider_requests':0,'work_messages_sent':0,'production_executed':False,
          'assets':{n:{'bytes':len(raw),'sha256':sha(raw)} for n,raw in assets.items()},'child_sha256':sha(child)}
    RELEASE.mkdir(exist_ok=True,parents=True)
    for filename,raw in [(name,source),('SOURCE.json',(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode())]:
        path=RELEASE/filename
        if path.exists():assert path.read_bytes()==raw,'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(raw)
    print(json.dumps(meta,sort_keys=True))


if __name__=='__main__':main()
