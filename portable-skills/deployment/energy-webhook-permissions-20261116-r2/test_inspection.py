"""Observe all explicitly named projects, without choosing a retry target."""
from pathlib import Path
previous = Path(__file__).resolve().parent.parent / 'energy-webhook-permissions-20261116-r1/test_inspection.py'
# Load the inherited test definitions using this sealer and this __file__.
source = previous.read_text(encoding='utf-8').rsplit("\nif __name__ == '__main__':unittest.main()", 1)[0]
exec(compile(source, '<retained-readonly-tests>', 'exec'), globals())


class MultipleProjects(unittest.TestCase):
    def test_two_real_sender_paths_are_both_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);Checks().fixture(base)
            second = base / 'workspace/second';second.mkdir()
            class DB:
                def transaction(self, **kwargs):
                    assert kwargs == {'readonly': True, 'isolation': 'repeatable_read'};return Transaction()
                async def fetchval(self, sql, thread):assert thread == THREAD;return 'synthetic-owner'
                async def fetch(self, sql, *args):
                    if sql == c['RUN_SQL']:return [{'run_id': 'synthetic-run'}]
                    assert sql == c['EVENT_SQL']
                    return [event(1, 'llm.ai.response', {'tool_calls': [call('python send_webhook.py --workspace ' + VIRTUAL),
                        call('python send_webhook.py --workspace /mnt/user-data/workspace/second')]})]
            class Paths:
                def sandbox_user_data_dir(self, thread, *, user_id):
                    assert thread == THREAD and user_id == 'synthetic-owner';return base
            module = types.ModuleType('deerflow.config.paths');module.get_paths = lambda: Paths()
            async def before(*args):return {'project_delivery': {'status': 'SINGLE_OWNING_PROJECT_NOT_ESTABLISHED'}}
            with patch.dict(c, {'_single_project_diagnosis': before}), patch.dict('sys.modules', {'deerflow.config.paths': module}):
                result = asyncio.run(c['diagnose_thread'](DB(), THREAD))
        self.assertEqual(len(result['project_delivery_candidates']), 2)
        self.assertEqual(result['project_delivery_candidates'][0]['result_version'], '2.1.7-portable')
        self.assertEqual(len(result['project_delivery_candidates'][0]['file_ownership']), 6)
        self.assertFalse(result['project_selected_for_retry_or_mutation'])
        for secret in (directory, 'SYNTHETIC_PRIVATE', TARGET):self.assertNotIn(secret, json.dumps(result))

    def test_more_than_four_projects_reads_no_files(self):
        class DB:
            def transaction(self, **kwargs):return Transaction()
            async def fetchval(self, *args):return 'synthetic-owner'
            async def fetch(self, sql, *args):
                if sql == c['RUN_SQL']:return [{'run_id': 'synthetic-run'}]
                return [event(1, 'llm.ai.response', {'tool_calls': [call('python send_webhook.py --workspace ' + VIRTUAL + str(i)) for i in range(5)]})]
        async def before(*args):return {'project_delivery': {'status': 'SINGLE_OWNING_PROJECT_NOT_ESTABLISHED'}}
        with patch.dict(c, {'_single_project_diagnosis': before, 'project_observation': lambda *args:self.fail('Unbounded reads')}):
            result = asyncio.run(c['diagnose_thread'](DB(), THREAD))
        self.assertEqual(result['multiple_sender_project_inspection']['error_code'], 'NAMED_SENDER_PROJECT_COUNT_BOUND')


if __name__ == '__main__':unittest.main()
