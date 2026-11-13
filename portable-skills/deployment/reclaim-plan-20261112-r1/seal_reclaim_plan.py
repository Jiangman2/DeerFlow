"""Seal the review-only plan with immutable known host identity helpers."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PRIOR=ROOT/'release/sandbox-retention-20261112-r1/inspect-sandbox-retention-20261112-r1.py'
PRIOR_SHA='dc91ddddfaa5ebbd4f915440f30ab378a597359a670f4fea0d7b3d3a310225c2'

def source_host():
    raw=PRIOR.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PRIOR_SHA:raise ValueError('HOST_IDENTITY_SOURCE_CHANGED')
    text=raw.decode();parts=[]
    functions={'need','canonical','digest','candidates','identity','ownership'}
    constants={'GATEWAY_UID','KUBE','LIST'}
    for node in ast.parse(text).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,ast.ClassDef) and node.name=='Refused' or isinstance(node,ast.FunctionDef) and node.name in functions:
            parts.append(ast.get_source_segment(text,node))
        elif isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id in constants for name in node.targets):
            parts.append(ast.get_source_segment(text,node))
    assets="""
ASSETS={}
def assets():
    need(set(ASSETS)=={'child'},'ASSETS_NOT_SEALED')
    item=ASSETS['child'];raw=base64.b64decode(item['base64'],validate=True)
    need(len(raw)<=524288 and digest(raw)==item['sha256'],'CHILD_ASSET_CHANGED')
    compile(raw,'<fixed-review-child>','exec');return raw.decode()
"""
    return ('"""Inspect one fixed scheduled-run failure and review-only sandbox capacity candidates."""\n'+'\n\n'.join(parts)+'\n'+assets+'\n'+(HERE/'inspect_reclaim_host_extra.py').read_text(encoding='utf-8')).encode()

def main():
    child=(HERE/'inspect_reclaim_child.py').read_bytes()
    compile(child,'<fixed-review-child>','exec')
    if len(child)>524288:raise ValueError('CHILD_SIZE_BOUND')
    raw=source_host();assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    needle=b'ASSETS={}'
    if raw.count(needle)!=1:raise ValueError('HOST_SEAL_MAPPING_CHANGED')
    sealed=raw.replace(needle,b'ASSETS='+repr(assets).encode())
    compile(sealed,'<fixed-review-host>','exec');ast.parse(sealed.decode(),feature_version=(3,7))
    directory=ROOT/'release/sandbox-reclaim-plan-20261112-r1';directory.mkdir(parents=True,exist_ok=True)
    target=directory/'inspect-energy-scheduled-run-and-capacity-20261112-r1.py'
    with target.open('xb') as stream:stream.write(sealed)
    meta={'schema':'energy-scheduled-run-capacity-inspection-package.v1','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),
        'child_sha256':hashlib.sha256(child).hexdigest(),'production_executed':False,'mutations_supported':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(meta,stream,indent=2);stream.write('\n')
    print(json.dumps(meta,sort_keys=True))

if __name__=='__main__':main()
