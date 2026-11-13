"""Seal the unchanged visibility child into the standalone readonly postcheck."""
import ast
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SOURCE=HERE/'verify-energy-public-runtime-20261112-r1.py'
OUTPUT=ROOT/'release/energy-daily-public-runtime-20261112-r1/verify-energy-public-runtime-20261112-r1.py'


def main():
    source=SOURCE.read_bytes()
    repair=ast.parse((HERE/'repair-energy-public-runtime-20261112-r1.py').read_text(encoding='utf-8'))
    child=next(ast.literal_eval(row.value) for row in repair.body if isinstance(row,ast.Assign)
               and any(isinstance(target,ast.Name) and target.id=='VISIBILITY_CHILD' for target in row.targets))
    if source.count(b"VISIBILITY_CHILD = ''")!=1:
        raise ValueError('POSTCHECK_NOT_UNSEALED')
    sealed=source.replace(b"VISIBILITY_CHILD = ''",b'VISIBILITY_CHILD = '+repr(child).encode('ascii'),1)
    ast.parse(sealed,feature_version=(3,7));ast.parse(child,feature_version=(3,7))
    with OUTPUT.open('xb') as stream:stream.write(sealed)
    metadata={'schema':'energy-public-runtime-visibility-package.v1','filename':OUTPUT.name,'bytes':len(sealed),
              'sha256':hashlib.sha256(sealed).hexdigest(),'visibility_child_sha256':hashlib.sha256(child.encode()).hexdigest(),
              'credentials_included':False,'production_executed':False,'readonly':True}
    with OUTPUT.with_suffix('.package.json').open('x',encoding='utf-8') as stream:
        json.dump(metadata,stream,indent=2);stream.write('\n')
    print(json.dumps(metadata,sort_keys=True))


if __name__=='__main__':
    main()
