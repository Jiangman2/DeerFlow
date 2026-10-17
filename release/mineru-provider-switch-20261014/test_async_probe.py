import ast
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import requests
import async_probe_remote as remote
import probe_async as controller

spec = importlib.util.spec_from_file_location('probe_candidate_test', '/candidate/app.py')
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)
TOKEN = 'fixture-key-DO-NOT-PRINT'


class Reply:
    def __init__(self, payload, code=200, headers=None):
        self.status_code, self.headers = code, headers or {}
        self.body = json.dumps(payload).encode()
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def iter_content(self, chunk_size): yield self.body


class NetworkFixture:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
    def mount(self, *args): pass
    def close(self): pass
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception): raise reply
        return reply


def result_payload(images=None):
    return {'results': {remote.FILENAME: {'md_content': '# Synthetic 8246\nSecond page 7734', 'images': images or {}}}}


class RemoteTests(unittest.TestCase):
    def transport(self, replies):
        network = NetworkFixture(replies)
        with patch('requests.Session', return_value=network):
            transport = remote.BoundedTransport(TOKEN, '/reviewed/ca.pem')
        self.assertFalse(network.trust_env)
        self.assertEqual(network.verify, '/reviewed/ca.pem')
        return transport, network

    def parse(self, transport):
        client = adapter.EnterpriseMinerUClient(api_url=remote.BASE, task_query_url=remote.TASKS,
            api_token=TOKEN, request_mode='async', timeout_seconds=240,
            poll_interval_seconds=3, transient_poll_retries=2, session=transport)
        with patch('time.sleep'), patch.object(remote, 'emit'):
            return client.parse_pdf(b'%PDF-synthetic-fixture', remote.FILENAME)

    def test_success_real_candidate_client_and_exact_query_routes(self):
        t, net = self.transport([Reply({'task_id': 'task-1'}, 202), Reply({'status': 'queued'}),
                                Reply({'status': 'completed'}), Reply(result_payload())])
        result = remote.check_result(adapter, self.parse(t))
        self.assertEqual((t.posts, t.status_gets, t.result_gets), (1, 2, 1))
        self.assertEqual(result['two_page_ocr_markers'], 'PASS')
        self.assertEqual(result['visual_return'], 'NOT_CONFIRMED')
        self.assertEqual(net.calls[0][1], remote.BASE + '/tasks')
        self.assertEqual(net.calls[-1][1], remote.TASKS + '/task-1/result')
        for _, _, kwargs in net.calls:
            self.assertFalse(kwargs['allow_redirects'])
            self.assertTrue(kwargs['stream'])
            self.assertEqual(kwargs['headers']['Authorization'], 'Bearer ' + TOKEN)

    def test_uncertain_post_no_second_submission(self):
        t, net = self.transport([requests.ReadTimeout('PRIVATE ' + TOKEN)])
        with self.assertRaises(requests.ReadTimeout): self.parse(t)
        with self.assertRaises(remote.ProbeFailure): self.parse(t)
        self.assertEqual(len(net.calls), 1)

    def test_no_redirect_follow_or_resubmit(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                t, net = self.transport([Reply({}, status, {'Location': 'https://untrusted.invalid'})])
                with self.assertRaisesRegex(remote.ProbeFailure, 'redirect_refused'): self.parse(t)
                self.assertEqual(len(net.calls), 1)

    def test_query_redirect_refused(self):
        t, net = self.transport([Reply({'task_id': 'x'}), Reply({}, 302)])
        with self.assertRaisesRegex(remote.ProbeFailure, 'redirect_refused'): self.parse(t)
        self.assertEqual([x[0] for x in net.calls], ['POST', 'GET'])

    def test_query_cannot_leave_task_or_origin(self):
        t, net = self.transport([])
        t.task_id = 'known'
        for url in ('https://other.invalid/tasks/known', remote.TASKS + '/another', remote.BASE + '/tasks/known'):
            with self.assertRaises(remote.ProbeFailure):
                t.get(url, headers={'Authorization': 'Bearer ' + TOKEN}, timeout=3, allow_redirects=False)
        self.assertFalse(net.calls)

    def test_transient_status_retries_same_task_not_post(self):
        t, net = self.transport([Reply({'task_id': 'x'}), Reply({}, 404),
                                Reply({'status': 'completed'}), Reply(result_payload())])
        self.parse(t)
        self.assertEqual(t.posts, 1)
        self.assertEqual(net.calls[1][1], net.calls[2][1])

    def test_terminal_failure_not_polled_forever(self):
        for status in ('failed', 'error'):
            t, net = self.transport([Reply({'task_id': 'x'}), Reply({'status': status, 'error': TOKEN})])
            with self.assertRaises(RuntimeError): self.parse(t)
            self.assertEqual((t.posts, t.status_gets, t.result_gets), (1, 1, 0))

    def test_poll_count_and_body_budgets(self):
        t, net = self.transport([])
        t.task_id = 'x'
        t.status_gets = 100
        with self.assertRaisesRegex(remote.ProbeFailure, 'status_request_budget'):
            t.get(remote.TASKS + '/x', headers={}, timeout=3, allow_redirects=False)
        t.result_gets = 10
        with self.assertRaisesRegex(remote.ProbeFailure, 'result_request_budget'):
            t.get(remote.TASKS + '/x/result', headers={}, timeout=3, allow_redirects=False)
        t, net = self.transport([Reply({}, 200, {'Content-Length': '65537'})])
        with self.assertRaisesRegex(remote.ProbeFailure, 'response_size_limit'): self.parse(t)

    def test_error_classes_do_not_expose_messages(self):
        for error in (requests.exceptions.SSLError(TOKEN), requests.ReadTimeout(TOKEN), RuntimeError(TOKEN)):
            self.assertNotIn(TOKEN, remote.error_class(error))

    def test_synthetic_document_is_bounded_two_page_pdf(self):
        pdf = remote.synthetic_pdf()
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertLess(len(pdf), 2097152)
        self.assertEqual(pdf.count(b'/Type /Page\n'), 2)

    def test_empty_and_missing_second_page_are_rejected(self):
        empty = {'results': {remote.FILENAME: {'md_content': ''}}}
        with self.assertRaisesRegex(RuntimeError, 'no parseable content'):
            remote.check_result(adapter, empty)
        missing = {'results': {remote.FILENAME: {'md_content': '8246 only'}}}
        with self.assertRaisesRegex(remote.ProbeFailure, 'two_page_ocr_markers_not_found'):
            remote.check_result(adapter, missing)

    def test_image_result_decodes_without_network(self):
        from PIL import Image
        content = io.BytesIO()
        Image.new('RGB', (5, 5), 'white').save(content, format='PNG')
        summary = remote.check_result(adapter, result_payload({'chart.png': base64.b64encode(content.getvalue()).decode()}))
        self.assertEqual(summary['decoded_images'], 1)
        self.assertEqual(summary['visual_return'], 'PRESENT')

    def test_token_validation_no_trim_or_echo(self):
        for value in ('', ' badkey ', 'newline\nkey', '密钥123456'):
            with self.assertRaisesRegex(remote.ProbeFailure, '^invalid_hidden_key$'): remote.validate_token(value)

    def test_candidate_loaded_in_memory_without_replacing_baseline(self):
        source = Path('/candidate/app.py').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            baseline = Path(tmp) / 'baseline.py'
            baseline.write_text('# unchanged live source fixture\n')
            before = baseline.read_bytes()
            with patch.object(remote, 'SOURCE', str(baseline)), \
                    patch.object(remote, 'SOURCE_SHA', hashlib.sha256(before).hexdigest()):
                module = remote.load_candidate(source, hashlib.sha256(source.encode()).hexdigest())
            self.assertTrue(hasattr(module, 'EnterpriseMinerUClient'))
            self.assertEqual(before, baseline.read_bytes())

    def test_full_remote_entry_success_and_uncertain_post_are_redacted(self):
        for replies, expected in (([Reply({'task_id': 'x'}), Reply({'status': 'completed'}), Reply(result_payload())], 0),
                                  ([requests.ReadTimeout(TOKEN)], 1)):
            network = NetworkFixture(replies)
            with tempfile.TemporaryDirectory() as tmp:
                ca = Path(tmp) / 'ca.pem'
                ca.write_bytes(b'fake-ca-only-for-offline-test')
                with patch.dict(os.environ, {'MINERU_CA_BUNDLE': str(ca)}), \
                        patch.object(remote, 'CA_SHA', hashlib.sha256(ca.read_bytes()).hexdigest()), \
                        patch.object(remote, 'load_candidate', return_value=adapter), \
                        patch.object(remote.resource, 'setrlimit'), patch.object(remote.signal, 'signal'), \
                        patch.object(remote.signal, 'alarm'), patch.object(remote.logging, 'disable'), \
                        patch('requests.Session', return_value=network), patch.object(remote, 'emit') as emit:
                    rc = remote.entry(TOKEN, 'candidate', 'sha')
                output = [call.args[0] for call in emit.call_args_list]
                self.assertNotIn(TOKEN, json.dumps(output))
                self.assertEqual(rc, expected)
                self.assertEqual(output[-1]['post_attempts'], 1)
                self.assertFalse(output[-1]['ready_for_rollout'])
                self.assertFalse(output[-1]['automatic_resubmission'])


class ControllerTests(unittest.TestCase):
    def test_python37_controller_syntax_and_no_mutating_kube_verbs(self):
        tree = ast.parse(Path(controller.__file__).read_text(), feature_version=(3, 7))
        values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        for word in ('patch', 'delete', 'apply', 'scale', 'rollout', 'secret', 'secrets'):
            self.assertNotIn(word, values)

    def test_atomic_started_record_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            controller.write_new(root, 'PROBE-STARTED.json', {'post_attempts': 1})
            with self.assertRaises(FileExistsError): controller.write_new(root, 'PROBE-STARTED.json', {})
            self.assertEqual((root / 'PROBE-STARTED.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((root / 'PROBE-STARTED.json').read_text())['post_attempts'], 1)

    def test_key_uses_stdin_not_command_args_or_environment(self):
        class Process:
            returncode = 0
            def communicate(self, input=None, timeout=None):
                outer.assertEqual(json.loads(input)['token'], TOKEN)
                return b'{"async_smoke":"PASS","post_attempts":1}\n', b''
        outer = self
        with patch.object(controller.subprocess, 'Popen', return_value=Process()) as popen, patch.object(controller.a, 'emit'):
            rc, receipts, uncertain = controller.execute_probe('worker', 'code', 'source', 'sha', TOKEN)
        args, kwargs = popen.call_args
        self.assertNotIn(TOKEN, repr(args) + repr(kwargs))
        self.assertEqual(rc, 0)
        self.assertFalse(uncertain)

    def test_credential_in_remote_output_is_never_exported(self):
        class Process:
            returncode = 1
            def communicate(self, **kwargs): return TOKEN.encode(), b''
        with patch.object(controller.subprocess, 'Popen', return_value=Process()), patch.object(controller.a, 'emit') as emit:
            with self.assertRaises(controller.a.CheckFailure):
                controller.execute_probe('worker', 'code', 'source', 'sha', TOKEN)
        emit.assert_not_called()

    def test_busy_worker_is_not_selected(self):
        p = {'metadata': {'name': 'worker'}, 'status': {'containerStatuses': [{'name': 'adapter', 'ready': True}]}}
        with patch.object(controller.c, 'owned', return_value=[p]), patch.object(controller.c, 'health', return_value={
                'http_status': 200, 'source_sha256': controller.SOURCE_SHA, 'parser': {'active': 1, 'waiting': 0}}), \
                patch.object(controller.a, 'emit'):
            with self.assertRaisesRegex(controller.a.CheckFailure, 'no_verified_idle_worker'):
                controller.select_worker({})

    def test_started_gate_blocks_all_network_and_key_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            gate = Path(tmp)
            controller.write_new(gate, 'PROBE-STARTED.json', {})
            with patch.object(controller, 'GATE', gate), patch.object(controller, 'safe_directory'), \
                    patch.object(controller.socket, 'gethostname', return_value='datax-01'), \
                    patch.object(sys, 'argv', ['probe_async.py', '--probe-once']), \
                    patch.object(controller.a, 'get') as kube, patch.object(controller.getpass, 'getpass') as password:
                with self.assertRaisesRegex(controller.a.CheckFailure, 'already_started_use_status'):
                    controller.main()
            kube.assert_not_called()
            password.assert_not_called()

    def test_transport_timeout_preserves_uncertain_outcome_and_does_not_retry_exec(self):
        class Process:
            returncode = -9
            killed = False
            def communicate(self, input=None, timeout=None):
                if not self.killed: raise subprocess.TimeoutExpired('kubectl', 15)
                return b'{"accepted_task_id":"existing-task","post_attempts":1}\n', b''
            def kill(self): self.killed = True
            def poll(self): return self.returncode if self.killed else None
        p = Process()
        with patch.object(controller.subprocess, 'Popen', return_value=p) as popen, \
                patch.object(controller.time, 'monotonic', side_effect=[0, 400]), patch.object(controller.a, 'emit'):
            rc, receipts, uncertain = controller.execute_probe('worker', 'code', 'source', 'sha', TOKEN)
        self.assertTrue(uncertain)
        self.assertTrue(p.killed)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(receipts[0]['accepted_task_id'], 'existing-task')


if __name__ == '__main__': unittest.main()
