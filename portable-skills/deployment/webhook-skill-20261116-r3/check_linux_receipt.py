"""Check the actual POSIX receipt path in an isolated offline Linux container."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'release/energy-webhook-skill-20261116-r3/install-energy-webhook-skill-20261116-r3.py'
scope = {'__name__': 'sealed_posix_path_check'}
exec(compile(path.read_bytes(), str(path), 'exec'), scope)
assets = scope['load_assets']()
expected = Path('/var/lib/portal-energy-work-preview-skill-install-20261115-r3/REPORT.json')
assert scope['OLD_INSTALL'] == expected and scope['OLD_INSTALL'].is_absolute()
assert os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename.split('.')[0] != 'datax-01'
assert not expected.exists(), 'Use an isolated container with an empty /var/lib tmpfs'
raw = (ROOT / 'release/energy-work-preview-skill-20261115-r3/OPERATOR-INSTALL-REPORT.json').read_bytes()
expected.parent.mkdir(mode=0o700, parents=True)
expected.write_bytes(raw); expected.chmod(0o600)
report, runtime, runtime_sha, binding = scope['qualification_binding']()
assert binding['path'] == str(expected) and binding['operation'] == 'db234834b59a4fbca2cc4d466d861bcd'
assert scope['sha'](raw) == binding['sha256']
sys.argv = ['tool', '--install-webhook-skill-once']
with contextlib.redirect_stdout(io.StringIO()) as output:
    code = scope['main']()
result = json.loads(output.getvalue().splitlines()[-1])
assert code == 1 and result['once_claimed'] is False and result['skill_updated'] is False
assert result.get('commands', 0) == 0 and result['gateway_modified'] is False
print(json.dumps({'status': 'ACTUAL_POSIX_RECEIPT_READ_AND_WRONG_HOST_GUARD_PASSED',
                  'sealed_assets_loaded': len(assets), 'production_queries': 0}))
