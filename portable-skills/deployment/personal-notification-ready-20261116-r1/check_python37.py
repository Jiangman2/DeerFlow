"""Verify standalone resources and wrong-host refusal using actual Python 3.7."""
import base64
import contextlib
import hashlib
import io
import json
from pathlib import Path

path = Path(__file__).resolve().parents[3] / 'release/energy-personal-notification-ready-20261116-r1/activate-energy-personal-notification-20261116-r1.py'
scope = {'__name__': 'sealed_operator_test'}
exec(compile(path.read_bytes(), str(path), 'exec'), scope)
for name in ('CHILD_ASSET', 'CODE_ASSET'):
    asset = scope[name]
    raw = base64.b64decode(asset['base64'], validate=True)
    assert hashlib.sha256(raw).hexdigest() == asset['sha256']
    compile(raw, '<sealed-resource>', 'exec')
assert scope['load_code']()
assert scope['load_child']()
for flag in ('--activate-personal-notification-once', '--verify-readonly'):
    with contextlib.redirect_stdout(io.StringIO()) as output:
        code = scope['main']([flag])
    result = json.loads(output.getvalue().splitlines()[-1])
    assert code == 1 and result['error_code'] == 'DATAX01_REQUIRED'
    assert result['commands'] == 0 and result['patch_request_count'] == 0
    assert not result['once_claimed'] and not result['persistent_files_modified']
print(json.dumps({'status': 'PYTHON37_TWO_RESOURCES_AND_BOTH_ZERO_COMMAND_ENTRY_GUARDS_PASSED'}))
