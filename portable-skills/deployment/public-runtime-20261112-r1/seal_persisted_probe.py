"""Seal the existing fixed classifier and persisted-reader into one operator."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
OUTPUT=ROOT/'release/energy-daily-public-runtime-20261112-r1'


def main():
    source=HERE/'inspect-energy-daily-thread-20261112-r1.py'
    tree=ast.parse(source.read_bytes())
    classifier=next(ast.literal_eval(item.value) for item in tree.body if isinstance(item,ast.Assign) and any(isinstance(name,ast.Name) and name.id=='CHILD' for name in item.targets)).encode()
    child=(HERE/'inspect_persisted_child.py').read_bytes()
    need_one=lambda raw,needle:raw.count(needle)==1
    if not need_one(child,b"CLASSIFIER_B64=''\n") or not need_one(child,b"CLASSIFIER_SHA=''\n"):raise ValueError('CHILD_NOT_UNSEALED')
    child=child.replace(b"CLASSIFIER_B64=''\n",b'CLASSIFIER_B64='+repr(base64.b64encode(classifier).decode()).encode()+b'\n')
    child=child.replace(b"CLASSIFIER_SHA=''\n",b'CLASSIFIER_SHA='+repr(hashlib.sha256(classifier).hexdigest()).encode()+b'\n')
    compile(child,'<fixed-persisted-child>','exec')
    host=HERE/'inspect-energy-daily-persisted-thread-20261112-r1.py'
    raw=host.read_bytes();assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    if raw.count(b'ASSETS={}')!=1:raise ValueError('HOST_NOT_UNSEALED')
    sealed=raw.replace(b'ASSETS={}',b'ASSETS='+repr(assets).encode())
    compile(sealed,str(host),'exec');OUTPUT.mkdir(parents=True,exist_ok=True)
    target=OUTPUT/host.name
    with target.open('xb') as stream:stream.write(sealed)
    metadata={'schema':'energy-persisted-inspection-package.v1','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),
              'child_sha256':hashlib.sha256(child).hexdigest(),'classifier_sha256':hashlib.sha256(classifier).hexdigest(),'production_executed':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(metadata,stream,indent=2);stream.write('\n')
    print(json.dumps(metadata,sort_keys=True))


if __name__=='__main__':main()
