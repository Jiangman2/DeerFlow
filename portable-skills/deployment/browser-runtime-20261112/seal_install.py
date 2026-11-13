"""Embed the fixed public code and synthetic check in one operator artifact."""
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
RELEASE=ROOT/'release/energy-daily-browser-fix-20261112-r1'


def main():
    test_files={}
    for name in ('test_energy_market_daily.py','fixtures/approved.json'):
        test_files[name]=base64.b64encode((ROOT/'portable-skills/tests'/name).read_bytes()).decode()
    raw_assets={'publisher':(HERE/'publish_browser_fix_child.py').read_bytes(),
                'verifier':(HERE/'publish_verify_child.py').read_bytes(),
                'package':(RELEASE/'energy-market-daily-2.0.1-public-code-r2.zip').read_bytes(),
                'test_files':json.dumps(test_files,sort_keys=True,separators=(',',':')).encode()}
    assets={name:{'base64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in raw_assets.items()}
    source=HERE/'install-energy-browser-fix-20261112-r1.py'
    raw=source.read_bytes()
    if raw.count(b'ASSETS = {}')!=1:
        raise ValueError('SOURCE_NOT_UNSEALED')
    sealed=raw.replace(b'ASSETS = {}',b'ASSETS = '+repr(assets).encode(),1)
    compile(sealed,str(source),'exec')
    target=RELEASE/source.name
    with target.open('xb') as stream:stream.write(sealed)
    metadata={'schema':'energy-browser-fix-install-package.v1','filename':target.name,'bytes':len(sealed),'sha256':hashlib.sha256(sealed).hexdigest(),
              'assets':{name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in raw_assets.items()},
              'production_execution_requested':False,'credentials_included':False}
    with target.with_suffix('.package.json').open('x',encoding='utf-8') as stream:json.dump(metadata,stream,indent=2);stream.write('\n')
    print(json.dumps(metadata,sort_keys=True))


if __name__=='__main__':main()
