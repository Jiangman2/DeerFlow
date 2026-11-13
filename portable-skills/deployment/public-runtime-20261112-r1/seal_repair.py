"""Seal the public-category browser repair against one explicit code ZIP."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package = args.package.read_bytes()
    digest = hashlib.sha256(package).hexdigest()
    with zipfile.ZipFile(args.package) as archive:
        members = archive.namelist()
        if len(members) != 106 or any('.private' in Path(name).parts for name in members):
            raise ValueError('PUBLIC_CODE_PACKAGE_REQUIRED')
        manifest = json.loads(archive.read('energy-market-daily/MANIFEST.json'))
        if manifest.get('version') != '2.0.2-portable':
            raise ValueError('VERSION_202_REQUIRED')
    files = {}
    for name in ('test_energy_market_daily.py', 'fixtures/approved.json'):
        files[name] = (ROOT / 'portable-skills/tests' / name).read_bytes()
    fixtures = {name: base64.b64encode(raw).decode('ascii') for name, raw in files.items()}
    fixture_hashes = {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
    publisher = (HERE / 'publisher.py').read_bytes().replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    verifier = (HERE / 'verify_child.py').read_bytes().replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    if verifier.count(b'FIXTURES = {}') != 1:
        raise ValueError('VERIFIER_NOT_UNSEALED')
    verifier = verifier.replace(b'FIXTURES = {}', b'FIXTURES = ' + repr(fixture_hashes).encode('ascii'), 1)
    raw_assets = {'publisher':publisher, 'verifier':verifier, 'package':package,
                  'test_files':json.dumps(fixtures,sort_keys=True,separators=(',',':')).encode('ascii')}
    assets = {name:{'base64':base64.b64encode(raw).decode('ascii'),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in raw_assets.items()}
    source = (HERE / 'repair-energy-public-runtime-20261112-r1.py').read_bytes()
    if source.count(b'ASSETS = {}') != 1:
        raise ValueError('HOST_NOT_UNSEALED')
    sealed = source.replace(b'UNSEALED_PACKAGE_SHA',digest.encode('ascii')).replace(b'ASSETS = {}',b'ASSETS = '+repr(assets).encode('ascii'),1)
    compile(sealed,str(args.output),'exec')
    for name,raw in raw_assets.items():
        if name in ('publisher','verifier'):
            compile(raw,'<fixed-'+name+'>','exec')
    with args.output.open('xb') as stream:
        stream.write(sealed)
    metadata = {'schema':'energy-public-runtime-repair-package.v1','filename':args.output.name,'bytes':len(sealed),
                'sha256':hashlib.sha256(sealed).hexdigest(),'code_package_sha256':digest,
                'assets':{name:{'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()} for name,raw in raw_assets.items()},
                'credentials_included':False,'production_executed':False}
    with args.output.with_suffix('.package.json').open('x',encoding='utf-8') as stream:
        json.dump(metadata,stream,indent=2);stream.write('\n')
    print(json.dumps(metadata,sort_keys=True))


if __name__ == '__main__':
    main()
