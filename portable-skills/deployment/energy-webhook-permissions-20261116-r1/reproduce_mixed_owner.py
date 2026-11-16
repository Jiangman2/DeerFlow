"""Reproduce 2.1.7 permission boundaries offline with an actual UID switch."""
import importlib.util
import json
import os
from pathlib import Path
import traceback

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'portable-skills/tests/test_energy_webhook_send_20261116.py'
spec = importlib.util.spec_from_file_location('legacy_webhook_offline_fixture', path)
fixture = importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
assert os.getuid() == 0, 'Isolated container root is required to create the mixed-owner fixture'
results = []
for boundary in ('request_file_chmod', 'delivery_directory_chmod', 'published_output_read'):
    case = fixture.WebhookTests();case.setUp()
    try:
        # Reproduce the AIO command user's files, then replace only the named
        # API-created object with a root-owned object. No HTTP is permitted.
        for item in (case.root, *case.root.rglob('*')):os.chown(item, 1000, 1000)
        if boundary == 'request_file_chmod':
            os.chown(case.request, 0, 1000);case.request.chmod(0o644)
        elif boundary == 'delivery_directory_chmod':
            os.chown(case.directory, 0, 1000);case.directory.chmod(0o770)
        else:
            item = case.output / Path(case.receipt['files'][0]['path']).name
            os.chown(item, 0, 0);item.chmod(0o600)
        os.setegid(1000);os.seteuid(1000)
        try:
            fixture.sender.send(case.project)
            raise AssertionError('Expected legacy PermissionError')
        except PermissionError as exc:
            frames = traceback.extract_tb(exc.__traceback__)
            frame = next(row for row in reversed(frames) if row.filename.endswith('send_webhook.py'))
            results.append({'boundary': boundary, 'error_class': 'PermissionError', 'function': frame.name,
                            'permission_operation': 'chmod' if 'chmod' in (frame.line or '') else 'read',
                            'network_calls': case.transport.call_count, 'uid': os.geteuid()})
            assert case.transport.call_count == 0
        finally:
            os.seteuid(0);os.setegid(0)
    finally:case.tearDown()
print(json.dumps({'schema': 'energy-webhook-legacy-permission-reproduction.v1', 'production_executed': False,
                  'messages_sent': 0, 'cases': results}, sort_keys=True))
