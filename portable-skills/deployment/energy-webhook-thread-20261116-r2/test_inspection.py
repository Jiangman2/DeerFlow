"""Focused checks for the composed, exact-chat readonly sender diagnosis."""
import ast
import asyncio
import base64
import datetime
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('webhook_thread_seal', HERE / 'seal.py')
seal = importlib.util.module_from_spec(spec);spec.loader.exec_module(seal)
HOST, CHILD = seal.build()
c = {'__name__': 'readonly_child'};exec(compile(CHILD, '<child>', 'exec'), c)
h = {'__name__': 'readonly_host'};exec(compile(HOST, '<host>', 'exec'), h)
THREAD = 'cd2ca7b0-139c-4576-ab5f-c67e9837db10'
TARGET = 'https://open.w.cnooc.com.cn/open-apis/bot/v2/hook/SYNTHETIC_PRIVATE_TOKEN_123'
VIRTUAL = '/mnt/user-data/workspace/energy-daily-20261116'
NOW = datetime.datetime(2026, 11, 16, 5, tzinfo=datetime.timezone.utc)


def event(seq, kind, value):
    raw = json.dumps(value)
    return dict(seq=seq, event_type=kind, created_at=NOW, bytes=len(raw.encode()), content=raw)


def call(command, ident='sender'):
    return {'id': ident, 'name': 'bash', 'args': {'command': command}}


class Transaction:
    async def __aenter__(self):return self
    async def __aexit__(self, *args):return False


