"""Check the actual standalone resource and wrong-host refusal on Python 3.7."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

path = Path(__file__).resolve().parents[3] / 'release/energy-personal-notification-inspection-20261116-r2/inspect-energy-personal-notification-20261116-r2.py'
scope = {'__name__': 'sealed_readonly_entry'};exec(compile(path.read_bytes(), str(path), 'exec'), scope)
asset = scope['ASSET'];child = base64.b64decode(asset['base64'])
assert len(child) == asset['bytes'] and hashlib.sha256(child).hexdigest() == asset['sha256']
compile(child, '<readonly-ownership-child>', 'exec')
sys.argv = ['tool', '--inspect-readonly']
with contextlib.redirect_stdout(io.StringIO()) as stream:code = scope['main']()
result = json.loads(stream.getvalue().splitlines()[-1])
assert code == 1 and result['error_code'] == 'DATAX01_ROOT_REQUIRED' and result['queries'] == 0
assert result['workload_modified'] is False and result['task_retry_requested'] is False
print(json.dumps({'status': 'SEALED_CHILD_AND_WRONG_HOST_ZERO_QUERY_GUARD_PASSED'}))
