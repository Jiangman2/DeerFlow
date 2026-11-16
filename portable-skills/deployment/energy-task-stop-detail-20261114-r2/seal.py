import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
TARGET='7f645bba-9bfd-4786-ab0a-e8636cf255d3'
s=importlib.util.spec_from_file_location('two_tasks_parent',HERE.parent/'energy-two-task-inspection-20261114-r1/seal.py')
parent=importlib.util.module_from_spec(s);s.loader.exec_module(parent)

def child_source():
    raw=parent.child_source().decode();parts=[]
    names={'need','sha','iso','text_content','objects','audit','runtime_modules','safe_word','flags','errors','command','extra_ai','metadata','seconds','observe','main'}
    consts={'DB_HOST','DB_PORT','DB_ADDRESSES','ALLOW_DB_DNS','TOOLS','ACTIONS'}
    for node in ast.parse(raw).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names or isinstance(node,ast.ClassDef) and node.name in {'Refused','Deadline','Discard'}:
            parts.append(ast.get_source_segment(raw,node))
        elif isinstance(node,ast.Assign) and any(isinstance(x,ast.Name) and x.id in consts for x in node.targets):parts.append(ast.get_source_segment(raw,node))
    result='\n\n'.join(parts)+'\n'+(HERE/'inspect_stop_extra.py').read_text(encoding='utf-8')+'\nTHREADS=(TARGET,)\nif __name__=="__main__":sys.exit(main())\n'
    return result.replace('energy-two-task-child.v1','energy-task-stop-detail-child.v2').replace('TWO_TASKS_READONLY_COLLECTED','TASK_STOP_DETAIL_READONLY_COLLECTED').replace('TWO_TASKS_PARTIALLY_OBSERVED','TASK_STOP_DETAIL_INCOMPLETE').encode()

def build():
    child=child_source();raw=parent.host_source().decode()
    raw=raw.replace('THREADS='+repr(parent.THREADS),'THREADS='+repr([TARGET]))
    raw=raw.replace('energy-two-task-inspection.v1','energy-task-stop-detail.v2').replace('READ_TWO_FIXED_TASKS_DURABLE_STATE','READ_FIXED_COMPLETED_RUN_TERMINAL_EXPLANATION')
    raw=raw.replace("('TWO_TASKS_READONLY_COLLECTED','TWO_TASKS_PARTIALLY_OBSERVED')","('TASK_STOP_DETAIL_READONLY_COLLECTED',)").replace('TWO_TASKS_OBSERVED_NO_CHANGE','TASK_STOP_DETAIL_OBSERVED_NO_CHANGE')
    payload={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    assert raw.count('ASSETS={}\n')==1
    sealed=raw.replace('ASSETS={}\n','ASSETS='+repr(payload)+'\n').encode()
    for source in (child,sealed):compile(source,'<stop-detail>','exec');ast.parse(source.decode(),feature_version=(3,7))
    return sealed,child

def main():
    sealed,child=build();directory=ROOT/'release/energy-task-stop-detail-20261114-r2';directory.mkdir(parents=True,exist_ok=True)
    filename='inspect-energy-task-stop-detail-20261114-r2.py'
    meta={'schema':'energy-task-stop-detail-package.v2','filename':filename,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),'child_sha256':hashlib.sha256(child).hexdigest(),
        'thread':TARGET,'run_sha256':'37988b84a33cee73da5075227de02c32d461958225bebc747e0f2269915afcb0','fixed_seqs':[10,11,12,16,17,18,19,20],
        'database_readonly':True,'production_executed':False,'messages_sent':0,'provider_requests':0,'terminal_visible_text_redacted_max_characters':4000}
    for p,data in ((directory/filename,sealed),(directory/'SOURCE.json',(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode())):
        if p.exists():assert p.read_bytes()==data,'EXISTING_RELEASE_DIFFERS'
        else:p.write_bytes(data)
    print(json.dumps(meta,sort_keys=True))

if __name__=='__main__':main()
