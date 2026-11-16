"""Load every sealed installer asset on Python 3.7, then refuse the wrong host."""
import ast
import contextlib
import io
import json
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[3]
path = root / 'release/energy-webhook-skill-20261116-r2/install-energy-webhook-skill-20261116-r2.py'
scope = {'__name__': 'sealed_webhook_installer_guard'}
exec(compile(path.read_bytes(), str(path), 'exec'), scope)
assets = scope['load_assets']()
assert set(assets) == {'publisher', 'verifier', 'package', 'test_files'}
sys.argv = ['tool', '--install-webhook-skill-once']
with contextlib.redirect_stdout(io.StringIO()) as output:
    code = scope['main']()
result = json.loads(output.getvalue().splitlines()[-1])
assert code == 1 and result['once_claimed'] is False and result['skill_updated'] is False
assert result.get('commands', 0) == 0 and result['gateway_modified'] is False
print(json.dumps({'status': 'PYTHON37_ALL_SEALED_ASSETS_AND_WRONG_HOST_GUARD_PASSED'}))
