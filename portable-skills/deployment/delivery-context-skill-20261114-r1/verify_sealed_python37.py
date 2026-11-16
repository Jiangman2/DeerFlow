"""Synthetic sealed entry under cached Python 3.7; temporary outputs only."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('synthetic_sealer_tests',HERE/'test_sealed_package.py')
tests=importlib.util.module_from_spec(spec)
spec.loader.exec_module(tests)

PROBE=r'''
import contextlib,hashlib,io,json,pathlib,runpy,sys
from unittest.mock import patch
root=pathlib.Path('/synthetic')
entry=root/'install-energy-delivery-context-skill-20261114-r1.py'
source=json.loads((root/'SOURCE.json').read_text())
scope=runpy.run_path(str(entry),run_name='synthetic_sealed_guard')
assets=scope['load_assets']()
assert len(assets)==4
for name,raw in assets.items():
 assert hashlib.sha256(raw).hexdigest()==source['assets'][name]['sha256']
 assert len(raw)==source['assets'][name]['bytes']
output=io.StringIO()
with patch.object(sys,'argv',[str(entry),'--install-delivery-context-skill-once']),patch.object(scope['subprocess'],'Popen',side_effect=AssertionError('NO_COMMAND_ALLOWED')),contextlib.redirect_stdout(output):
 code=scope['main']()
row=json.loads(output.getvalue())
assert code==1 and row['error_code']=='DATAX01_ROOT_REQUIRED' and row['commands']==0 and row['once_claimed'] is False
print(json.dumps({'status':'SYNTHETIC_SEALED_ASSETS_AND_WRONG_HOST_ACCEPTED','python':sys.version.split()[0],
 'sealed_assets':len(assets),'commands':0,'provider_requests':0,'messages_sent':0,'production_executed':False},sort_keys=True))
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--docker',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='energy-context-python37-synthetic-') as name:
        root=Path(name)
        receipt=root/'SYNTHETIC-RUNTIME-ACCEPTANCE.json'
        receipt.write_bytes(tests.seal.canonical(tests.synthetic_receipt()))
        files,_=tests.seal.build(receipt)
        tests.seal.write_output(files,root)
        (root/'probe.py').write_text(PROBE,encoding='utf-8')
        command=[args.docker,'run','--rm','--pull','never','--network','none','--user','1000:1000','--read-only',
                 '--mount','type=bind,source='+root.as_posix()+',target=/synthetic,readonly',
                 'python:3.7-slim','python','-I','-B','/synthetic/probe.py']
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
        if result.returncode:
            raise ValueError('PYTHON37_OFFLINE_GUARD_FAILED')
        output=json.loads(result.stdout)
        if not output['python'].startswith('3.7.') or output['commands']!=0:
            raise ValueError('PYTHON37_IDENTITY_FAILED')
        print(json.dumps(output,sort_keys=True))


if __name__=='__main__':
    main()
