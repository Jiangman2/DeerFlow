"""Seal a fixed-thread read-only delivery/budget inspector."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PRIOR=HERE.parent/'reclaim-plan-20261112-r1/seal_reclaim_plan.py'
spec=importlib.util.spec_from_file_location('prior_review_sealer',PRIOR)
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)

def source_host():
    raw=prior.source_host().decode();tree=ast.parse(raw);parts=[]
    functions={'need','canonical','digest','candidates','identity','ownership','assets'}
    constants={'GATEWAY_UID','KUBE','LIST','ASSETS'}
    for node in tree.body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,ast.ClassDef) and node.name=='Refused' or isinstance(node,ast.FunctionDef) and node.name in functions:
            parts.append(ast.get_source_segment(raw,node))
        elif isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id in constants for name in node.targets):parts.append(ast.get_source_segment(raw,node))
    return ('"""Inspect fixed delivery metadata and non-secret scheduler budget; never send or retry."""\n'+'\n\n'.join(parts)+'\n'+(HERE/'inspect_delivery_host_extra.py').read_text(encoding='utf-8')).encode()

def combined_child():
    runtime=(HERE/'runtime_summary.py').read_bytes()
    child=(HERE/'inspect_delivery_child.py').read_bytes()
    needle=b'RUNTIME_OBSERVER = None'
    if child.count(needle)!=1:raise ValueError('RUNTIME_OBSERVER_BINDING_CHANGED')
    return runtime+b'\n'+child.replace(needle,b'RUNTIME_OBSERVER = observe_runtime')

def main():
    child=combined_child();compile(child,'<sealed-delivery-child>','exec')
    if len(child)>524288:raise ValueError('CHILD_SIZE_BOUND')
    raw=source_host();assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    if raw.count(b'ASSETS={} ')==1:raise ValueError('UNEXPECTED_ASSET_FORMAT')
    if raw.count(b'ASSETS={}\n')!=1:raise ValueError('PARENT_SEAL_MAPPING_CHANGED')
    sealed=raw.replace(b'ASSETS={}\n',b'ASSETS='+repr(assets).encode()+b'\n')
    compile(sealed,'<sealed-delivery-host>','exec');ast.parse(sealed.decode(),feature_version=(3,7))
    directory=ROOT/'release/energy-delivery-budget-inspection-20261112-r1';directory.mkdir(parents=True,exist_ok=True)
    target=directory/'inspect-energy-daily-webhook-budget-20261112-r1.py'
    with target.open('xb') as stream:stream.write(sealed)
    meta={'schema':'energy-delivery-budget-inspection-package.v1','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),
        'child_sha256':hashlib.sha256(child).hexdigest(),'production_executed':False,'messages_sent':0,'mutations_supported':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(meta,stream,indent=2);stream.write('\n')
    print(json.dumps(meta,sort_keys=True))

if __name__=='__main__':main()
