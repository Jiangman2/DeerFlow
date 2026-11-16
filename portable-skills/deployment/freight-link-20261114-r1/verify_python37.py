"""Exercise sealed assets and wrong-host refusal without production access."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

repo = Path(__file__).resolve().parents[3]
release = repo / 'release/energy-daily-freight-links-20261114-r1'
source = json.loads((release / 'SOURCE.json').read_text(encoding='utf-8'))
entry = release / 'install-energy-freight-links-20261114-r1.py'
scope = runpy.run_path(str(entry), run_name='sealed_operator_readonly_validation')
assets = scope['load_assets']()
assert len(assets) == 4
for name, raw in assets.items():
    assert hashlib.sha256(raw).hexdigest() == source['assets'][name]['sha256']
    assert len(raw) == source['assets'][name]['bytes']
output = io.StringIO()
with patch.object(sys, 'argv', [str(entry), '--install-freight-links-once']), \
     patch.object(scope['subprocess'], 'Popen', side_effect=AssertionError('NO_COMMAND_ALLOWED')), \
     contextlib.redirect_stdout(output):
    code = scope['main']()
result = json.loads(output.getvalue())
assert code == 1 and result['error_code'] == 'DATAX01_ROOT_REQUIRED'
assert result['commands'] == 0 and result['once_claimed'] is False and result['skill_updated'] is False
print(json.dumps({'status': 'SEALED_ASSETS_AND_WRONG_HOST_PROTECTION_PASSED',
                  'python': sys.version.split()[0], 'sealed_assets': len(assets),
                  'commands': 0, 'credentials_read': False, 'provider_requests': 0,
                  'production_modified': False}, sort_keys=True))
