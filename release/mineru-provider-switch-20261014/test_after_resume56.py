import ast
import contextlib
import datetime
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import glm_vlm_wire_probe as g
import review_after_resume56 as r
import observe_pipeline as obs


def event(row):
    return b'data: ' + json.dumps(row).encode() + b'\n\n'


class WireTests(unittest.TestCase):
    def good(self):
        return (event({'choices': []}) + event({'choices': [{'delta': {'reasoning_content': 'NEVER_EXPORT_REASONING'}}]})
                + event({'choices': [{'delta': {'content': 'Red'}, 'finish_reason': 'stop'}]}) + b'data: [DONE]\n\n')

    def test_payload_variants_and_no_disable_thinking(self):
        for mode, first, streamed in [('image_first_stream', 'image_url', True), ('native_text_first_nonstream', 'text', False)]:
            p = g.payload(mode)
            self.assertEqual(p['messages'][0]['content'][0]['type'], first)
            self.assertEqual(p.get('stream', False), streamed)
            self.assertEqual(p['max_tokens'], 5000)
            self.assertEqual(p['temperature'], .1)
            self.assertNotIn('thinking', p)
            self.assertNotIn('reasoning_effort', p)

    def test_unknown_variant(self):
        with self.assertRaises(g.ProbeFailure):
            g.payload('other_url')

    def test_png_is_actual_rgb_red(self):
        import struct, zlib
        data = g.red_png(); self.assertTrue(data.startswith(b'\x89PNG\r\n\x1a\n'))
        off = 8; body = b''
        while off < len(data):
            n = struct.unpack('!I', data[off:off+4])[0]; kind = data[off+4:off+8]; val = data[off+8:off+8+n]
            self.assertEqual(zlib.crc32(kind+val) & 0xffffffff, struct.unpack('!I', data[off+8+n:off+12+n])[0])
            if kind == b'IDAT': body += val
            off += n+12
        self.assertEqual(zlib.decompress(body), (b'\0'+b'\xff\0\0'*32)*32)

    def test_sse_arbitrary_fragmentation_and_reasoning_not_exported(self):
        parser = g.SSE()
        for c in self.good(): parser.feed(bytes([c]))
        result = parser.result()
        self.assertTrue(result['synthetic_red_check'])
        self.assertTrue(result['reasoning_discarded'])
        self.assertNotIn('NEVER_EXPORT_REASONING', json.dumps(result))

    def test_finish_and_content_same_chunk_preserved(self):
        parser = g.SSE(); parser.feed(self.good())
        self.assertEqual(parser.result()['visible_characters'], 3)

    def test_crlf_and_comments(self):
        parser = g.SSE(); parser.feed(b':keepalive\r\n\r\n'+self.good().replace(b'\n', b'\r\n'))
        self.assertTrue(parser.result()['done'])

    def test_eof_without_done_fails(self):
        parser = g.SSE(); parser.feed(self.good().split(b'data: [DONE]')[0])
        with self.assertRaises(g.ProbeFailure): parser.result()

    def test_done_without_finish_fails(self):
        parser = g.SSE(); parser.feed(event({'choices': [{'delta': {'content': 'red'}}]}) + b'data: [DONE]\n\n')
        with self.assertRaises(g.ProbeFailure): parser.result()

    def test_error_after_finish_not_ignored(self):
        parser = g.SSE()
        with self.assertRaises(g.ProbeFailure):
            parser.feed(self.good().split(b'data: [DONE]')[0]+event({'error': {'message': 'SENSITIVE'}}))

    def test_error_event(self):
        with self.assertRaises(g.ProbeFailure): g.SSE().feed(b'event: error\n')

    def test_length_finish_fails(self):
        with self.assertRaises(g.ProbeFailure):
            g.SSE().feed(event({'choices': [{'delta': {'content': 'red'}, 'finish_reason': 'length'}]}))

    def test_tools_rejected(self):
        with self.assertRaises(g.ProbeFailure):
            g.SSE().feed(event({'choices': [{'delta': {'tool_calls': [{}]}}]}))

    def test_line_limit(self):
        with self.assertRaises(g.ProbeFailure): g.SSE().feed(b'x'*65537)

    def test_whole_response_limit(self):
        with self.assertRaises(g.ProbeFailure): g.SSE().feed(b'x'*(g.LIMIT+1))

    def test_bad_json(self):
        with self.assertRaises(ValueError): g.SSE().feed(b'data: bad\n\n')

    def test_multi_choices_rejected(self):
        with self.assertRaises(g.ProbeFailure): g.SSE().feed(event({'choices': [{}, {}]}))

    def test_invalid_content(self):
        with self.assertRaises(g.ProbeFailure): g.SSE().feed(event({'choices': [{'delta': {'content': ['red']}}]}))

    def test_nonstream_redacted(self):
        data = {'choices': [{'message': {'content': 'red', 'reasoning_content': 'SENSITIVE'}, 'finish_reason': 'stop'}]}
        result = g.nonstream(json.dumps(data).encode())
        self.assertTrue(result['synthetic_red_check'])
        self.assertNotIn('SENSITIVE', json.dumps(result))

    def test_empty_content_and_truncated_nonstream_rejected(self):
        for text, reason in [('', 'stop'), ('red', 'length')]:
            with self.assertRaises(g.ProbeFailure):
                g.nonstream(json.dumps({'choices': [{'message': {'content': text}, 'finish_reason': reason}]}).encode())

    def test_wrong_colour_not_pass(self):
        self.assertFalse(g.visible_result('blue')['synthetic_red_check'])
        self.assertFalse(g.visible_result('not red')['synthetic_red_check'])

    def test_alarm_raises_fixed_label(self):
        with self.assertRaisesRegex(g.ProbeFailure, '^total_deadline_outcome_uncertain$'):
            g.timeout_handler(None, None)

    def test_http_redirect_never_followed(self):
        c = SimpleNamespace(request=lambda *a: None, getresponse=lambda: SimpleNamespace(status=302), close=lambda: None)
        with patch.object(g.signal, 'SIGALRM', 14, create=True), patch.object(g.signal, 'ITIMER_REAL', 0, create=True), \
             patch.object(g.signal, 'signal'), patch.object(g.signal, 'setitimer', create=True), \
             patch.object(g.http.client, 'HTTPSConnection', return_value=c) as connect:
            row = g.one('image_first_stream', 'test-only-token', None)
        self.assertEqual(connect.call_count, 1)
        self.assertEqual(row['failure'], 'redirect_refused')
        self.assertEqual(row['post_attempts'], 1)

    def test_timeout_never_retries(self):
        c = SimpleNamespace(request=lambda *a: (_ for _ in ()).throw(TimeoutError('SECRET')), close=lambda: None)
        with patch.object(g.signal, 'SIGALRM', 14, create=True), patch.object(g.signal, 'ITIMER_REAL', 0, create=True), \
             patch.object(g.signal, 'signal'), patch.object(g.signal, 'setitimer', create=True), \
             patch.object(g.http.client, 'HTTPSConnection', return_value=c) as connect:
            row = g.one('image_first_stream', 'test-only-token', None)
        self.assertEqual(connect.call_count, 1)
        self.assertNotIn('SECRET', json.dumps(row))

    def test_positive_control_failure_stops_comparison(self):
        resource = SimpleNamespace(RLIMIT_CORE=0, setrlimit=lambda *a: None)
        with patch.dict('sys.modules', {'resource': resource}), patch.object(g, 'context', return_value=(None, {})), \
             patch.object(g, 'one', return_value={'result': 'FAIL', 'post_attempts': 1}) as one:
            result = g.run('test-only-token')
        self.assertEqual(one.call_count, 1)
        self.assertEqual(len(result['results']), 1)

    def test_two_distinct_requests_not_retries(self):
        resource = SimpleNamespace(RLIMIT_CORE=0, setrlimit=lambda *a: None)
        with patch.dict('sys.modules', {'resource': resource}), patch.object(g, 'context', return_value=(None, {})), \
             patch.object(g, 'one', return_value={'result': 'PASS', 'post_attempts': 1}) as one:
            result = g.run('test-only-token')
        self.assertEqual([x.args[0] for x in one.call_args_list], ['image_first_stream', 'native_text_first_nonstream'])
        self.assertEqual(sum(x['post_attempts'] for x in result['results']), 2)
        self.assertEqual(result['native_app_acceptance'], 'NOT_TESTED')

    def test_credential_newline_rejected_before_network(self):
        resource = SimpleNamespace(RLIMIT_CORE=0, setrlimit=lambda *a: None)
        with patch.dict('sys.modules', {'resource': resource}), patch.object(g, 'context') as context:
            with self.assertRaises(g.ProbeFailure): g.run('test-key\r\nInjected: bad')
        context.assert_not_called()


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.p = patch.object(r, 'obs', obs); self.p.start(); self.addCleanup(self.p.stop)

    def report(self, text):
        return r.log_report(text.encode(), {'10.1.2.3': 'known-worker'},
                 datetime.datetime(2026, 10, 16, 6, 0, tzinfo=datetime.timezone.utc), obs.log_stats)

    def test_timestamps_offsets_nanoseconds(self):
        a = r.timestamp('2026-10-16T14:00:00.123456789+08:00 x')
        b = r.timestamp('2026-10-16T06:00:00.123456789Z x')
        self.assertEqual(a, b)
        self.assertEqual(a, '2026-10-16T06:00:00.123456+00:00')

    def test_bad_timestamp_returns_none(self):
        self.assertIsNone(r.timestamp('bad timestamp token'))
        self.assertIsNone(r.timestamp('2026-99-16T06:00:00Z x'))

    def test_parse_start_finish_matching_only_in_memory(self):
        data = ('2026-10-16T05:00:00Z Parse started request_id=abcdef012345 filename=SECRET\n'
                '2026-10-16T05:00:01Z Parse finished request_id=abcdef012345 filename=SECRET status=completed elapsed_seconds=1.000\n')
        result = self.report(data)
        self.assertEqual(result['parse_starts_without_finish_in_window'], 0)
        self.assertEqual(result['statistics']['parse_completed']['count'], 1)
        self.assertNotIn('abcdef012345', json.dumps(result))
        self.assertNotIn('SECRET', json.dumps(result))

    def test_unmatched_is_not_inflight_proof(self):
        result = self.report('2026-10-16T05:59:00Z Parse started request_id=abcdef012345 filename=SECRET')
        self.assertEqual(result['oldest_unmatched_start_age_seconds'], 60)
        self.assertTrue(result['unmatched_start_is_not_inflight_proof'])

    def test_quarantine_history_not_current(self):
        result = self.report('2026-10-16T14:00:00+08:00 MinerU dispatcher quarantined backend=10.1.2.3 cooldown_seconds=60.0 reason=ReadTimeout')
        self.assertEqual(result['quarantine_events'][0]['pod'], 'known-worker')
        self.assertFalse(result['quarantine_membership_proven'])
        self.assertNotIn('10.1.2.3', json.dumps(result))

    def test_unknown_reason_not_leaked(self):
        result = self.report('MinerU dispatcher quarantined backend=10.1.2.3 cooldown_seconds=60.0 reason=SECRET')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_error_classes_no_message_export(self):
        result = self.report('2026-10-16T06:00:00Z MinerUHTTPError: Enterprise MinerU HTTP 502 during task status: SECRET')
        self.assertEqual(result['error_class_counts'], {'PROVIDER_HTTP_502_TASK_STATUS': 1})
        self.assertNotIn('SECRET', json.dumps(result))

    def test_async_deadline_and_local_timeout_separated(self):
        self.assertEqual(r.error_classes('TimeoutError: Enterprise MinerU task abcd exceeded 1800 seconds'), ['PROVIDER_TASK_DEADLINE'])
        self.assertEqual(r.error_classes('RuntimeError: LibreOffice timed out after 120s SECRET'), ['LOCAL_LIBREOFFICE_TIMEOUT'])

    def test_log_limit_explicit(self):
        result = self.report('x'*r.MAX_LOG)
        self.assertTrue(result['limit_may_truncate'])

    def test_new_binding_requires_fresh_uid(self):
        accepted = {'consumer_recovery': 'PASS', 'new_pod': {'uid': r.APP_UID}}
        fake = SimpleNamespace(locked_receipt=lambda *a: accepted, load=lambda *a: accepted, deployment=lambda: {})
        with patch.object(r, 'e', fake), patch.object(r.recovery, 'load_plan', return_value={'target': {}}), \
             patch.object(r.recovery, 'exact_app', return_value={'metadata': {'uid': 'old-pod'}}):
            with self.assertRaisesRegex(r.helper.AuditFailure, 'new_app_pod_changed'):
                r.bind()

    def test_bind_requires_current_config_and_active56(self):
        accepted = {'consumer_recovery': 'PASS', 'new_pod': {'uid': r.APP_UID}}
        app = {'metadata': {'uid': r.APP_UID}}
        fake = SimpleNamespace(locked_receipt=lambda *a: accepted, load=lambda *a: accepted, deployment=lambda: {},
                  CURRENT_ENV={'a': 'b'}, CURRENT={'core': 8}, runtime_env=lambda *a: {},
                  redis_binding=lambda *a: (), snapshot=lambda *a: {})
        with patch.object(r, 'e', fake), patch.object(r.recovery, 'load_plan', return_value={'target': {}, 'service': {}}), \
             patch.object(r.recovery, 'exact_app', return_value=app), patch.object(r.recovery, 'endpoint_check'), \
             patch.object(fake, 'check_capacities', create=True) as capacity:
            r.bind()
        capacity.assert_called_once_with({}, {'core': 8}, 'active')

    def test_numeric_env_remote_code_is_valid(self):
        seen = []
        fake = SimpleNamespace(container=lambda p: {'name': 'adapter'},
             call=lambda args, **k: (seen.append(args[-1]) or json.dumps(dict.fromkeys(r.CONFIG_KEYS, 'UNSET')).encode()))
        with patch.object(r, 'obs', fake):
            self.assertEqual(r.numeric_config({'metadata': {'name': 'worker'}}), dict.fromkeys(r.CONFIG_KEYS, 'UNSET'))
        ast.parse(seen[0], feature_version=(3, 7))
        with patch.dict('os.environ', {'MINERU_TIMEOUT_SECONDS': '1800', 'MINERU_DISPATCH_PROBE_TIMEOUT_SECONDS': '.25',
                                     'MINERU_API_TOKEN': 'NEVER_EXPORT'}), contextlib.redirect_stdout(io.StringIO()) as out:
            exec(seen[0], {})
        row = json.loads(out.getvalue())
        self.assertEqual(row['MINERU_TIMEOUT_SECONDS'], 1800)
        self.assertEqual(row['MINERU_DISPATCH_PROBE_TIMEOUT_SECONDS'], .25)
        self.assertNotIn('NEVER_EXPORT', out.getvalue())

    def test_policy_and_bootstrap_python37(self):
        ast.parse(r.APP_POLICY, feature_version=(3, 7))
        ast.parse(r.BOOTSTRAP, feature_version=(3, 7))

    def test_wrong_host_no_side_effects(self):
        with patch.object(r.socket, 'gethostname', return_value='laptop'), patch.object(r.recovery, 'initialize') as init, \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(r.main(['--audit-and-glm-once']), 1)
        init.assert_not_called()
        self.assertEqual(json.loads(out.getvalue())['failure']['check'], 'run_on_datax01_as_root')

    def test_python37_grammar(self):
        for name in ('review_after_resume56.py', 'glm_vlm_wire_probe.py'):
            ast.parse(Path(__file__).with_name(name).read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_old_files_not_changed(self):
        pins = {'tune_weknora88.py': '680a61d26f6aa3cf6ce13d28a645022760caeb0ed138c938841bcd183456bfb5',
                'recover_weknora56_consumers.py': '6b8826afb2076c588d9607ecd8f36271bda8ffc4df32d25f71740ff93d9927d5',
                'observe_pipeline.py': 'dd30806b5225824650d9fa9326a76878dad8af497e33d823732f892c267d5551'}
        for name, sha in pins.items():
            self.assertEqual(hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest(), sha)

    def test_no_mutator_calls_in_new_controller(self):
        source = Path(r.__file__).read_text(encoding='utf-8')
        tree = ast.parse(source)
        names = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
        self.assertFalse(names & {'recover', 'mutate', 'patch_call', 'confirm_stopped', 'send_signal', 'killpg'})
        self.assertNotIn("recovery.ROOT/'controller.lock'", source)


if __name__ == '__main__':
    unittest.main()
