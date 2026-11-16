"""Exercise UID1000 build -> root copy2 -> UID1000 sender without any HTTP."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'portable-skills/tests/test_energy_publication_owner_20261116.py'
spec = importlib.util.spec_from_file_location('publication_owner_fixtures', path)
fixture = importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
assert os.getuid() == 0, 'Requires isolated Linux root to model Gateway-owned published copies'
results = []


def enter_user():os.setegid(1000);os.seteuid(1000)
def enter_gateway():os.seteuid(0);os.setegid(0)


for scenario in ('new_generation', 'legacy_private_published_json', 'rebuild_217_without_recollection'):
    case = fixture.WebhookTests();case.setUp()
    try:
        for item in (case.root, *case.root.rglob('*')):os.chown(item, 1000, 1000)
        os.chown(case.root, 0, 0);case.root.chmod(0o755)
        os.chown(case.output, 0, 0);case.output.chmod(0o777)
        if scenario != 'new_generation':
            source = next(Path(item['path']) for item in case.receipt['files'] if item['path'].endswith('.json'))
            source.chmod(0o600)
        if scenario == 'rebuild_217_without_recollection':
            value = fixture.load(case.project / 'RESULT.json');value['version'] = '2.1.7-portable'
            (case.project / 'RESULT.json').write_text(json.dumps(value), encoding='utf-8')
            enter_user()
            try:
                with patch.object(fixture.daily, 'png', side_effect=fixture.fx.mock_png):
                    case.receipt = fixture.daily.build(types.SimpleNamespace(workspace=str(case.project)))
            finally:enter_gateway()
        # Gateway's verified source uses shutil.copy2, not copyfile. Remove the
        # same-user test doubles before simulating the real root publication.
        for item in case.receipt['files']:
            target = case.output / Path(item['path']).name
            target.unlink();shutil.copy2(item['path'], target)
            assert target.stat().st_uid == 0
        enter_user()
        try:
            if scenario == 'legacy_private_published_json':
                try:fixture.sender.send(case.project)
                except fixture.Failure as exc:
                    assert exc.code == 'WEBHOOK_PUBLISHED_FILE_PERMISSION_DENIED'
                    assert exc.details['file_extension'] == '.json' and exc.details['provider_request_started'] is False
                    assert case.transport.call_count == 0
                    results.append({'scenario': scenario, 'status': exc.code, 'file_extension': '.json', 'transport_calls': 0})
                else:raise AssertionError('Must reject an unreadable Gateway-owned artifact')
            else:
                sent = fixture.sender.send(case.project)
                assert sent['status'] == 'WEBHOOK_SENT' and case.transport.call_count == 1
                assert fixture.sender.send(case.project)['status'] == 'WEBHOOK_ALREADY_SENT'
                assert case.transport.call_count == 1
                results.append({'scenario': scenario, 'status': 'MOCK_SEND_ONCE_ACCEPTED',
                                'uid': os.geteuid(), 'published_owner_uid': 0, 'transport_calls': 1})
        finally:enter_gateway()
    finally:case.tearDown()
print(json.dumps({'schema': 'energy-publication-owner-qualification.v1', 'messages_sent': 0,
                  'real_http_used': False, 'production_executed': False, 'cases': results}, sort_keys=True))
