"""Load exact sealed child and exercise the datax-only entry on Python 3.7."""
import contextlib
import io
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[3]
path=ROOT/'release/scheduler-queue-inspection-20261116-r1/inspect-scheduler-queue-20261116-r1.py'
scope={'__name__':'python37_guard'}
exec(compile(path.read_bytes(),str(path),'exec'),scope)
sys.argv=['tool','--inspect-readonly']
with contextlib.redirect_stdout(io.StringIO()) as output:
    code=scope['main']()
row=json.loads(output.getvalue().splitlines()[-1])
assert code==1 and row['error_code']=='DATAX01_ROOT_REQUIRED' and row['queries']==0
assert row['workload_modified'] is False and row['task_retry_requested'] is False
print(json.dumps({'status':'PYTHON37_ENTRY_AND_ZERO_QUERY_WRONG_HOST_PROTECTION_PASSED'}))
