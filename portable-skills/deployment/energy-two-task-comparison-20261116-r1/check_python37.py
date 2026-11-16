"""Verify the sealed two-chat host entry without touching Kubernetes."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
path=ROOT/'release/energy-two-task-comparison-20261116-r1/inspect-energy-two-results-20261116-r1.py'
scope={'__name__':'python37_comparison_guard'}
exec(compile(path.read_bytes(),str(path),'exec'),scope)
asset=scope['ASSET'];child=base64.b64decode(asset['base64'])
assert len(child)==asset['bytes'] and hashlib.sha256(child).hexdigest()==asset['sha256']
compile(child,'<sealed-child>','exec')
sys.argv=['tool','--inspect-readonly']
with contextlib.redirect_stdout(io.StringIO()) as output:code=scope['main']()
row=json.loads(output.getvalue().splitlines()[-1])
assert code==1 and row['error_code']=='DATAX01_ROOT_REQUIRED' and row['queries']==0
assert row['workload_modified'] is False and row['task_retry_requested'] is False
print(json.dumps({'status':'PYTHON37_SEALED_CHILD_AND_WRONG_HOST_GUARD_PASSED'}))
