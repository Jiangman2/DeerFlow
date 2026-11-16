"""Retain 27 scoped evidence tests and check the personal channel observation."""
from pathlib import Path
previous = Path(__file__).resolve().parent.parent / 'energy-webhook-permissions-20261116-r2/test_inspection.py'
source = previous.read_text(encoding='utf-8').rsplit("\nif __name__ == '__main__':unittest.main()", 1)[0]
source = source.replace('cd2ca7b0-139c-4576-ab5f-c67e9837db10', '9a762837-e4c4-4376-a14d-fcefbacd4bbc')
# The inherited nested fixture loads the old literal separately. Replace only
# that test module's THREAD variable after its definitions have loaded.
exec(compile(source, '<retained-personal-inspection-tests>', 'exec'), globals())
THREAD = seal.THREAD


class PersonalChecks(unittest.TestCase):
    def test_channel_sql_is_thread_and_owner_bound(self):
        query = c['CHANNEL_ROUTE_SQL']
        self.assertIn('m.thread_id=$1 AND m.owner_user_id=$2', query)
        self.assertIn('LIMIT 5', query)
        for forbidden in ('SELECT ciphertext', 'external_conversation_id', 'UPDATE ', 'DELETE '):self.assertNotIn(forbidden, query)

    def test_channel_mapping_observation_is_not_delivery_claim(self):
        class DB:
            def transaction(self, **kwargs):return Transaction()
            async def fetchval(self, query, thread):assert thread == THREAD;return 'synthetic-owner'
            async def fetch(self, query, thread, owner):
                assert query == c['CHANNEL_ROUTE_SQL'] and thread == THREAD and owner == 'synthetic-owner'
                return [{'provider': 'feishu', 'created_at': NOW, 'updated_at': NOW, 'connection_status': 'connected',
                         'connection_owner_matches': True, 'topic_present': False}]
        async def before(*args):return {'source': 'NO_SCHEDULE_OCCURRENCE_FOR_THREAD'}
        raw = b'def final():\n return OutboundMessage(text=response_text,is_final=True)\n'
        with patch.dict(c, {'_before_personal_delivery': before, 'read_source': lambda path: raw}):
            result = asyncio.run(c['diagnose_thread'](DB(), THREAD))
        self.assertTrue(result['work_channel_route']['mapping_is_not_actual_send_proof'])
        self.assertEqual(result['work_channel_route']['mappings'][0]['provider'], 'feishu')
        self.assertEqual(result['channel_manager_source']['final_sites_passing_response_text'], 1)
        self.assertFalse(result['channel_manager_source']['application_imported'])

    def test_channel_failure_keeps_prior_notification_result(self):
        async def before(*args):return {'work_notification': {'outbox': [{'status': 'sent'}]}}
        with patch.dict(c, {'_before_personal_delivery': before}):result = asyncio.run(c['diagnose_thread'](object(), THREAD))
        self.assertEqual(result['work_notification']['outbox'][0]['status'], 'sent')
        self.assertEqual(result['personal_delivery_inspection']['status'], 'INCOMPLETE')


if __name__ == '__main__':unittest.main()
