"""Exercise the real sealed standalone entry without production queries."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'release/energy-webhook-thread-20261116-r2/inspect-energy-webhook-thread-20261116-r2.py'
scope = {'__name__': 'sealed_entry_guard'}
exec(compile(path.read_bytes(), str(path), 'exec'), scope)
asset = scope['ASSET'];child = base64.b64decode(asset['base64'])
assert len(child) == asset['bytes'] and hashlib.sha256(child).hexdigest() == asset['sha256']
compile(child, '<sealed-child>', 'exec')
assert scope['THREADS'] == ['cd2ca7b0-139c-4576-ab5f-c67e9837db10']
sys.argv = ['tool', '--inspect-readonly']
with contextlib.redirect_stdout(io.StringIO()) as output:code = scope['main']()
result = json.loads(output.getvalue().splitlines()[-1])
assert code == 1 and result['error_code'] == 'DATAX01_ROOT_REQUIRED' and result['queries'] == 0
assert result['workload_modified'] is False and result['task_retry_requested'] is False
print(json.dumps({'status': 'SEALED_CHILD_AND_WRONG_HOST_ZERO_QUERY_GUARD_PASSED'}))
