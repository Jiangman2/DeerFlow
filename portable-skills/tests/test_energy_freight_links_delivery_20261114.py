"""Conversation-link composition checks; offline, no publication or sending."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.1'
ENTRY = SKILL / 'scripts/daily.py'


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SKILL / 'scripts'))
try:
    delivery = load_module('energy_delivery_links_20261114', SKILL / 'scripts/delivery_text.py')
finally:
    sys.path.pop(0)
fixtures = load_module('energy_delivery_original_fixtures_20261114',
                       REPO / 'portable-skills/diagnostics/energy-market-daily-2.1.0-final-text-r1/test_final_text.py')
fixtures.ENTRY = ENTRY
URL, THREAD = fixtures.URL, fixtures.THREAD


class ConversationLinkDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.project = Path(self.temporary.name).resolve() / 'project'
        self.result, self.data, self.original_text = fixtures.seed(self.project)

    def tearDown(self):
        self.temporary.cleanup()

    def summarize(self, projection):
        return delivery.publication_summary(projection, self.result['files'])

    def test_successful_publication_retains_link(self):
        for artifact in (False, True):
            actual = self.summarize(fixtures.projection(self.result, artifact=artifact))
            self.assertTrue(actual['confirmed'])
            self.assertEqual(actual['conversation_url'], URL)

    def test_partial_publication_retains_link_without_success_claim(self):
        projection = fixtures.projection(self.result, artifact=True)
        projection['artifact']['presented_files'].pop()
        actual = self.summarize(projection)
        self.assertFalse(actual['confirmed'])
        self.assertEqual(actual['conversation_url'], URL)
        text = delivery.compose(self.data, self.original_text, actual)
        self.assertIn('附件发布状态尚未确认', text)
        self.assertNotIn('已在本次对话发布', text)
        self.assertTrue(text.endswith('[点击查看完整日报](' + URL + ')\n'))

    def test_failed_host_result_has_link_with_or_without_artifact(self):
        for artifact in (False, True):
            projection = fixtures.projection(self.result)
            projection.update(status='error', content='Error: Synthetic delivery failure\nFile delivery incomplete. conversation_url: ' + URL)
            if artifact:
                projection['artifact'] = {'publication_status': 'failed', 'presented_files': [], 'conversation_url': URL}
            actual = self.summarize(projection)
            self.assertFalse(actual['confirmed'])
            self.assertTrue(actual['conversation_link_available'])
            self.assertEqual(actual['conversation_url'], URL)

    def test_missing_publication_metadata_does_not_hide_host_link(self):
        for missing in ('requested_files', 'publication_run_id', 'status'):
            projection = fixtures.projection(self.result, artifact=missing == 'publication_run_id')
            if missing == 'publication_run_id':
                projection['artifact'].pop(missing)
            else:
                projection.pop(missing)
            actual = self.summarize(projection)
            self.assertFalse(actual['confirmed'])
            self.assertEqual(actual['conversation_url'], URL)

    def test_artifact_only_url_is_accepted_as_host_field(self):
        projection = fixtures.projection(self.result, artifact=True)
        projection['content'] = 'Successfully presented files'
        actual = self.summarize(projection)
        self.assertTrue(actual['confirmed'])
        self.assertEqual(actual['conversation_url'], URL)

    def test_host_link_is_not_bound_to_requested_file_set(self):
        projection = fixtures.projection(self.result)
        projection['requested_files'][0] = '/different/' + Path(self.result['files'][0]['path']).name
        actual = self.summarize(projection)
        self.assertFalse(actual['confirmed'])
        self.assertEqual(actual['conversation_url'], URL)

    def test_url_conflict_between_artifact_and_visible_text_is_rejected(self):
        projection = fixtures.projection(self.result, artifact=True)
        projection['artifact']['conversation_url'] = URL.replace(THREAD, '00000000-0000-0000-0000-000000000000')
        actual = self.summarize(projection)
        self.assertIsNone(actual['conversation_url'])
        self.assertEqual(actual['conversation_reason'], 'HOST_CONVERSATION_URL_CONFLICT')

    def test_multiple_visible_url_lines_are_rejected_even_if_equal(self):
        projection = fixtures.projection(self.result)
        projection['content'] += '\nconversation_url: ' + URL
        self.assertIsNone(self.summarize(projection)['conversation_url'])

    def test_malformed_urls_never_become_links(self):
        bad_urls = (URL.replace('https:', 'http:'), URL + '?token=private', URL + '#fragment',
                    URL.replace('https://', 'https://user:password@'), URL.replace(THREAD, 'not-a-uuid'),
                    URL.replace('cnoocclaw.cnooc.com.cn', '127.0.0.1'),
                    URL.replace('cnoocclaw.cnooc.com.cn', 'localhost'),
                    'https://cnoocclaw.cnooc.com.cn/drive/home/', URL + '\nextra')
        for bad in bad_urls:
            projection = fixtures.projection(self.result, artifact=True)
            projection['artifact']['conversation_url'] = bad
            self.assertIsNone(self.summarize(projection)['conversation_url'], bad)

    def test_wrong_optional_thread_identity_is_rejected(self):
        projection = fixtures.projection(self.result)
        projection['thread_id'] = '00000000-0000-0000-0000-000000000000'
        self.assertIsNone(self.summarize(projection)['conversation_url'])
        projection['thread_id'] = THREAD
        self.assertEqual(self.summarize(projection)['conversation_url'], URL)

    def test_arbitrary_messages_and_invalid_call_identity_are_rejected(self):
        for field, value in (('name', 'bash'), ('tool_call_id', ''), ('tool_call_id', 'bad id'),
                             ('status', 'completed'), ('content', 'Model says: ' + URL)):
            projection = fixtures.projection(self.result, artifact=True)
            projection[field] = value
            self.assertIsNone(self.summarize(projection)['conversation_url'], (field, value))

    def test_no_receipt_cannot_invent_current_thread(self):
        for projection in (None, {}, {'thread_id': THREAD}, {'conversation_url': URL}):
            actual = self.summarize(projection)
            self.assertIsNone(actual['conversation_url'])
            self.assertFalse(actual['confirmed'])
            self.assertNotIn('点击查看完整日报', delivery.compose(self.data, self.original_text, actual))

    def test_successful_file_publication_without_url_does_not_claim_link(self):
        projection = fixtures.projection(self.result)
        projection['content'] = 'Successfully presented files'
        actual = self.summarize(projection)
        self.assertTrue(actual['confirmed'])
        self.assertIsNone(actual['conversation_url'])

    def test_host_origin_is_preserved_without_fixed_domain(self):
        projection = fixtures.projection(self.result)
        supplied = URL.replace('cnoocclaw.cnooc.com.cn', 'portal.example.com')
        projection['content'] = 'Successfully presented files\nconversation_url: ' + supplied
        self.assertEqual(self.summarize(projection)['conversation_url'], supplied)

    def test_freight_references_keep_each_actual_date_and_are_not_missing_prices(self):
        for index, quote in enumerate(self.data['freight']):
            quote.update(status='latest_available', date='2026-11-05' if index % 2 else '2026-11-04',
                         target_date='2026-11-06', target_status='missing', quote_age_days=1 if index % 2 else 2,
                         reference_policy='latest_available_in_three_month_window')
        self.data['freight_reference_search_complete'] = True
        note = delivery.quality_note(self.data)
        self.assertIn('均非目标日报价', note)
        self.assertNotIn('航运8项数据未取得', note)
        for quote in self.data['freight']:
            self.assertIn(quote['name'] + '：未取得目标日报价；参考最近已取得报价 ' + quote['date'], note)

    def test_mixed_freight_missing_reference_and_holiday_are_distinguished(self):
        self.data['freight'][0].update(status='latest_available', date='2026-11-05', target_date='2026-11-06',
                                       target_status='non_trading', quote_age_days=1)
        self.data['freight'][1].update(status='missing', value=None)
        self.data['gas'][0].update(status='non_trading', value=None)
        self.data['freight_reference_search_complete'] = False
        note = delivery.quality_note(self.data)
        self.assertIn('航运1项数据未取得', note)
        self.assertIn('天然气1项按已核实来源标注休市', note)
        self.assertIn('目标日休市；参考最近已取得报价 2026-11-05', note)
        self.assertIn('不保证未核验日期没有更新报价', note)
        self.assertNotIn('未补入旧价', note)

    def test_final_reply_retains_reference_dates_and_current_host_link(self):
        self.data['freight'][0].update(status='latest_available', date='2026-11-05', target_date='2026-11-06',
                                       target_status='missing', quote_age_days=1)
        actual = self.summarize(fixtures.projection(self.result))
        text = delivery.compose(self.data, self.original_text, actual)
        self.assertIn('参考最近已取得报价 2026-11-05', text)
        self.assertTrue(text.endswith('[点击查看完整日报](' + URL + ')\n'))

    def test_real_cli_partial_publication_links_and_is_idempotent(self):
        projection = fixtures.projection(self.result, artifact=True)
        projection['artifact'].pop('publication_run_id')
        before_files = copy.deepcopy(self.result['files'])
        code, output = fixtures.cli(self.project, projection)
        self.assertEqual(code, 0, output)
        self.assertTrue(output['conversation_link_available'])
        self.assertFalse(output['publication_confirmed_by_caller_projection'])
        final_path, manifest_path = Path(output['final_text_file']), Path(output['manifest_file'])
        final, manifest = final_path.read_bytes(), manifest_path.read_bytes()
        accepted_text = next(Path(item['path']).read_bytes() for item in before_files if item['path'].endswith('.txt'))
        self.assertTrue(final.startswith(accepted_text))
        self.assertTrue(final.decode().endswith('[点击查看完整日报](' + URL + ')\n'))
        self.assertNotIn('当前没有已确认的完整日报链接', final.decode())
        code, repeated = fixtures.cli(self.project, projection)
        self.assertEqual(code, 0, repeated)
        self.assertTrue(repeated['reused'])
        self.assertEqual(final_path.read_bytes(), final)
        self.assertEqual(manifest_path.read_bytes(), manifest)
        self.assertEqual([fixtures.fingerprint(Path(item['path'])) for item in before_files], before_files)
        self.assertFalse(json.loads(manifest)['publication']['current_thread_identity_independently_verified'])

    def test_cli_has_no_network_and_no_sender(self):
        projection = fixtures.projection(self.result)
        projection.update(status='error', content='Error: Synthetic failure\nFile delivery incomplete. conversation_url: ' + URL)
        path = self.project / 'delivery/host-publication.json'
        fixtures.write_json(path, projection)
        program = "import runpy,sys\ndef audit(event,args):\n if event in ('socket.connect','socket.getaddrinfo','subprocess.Popen'): raise RuntimeError('NETWORK_OR_CHILD_BLOCKED')\nsys.addaudithook(audit)\nsys.argv=[sys.argv[1]]+sys.argv[2:]\nrunpy.run_path(sys.argv[0],run_name='__main__')"
        output = subprocess.run([sys.executable, '-I', '-B', '-c', program, str(ENTRY), 'final-text', '--workspace', str(self.project), '--publication-receipt', str(path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(output.returncode, 0, output.stderr.decode())
        result = json.loads(output.stdout)
        self.assertEqual(result['messages_sent'], 0)
        self.assertEqual(result['conversation_url'], URL)


if __name__ == '__main__':
    unittest.main()
