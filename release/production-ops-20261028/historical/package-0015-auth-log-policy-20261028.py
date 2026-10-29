"""Embed the verified transfer implementation in the permanent policy installer."""
import argparse
import ast
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def build():
    source=(HERE/'portal-auth-log-policy-0015-20261028-r1.py').read_text(encoding='utf-8')
    raw=(HERE/'rotate-relocate-portal-0015-auth-log-20261028-r1.py').read_bytes()
    assert hashlib.sha256(raw).hexdigest()=='c4bb6ad4c5b39bb646f207c4c00e7d4ccd7e03067311f4c49f51d23cbd14e546'
    assert source.count('BASE_SOURCE=None')==1
    source=source.replace('BASE_SOURCE=None','BASE_SOURCE='+repr(raw.decode()),1)
    ast.parse(source,feature_version=(3,7))
    return source.encode()


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    raw=build()
    with a.output.open('xb') as f:f.write(raw)
    print(json.dumps(dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),installed=False)))
