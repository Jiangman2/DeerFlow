"""Retain send-once behavior and verify only final artifacts get shared modes."""
from pathlib import Path

source_path = Path(__file__).with_name('test_energy_webhook_send_20261116.py')
source = source_path.read_text(encoding='utf-8').replace('energy-market-daily-2.1.7', 'energy-market-daily-2.1.8')
source = source.rsplit("\nif __name__ == '__main__':", 1)[0]
exec(compile(source, str(source_path), 'exec'), globals())


class PublicationOwnerTests(unittest.TestCase):
    setUp = WebhookTests.setUp
    tearDown = WebhookTests.tearDown

    def test_final_artifacts_shareable_but_private_state_stays_private(self):
        if os.name != 'posix':return
        for item in self.receipt['files']:
            self.assertEqual(Path(item['path']).stat().st_mode & 0o777, 0o644)
        for name in ('PROJECT.json', 'RESULT.json'):
            self.assertEqual((self.project / name).stat().st_mode & 0o777, 0o600)
        before = {item['path']: Path(item['path']).read_bytes() for item in self.receipt['files']}
        daily.prepare_publication_permissions([Path(item['path']) for item in self.receipt['files']])
        self.assertEqual(before, {item['path']: Path(item['path']).read_bytes() for item in self.receipt['files']})

    def test_reused_generation_restores_artifact_modes_without_render(self):
        item = next(Path(item['path']) for item in self.receipt['files'] if item['path'].endswith('.json'))
        item.chmod(0o600)
        with patch.object(daily, 'png', side_effect=AssertionError('must not rerender')):
            result = daily.build(types.SimpleNamespace(workspace=str(self.project)))
        self.assertTrue(result['reused'])
        if os.name == 'posix':self.assertEqual(item.stat().st_mode & 0o777, 0o644)
        self.assertEqual(result['files'], self.receipt['files'])

    def test_old_217_accepted_receipt_remains_valid_for_diagnostics(self):
        prior = copy.deepcopy(self.receipt);prior['version'] = '2.1.7-portable'
        daily.validate_result(prior, self.project)

    def test_published_copy_permission_failure_names_stage_and_type(self):
        original = sender.bounded_file
        def denied(path, limit):
            if path.parent == self.output and path.suffix == '.json':raise PermissionError(13, 'DO_NOT_EXPORT_PRIVATE_PATH')
            return original(path, limit)
        with patch.object(sender, 'bounded_file', denied), patch('builtins.print') as output:
            code = sender.main(['--workspace', str(self.project)])
        result = json.loads(output.call_args.args[0])
        self.assertEqual(code, 1)
        self.assertEqual(result['error_code'], 'WEBHOOK_PUBLISHED_FILE_PERMISSION_DENIED')
        self.assertEqual(result['file_extension'], '.json')
        self.assertEqual(result['stage'], 'verify_published_outputs')
        self.assertFalse(result['provider_request_started'])
        self.assertNotIn('DO_NOT_EXPORT', output.call_args.args[0]);self.transport.assert_not_called()

    def test_permission_denied_after_send_is_not_reported_as_pre_send(self):
        original = sender.write_record
        def denied(path, value, exclusive=False):
            if not exclusive:raise PermissionError(13, 'PRIVATE_PATH')
            return original(path, value, exclusive)
        with patch.object(sender, 'write_record', denied), patch('builtins.print') as output:
            code = sender.main(['--workspace', str(self.project)])
        result = json.loads(output.call_args.args[0])
        self.assertEqual(code, 1);self.assertTrue(result['provider_request_started'])
        self.assertEqual(result['stage'], 'persist_send_receipt')
        self.transport.assert_called_once()
        with self.assertRaisesRegex(Failure, 'WEBHOOK_PREVIOUS_OUTCOME_UNKNOWN'):sender.send(self.project)
        self.transport.assert_called_once()


if __name__ == '__main__':unittest.main()
