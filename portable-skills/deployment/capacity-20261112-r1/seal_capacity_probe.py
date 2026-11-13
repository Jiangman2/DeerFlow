import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]

def main():
    child=(HERE/'inspect_capacity_child.py').read_bytes();compile(child,'<fixed-provisioner-reader>','exec')
    host=HERE/'inspect-sandbox-capacity-20261112-r1.py';raw=host.read_bytes()
    if raw.count(b'ASSETS={}')!=1:raise ValueError('UNSEALED_HOST_REQUIRED')
    assets={'child':{'base64':base64.b64encode(child).decode(),'sha256':hashlib.sha256(child).hexdigest()}}
    sealed=raw.replace(b'ASSETS={}',b'ASSETS='+repr(assets).encode());compile(sealed,str(host),'exec')
    target=ROOT/'release/sandbox-capacity-20261112-r1'/host.name;target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as stream:stream.write(sealed)
    meta={'schema':'sandbox-capacity-package.v1','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),'child_sha256':hashlib.sha256(child).hexdigest(),'production_executed':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(meta,stream,indent=2);stream.write('\n')
    print(json.dumps(meta,sort_keys=True))

if __name__=='__main__':main()
