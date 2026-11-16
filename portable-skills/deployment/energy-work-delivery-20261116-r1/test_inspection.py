"""Keep prior privacy/receipt regressions and verify single-chat delivery scope."""
import asyncio
import json
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
OTHER = '945b81c9-225f-4a12-a033-19d13da92903'
source = (HERE.parent / 'energy-two-task-comparison-20261116-r1/test_inspection.py').read_text(encoding='utf-8')
# The prior comparison's second thread is a synthetic negative-scope fixture.
# Keep that fixture without adding a second production chat to this inspector.
source = source.replace("c['THREADS'][1]", repr(OTHER))
source = source.replace("tree=ast.parse(source)",
    "source=source.replace(\"c['THREADS'][1]\", " + repr(repr(OTHER)) + ")\n"
    "for before,after in seal.RENAMES.items():source=source.replace(before,after)\n"
    "tree=ast.parse(source)")
for before, after in {
    'TWO_TASKS_READONLY_COLLECTED': 'NAMED_TASK_READONLY_COLLECTED',
    'TWO_TASKS_PARTIALLY_OBSERVED': 'NAMED_TASK_PARTIALLY_OBSERVED',
}.items():
    source = source.replace(before, after)
source = source.replace("if __name__=='__main__':unittest.main()", '')
exec(compile(source, '<retained-readonly-inspection-tests>', 'exec'), globals())


class NamedNotificationChecks(unittest.TestCase):
    def test_only_one_real_chat_is_embedded(self):
        expected = ['d99e944a-6d4b-4f22-9f53-f85d04a12fc0']
        self.assertEqual(h['THREADS'], expected)
        self.assertEqual(list(c['THREADS']), expected)
        self.assertNotIn(OTHER, HOST.decode())
        self.assertNotIn(OTHER, CHILD.decode())

    def test_wrong_thread_cannot_start_database_access(self):
        with patch.dict(c, {'runtime_modules': lambda: self.fail('database access before scope guard')}):
            result = asyncio.run(c['observe']({'thread_ids': [OTHER]}))
        self.assertEqual(result['error_code'], 'EXACT_NAMED_THREAD_REQUIRED')

    def test_notification_binding_and_send_state_are_distinct(self):
        class DB:
            def __init__(self, configured, sent):
                self.configured, self.sent = configured, sent

            async def fetchval(self, query, thread):
                assert thread == THREAD
                return 'synthetic-owner'

            async def fetch(self, query, *args):
                if query == c['NOTIFY_RUN_SQL']:
                    return []
                if query == c['NOTIFY_OCCURRENCE_SQL']:
                    return [{'id': 'synthetic-occurrence', 'task_id': 'synthetic-task', 'run_id': 'synthetic-run',
                             'status': 'success', 'created_at': NOW, 'finished_at': NOW.isoformat(), 'trigger': 'manual'}]
                if query == c['NOTIFY_OUTBOX_SQL']:
                    return [{'namespace': c['sha']('scheduled-work-notifications:v1:production'),
                             'status': self.sent, 'revision': 1, 'updated_at': NOW.timestamp(),
                             'expires_at': NOW.timestamp() + 3600, 'payload_retained': False}] if self.sent else []
                raise AssertionError(query)

            async def fetchrow(self, query, *args):
                if query == c['NOTIFY_TASK_SQL']:
                    return {'id': 'synthetic-task', 'user_id': 'synthetic-owner', 'status': 'enabled',
                            'notification_configured': self.configured,
                            'connection_id': 'synthetic-connection' if self.configured else None,
                            'binding_key': 'synthetic-binding' if self.configured else None}
                if query == c['NOTIFY_CONNECTION_SQL']:
                    return {'status': 'connected', 'provider': 'feishu', 'updated_at': NOW,
                            'last_seen_at': None, 'notification_target_present': True}
                raise AssertionError(query)

        for configured, sent in [(False, None), (True, 'pending'), (True, 'failed'), (True, 'sent')]:
            result = asyncio.run(c['inspect_notification'](DB(configured, sent), THREAD, {}, ORIGIN, 'production'))
            self.assertEqual(result['occurrences'][0]['native_notification_configured'], configured)
            self.assertEqual([row['status'] for row in result['outbox']], [sent] if sent else [])
            self.assertFalse(result['notification_ciphertext_read'])
            self.assertTrue(result['send_error_reason_not_persisted_by_current_outbox'])


if __name__ == '__main__':
    unittest.main()
