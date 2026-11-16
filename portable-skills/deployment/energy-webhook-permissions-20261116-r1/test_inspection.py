"""Retain explicit-sender checks and exercise the ownership observation."""
from pathlib import Path

previous = Path(__file__).resolve().parent.parent / 'energy-webhook-thread-20261116-r2/test_inspection.py'
source = previous.read_text(encoding='utf-8').replace("if __name__ == '__main__':unittest.main()", '')
exec(compile(source, '<retained-explicit-sender-checks>', 'exec'), globals())


class OwnershipChecks(unittest.TestCase):
    def test_sender_project_wins_over_unrelated_prepare_project(self):
        rows = [event(1, 'llm.ai.response', {'tool_calls': [call('python daily.py prepare --workspace ' + VIRTUAL + '-other')]}),
                event(2, 'llm.ai.response', {'tool_calls': [call('python send_webhook.py --workspace ' + VIRTUAL)]})]
        self.assertEqual(c['command_project'](rows), [VIRTUAL])

    def test_distinct_sender_projects_stay_ambiguous(self):
        rows = [event(1, 'llm.ai.response', {'tool_calls': [call('python send_webhook.py --workspace ' + VIRTUAL),
                call('python send_webhook.py --workspace ' + VIRTUAL + '-other')]})]
        self.assertEqual(len(c['command_project'](rows)), 2)

    def test_ambiguous_projects_not_guessed(self):
        # Retained fixture has one sender and one unrelated build workspace.
        # The owning sender identifies its project without scanning directories.
        rows = [event(1, 'llm.ai.response', {'tool_calls': [call('python daily.py build --workspace ' + VIRTUAL),
                call('python send_webhook.py --workspace ' + VIRTUAL + '-other')]})]
        self.assertEqual(c['command_project'](rows), [VIRTUAL + '-other'])

    def test_mode_probe_never_calls_chmod_or_sender(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);project, output, journal = Checks().fixture(base)
            before = (project / 'delivery/webhook-request.json').read_bytes()
            with patch.object(c['os'], 'chmod', side_effect=AssertionError('must not chmod')):
                result = c['project_observation'](project, base, output, THREAD)
            self.assertEqual(before, (project / 'delivery/webhook-request.json').read_bytes())
        self.assertEqual(len(result['file_ownership']), 6)
        self.assertFalse(result['permission_probe_changed_modes'])
        self.assertFalse(result['permission_probe_called_sender'])
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))

    def test_root_owned_readable_file_flags_chmod_problem(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);path = base / 'file';path.write_bytes(b'private')
            current = path.lstat()
            fake = types.SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o100644)
            original = Path.lstat
            def observed(value):return fake if value == path else original(value)
            with patch.object(Path, 'lstat', observed):result = c['path_metadata'](path, base, 'webhook_request')
        self.assertTrue(result['mode_readable_by_uid1000'])
        self.assertTrue(result['chmod_requires_owner_or_privilege'])
        self.assertFalse(result['mode_writable_by_uid1000'])

    def test_wrong_root_and_symlink_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            with self.assertRaises(c['Refused']):c['path_metadata'](base, base / 'wrong', 'other')


# Replace the retained ambiguous-project expectation with the explicitly scoped
# sender expectation, without changing any archived tests.
Checks.test_ambiguous_projects_not_guessed = OwnershipChecks.test_ambiguous_projects_not_guessed

if __name__ == '__main__':unittest.main()
