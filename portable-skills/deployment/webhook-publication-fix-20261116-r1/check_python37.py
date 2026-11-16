"""Check actual Linux receipt paths and load the sealed host on Python 3.7."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'release/energy-publication-fix-20261116-r1/install-energy-publication-fix-20261116-r1.py'
scope = {'__name__': 'sealed_publication_guard'};exec(compile(path.read_bytes(), str(path), 'exec'), scope)
assets = scope['load_assets']();assert len(assets) == 4
assert os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename.split('.')[0] != 'datax-01'
fixtures = {
    '/var/lib/portal-energy-webhook-skill-install-20261116-r3/REPORT.json':
        'release/energy-webhook-skill-20261116-r3/OPERATOR-INSTALL-REPORT.json',
    '/var/lib/portal-energy-reader-work-repair-20261115-r2/REPORT.json':
        'release/energy-reader-work-repair-20261115-r2/REPORT-RECONSTRUCTED.json',
    '/var/lib/portal-scheduler-concurrency-20261116-r2/REPORT.json':
        'release/scheduler-concurrency-20261116-r2/OPERATOR-INSTALL-REPORT.json',
}
for destination, source in fixtures.items():
    target = Path(destination);assert not target.exists(), 'Use isolated tmpfs /var/lib'
    target.parent.mkdir(mode=0o700, parents=True);target.write_bytes((ROOT / source).read_bytes());target.chmod(0o600)
assert str(scope['OLD_INSTALL']) == '/var/lib/portal-energy-webhook-skill-install-20261116-r3/REPORT.json'
binding = scope['qualification_binding']()[3]
assert binding['operation'] == '1ed48e84ae104d2abd3645c01221cf34'
scope['gateway_context_binding']({'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 419,
                                'spec_sha256': '08a8afffb65eaca6259fe8b01111a5c68a2523fba639f17a4b25611e9ccb98c8'})
sys.argv = ['tool', '--install-publication-fix-once']
with contextlib.redirect_stdout(io.StringIO()) as output:code = scope['main']()
result = json.loads(output.getvalue().splitlines()[-1])
assert code == 1 and result['once_claimed'] is False and result['skill_updated'] is False
assert result.get('commands', 0) == 0 and result['gateway_modified'] is False
print(json.dumps({'status': 'PYTHON37_ACTUAL_THREE_RECEIPT_PATHS_AND_ZERO_QUERY_GUARD_PASSED',
                  'sealed_assets_loaded': len(assets), 'messages_sent': 0}))