class Checks(unittest.TestCase):
    def test_exact_single_thread_and_sealed_child(self):
        self.assertEqual(h['THREADS'], [THREAD]);self.assertEqual(list(c['THREADS']), [THREAD])
        self.assertEqual(base64.b64decode(h['ASSET']['base64']), CHILD)
        for source in (HOST, CHILD):ast.parse(source.decode(), feature_version=(3, 7))

    def test_wrong_thread_zero_database_calls(self):
        with patch.dict(c, {'runtime_modules': lambda: self.fail('Unexpected database call')}):
            result = asyncio.run(c['observe']({'thread_ids': ['other']}))
        self.assertEqual(result['error_code'], 'EXACT_NAMED_THREAD_REQUIRED')

    def test_webhook_errors_preserved_without_token(self):
        result = c['errors'](json.dumps({'status': 'WEBHOOK_FAILED', 'error_code': 'WEBHOOK_FILES_NOT_PUBLISHED', 'url': TARGET}))
        self.assertEqual(result['error_codes'], ['WEBHOOK_FILES_NOT_PUBLISHED'])
        self.assertEqual(result['statuses'], ['WEBHOOK_FAILED'])
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))
        self.assertIsNone(c['safe_word'](TARGET))

    def test_actual_paired_sender_failure_is_not_only_proposal(self):
        rows = [event(1, 'llm.ai.response', {'tool_calls': [call('python send_webhook.py --workspace ' + VIRTUAL)]}),
                event(2, 'llm.tool.result', {'tool_call_id': 'sender', 'name': 'bash', 'status': 'success',
                      'content': 'Process exited with code 1\n' + json.dumps({'status': 'WEBHOOK_FAILED',
                          'error_code': 'WEBHOOK_PROVIDER_REJECTED', 'http_status': 200, 'provider_codes': [19024],
                          'delivery_status': 'FAILED', 'retry_allowed': False})})]
        result = c['event_summary'](rows, THREAD)
        receipt = result['timeline'][1]
        self.assertTrue(receipt['owner_seen']);self.assertEqual(receipt['actions'], ['send-webhook'])
        self.assertEqual(result['first_observed_error']['seq'], 2)
        facts = next(row for row in receipt['stage_facts'] if 'provider_codes' in row)
        self.assertEqual(facts['provider_codes'], [19024]);self.assertEqual(facts['http_status'], 200)
        self.assertFalse(facts['retry_allowed'])

    def test_sender_success_and_unknown(self):
        for status in ('WEBHOOK_SENT', 'WEBHOOK_ALREADY_SENT', 'WEBHOOK_FAILED'):
            value = json.dumps({'status': status, 'messages_sent_this_call': 1, 'delivery_status': 'UNKNOWN'})
            self.assertEqual(c['stage_facts'](value)[-1]['delivery_status'], 'UNKNOWN')

    def test_target_policy_matches_sender_and_is_redacted(self):
        self.assertTrue(c['target_summary'](TARGET)['matches_sender_endpoint_policy'])
        for value in (TARGET + '?sign=hidden', TARGET.replace('https:', 'http:'), TARGET.replace('open.w.cnooc.com.cn', 'evil.invalid'), TARGET + '#fragment'):
            self.assertFalse(c['target_summary'](value)['matches_sender_endpoint_policy'])
        result = c['description_targets']('生成后发送到 ' + TARGET)
        self.assertEqual(len(result['webhook_url_candidates']), 1)
        self.assertNotIn(TARGET, json.dumps(result))

    def test_private_request_write_shape(self):
        result = c['command']({'name': 'write_file', 'id': 'request', 'args': {
            'path': VIRTUAL + '/delivery/webhook-request.json', 'content': json.dumps({'webhook_url': TARGET})}})
        self.assertTrue(result['webhook_request_shape_valid'])
        self.assertTrue(result['webhook_request_target']['matches_sender_endpoint_policy'])
        self.assertNotIn(TARGET, json.dumps(result))

    def test_project_commands_parsed_without_execution(self):
        records = [event(1, 'llm.ai.response', {'tool_calls': [call('python daily.py build --workspace ' + VIRTUAL)]})]
        self.assertEqual(c['command_project'](records), [VIRTUAL])
        for value in ('/mnt/user-data/workspace/../other', '/elsewhere', '/mnt/user-data/workspace/daily-$(date)'):
            records[0] = event(1, 'llm.ai.response', {'tool_calls': [call('python daily.py build --workspace ' + value)]})
            self.assertEqual(c['command_project'](records), [])

    def test_ambiguous_projects_not_guessed(self):
        records = [event(1, 'llm.ai.response', {'tool_calls': [call('python daily.py build --workspace ' + VIRTUAL),
                   call('python send_webhook.py --workspace ' + VIRTUAL + '-other')]})]
        self.assertEqual(len(c['command_project'](records)), 2)

    def fixture(self, base):
        project = base / 'workspace/energy-daily-20261116';project.mkdir(parents=True)
        output = base / 'outputs';output.mkdir()
        delivery = project / 'delivery';delivery.mkdir()
        files = []
        for ext in ('.html', '.png', '.json', '.txt'):
            name = 'report' + ext;raw = b'SYNTHETIC_REPORT_BODY';(output / name).write_bytes(raw)
            files.append({'path': VIRTUAL + '/outputs/' + name, 'bytes': len(raw), 'sha256': c['sha'](raw)})
        (project / 'RESULT.json').write_text(json.dumps({'status': 'REPORT_ACCEPTED', 'version': '2.1.7-portable',
            'conversation_thread_id': THREAD, 'files': files}), encoding='utf-8')
        (delivery / 'webhook-request.json').write_text(json.dumps({'webhook_url': TARGET}), encoding='utf-8')
        journal = delivery / ('webhook-' + 'a' * 64 + '.json')
        journal.write_text(json.dumps({'status': 'FAILED', 'reason': 'WEBHOOK_PROVIDER_REJECTED',
            'provider_codes': [19024], 'http_status': 200, 'target_sha256': c['sha'](TARGET),
            'reader_url': 'https://private.invalid/DO_NOT_EXPORT'}), encoding='utf-8')
        return project, output, journal

    def test_real_files_examined_and_no_body_or_target_export(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);project, output, journal = self.fixture(base)
            result = c['project_observation'](project, base, output, THREAD)
        self.assertEqual(result['result_version'], '2.1.7-portable')
        self.assertTrue(all(item['published_copy_matches_receipt'] for item in result['published_copies']))
        self.assertEqual(result['send_journals'][0]['provider_codes'], [19024])
        for word in ('SYNTHETIC_REPORT', 'SYNTHETIC_PRIVATE', 'private.invalid', directory):self.assertNotIn(word, json.dumps(result))

    def test_missing_and_changed_published_copy_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);project, output, journal = self.fixture(base)
            (output / 'report.png').unlink();(output / 'report.html').write_bytes(b'CHANGED')
            result = c['project_observation'](project, base, output, THREAD)
        self.assertFalse(result['published_copies'][0]['published_copy_matches_receipt'])
        self.assertFalse(result['published_copies'][1]['published_copy_present'])

    def test_read_outside_owner_root_and_oversize_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);file = base / 'receipt';file.write_bytes(b'123456')
            with self.assertRaises(c['Refused']):c['read_project_file'](file, base / 'different', 8)
            with self.assertRaises(c['Refused']):c['read_project_file'](file, base, 2)

    @unittest.skipIf(os.name == 'nt', 'Linux production symlink behavior')
    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);file = base / 'real';file.write_bytes(b'123')
            link = base / 'link';link.symlink_to(file)
            with self.assertRaises(c['Refused']):c['read_project_file'](link, base, 8)

    def test_sql_scoped_and_select_only(self):
        for name in ('INPUT_SQL', 'PROMPT_SQL'):
            self.assertTrue(c[name].startswith('SELECT'));self.assertIn('thread_id=$1', c[name])
            for forbidden in ('UPDATE ', 'DELETE ', 'INSERT ', 'ciphertext'):self.assertNotIn(forbidden, c[name])

    def test_audit_blocks_write_subprocess_and_remote_network(self):
        for name, args in (('open', ('/tmp/file', 'w', 1)), ('subprocess.Popen', ('cmd',)),
                           ('socket.connect', (None, ('1.2.3.4', 443)))):
            with self.assertRaises(c['Refused']):c['audit'](name, args)

    def test_current_prompt_and_original_input_are_distinct(self):
        class DB:
            def transaction(self, **kwargs):
                assert kwargs == {'readonly': True, 'isolation': 'repeatable_read'};return Transaction()
            async def fetchval(self, sql, thread):assert thread == THREAD;return 'synthetic-owner'
            async def fetchrow(self, sql, *args):
                if sql == c['PROMPT_SQL']:return {'user_id': 'synthetic-owner', 'bounded_prompt': 'no webhook now'}
                if sql == c['INPUT_SQL']:return {'bounded_input': json.dumps({'messages': [{'role': 'user', 'content': 'send ' + TARGET}]})}
                raise AssertionError('Unexpected query')
            async def fetch(self, sql, *args):
                if sql == c['RUN_SQL']:return [{'run_id': 'synthetic-run'}]
                if sql == c['EVENT_SQL']:return []
                raise AssertionError('Unexpected query')
        async def base(*args):return {'status': 'TASK_DURABLE_STATE_OBSERVED_READONLY'}
        with patch.dict(c, {'_base_diagnose': base}):result = asyncio.run(c['diagnose_thread'](DB(), THREAD))
        self.assertTrue(result['explicit_webhook_inspection_readonly'])
        self.assertEqual(result['current_schedule_prompt']['webhook_url_candidates'], [])
        self.assertEqual(len(result['persisted_run_input']['webhook_url_candidates']), 1)
        self.assertEqual(result['project_delivery']['status'], 'SINGLE_OWNING_PROJECT_NOT_ESTABLISHED')
        self.assertNotIn(TARGET, json.dumps(result))

    def test_paths_derive_from_trusted_owner_and_project(self):
        class DB:
            def transaction(self, **kwargs):return Transaction()
            async def fetchval(self, *args):return 'synthetic-owner'
            async def fetchrow(self, *args):return None
            async def fetch(self, sql, *args):
                if sql == c['RUN_SQL']:return [{'run_id': 'synthetic-run'}]
                return [event(1, 'llm.ai.response', {'tool_calls': [call('python daily.py build --workspace ' + VIRTUAL)]})]
        async def original(*args):return {'status': 'TASK_DURABLE_STATE_OBSERVED_READONLY'}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory);self.fixture(base)
            class Paths:
                def sandbox_user_data_dir(self, thread, *, user_id):
                    assert thread == THREAD and user_id == 'synthetic-owner';return base
            module = types.ModuleType('deerflow.config.paths');module.get_paths = lambda: Paths()
            with patch.dict(c, {'_base_diagnose': original}), patch.dict('sys.modules', {'deerflow.config.paths': module}):
                result = asyncio.run(c['diagnose_thread'](DB(), THREAD))
        self.assertEqual(result['project_delivery']['result_version'], '2.1.7-portable')

    def test_diagnostic_failure_keeps_execution_evidence(self):
        async def original(*args):return {'runs': [{'status': 'error'}]}
        with patch.dict(c, {'_base_diagnose': original}):result = asyncio.run(c['diagnose_thread'](object(), THREAD))
        self.assertEqual(result['runs'][0]['status'], 'error')
        self.assertEqual(result['explicit_webhook_inspection']['status'], 'INCOMPLETE')

    def test_wrong_host_does_not_query(self):
        with patch.object(h['sys'], 'argv', ['tool', '--inspect-readonly']), patch.object(h['os'], 'name', 'nt'), \
                patch.object(h['subprocess'], 'Popen') as process, patch('builtins.print') as output:
            code = h['main']()
        self.assertEqual(code, 1);process.assert_not_called()
        result = json.loads(output.call_args.args[0]);self.assertEqual(result['queries'], 0)


if __name__ == '__main__':unittest.main()
