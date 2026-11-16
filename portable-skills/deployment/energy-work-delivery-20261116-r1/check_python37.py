"""Exercise the actual sealed entry on an unapproved host, without queries."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'release/energy-work-delivery-20261116-r1/inspect-energy-work-delivery-20261116-r1.py'
scope = {'__name__': 'sealed_wrong_host_check'}
exec(compile(path.read_bytes(), str(path), 'exec'), scope)
asset = scope['ASSET']
child = base64.b64decode(asset['base64'])
assert len(child) == asset['bytes'] and hashlib.sha256(child).hexdigest() == asset['sha256']
compile(child, '<sealed-child>', 'exec')
sys.argv = ['tool', '--inspect-readonly']
with contextlib.redirect_stdout(io.StringIO()) as output:
    code = scope['main']()
result = json.loads(output.getvalue().splitlines()[-1])
assert code == 1 and result['error_code'] == 'DATAX01_ROOT_REQUIRED' and result['queries'] == 0
assert result['workload_modified'] is False and result['task_retry_requested'] is False
print(json.dumps({'status': 'SEALED_CHILD_AND_WRONG_HOST_ZERO_QUERY_GUARD_PASSED'}))
