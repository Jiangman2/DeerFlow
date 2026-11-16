"""Keep the update scoped to the accepted Skill and bind current 419 config."""
import ast
import base64
import importlib.util
import io
import json
from pathlib import Path, PurePosixPath
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('publication_fix_seal', HERE / 'seal.py')
builder = importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
FILES, META = builder.build()
host = {'__name__': 'installer_definition'};exec(compile(FILES[builder.NAME], '<installer>', 'exec'), host)
assets = host['load_assets']()
pub = {'__name__': 'publisher_definition'};exec(compile(assets['publisher'], '<publisher>', 'exec'), pub)
ver = {'__name__': 'verifier_definition'};exec(compile(assets['verifier'], '<verifier>', 'exec'), ver)


class InstallerChecks(unittest.TestCase):
    def test_counts_versions_and_payloads_match(self):
        self.assertEqual(host['CANDIDATE_VERSION'], '2.1.8-portable')
        self.assertEqual(pub['CANDIDATE_VERSION'], ver['CANDIDATE_VERSION'])
        self.assertEqual((pub['OLD_COUNT'], pub['CODE_COUNT'], pub['NEW_COUNT']), (110, 109, 110))
        self.assertEqual(host['CANDIDATE_FILES'], 110);self.assertEqual(ver['OFFLINE_TEST_COUNT'], 62)
        self.assertEqual(host['PACKAGE_SHA'], builder.sha(assets['package']))

    def test_only_this_skill_can_change(self):
        operation = host['Operation'](assets)
        with patch.object(host['subprocess'], 'Popen') as process:
            for command in (['patch', 'deployment', 'gateway'], ['get', 'secret', 'deer-flow-env', '-o', 'json'],
                            ['delete', 'deployment', 'gateway'], ['patch', 'deployment', 'provisioner']):
                with self.assertRaises(host['Refused']):operation.command(command)
        process.assert_not_called()

    def test_accepted_217_install_receipt_and_literal_path(self):
        report = builder.ROOT / 'release/energy-webhook-skill-20261116-r3/OPERATOR-INSTALL-REPORT.json'
        def read(path):
            self.assertEqual(str(path).replace('\\', '/'), '/var/lib/portal-energy-webhook-skill-install-20261116-r3/REPORT.json')
            return report.read_bytes()
        with patch.dict(host, {'read': read}):binding = host['qualification_binding']()[3]
        self.assertEqual(binding['operation'], '1ed48e84ae104d2abd3645c01221cf34')
        tree = ast.parse(FILES[builder.NAME])
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'OLD_INSTALL' for t in n.targets))
        value = ast.literal_eval(node.value.args[0]);self.assertTrue(PurePosixPath(value).is_absolute());self.assertNotIn('\\', value)

    def test_core_bytes_and_browser_binding_stay_preserved(self):
        with zipfile.ZipFile(io.BytesIO(assets['package'])) as archive:
            self.assertEqual(len(archive.infolist()), 109)
            self.assertNotIn('energy-market-daily/assets/browser-runtime.json', archive.namelist())
            for name in ('common.py', 'market.py', 'research.py', 'report_renderer.py', 'browser_runtime.py'):
                self.assertEqual(archive.read('energy-market-daily/scripts/' + name),
                    (builder.SKILL.with_name('energy-market-daily-2.1.7') / 'scripts' / name).read_bytes())

    def test_fixtures_hashes_and_actual_extra_suite(self):
        fixtures = json.loads(assets['test_files']);self.assertEqual(set(fixtures), set(ver['FIXTURES']))
        for name, value in fixtures.items():self.assertEqual(builder.sha(base64.b64decode(value)), ver['FIXTURES'][name])
        self.assertEqual(ver['OFFLINE_SUITES']['test_energy_webhook_send_20261116.py'], ['WebhookTests', 'PublicationOwnerTests'])

    def test_uid1000_real_public_mount_and_fixed_image(self):
        op = host['Operation'](assets);pod = op.requested
        self.assertFalse(pod['spec']['automountServiceAccountToken'])
        container = next(row for row in pod['spec']['containers'] if row['name'] == 'qualification')
        self.assertEqual(container['securityContext']['runAsUser'], 1000)
        self.assertTrue(any(row.get('subPath') == 'public' and row.get('readOnly') is True and row['mountPath'] == '/mnt/skills/public' for row in container['volumeMounts']))
        self.assertTrue(all(row['imagePullPolicy'] == 'Never' for row in pod['spec']['containers'] + pod['spec']['initContainers']))

    def test_new_control_and_candidate_mount_share_release_name(self):
        op = host['Operation'](assets)
        self.assertEqual(host['CONTROL'].as_posix(), '/var/lib/portal-energy-publication-fix-20261116-r1')
        container = next(row for row in op.requested['spec']['containers'] if row['name'] == 'qualification')
        target = next(row['subPath'] for row in container['volumeMounts'] if row['mountPath'] == '/mnt/skills/public/energy-market-daily')
        self.assertEqual(target, '.energy-publication-fix-20261116-r1-' + op.operation + '/candidate')

    def test_actual_visibility_child_checks_110_files(self):
        candidates = [value for value in host.values() if isinstance(value, str) and 'EXPECTED_SKILL_COUNT' in value]
        self.assertEqual(len(candidates), 1)
        tree = ast.parse(candidates[0])
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'EXPECTED_SKILL_COUNT' for t in n.targets))
        self.assertEqual(ast.literal_eval(node.value), 110)

    def context_read(self, path):
        text = str(path).replace('\\', '/')
        if text == '/var/lib/portal-energy-reader-work-repair-20261115-r2/REPORT.json':
            return (builder.ROOT / 'release/energy-reader-work-repair-20261115-r2/OPERATOR-REPORT.json').read_bytes()
        if text == '/var/lib/portal-scheduler-concurrency-20261116-r2/REPORT.json':
            return (builder.ROOT / 'release/scheduler-concurrency-20261116-r2/OPERATOR-INSTALL-REPORT.json').read_bytes()
        raise AssertionError('Unexpected path')

    def test_419_exact_spec_is_required_and_no_rollout_completion_claim(self):
        # Obtain the original historical receipts from their known inventories,
        # independently of any Windows path normalization in the installer.
        gateway = builder.ROOT / 'release/energy-reader-work-repair-20261115-r2'
        scheduler = builder.ROOT / 'release/scheduler-concurrency-20261116-r2'
        fixed = {}
        for folder, expected in ((gateway, 'bae5e21202545028c175e20e73ce651cb1018d7850368aeb4cd5bbde4813c1ba'),
                                 (scheduler, '959343f4fe60773657d31f2831d2114ade04a493b35b13fdcaaa81c32a7a4a54')):
            candidates = [p.read_bytes() for p in folder.glob('*.json') if builder.sha(p.read_bytes()) == expected]
            self.assertEqual(len(candidates), 1);fixed[expected] = candidates[0]
        def read(path):
            if str(path).replace('\\', '/').endswith('portal-scheduler-concurrency-20261116-r2/REPORT.json'):
                return fixed['959343f4fe60773657d31f2831d2114ade04a493b35b13fdcaaa81c32a7a4a54']
            return fixed['bae5e21202545028c175e20e73ce651cb1018d7850368aeb4cd5bbde4813c1ba']
        with patch.dict(host, {'read': read}):
            before = host['gateway_context_binding']()
            self.assertEqual(before, host['gateway_context_binding'](builder.GATEWAY419))
            with self.assertRaisesRegex(host['Refused'], 'GATEWAY_CONTEXT_LIVE_IDENTITY_CHANGED'):
                host['gateway_context_binding'](dict(builder.GATEWAY419, generation=420))
        self.assertEqual(before['gateway']['generation'], 418)

    def test_verifier_requires_public_and_private_mode_boundaries(self):
        self.assertIn('PUBLIC_ARTIFACT_MODE_REQUIRED', assets['verifier'].decode())
        self.assertIn('PRIVATE_PROJECT_MODES_CHANGED', assets['verifier'].decode())
        self.assertIn('PUBLICATION_PERMISSION_QUALIFICATION_REQUIRED', FILES[builder.NAME].decode())

    def test_host_python37_syntax_and_sealed_children_compile(self):
        ast.parse(FILES[builder.NAME].decode(), feature_version=(3, 7))
        for key in ('publisher', 'verifier'):compile(assets[key], '<sealed-' + key + '>', 'exec')

    def test_no_replay_or_actual_send_is_in_installer(self):
        self.assertNotIn('send_webhook.py --workspace', FILES[builder.NAME].decode())
        self.assertFalse(META['production_executed']);self.assertFalse(META['gateway_modified'])


if __name__ == '__main__':unittest.main()
