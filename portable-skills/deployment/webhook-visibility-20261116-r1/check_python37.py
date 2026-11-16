"""Check the exact sealed receipt read in isolated Linux, without Kubernetes."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'release/energy-webhook-visibility-20261116-r1/verify-energy-webhook-skill-20261116-r1.py'
scope = {'__name__': 'sealed_visibility_path_check'}
exec(compile(path.read_bytes(), str(path), 'exec'), scope)
assert os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename.split('.')[0] != 'datax-01'
expected = Path('/var/lib/portal-energy-webhook-skill-install-20261116-r3/REPORT.json')
assert scope['RECEIPT'] == expected and not expected.exists()
raw = (ROOT / 'release/energy-webhook-skill-20261116-r3/OPERATOR-INSTALL-REPORT.json').read_bytes()
expected.parent.mkdir(parents=True, mode=0o700)
expected.write_bytes(raw); expected.chmod(0o600)
assert scope['fixed_read'](scope['RECEIPT'], scope['RECEIPT_SHA'], 9782) == raw
compile(scope['VISIBILITY_CHILD'], '<sealed-110-file-child>', 'exec')
sys.argv = ['tool', '--verify-readonly']
with contextlib.redirect_stdout(io.StringIO()) as output:
    code = scope['main']()
result = json.loads(output.getvalue().splitlines()[-1])
assert code == 1 and result['queries'] == 0 and result['error_code'] == 'DATAX01_ROOT_REQUIRED'
assert result['workload_modified'] is False and result['persistent_files_modified'] is False
print(json.dumps({'status': 'PYTHON37_ACTUAL_RECEIPT_READ_SEALED_CHILD_AND_ZERO_QUERY_GUARD_PASSED'}))
