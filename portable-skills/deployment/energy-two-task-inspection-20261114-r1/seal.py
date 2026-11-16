"""Seal a fresh read-only two-task diagnostic. Historical tools stay immutable."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PRIOR=HERE.parent/'delivery-sequence-20261114-r1'
THREADS=['1a749b15-a6dc-4fa6-99a3-7a3b2ed195c4','7f645bba-9bfd-4786-ab0a-e8636cf255d3']

def child_source():
    raw=(PRIOR/'inspect_delivery_child.py').read_text(encoding='utf-8');parts=[]
    functions={'need','sha','iso','text_content','objects','audit','runtime_modules'}
    classes={'Refused','Deadline','Discard'}
    for node in ast.parse(raw).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,ast.FunctionDef) and node.name in functions or isinstance(node,ast.ClassDef) and node.name in classes:
            parts.append(ast.get_source_segment(raw,node))
    return ('\n\n'.join(parts)+'\n\n'+(HERE/'inspect_tasks_extra.py').read_text(encoding='utf-8')).encode()

def host_source():
    spec=importlib.util.spec_from_file_location('earlier_sequence_sealer',PRIOR/'seal.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    raw=old.source_host().decode()
    replacements={
        "THREAD='8e27d29f-730b-4a89-9681-36fdddc87887'":"THREADS="+repr(THREADS),
        "{'thread_id':THREAD}":"{'thread_ids':THREADS}",
        "'thread_id':THREAD":"'thread_ids':THREADS",
        "energy-delivery-sequence-inspection.v1":"energy-two-task-inspection.v1",
        "READ_FIXED_THREAD_DELIVERY_SEQUENCE":"READ_TWO_FIXED_TASKS_DURABLE_STATE",
        "result.get('requested_thread_id')==THREAD":"result.get('requested_thread_ids')==THREADS",
        "('DELIVERY_TIMELINE_OBSERVED_READONLY','NAMED_THREAD_RUN_NOT_FOUND')":"('TWO_TASKS_READONLY_COLLECTED','TWO_TASKS_PARTIALLY_OBSERVED')",
        "DELIVERY_SEQUENCE_OBSERVED_NO_CHANGE":"TWO_TASKS_OBSERVED_NO_CHANGE",
        "--request-timeout=65s":"--request-timeout=75s",
        "timeout=70":"timeout=80",
        "gateway_mount_metadata=mount_metadata(pod)":"gateway_mount_metadata=mount_metadata(pod),gateway_matches_last_accepted_417=(dep_identity['generation']==417 and dep_identity['spec_sha256']=='7faa311dc54be05137ec9c756aa278927e78d47f28118f6779415729c9029aa1'),baseline_drift_does_not_skip_readonly_diagnostic=True",
    }
    for before,after in replacements.items():
        assert before in raw,before;raw=raw.replace(before,after)
    return raw.encode()

def build():
    child=child_source();raw=host_source()
    compile(child,'<two-task-child>','exec');ast.parse(child.decode(),feature_version=(3,7))
    assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    assert raw.count(b'ASSETS={}\n')==1
    sealed=raw.replace(b'ASSETS={}\n',b'ASSETS='+repr(assets).encode()+b'\n')
    compile(sealed,'<two-task-host>','exec');ast.parse(sealed.decode(),feature_version=(3,7))
    return sealed,child

def main():
    sealed,child=build();directory=ROOT/'release/energy-two-task-inspection-20261114-r1';directory.mkdir(parents=True,exist_ok=True)
    name='inspect-energy-two-tasks-20261114-r1.py'
    metadata={'schema':'energy-two-task-inspection-package.v1','filename':name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),
        'child_sha256':hashlib.sha256(child).hexdigest(),'threads':THREADS,'production_executed':False,'database_readonly':True,
        'mutations_supported':False,'messages_sent':0,'provider_requests':0,'raw_messages_exported':False}
    for path,data in ((directory/name,sealed),(directory/'SOURCE.json',(json.dumps(metadata,sort_keys=True,indent=2)+'\n').encode())):
        if path.exists():assert path.read_bytes()==data,'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(data)
    print(json.dumps(metadata,sort_keys=True))

if __name__=='__main__':main()
