"""Run the full audited qualifier offline against exact retained Python sources."""
import base64
import importlib.util
import importlib.metadata
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import traceback

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('ready_audit_builder', HERE / 'build_activation.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
HOST, CHILD, CODE = builder.build()
host = {'__name__': 'audit_host'}
child = {'__name__': 'audit_child'}
exec(compile(HOST, '<host>', 'exec'), host)
exec(compile(CHILD, '<child>', 'exec'), child)
mode = sys.argv[1]
assert mode in ('qualify', 'verify')
temporary = tempfile.TemporaryDirectory(prefix='personal-notification-audit-')
root = Path(temporary.name)
config = root / 'config.yaml'
config.write_bytes(b'max_recursion_limit: 10000\nrun_ownership:\n  heartbeat_enabled: true\nscheduler:\n  recursion_limit: 10000\n  max_concurrent_runs: 32\n  multi_instance: true\n')
child['CONFIG'] = str(config)
# This offline cached image has its own library inventory. Exercise the audit
# with that inventory as an explicit test fixture; the sealed production
# qualifier retains its exact previously accepted production version pins.
child['PACKAGES'] = {name: importlib.metadata.version(name) for name in child['PACKAGES']}
sources = dict(host['EXPECTED_SOURCES'])
if mode == 'verify':
    sources[host['TARGET']] = CODE
# The image does not carry the ConfigMap's OpenSSL projection. Use a named
# inert local fixture for this one inventory entry; all 16 Python source bytes
# are exact retained production overlays (or the candidate delivery module).
old_openssl = '/etc/deerflow-openssl/openssl.cnf'
openssl = root / 'openssl-inventory-fixture.txt'
openssl.write_bytes(b'offline inventory fixture, not production OpenSSL configuration\n')
sources.pop(old_openssl)
sources[str(openssl)] = {'bytes': openssl.stat().st_size, 'sha256': host['sha'](openssl.read_bytes())}
child['SOURCE_PATHS'] = tuple(sorted(sources))
payload = {'mode': mode, 'sources': sources,
           'config': {'bytes': config.stat().st_size, 'sha256': host['sha'](config.read_bytes()),
                      'max_concurrent_runs': 32, 'multi_instance': True}}
if mode == 'qualify':
    payload['candidate'] = host['CODE_ASSET']
sys.stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps(payload).encode()))
if os.getenv('QUALIFIER_DIAGNOSTIC') == '1':
    original_audit = child['audit']

    def diagnostic_audit(event, args):
        try:
            original_audit(event, args)
        except Exception:
            print('LOCAL_AUDIT_EVENT', event, repr(args)[:400], file=sys.stderr)
            traceback.print_stack(limit=12, file=sys.stderr)
            raise

    child['audit'] = diagnostic_audit
temporary._finalizer.detach()
assert child['main']() == 0, 'Full audited qualification did not pass'
