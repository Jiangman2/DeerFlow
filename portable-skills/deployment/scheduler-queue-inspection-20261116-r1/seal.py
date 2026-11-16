"""Seal a screenshot-scoped scheduler metadata inspector; no recovery writes."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
RELEASE=REPO/'release/scheduler-queue-inspection-20261116-r1'
FILENAME='inspect-scheduler-queue-20261116-r1.py'

def assignment(source,name,value):
    nodes=[n for n in ast.parse(source).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)]
    assert len(nodes)==1;node=nodes[0];lines=source.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[name+'='+repr(value)+'\n'];return ''.join(lines)

def build():
    source=(HERE.parent/'delivery-sequence-20261114-r1/inspect_delivery_child.py').read_text(encoding='utf-8')
    functions={'need','sha','iso','audit','runtime_modules'};classes={'Refused','Deadline','Discard'}
    selected=[]
    for node in ast.parse(source).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,ast.FunctionDef) and node.name in functions or isinstance(node,ast.ClassDef) and node.name in classes:selected.append(ast.get_source_segment(source,node))
    extra=(HERE/'child_extra.py').read_text(encoding='utf-8')
    child=('\n\n'.join(selected)+'\n\n'+extra).encode()
    asset={'base64':base64.b64encode(child).decode(),'bytes':len(child),'sha256':hashlib.sha256(child).hexdigest()}
    host=assignment((HERE/'host.py').read_text(encoding='utf-8'),'ASSET',asset).encode()
    for raw in (child,host):compile(raw,'<readonly-inspector>','exec');ast.parse(raw.decode(),feature_version=(3,7))
    return host,child

def main():
    host,child=build();RELEASE.mkdir(exist_ok=True,parents=True)
    metadata={'schema':'scheduler-queue-inspection-package.v1','filename':FILENAME,'bytes':len(host),'sha256':hashlib.sha256(host).hexdigest(),
              'child_sha256':hashlib.sha256(child).hexdigest(),'task_title':'油气与金融市场日报','mutations_supported':False,
              'provider_requests':0,'work_messages_sent':0,'raw_messages_or_prompts_exported':False,
              'database_readonly':True,'live_application_poller_state_inspected':False,'production_executed':False}
    for name,raw in [(FILENAME,host),('SOURCE.json',(json.dumps(metadata,sort_keys=True,indent=2)+'\n').encode())]:
        path=RELEASE/name
        if path.exists():assert path.read_bytes()==raw,'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(raw)
    print(json.dumps(metadata,sort_keys=True))

if __name__=='__main__':main()
