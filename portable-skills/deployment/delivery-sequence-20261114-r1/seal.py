"""Seal the new fixed-thread read-only sequence inspector without changing history."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
PRIOR=HERE.parent/'webhook-budget-20261112-r1/seal_delivery_probe.py'
spec=importlib.util.spec_from_file_location('prior_delivery_probe_sealer',PRIOR)
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)


def source_host():
    raw=prior.source_host().decode();tree=ast.parse(raw);parts=[]
    functions={'need','canonical','digest','candidates','identity','ownership','assets'}
    constants={'GATEWAY_UID','KUBE','LIST','ASSETS'}
    for node in tree.body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,ast.ClassDef) and node.name=='Refused' or isinstance(node,ast.FunctionDef) and node.name in functions:
            parts.append(ast.get_source_segment(raw,node))
        elif isinstance(node,ast.Assign) and any(isinstance(name,ast.Name) and name.id in constants for name in node.targets):
            parts.append(ast.get_source_segment(raw,node))
    return ('"""Inspect one fixed task delivery sequence; never send, retry or mutate."""\n'+'\n\n'.join(parts)+'\n'+(HERE/'inspect_delivery_host_extra.py').read_text(encoding='utf-8')).encode()


def build():
    child=(HERE/'inspect_delivery_child.py').read_bytes()
    compile(child,'<sealed-sequence-child>','exec')
    assert len(child)<=524288
    raw=source_host()
    assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    assert raw.count(b'ASSETS={}\n')==1
    sealed=raw.replace(b'ASSETS={}\n',b'ASSETS='+repr(assets).encode()+b'\n')
    compile(sealed,'<sealed-sequence-host>','exec');ast.parse(sealed.decode(),feature_version=(3,7))
    return sealed,child


def main():
    sealed,child=build()
    directory=ROOT/'release/energy-delivery-sequence-inspection-20261114-r1'
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/'inspect-energy-delivery-sequence-20261114-r1.py'
    meta={'schema':'energy-delivery-sequence-inspection-package.v1','filename':target.name,'bytes':len(sealed),
          'sha256':hashlib.sha256(sealed).hexdigest(),'child_sha256':hashlib.sha256(child).hexdigest(),
          'fixed_thread':'8e27d29f-730b-4a89-9681-36fdddc87887','production_executed':False,'messages_sent':0,
          'mutations_supported':False,'raw_messages_exported':False,'database_readonly':True}
    for path,data in ((target,sealed),(target.with_suffix('.package.json'),(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode())):
        if path.exists():
            assert path.read_bytes()==data, 'EXISTING_PACKAGE_DIFFERS'
        else:
            path.write_bytes(data)
    print(json.dumps(meta,sort_keys=True))


if __name__=='__main__':main()
