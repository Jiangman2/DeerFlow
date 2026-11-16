"""Seal one field-bound edge repair and two-module Work notification release."""
import ast
import base64
import copy
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
RELEASE=REPO/'release/energy-reader-work-repair-20261115-r1'
FILENAME='repair-energy-reader-work-20261115-r1.py'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def assignment(source,name,value):
    found=[n for n in ast.parse(source).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)]
    assert len(found)==1,name
    node=found[0];lines=source.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[name+' = '+repr(value)+'\n']
    return ''.join(lines)

def isolated_tests():
    source=(HERE/'test_notification_links.py').read_text(encoding='utf-8')
    selected=[]
    for node in ast.parse(source).body:
        if isinstance(node,(ast.Import,ast.ImportFrom,ast.FunctionDef,ast.ClassDef)):
            selected.append(node)
        elif isinstance(node,ast.Assign) and all(isinstance(t,ast.Name) and t.id in {'THREAD','ORIGIN','READER','CHAT'} for t in node.targets):
            selected.append(node)
    source=ast.unparse(ast.Module(body=selected,type_ignores=[]))+'\n'
    old="source = (SOURCE / filename).read_text(encoding='utf-8')"
    assert source.count(old)==1
    return source.replace(old,'source = CANDIDATES[filename]')

def asset(raw):return {'bytes':len(raw),'sha256':sha(raw),'base64':base64.b64encode(raw).decode()}

def build():
    binding=json.loads((HERE/'FIELD-BINDING.json').read_bytes())
    assert binding['schema']=='energy-reader-link-repair-field-binding.v1' and binding['native_work_notification_confirmed'] is True
    parent=binding['gateway_parent_sources'];assert len(parent)==14
    candidates={p.name:p.read_bytes() for p in sorted((HERE/'backend/app/scheduler').glob('*.py'))}
    assert set(candidates)=={'work_notification_delivery.py','work_notifications.py'}
    for raw in candidates.values():compile(raw,'<notification-candidate>','exec')
    next_sources=copy.deepcopy(parent)
    for filename,raw in candidates.items():next_sources['/app/backend/app/scheduler/'+filename]={'bytes':len(raw),'sha256':sha(raw)}
    assert sum(parent[k]!=next_sources[k] for k in parent)==2
    child=(HERE/'gateway_child.py').read_text(encoding='utf-8')
    for name,value in [('PARENT_SOURCES',parent),('NEXT_SOURCES',next_sources),('CANDIDATES',{n:asset(raw) for n,raw in candidates.items()}),('TEST_SOURCE',isolated_tests())]:
        child=assignment(child,name,value)
    raw_assets={'reader':(HERE/'nginx_reader.py').read_bytes(),'startup':(HERE/'startup_policy.py').read_bytes(),
                'gateway_child':child.encode(),'code':json.dumps({n:raw.decode() for n,raw in candidates.items()},ensure_ascii=True,sort_keys=True).encode()}
    assert sha(raw_assets['startup'])=='cdb21eb0c088071455f731ea96408e569e5decc082d9e63a688034ab469eb006'
    source=(HERE/'repair_operator.py').read_text(encoding='utf-8')
    source=assignment(assignment(source,'BINDING',binding),'ASSETS',{n:asset(raw) for n,raw in raw_assets.items()}).encode()
    for name,raw in [('operator',source)]+list(raw_assets.items()):
        if name!='code':compile(raw,'<sealed-'+name+'>','exec');ast.parse(raw.decode(),feature_version=(3,7))
    return source,raw_assets,next_sources

def main():
    source,assets,next_sources=build()
    meta={'schema':'energy-reader-work-repair-package.v1','filename':FILENAME,'bytes':len(source),'sha256':sha(source),
          'assets':{n:{'bytes':len(raw),'sha256':sha(raw)} for n,raw in assets.items()},
          'gateway_next_sources':next_sources,'changed_gateway_files':['work_notification_delivery.py','work_notifications.py'],
          'scheduled_daily_message':'[点击查看日报网页](<current-task-authenticated-html-reader-url>)',
          'skill_updated':False,'schedule_updated':False,'provider_requests':0,'work_messages_sent':0,
          'gateway_rollout_required':True,'serving_nginx_deployment':'nginx-security-heai-20261012-r1','production_executed':False}
    RELEASE.mkdir(exist_ok=True,parents=True)
    for name,raw in [(FILENAME,source),('SOURCE.json',(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode())]:
        path=RELEASE/name
        if path.exists():assert path.read_bytes()==raw,'SEALED_RELEASE_ALREADY_EXISTS_WITH_DIFFERENT_CONTENT'
        else:path.write_bytes(raw)
    print(json.dumps({'filename':str(RELEASE/FILENAME),'bytes':len(source),'sha256':sha(source),'changed_gateway_files':2,'synthetic_notification_checks':12}))

if __name__=='__main__':main()
