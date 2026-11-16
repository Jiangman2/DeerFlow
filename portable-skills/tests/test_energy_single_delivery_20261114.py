"""2.1.2 single linked TXT delivery, entirely offline and without real browser."""
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.2'
sys.path.insert(0, str(SKILL / 'scripts'))
import daily
import delivery_text as delivery
from common import Failure, digest, load, save

assert Path(daily.__file__).resolve() == SKILL / 'scripts/daily.py'
fixture_path = Path(__file__).with_name('test_energy_market_daily.py')
source = fixture_path.read_text(encoding='utf-8')
marker = "SKILL = Path(__file__).resolve().parents[1] / 'energy-market-daily'"
assert source.count(marker) == 1
source = source.replace(marker, 'SKILL = Path(' + repr(str(SKILL)) + ')')
fixtures = types.ModuleType('single_delivery_fixtures')
fixtures.__file__ = str(fixture_path)
exec(compile(source, str(fixture_path), 'exec'), fixtures.__dict__)

THREAD = '8e27d29f-730b-4a89-9681-36fdddc87887'
URL = 'https://cnoocclaw.cnooc.com.cn/workspace/chats/' + THREAD
OTHER = 'https://cnoocclaw.cnooc.com.cn/workspace/chats/545097ca-bd47-4416-888e-b6579229c865'


def mock_png(path):
    path.with_suffix('.png').write_bytes(b'\x89PNG\r\n\x1a\n' + b'\x00' * 8 + struct.pack('>II', 780, 5250) + b'\x00' * 1200)
    return {'width': 780, 'height': 5250, 'browser_fallback_count': 0}


class SingleDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='energy-212-')
        self.project = Path(self.temp.name)
        fixtures.project_fixture(self.project)
        self.environment = patch.dict(os.environ, {delivery.CONVERSATION_ENV: URL})
        self.environment.start()
        self.renderer = patch.object(daily, 'png', side_effect=mock_png)
        self.browser = self.renderer.start()
        self.args = types.SimpleNamespace(workspace=str(self.project), visual_reviewed=True, publication_receipt=None)

    def tearDown(self):
        self.renderer.stop()
        self.environment.stop()
        self.temp.cleanup()

    def build(self):
        return daily.build(self.args)

    def accept(self):
        self.build()
        return daily.accept(self.args)

    def legacy(self):
        receipt = self.accept()
        data = daily.snapshot(self.project)
        path = Path(receipt['reply_text_file'])
        path.write_text(daily.reply_text(data), encoding='utf-8')
        for item in receipt['files']:
            if item['path'] == str(path):
                item.update(bytes=path.stat().st_size, sha256=digest(path.read_bytes()))
        receipt['version'] = '2.1.1-portable'
        for key in tuple(receipt):
            if key.startswith('conversation_') or key in ('reply_text_ready', 'external_delivery_ready', 'ready_after_successful_publication'):
                receipt.pop(key)
        save(self.project / 'RESULT.json', receipt)
        return receipt

    def projection(self, receipt, url=URL, failed=False):
        result = {'name': 'present_files', 'status': 'error' if failed else 'success',
                  'tool_call_id': 'call-current-publication',
                  'content': ('Error: Synthetic delivery error\nFile delivery incomplete. conversation_url: ' if failed
                              else 'Successfully presented files\nconversation_url: ') + url,
                  'requested_files': [item['path'] for item in receipt['files']]}
        path = self.project / 'delivery/host-publication.json'
        save(path, result)
        return path

    def test_build_includes_one_link_and_entire_original_text(self):
        receipt = self.build()
        text = Path(receipt['reply_text_file']).read_text(encoding='utf-8')
        original = daily.reply_text(daily.snapshot(self.project))
        self.assertEqual(text, original.rstrip() + '\n\n[点击查看完整日报](' + URL + ')\n')
        self.assertEqual(text.count(URL), 1)
        self.assertNotIn('发布状态尚未确认', text)
        self.assertNotIn('当前平台未返回', text)
        self.assertNotIn('已在本次对话发布', text)
        self.assertTrue(receipt['reply_text_ready'])
        self.assertTrue(receipt['ready_after_successful_publication'])
        self.assertFalse(receipt['external_delivery_ready'])

    def test_accept_directs_one_publication_and_same_txt_without_transcription(self):
        receipt = self.accept()
        self.assertEqual(receipt['next_action'], 'publish_files_then_reply_once')
        self.assertEqual(receipt['final_text_file'], receipt['reply_text_file'])
        self.assertFalse(receipt['model_publication_receipt_transcription_required'])
        self.assertFalse(receipt['final_text_command_required'])
        self.assertTrue(receipt['delivery_requires_successful_publication'])
        self.assertNotIn('after_publication_command', receipt)
        self.assertNotIn('publication_projection_file', receipt)

    def test_no_context_keeps_four_files_but_blocks_external_report(self):
        os.environ.pop(delivery.CONVERSATION_ENV)
        receipt = self.accept()
        self.assertEqual(len(receipt['files']), 4)
        self.assertEqual(receipt['next_action'], 'publish_files_without_external_send')
        self.assertFalse(receipt['reply_text_ready'])
        self.assertFalse(receipt['ready_after_successful_publication'])
        self.assertNotIn('点击查看完整日报', Path(receipt['reply_text_file']).read_text(encoding='utf-8'))

    def test_invalid_context_never_copies_guessed_url_to_outputs(self):
        os.environ[delivery.CONVERSATION_ENV] = 'https://example.com/drive/home/'
        receipt = self.build()
        self.assertEqual(receipt['conversation_context_error'], 'HOST_CONVERSATION_URL_INVALID')
        self.assertFalse(receipt['conversation_link_available'])
        self.assertNotIn('example.com', Path(receipt['reply_text_file']).read_text(encoding='utf-8'))

    def test_same_context_reuses_complete_accepted_generation(self):
        receipt = self.accept()
        repeated = self.build()
        self.assertTrue(repeated['reused'])
        self.assertEqual(receipt['outputs_generation'], repeated['outputs_generation'])
        self.assertEqual(self.browser.call_count, 1)

    def test_cross_thread_build_refuses_without_mutating_previous_files(self):
        receipt = self.accept()
        before = (self.project / 'RESULT.json').read_bytes()
        os.environ[delivery.CONVERSATION_ENV] = OTHER
        with self.assertRaisesRegex(Failure, 'PROJECT_CONVERSATION_MISMATCH'):
            self.build()
        self.assertEqual((self.project / 'RESULT.json').read_bytes(), before)
        self.assertEqual(self.browser.call_count, 1)
        self.assertEqual(Path(receipt['reply_text_file']).read_text(encoding='utf-8').count(URL), 1)

    def test_existing_linked_project_cannot_lose_current_context_on_build(self):
        self.accept()
        os.environ.pop(delivery.CONVERSATION_ENV)
        with self.assertRaisesRegex(Failure, 'HOST_CONVERSATION_CONTEXT_REQUIRED'):
            self.build()

    def test_accept_requires_same_thread_and_current_origin(self):
        self.build()
        os.environ[delivery.CONVERSATION_ENV] = OTHER
        with self.assertRaisesRegex(Failure, 'PROJECT_CONVERSATION_MISMATCH'):
            daily.accept(self.args)
        os.environ[delivery.CONVERSATION_ENV] = URL.replace('cnoocclaw.cnooc.com.cn', 'new.example.com')
        with self.assertRaisesRegex(Failure, 'REPORT_CONVERSATION_CONTEXT_CHANGED'):
            daily.accept(self.args)

    def test_new_context_requires_rebuild_of_unlinked_generation(self):
        os.environ.pop(delivery.CONVERSATION_ENV)
        self.build()
        os.environ[delivery.CONVERSATION_ENV] = URL
        with self.assertRaisesRegex(Failure, 'REPORT_REBUILD_FOR_CURRENT_CONVERSATION_REQUIRED'):
            daily.accept(self.args)
        receipt = self.accept()
        self.assertTrue(receipt['conversation_link_available'])

    def test_old_prepared_project_rebuilds_text_without_reacquiring_sources(self):
        previous = self.legacy()
        source_names = ('market.json', 'financial.json', 'research.json', 'news.json', 'PROJECT.json')
        before = {name: (self.project / name).read_bytes() for name in source_names}
        receipt = self.build()
        self.assertEqual(receipt['version'], '2.1.2-portable')
        self.assertNotEqual(receipt['outputs_generation'], previous['outputs_generation'])
        self.assertTrue(all((self.project / name).read_bytes() == raw for name, raw in before.items()))
        self.assertTrue(Path(receipt['reply_text_file']).read_text(encoding='utf-8').endswith('[点击查看完整日报](' + URL + ')\n'))

    def test_legacy_no_context_never_returns_ready_or_overwrites_linked_final(self):
        self.legacy()
        directory = self.project / 'delivery'
        directory.mkdir(exist_ok=True)
        prior = b'prior verified linked final - immutable until current context available'
        (directory / 'final.md').write_bytes(prior)
        os.environ.pop(delivery.CONVERSATION_ENV)
        result = daily.final_text(self.args)
        self.assertEqual(result['status'], 'WAITING_FOR_CONVERSATION_LINK')
        self.assertFalse(result['external_delivery_ready'])
        self.assertIsNone(result['final_text_file'])
        self.assertEqual((directory / 'final.md').read_bytes(), prior)
        self.assertFalse((directory / 'MANIFEST.json').exists())

    def test_legacy_projection_cannot_replace_missing_or_invalid_current_context(self):
        receipt = self.legacy()
        self.args.publication_receipt = str(self.projection(receipt))
        prior = b'previous linked final'
        final = self.project / 'delivery/final.md'
        final.write_bytes(prior)
        for value in (None, 'https://example.com/drive/home/'):
            with self.subTest(context=value):
                if value is None:
                    os.environ.pop(delivery.CONVERSATION_ENV, None)
                else:
                    os.environ[delivery.CONVERSATION_ENV] = value
                result = daily.final_text(self.args)
                self.assertEqual(result['status'], 'WAITING_FOR_CONVERSATION_LINK')
                self.assertTrue(result['caller_projection_cannot_supply_current_identity'])
                self.assertFalse(result['external_delivery_ready'])
                self.assertEqual(final.read_bytes(), prior)

    def test_legacy_failed_publication_keeps_url_but_is_not_external_ready(self):
        receipt = self.legacy()
        self.args.publication_receipt = str(self.projection(receipt, failed=True))
        result = daily.final_text(self.args)
        self.assertEqual(result['status'], 'FINAL_TEXT_READY')
        self.assertFalse(result['external_delivery_ready_by_caller_projection'])
        self.assertEqual(result['conversation_url'], URL)

    def test_context_validator_matches_platform_task_id_and_rejects_route_injection(self):
        origin = 'https://example.com/workspace/chats/'
        for task in ('thread-current', 'Task_ID-12', 'a', 'x' * 64, THREAD):
            with self.subTest(task=task):
                self.assertEqual(delivery.public_conversation_url(origin + task), origin + task)
        for task in ('', 'x' * 65, '.', '..', '../other', 'thread%2fother', '%2e%2e', 'thread/other', 'thread?x=1', 'thread#fragment', '任务'):
            with self.subTest(task=task):
                self.assertIsNone(delivery.public_conversation_url(origin + task))

    def test_legacy_projection_cannot_override_current_host_context(self):
        receipt = self.legacy()
        self.args.publication_receipt = str(self.projection(receipt, url=OTHER))
        with self.assertRaisesRegex(Failure, 'HOST_CONVERSATION_LINK_CONFLICT'):
            daily.final_text(self.args)

    def test_compatibility_finalize_preserves_exact_new_txt_and_single_link(self):
        receipt = self.accept()
        text = Path(receipt['reply_text_file']).read_text(encoding='utf-8')
        result = daily.final_text(self.args)
        self.assertEqual(result['status'], 'FINAL_TEXT_READY')
        self.assertEqual(Path(result['final_text_file']).read_text(encoding='utf-8'), text)
        self.assertFalse(result['external_delivery_ready_by_caller_projection'])

    def test_wrong_link_in_new_txt_fails_even_if_file_hash_was_recomputed(self):
        receipt = self.build()
        path = Path(receipt['reply_text_file'])
        path.write_text(path.read_text(encoding='utf-8').replace(URL, OTHER), encoding='utf-8')
        for item in receipt['files']:
            if item['path'] == str(path):
                item.update(bytes=path.stat().st_size, sha256=digest(path.read_bytes()))
        with self.assertRaisesRegex(Failure, 'RESULT_REPLY_TEXT_MISMATCH'):
            daily.validate_result(receipt, self.project)

    def test_result_cannot_redirect_the_reply_to_another_file(self):
        receipt = self.accept()
        for field in ('reply_text_file', 'final_text_file'):
            with self.subTest(field=field):
                wrong = dict(receipt, **{field: str(self.project / 'old-report.txt')})
                with self.assertRaisesRegex(Failure, 'RESULT_REPLY_TEXT_IDENTITY'):
                    daily.validate_result(wrong, self.project)
        for field in ('visual_reviewed', 'publication_allowed'):
            with self.subTest(field=field):
                with self.assertRaisesRegex(Failure, 'RESULT_ACCEPTANCE_INVALID'):
                    daily.validate_result(dict(receipt, **{field: False}), self.project)


if __name__ == '__main__':
    unittest.main()
