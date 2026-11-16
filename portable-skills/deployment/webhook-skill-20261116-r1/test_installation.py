"""Deployment boundaries for the Skill-only explicit-webhook release."""
import ast
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('webhook_release', HERE / 'seal.py')
builder = importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
FILES, META = builder.build()
host = {'__name__': 'installer_definitions'}; exec(compile(FILES[builder.NAME], '<host>', 'exec'), host)
ASSETS = host['load_assets']()
pub = {'__name__': 'publisher_definitions'}; exec(compile(ASSETS['publisher'], '<publisher>', 'exec'), pub)
ver = {'__name__': 'verifier_definitions'}; exec(compile(ASSETS['verifier'], '<verifier>', 'exec'), ver)


class InstallerChecks(unittest.TestCase):
    def test_versions_file_counts_and_hashes_are_consistent(self):
        self.assertEqual(host['CANDIDATE_VERSION'], '2.1.7-portable')
        self.assertEqual(pub['CANDIDATE_VERSION'], ver['CANDIDATE_VERSION'])
        self.assertEqual((pub['OLD_COUNT'], pub['CODE_COUNT'], pub['NEW_COUNT']), (108, 109, 110))
        self.assertEqual(host['CANDIDATE_FILES'], ver['CANDIDATE_FILES'])
        self.assertEqual(host['CANDIDATE_FILES'], 110)
        self.assertEqual(ver['OFFLINE_TEST_COUNT'], 57)
        self.assertEqual(host['PACKAGE_SHA'], builder.sha(ASSETS['package']))

    def test_gateway_and_credentials_cannot_be_modified(self):
        operation = host['Operation'](ASSETS)
        with patch.object(host['subprocess'], 'Popen') as process:
            for command in (['patch', 'deployment', 'gateway'], ['get', 'secret', 'deer-flow-env', '-o', 'json'],
                            ['delete', 'deployment', 'gateway'], ['patch', 'deployment', 'provisioner']):
                with self.assertRaises(host['Refused']):operation.command(command)
        process.assert_not_called()

    def test_accepted_216_install_is_bound_without_requiring_failed_old_attempts(self):
        report = builder.REPO / 'release/energy-work-preview-skill-20261115-r3/OPERATOR-INSTALL-REPORT.json'
        with patch.dict(host, {'read': lambda path: report.read_bytes()}):
            binding = host['qualification_binding']()[3]
        self.assertEqual(binding['operation'], 'db234834b59a4fbca2cc4d466d861bcd')
        self.assertEqual(host['OLD_INSTALL'].as_posix(), '/var/lib/portal-energy-work-preview-skill-install-20261115-r3/REPORT.json')
        self.assertNotIn('prior_failure_binding', FILES[builder.NAME].decode())
        self.assertEqual(host['OLD_TREE'], builder.OLD_TREE)

    def test_package_preserves_the_five_acquisition_rendering_runtime_sources(self):
        with zipfile.ZipFile(io.BytesIO(ASSETS['package'])) as package:
            self.assertEqual(len(package.infolist()), 109)
            self.assertNotIn('energy-market-daily/assets/browser-runtime.json', package.namelist())
            for name in ('common.py', 'market.py', 'research.py', 'report_renderer.py', 'browser_runtime.py'):
                previous = builder.SKILL.with_name('energy-market-daily-2.1.6') / 'scripts' / name
                self.assertEqual(package.read('energy-market-daily/scripts/' + name), previous.read_bytes())
            self.assertIn('energy-market-daily/scripts/send_webhook.py', package.namelist())

    def test_each_relocated_fixture_is_hashed_and_new_sender_suite_is_included(self):
        fixtures = json.loads(ASSETS['test_files'])
        import base64
        self.assertEqual(set(fixtures), set(ver['FIXTURES']))
        for name, value in fixtures.items():
            self.assertEqual(builder.sha(base64.b64decode(value, validate=True)), ver['FIXTURES'][name])
        self.assertEqual(ver['OFFLINE_SUITES']['test_energy_webhook_send_20261116.py'], ['WebhookTests'])

    def test_actual_ordinary_mount_and_uid1000_are_preserved(self):
        operation = host['Operation'](ASSETS)
        pod = operation.requested
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        container = next(row for row in pod['spec']['containers'] if row['name'] == 'qualification')
        self.assertEqual(container['securityContext']['runAsUser'], 1000)
        self.assertTrue(any(row.get('subPath') == 'public' and row.get('readOnly') is True
                            and row['mountPath'] == '/mnt/skills/public' for row in container['volumeMounts']))
        self.assertTrue(all(row['imagePullPolicy'] == 'Never' for row in pod['spec']['containers'] + pod['spec']['initContainers']))

    def test_current_control_and_candidate_mount_are_the_same_release(self):
        operation = host['Operation'](ASSETS)
        self.assertEqual(host['CONTROL'].as_posix(), '/var/lib/portal-energy-webhook-skill-install-20261116-r2')
        container = next(row for row in operation.requested['spec']['containers'] if row['name'] == 'qualification')
        target = next(row['subPath'] for row in container['volumeMounts'] if row['mountPath'] == '/mnt/skills/public/energy-market-daily')
        self.assertEqual(target, '.energy-webhook-skill-install-20261116-r2-' + operation.operation + '/candidate')
        self.assertIn('.energy-webhook-skill-install-20261116-r2-', ASSETS['publisher'].decode())

    def test_sealed_sources_and_legacy_host_syntax_compile(self):
        ast.parse(FILES[builder.NAME].decode(), feature_version=(3, 7))
        for key in ('publisher', 'verifier'):
            compile(ASSETS[key], '<sealed-' + key + '>', 'exec')


if __name__ == '__main__':
    unittest.main()
