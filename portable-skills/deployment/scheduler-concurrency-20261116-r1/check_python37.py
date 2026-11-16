"""Load sealed helper and prove wrong-host entry cannot query or mutate."""
import contextlib
import io
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
path=ROOT/'release/scheduler-concurrency-20261116-r2/activate-scheduler-concurrency-20261116-r2.py'
scope={'__name__':'concurrency_guard'}
exec(compile(path.read_bytes(),str(path),'exec'),scope)
assert scope['load_child']()
with contextlib.redirect_stdout(io.StringIO()) as output:
    result=scope['main'](['--activate-concurrency-once'])
row=json.loads(output.getvalue().splitlines()[-1])
assert result==1 and row['error_code']=='DATAX01_REQUIRED' and row['commands']==0
assert row['patch_request_count']==row['configmap_create_requests']==0
assert row['once_claimed'] is False and row['scheduler_max_concurrent_runs_after']==32
print(json.dumps({'status':'PYTHON37_SEALED_CHILD_AND_ZERO_QUERY_WRONG_HOST_GUARD_PASSED'}))
