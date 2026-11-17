"""Retain prior read-only transport boundaries and check this chat's snapshot."""
from pathlib import Path

previous = Path(__file__).resolve().parent.parent / 'energy-personal-delivery-20261116-r1/test_inspection.py'
source = previous.read_text(encoding='utf-8').rsplit("\nif __name__ == '__main__':unittest.main()", 1)[0]
source = source.replace('9a762837-e4c4-4376-a14d-fcefbacd4bbc', 'a5f059b0-1858-4687-8d16-2de8f853c418')
exec(compile(source, '<retained-personal-readonly-boundaries>', 'exec'), globals())


class CurrentNotificationChecks(unittest.TestCase):
    def database(self, owner='owner-a'):
        url = 'https://cnoocclaw.cnooc.com.cn/reports/energy-daily/' + THREAD + '/2026-11-16.html'
        answer = '[点击查看日报网页](' + url + ')\nnever-export-private-message'
        class DB:
            def transaction(self, **kwargs):
                assert kwargs == {'readonly': True, 'isolation': 'repeatable_read'}
                return Transaction()
            async def execute(self, sql):
                assert sql.startswith('SET LOCAL ')
            async def fetchval(self, sql, *params):
                if sql == 'SHOW transaction_read_only':return 'on'
                assert params == (THREAD,)
                return owner
            async def fetch(self, sql, *params):
                if sql == c['NOTIFY_RUN_SQL']:
                    assert params == (THREAD,)
                    return [{'run_id': 'run-a', 'status': 'success', 'created_at': NOW, 'updated_at': NOW, 'message_bytes': len(answer.encode()), 'last_ai_message': answer}]
                if sql == c['NOTIFY_OCCURRENCE_SQL']:
                    assert params == (THREAD,)
                    return [{'id': 'occ-a', 'task_id': 'task-a', 'run_id': 'run-a', 'status': 'success', 'trigger': 'scheduled', 'created_at': NOW, 'finished_at': NOW}]
                if sql == c['NOTIFY_EVENT_SQL']:
                    assert params == (THREAD, 'run-a')
                    return [{'seq': 44, 'content': json.dumps({'content': answer}), 'bytes': len(answer.encode())}]
                if sql == c['NOTIFY_OUTBOX_SQL']:
                    assert params == (c['sha']('occ-a'), c['sha'](owner))
                    return [{'status': 'sent', 'revision': 1, 'updated_at': NOW.timestamp(), 'expires_at': NOW.timestamp() + 86400, 'payload_retained': False}]
                raise AssertionError('Unexpected database read')
            async def fetchrow(self, sql, *params):
                if sql == c['PERSONAL_INPUT_SQL']:
                    assert params == (THREAD, 'run-a', owner)
                    return {'original_input': '/energy-market-daily 生成日报', 'task_id': 'task-a', 'occurrence_id': 'occ-a'}
                if sql == c['NOTIFY_TASK_SQL']:
                    assert params == ('task-a',)
                    return {'user_id': owner, 'notification_configured': True, 'connection_id': 'connection-a', 'binding_key': 'private-binding', 'status': 'enabled'}
                if sql == c['PERSONAL_PROMPT_SQL']:
                    assert params == ('task-a', owner)
                    return {'prompt': '/energy-market-daily 生成日报'}
                if sql == c['NOTIFY_CONNECTION_SQL']:
                    assert params == ('connection-a', owner)
                    return {'status': 'connected', 'provider': 'feishu', 'updated_at': NOW, 'last_seen_at': NOW, 'notification_target_present': True}
                raise AssertionError('Unexpected database row read')
        return DB(), url

    def test_observer_uses_small_notification_path_only(self):
        import ast
        node = next(n for n in ast.parse(CHILD.decode()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'observe')
        calls = {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        self.assertIn('diagnose_current_personal', calls)
        self.assertNotIn('diagnose_thread', calls)

    def test_links_are_same_thread_unsigned_and_real_calendar_dates(self):
        url = 'https://cnoocclaw.cnooc.com.cn/reports/energy-daily/' + THREAD + '/2026-11-16.html'
        value = '[点击查看日报网页](' + url + ')'
        facts = c['reader_facts'](value, THREAD)
        self.assertEqual(facts['valid_current_html_links'], [url])
        self.assertFalse(facts['raw_text_exported'])
        self.assertEqual(facts['standalone_reader_lines'], 1)
        for bad in (url.replace(THREAD, 'foreign'), url + '?token=private', url.replace('2026-11-16', '2026-02-30'), url.replace('cnoocclaw.cnooc.com.cn', 'evil.invalid')):
            self.assertEqual(c['reader_facts'](bad, THREAD)['valid_current_html_links'], [])

    def test_original_input_query_requires_thread_run_and_owner(self):
        self.assertIn('thread_id=$1 AND run_id=$2 AND user_id=$3 LIMIT 1', c['PERSONAL_INPUT_SQL'])
        self.assertIn('left(kwargs_json::jsonb', c['PERSONAL_INPUT_SQL'])
        self.assertTrue(c['explicit_daily']('/energy-market-daily 生成今天日报'))
        self.assertFalse(c['explicit_daily']('普通任务'))

    def test_small_snapshot_reports_link_and_delivery_without_raw_messages(self):
        db, url = self.database()
        with patch.dict(c, {'read_source': lambda path: b'synthetic-source-with-no-execution'}):
            result = asyncio.run(c['diagnose_current_personal'](db, THREAD))
        self.assertEqual(result['status'], 'TASK_DURABLE_STATE_OBSERVED_READONLY')
        self.assertEqual(result['runs'][0]['stored_result']['valid_current_html_links'], [url])
        self.assertTrue(result['runs'][0]['run_metadata_matches_occurrence'])
        self.assertEqual(result['outbox'][0]['status'], 'sent')
        self.assertFalse(result['formatter_functions_executed'])
        self.assertFalse(result['project_files_read'])
        text = json.dumps(result)
        for private in ('never-export-private-message', 'private-binding', 'owner-a', 'task-a'):
            self.assertNotIn(private, text)

    def test_snapshot_requires_trusted_thread_owner(self):
        db, _ = self.database(owner=None)
        with patch.dict(c, {'read_source': lambda path: b'synthetic-source'}), self.assertRaises(c['Refused']):
            asyncio.run(c['diagnose_current_personal'](db, THREAD))


if __name__ == '__main__':
    unittest.main()
