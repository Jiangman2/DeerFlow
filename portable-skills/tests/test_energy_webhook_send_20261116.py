"""Deterministic send-once and publication checks; never contact a real bot."""
import concurrent.futures
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
import urllib.error
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.7'
sys.path.insert(0, str(SKILL / 'scripts'))
import daily
import send_webhook as sender
from common import Failure, load
CHILD_POST = sender.child_post

fixture = Path(__file__).with_name('test_energy_work_preview_20261115.py')
source = fixture.read_text(encoding='utf-8').replace('energy-market-daily-2.1.6', 'energy-market-daily-2.1.7')
source = source.replace("SKILL = " + "REPO / 'portable-skills/candidates/energy-market-daily-2.1.7'",
                        'SKILL = Path(' + repr(str(SKILL)) + ')')
fx = types.ModuleType('webhook_report_fixtures'); fx.__file__ = str(fixture)
exec(compile(source, str(fixture), 'exec'), fx.__dict__)
URL = 'https://open.w.cnooc.com.cn/open-apis/bot/v2/hook/11111111-2222-3333-4444-555555555555'


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='energy-webhook-')
        self.root = Path(self.temp.name)
        self.project = self.root / 'workspace/daily'; self.project.mkdir(parents=True)
        fx.fixtures.project_fixture(self.project)
        self.env = patch.dict(os.environ, {'DEERFLOW_CONVERSATION_URL': fx.URL}); self.env.start()
        with patch.object(daily, 'png', side_effect=fx.mock_png):
            self.receipt = daily.build(types.SimpleNamespace(workspace=str(self.project)))
        self.output = self.root / 'outputs'; self.output.mkdir()
        for file in self.receipt['files']:
            shutil.copyfile(file['path'], self.output / Path(file['path']).name)
        self.directory = self.project / 'delivery'; self.directory.mkdir()
        self.request = self.directory / 'webhook-request.json'
        self.request.write_text(json.dumps({'webhook_url': URL}), encoding='utf-8')
        self.post = patch.object(sender, 'child_post', return_value={'outcome': 'SENT', 'http_status': 200, 'provider_codes': [0]})
        self.transport = self.post.start()

    def tearDown(self):
        self.post.stop(); self.env.stop(); self.temp.cleanup()

    def test_sends_only_one_clickable_reader(self):
        result = sender.send(self.project)
        self.assertEqual(result['status'], 'WEBHOOK_SENT')
        url, payload = self.transport.call_args.args
        self.assertEqual(url, URL)
        self.assertEqual(payload, {'msg_type': 'post', 'content': {'post': {'zh_cn': {'content': [[
            {'tag': 'a', 'text': '点击查看日报网页', 'href': self.receipt['html_report_url']}]]}}}})
        self.assertNotIn(URL, Path(result['receipt_file']).read_text(encoding='utf-8'))

    def test_repeated_success_does_not_post_again(self):
        sender.send(self.project)
        result = sender.send(self.project)
        self.assertEqual(result['status'], 'WEBHOOK_ALREADY_SENT')
        self.transport.assert_called_once()

    def test_concurrent_calls_attempt_only_one_post(self):
        def call():
            try:return sender.send(self.project)['status']
            except Failure as exc:return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: call(), range(4)))
        self.assertIn('WEBHOOK_SENT', results)
        self.transport.assert_called_once()

    def test_uncertain_result_is_never_retried(self):
        self.transport.return_value = {'outcome': 'UNKNOWN', 'reason': 'WEBHOOK_TRANSPORT_OUTCOME_UNKNOWN'}
        for expected in ('WEBHOOK_TRANSPORT_OUTCOME_UNKNOWN', 'WEBHOOK_PREVIOUS_OUTCOME_UNKNOWN'):
            with self.assertRaisesRegex(Failure, expected):sender.send(self.project)
        self.transport.assert_called_once()

    def test_explicit_rejection_is_not_automatically_retried(self):
        self.transport.return_value = {'outcome': 'FAILED', 'reason': 'WEBHOOK_PROVIDER_REJECTED', 'provider_codes': [19002]}
        for expected in ('WEBHOOK_PROVIDER_REJECTED', 'WEBHOOK_PREVIOUS_SEND_FAILED'):
            with self.assertRaisesRegex(Failure, expected):sender.send(self.project)
        self.transport.assert_called_once()

    def test_missing_published_copy_blocks_network(self):
        (self.output / Path(self.receipt['files'][0]['path']).name).unlink()
        with self.assertRaisesRegex(Failure, 'WEBHOOK_FILES_NOT_PUBLISHED'):sender.send(self.project)
        self.transport.assert_not_called()

    def test_changed_published_copy_blocks_network(self):
        (self.output / Path(self.receipt['files'][0]['path']).name).write_bytes(b'stale')
        with self.assertRaisesRegex(Failure, 'WEBHOOK_PUBLISHED_FILE_CHANGED'):sender.send(self.project)
        self.transport.assert_not_called()

    def test_wrong_chat_blocks_network(self):
        os.environ['DEERFLOW_CONVERSATION_URL'] = fx.OTHER
        with self.assertRaisesRegex(Failure, 'PROJECT_CONVERSATION_MISMATCH'):sender.send(self.project)
        self.transport.assert_not_called()

    def test_missing_host_context_blocks_network(self):
        os.environ.pop('DEERFLOW_CONVERSATION_URL')
        with self.assertRaisesRegex(Failure, 'WEBHOOK_HOST_CONTEXT_REQUIRED'):sender.send(self.project)
        self.transport.assert_not_called()

    def test_unauthorized_host_and_placeholder_are_rejected(self):
        for url in ('https://evil.invalid/open-apis/bot/v2/hook/' + 'a' * 32, URL.replace('https:', 'http:'),
                    URL + '?secret=a', URL + '#fragment', URL.replace('open.w.cnooc.com.cn', 'open.w.cnooc.com.cn.evil.invalid'),
                    URL.replace('11111111-2222-3333-4444-555555555555', '****')):
            with self.subTest(url=url), self.assertRaises(Failure):sender.endpoint(url)

    def test_request_must_be_explicit_and_in_current_project(self):
        self.request.write_text(json.dumps({'url': URL}), encoding='utf-8')
        with self.assertRaisesRegex(Failure, 'WEBHOOK_EXPLICIT_URL_REQUIRED'):sender.send(self.project)
        other = self.root / 'request.json'; other.write_text(json.dumps({'webhook_url': URL}), encoding='utf-8')
        with self.assertRaisesRegex(Failure, 'WEBHOOK_REQUEST_PATH_INVALID'):sender.send(self.project, other)
        self.transport.assert_not_called()

    def test_interruption_journal_blocks_second_send(self):
        self.transport.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):sender.send(self.project)
        self.transport.side_effect = None
        with self.assertRaisesRegex(Failure, 'WEBHOOK_PREVIOUS_OUTCOME_UNKNOWN'):sender.send(self.project)
        self.transport.assert_called_once()

    def test_http200_requires_explicit_provider_zero(self):
        class Response:
            status = 200
            def __init__(self, raw):self.raw = raw
            def __enter__(self):return self
            def __exit__(self, *args):return False
            def read(self, limit):return self.raw[:limit]
        for raw, expected in [(b'{"code":0}', 'SENT'), (b'{"StatusCode":0}', 'SENT'),
                (b'{"code":19002}', 'FAILED'), (b'{"code":0,"StatusCode":1}', 'FAILED'),
                (b'{}', 'UNKNOWN'), (b'{"code":false}', 'UNKNOWN'), (b'{"code":"0"}', 'UNKNOWN'),
                (b'<html>ok</html>', 'UNKNOWN'), (b'x' * (sender.MAX_RESPONSE + 1), 'UNKNOWN')]:
            opener = types.SimpleNamespace(open=lambda *args, **kwargs: Response(raw))
            with self.subTest(raw=raw[:40]), patch.object(sender.urllib.request, 'build_opener', return_value=opener):
                result = sender.post_once(URL, sender.message(self.receipt['html_report_url']))
                self.assertEqual(result['outcome'], expected)

    def test_http_failure_never_changes_payload_or_follows_redirect(self):
        for status, expected in [(302, 'UNKNOWN'), (400, 'FAILED'), (429, 'FAILED'), (500, 'UNKNOWN')]:
            opener = types.SimpleNamespace(open=lambda *args, **kwargs: (_ for _ in ()).throw(
                urllib.error.HTTPError(URL, status, 'DO_NOT_EXPORT_TOKEN', {}, None)))
            with patch.object(sender.urllib.request, 'build_opener', return_value=opener):
                result = sender.post_once(URL, sender.message(self.receipt['html_report_url']))
                self.assertEqual(result['outcome'], expected)
                self.assertNotIn('DO_NOT_EXPORT', json.dumps(result))
        self.assertIsNone(sender.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.invalid'))

    def test_subprocess_timeout_is_unknown_without_token_in_argv(self):
        with patch.object(sender.subprocess, 'run', side_effect=sender.subprocess.TimeoutExpired('child', 20)) as run:
            result = CHILD_POST(URL, sender.message(self.receipt['html_report_url']))
        self.assertEqual(result['outcome'], 'UNKNOWN')
        self.assertNotIn(URL, ' '.join(run.call_args.args[0]))

    def test_accepted_previous_version_does_not_require_reacquisition_or_render(self):
        receipt = copy.deepcopy(self.receipt); receipt['version'] = '2.1.6-portable'
        (self.project / 'RESULT.json').write_text(json.dumps(receipt), encoding='utf-8')
        with patch.object(daily, 'png', side_effect=AssertionError('must not render again')):
            self.assertEqual(sender.send(self.project)['status'], 'WEBHOOK_SENT')
        self.transport.assert_called_once()

    def test_private_child_rejects_other_payload_or_chat_before_network(self):
        payload = sender.message(self.receipt['html_report_url'])
        variants = [dict(payload, extra='unexpected'), sender.message(self.receipt['html_report_url'].replace(
            fx.URL.rsplit('/', 1)[1], fx.OTHER.rsplit('/', 1)[1]))]
        for item in variants:
            stdin = types.SimpleNamespace(buffer=io.BytesIO(json.dumps({'url': URL, 'payload': item}).encode()))
            with patch.object(sender.sys, 'stdin', stdin), patch.object(sender, 'post_once') as post, patch('builtins.print') as output:
                sender.main(['__post'])
            post.assert_not_called()
            self.assertEqual(json.loads(output.call_args.args[0])['outcome'], 'UNKNOWN')

    def test_main_reports_failed_target_without_printing_the_secret(self):
        self.request.write_text(json.dumps({'webhook_url': URL + '?private=DO_NOT_EXPORT'}), encoding='utf-8')
        with patch('builtins.print') as output:
            code = sender.main(['--workspace', str(self.project)])
        result = json.loads(output.call_args.args[0])
        self.assertEqual(code, 1)
        self.assertFalse(result['retry_allowed'])
        self.assertEqual(result['recovery_scope'], 'operator')
        self.assertNotIn('DO_NOT_EXPORT', output.call_args.args[0])
        self.transport.assert_not_called()


if __name__ == '__main__':
    unittest.main()
