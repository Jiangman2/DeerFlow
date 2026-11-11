"""Offline transport and read-only polling checks; no sockets are opened."""
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import requests

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'release/energy-market-released-20261110/proxy/repair'
OLD = ROOT / 'release/energy-market-released-20261110/baseline/energy_market_collector.py'
UPSTREAM = ROOT / 'skills/public/energy-market-daily/upstream'
spec = importlib.util.spec_from_file_location('proxy_collector_candidate', HERE / 'energy_market_collector.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)
ENDPOINT = 'http://ip91.jkx.com.cn:6688/jkx_eecc.aspx'
PROXY = 'http://10.68.211.1:8080'
KEY = 'synthetic-private-value-never-export'
DAY = '2026-11-09'


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


class TrackedResponse(requests.Response):
    def __init__(self, status=200, body=b'{"ok":true}', fail_read=False):
        super().__init__()
        self.status_code = status
        self.encoding = 'utf-8'
        self.raw = io.BytesIO(body)
        self.closed_count = 0
        self.fail_read = fail_read
        if status in range(300, 400):
            self.headers['Location'] = 'http://forbidden.invalid/secret'

    def iter_content(self, chunk_size=1, decode_unicode=False):
        if self.fail_read:
            raise requests.Timeout('synthetic-' + KEY)
        yield from super().iter_content(chunk_size, decode_unicode)

    def close(self):
        self.closed_count += 1
        super().close()


class RunnerTests(unittest.TestCase):
    def run_runner(self, action=None, responses=None, env=None, budget='188', original=None):
        if original is None:
            original = types.ModuleType('fetch_market_history')
            original.requests = requests
            original.main = action or (lambda: requests.get(
                ENDPOINT, params={'p': KEY, 'pdate': DAY}, timeout=30).json())
        fake_responses = list(responses or [TrackedResponse()])
        self.sent = []
        self.guard = None
        session = requests.Session()
        self.session = session
        original_get = requests.get
        before_path = list(sys.path)
        out = io.StringIO()

        def add_hook(hook):
            self.guard = hook

        def send(adapter, request, **kwargs):
            self.sent.append((request, kwargs))
            response = fake_responses[min(len(self.sent) - 1, len(fake_responses) - 1)]
            if isinstance(response, Exception):
                raise response
            response.request = request
            response.url = request.url
            return response

        values = {'ENERGY_MARKET_HTTP_PROXY': PROXY, 'JKX_API_PASSWORD': KEY,
                  'JKX_API_ENDPOINT': ENDPOINT}
        values.update(env or {})
        try:
            with mock.patch.dict(os.environ, values, clear=True), \
                 mock.patch.dict(sys.modules, {'fetch_market_history': original}), \
                 mock.patch.object(sys, 'argv', ['-c', str(UPSTREAM / 'app'), DAY, budget]), \
                 mock.patch.object(sys, 'addaudithook', add_hook), \
                 mock.patch.object(requests, 'Session', return_value=session), \
                 mock.patch.object(requests.adapters.HTTPAdapter, 'send', send), \
                 mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('REAL_NETWORK_FORBIDDEN')), \
                 mock.patch.object(session, 'close', wraps=session.close) as close, \
                 contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(compile(collector.RUNNER, '<candidate-runner>', 'exec'), {})
                self.session_closed = close.call_count
        finally:
            sys.path[:] = before_path
        self.assertIs(requests.get, original_get)
        self.output = out.getvalue()
        self.assertNotIn(KEY, self.output)
        self.assertNotIn('?p=', self.output)
        return json.loads(self.output)

    def test_explicit_proxy_preserves_params_and_timeout(self):
        response = TrackedResponse()
        out = self.run_runner(responses=[response])
        self.assertEqual(out['status'], 'ORIGINAL_MARKET_COMPLETE')
        request, kwargs = self.sent[0]
        self.assertEqual(request.method, 'GET')
        parsed = urlsplit(request.url)
        self.assertEqual(parsed._replace(query='').geturl(), ENDPOINT)
        self.assertEqual(parse_qs(parsed.query), {'p': [KEY], 'pdate': [DAY]})
        self.assertEqual(kwargs['proxies'], {'http': PROXY})
        self.assertEqual(kwargs['timeout'], 30)
        self.assertEqual(response.closed_count, 1)
        self.assertEqual(response.json(), {'ok': True})
        self.assertEqual(self.session_closed, 1)

    def test_no_proxy_and_foreign_environment_proxy_cannot_bypass(self):
        self.run_runner(env={'NO_PROXY': '*', 'no_proxy': 'ip91.jkx.com.cn',
                             'HTTP_PROXY': 'http://forbidden.invalid:9',
                             'HTTPS_PROXY': 'http://forbidden.invalid:8',
                             'ALL_PROXY': 'http://forbidden.invalid:7'})
        self.assertEqual(self.sent[0][1]['proxies'], {'http': PROXY})
        self.assertNotIn('Proxy-Authorization', self.sent[0][0].headers)

    def test_adapter_has_no_additional_retry(self):
        seen = []
        real_adapter = requests.adapters.HTTPAdapter
        def factory(*args, **kwargs):
            adapter = real_adapter(*args, **kwargs)
            seen.append(adapter.max_retries.total)
            return adapter
        with mock.patch.object(requests.adapters, 'HTTPAdapter', side_effect=factory):
            # Isolate this constructor assertion from the send monkeypatch helper.
            adapter = requests.adapters.HTTPAdapter(max_retries=0)
        self.assertEqual(seen, [0])
        self.assertEqual(adapter.max_retries.total, 0)
        self.assertIn("HTTPAdapter(max_retries=0)", collector.RUNNER)

    def test_redirect_is_closed_without_followup(self):
        response = TrackedResponse(302, fail_read=True)
        out = self.run_runner(responses=[response])
        self.assertEqual(out['status'], 'INCOMPLETE')
        self.assertEqual(out['error_class'], 'ValueError')
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(response.closed_count, 1)
        self.assertEqual(self.session_closed, 1)

    def test_cookie_state_is_not_carried_between_original_get_calls(self):
        def action():
            self.session.cookies.set('synthetic-session', 'first')
            requests.get(ENDPOINT, params={'p': KEY, 'pdate': DAY}, timeout=30)
            self.session.cookies.set('synthetic-session', 'second')
            requests.get(ENDPOINT, params={'p': KEY, 'pdate': DAY}, timeout=30)
        out = self.run_runner(action=action, responses=[TrackedResponse(), TrackedResponse()])
        self.assertEqual(out['status'], 'ORIGINAL_MARKET_COMPLETE')
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all('Cookie' not in request.headers for request, _ in self.sent))
        self.assertEqual(len(self.session.cookies), 0)

    def test_decoded_response_bound_and_close(self):
        response = TrackedResponse(body=b'x' * 262145)
        out = self.run_runner(responses=[response])
        self.assertEqual(out['status'], 'INCOMPLETE')
        self.assertEqual(response.closed_count, 1)

    def test_exact_body_limit_passes(self):
        body = b'"' + b'x' * 262142 + b'"'
        response = TrackedResponse(body=body)
        out = self.run_runner(responses=[response])
        self.assertEqual(out['status'], 'ORIGINAL_MARKET_COMPLETE')
        self.assertEqual(response.content, body)

    def test_read_error_closes_response_and_redacts(self):
        response = TrackedResponse(fail_read=True)
        out = self.run_runner(responses=[response])
        self.assertEqual(out['status'], 'INCOMPLETE')
        self.assertEqual(response.closed_count, 1)

    def test_http_error_retains_raise_for_status_interface(self):
        def action():
            response = requests.get(ENDPOINT, params={'p': KEY, 'pdate': DAY}, timeout=30)
            response.raise_for_status()
        out = self.run_runner(action=action, responses=[TrackedResponse(407)])
        self.assertEqual(out['error_class'], 'HTTPError')

    def test_proxy_is_required_and_exact(self):
        for value in ('', 'http://10.68.211.1:8080/', 'http://user:pass@10.68.211.1:8080',
                      'http://116.204.43.227:6688', 'https://10.68.211.1:8080'):
            with self.subTest(value=value):
                out = self.run_runner(env={'ENERGY_MARKET_HTTP_PROXY': value})
                self.assertEqual(out['error_code'], 'FIXED_PROXY_REQUIRED')
                self.assertEqual(self.sent, [])

    def test_provider_url_and_parameter_boundary(self):
        variants = [
            ('http://other.invalid/', {'p': KEY, 'pdate': DAY}, 30),
            (ENDPOINT + '?extra=1', {'p': KEY, 'pdate': DAY}, 30),
            (ENDPOINT, {'p': KEY, 'pdate': DAY, 'extra': '1'}, 30),
            (ENDPOINT, {'p': 'wrong', 'pdate': DAY}, 30),
            (ENDPOINT, {'p': KEY, 'pdate': '2026-11-99'}, 30),
            (ENDPOINT, {'p': KEY, 'pdate': DAY}, 120),
        ]
        for url, params, timeout in variants:
            with self.subTest(url=url, params=list(params), timeout=timeout):
                out = self.run_runner(action=lambda: requests.get(url, params=params, timeout=timeout))
                self.assertEqual(out['status'], 'INCOMPLETE')
                self.assertEqual(self.sent, [])

    def test_audit_only_allows_fixed_proxy_dns_and_connect(self):
        self.run_runner()
        self.guard('socket.getaddrinfo', ('10.68.211.1', 8080))
        self.guard('socket.connect', (None, ('10.68.211.1', 8080)))
        for name in ('ip91.jkx.com.cn', '116.204.43.227', 'other.invalid', '127.0.0.1'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(RuntimeError, 'NETWORK_TARGET_REFUSED'):
                    self.guard('socket.getaddrinfo', (name, 6688))
                with self.assertRaisesRegex(RuntimeError, 'NETWORK_TARGET_REFUSED'):
                    self.guard('socket.connect', (None, (name, 6688)))
        with self.assertRaisesRegex(RuntimeError, 'NETWORK_TARGET_REFUSED'):
            self.guard('socket.connect', (None, ('10.68.211.1', 6688)))

    def test_connection_budget_is_preserved(self):
        self.run_runner(budget='1')
        self.guard('socket.connect', (None, ('10.68.211.1', 8080)))
        with self.assertRaisesRegex(RuntimeError, 'NETWORK_BUDGET'):
            self.guard('socket.connect', (None, ('10.68.211.1', 8080)))

    def test_original_fetch_retries_exactly_twice_without_source_changes(self):
        before = hashes(UPSTREAM)
        path_before = list(sys.path)
        try:
            sys.path.insert(0, str(UPSTREAM / 'app'))
            spec = importlib.util.spec_from_file_location('original_for_proxy_test', UPSTREAM / 'app/fetch_market_history.py')
            original = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(original)
            with tempfile.TemporaryDirectory() as folder:
                original.ROOT = Path(folder)
                original.main = lambda: original.fetch(DAY)
                out = self.run_runner(original=original, responses=[requests.ConnectionError('private-' + KEY)])
            self.assertEqual(out['error_code'], 'PROVIDER_REQUEST_FAILED')
            self.assertEqual(len(self.sent), 2)
            self.assertEqual(hashes(UPSTREAM), before)
            self.assertEqual(len(before), 158)
        finally:
            sys.path[:] = path_before


class ConfigurationAndStatusTests(unittest.TestCase):
    def setUp(self):
        self.day = dt.datetime.now(collector.TZ).date().isoformat()
        self.old_state = dict(collector.STATE)
        self.old_active = collector.ACTIVE
        collector.STATE.clear()
        collector.ACTIVE = None

    def tearDown(self):
        collector.STATE.clear()
        collector.STATE.update(self.old_state)
        collector.ACTIVE = self.old_active

    def get(self, path):
        handler = object.__new__(collector.Handler)
        handler.path = path
        answers = []
        handler.reply = lambda code, body: answers.append((code, body))
        before = (dict(collector.STATE), collector.ACTIVE)
        with mock.patch.object(collector.threading, 'Thread', side_effect=AssertionError('NO_NEW_COLLECTION')):
            handler.do_GET()
        self.assertEqual((dict(collector.STATE), collector.ACTIVE), before)
        self.assertEqual(len(answers), 1)
        return answers[0]

    def test_configured_proxy_requires_exact_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, 'FIXED_PROXY_REQUIRED'):
                collector.configured_proxy()
        with mock.patch.dict(os.environ, {'ENERGY_MARKET_HTTP_PROXY': PROXY}, clear=True):
            self.assertEqual(collector.configured_proxy(), PROXY)

    def test_missing_proxy_does_not_read_credentials_or_start_child(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch.object(collector, 'credential', side_effect=AssertionError('NO_CREDENTIAL_READ')), \
             mock.patch.object(collector.subprocess, 'Popen', side_effect=AssertionError('NO_PROCESS')):
            collector.collect(self.day)
        self.assertEqual(collector.STATE[self.day]['status'], 'INCOMPLETE')

    def test_status_unrequested(self):
        self.assertEqual(self.get('/status/' + self.day),
                         (200, {'status': 'NOT_REQUESTED', 'data_date': self.day}))

    def test_status_other_active_date(self):
        other = (dt.date.fromisoformat(self.day) - dt.timedelta(days=1)).isoformat()
        collector.ACTIVE = other
        code, value = self.get('/status/' + self.day)
        self.assertEqual(code, 200)
        self.assertEqual(value, {'status': 'NOT_REQUESTED', 'data_date': self.day, 'active_data_date': other})

    def test_status_active_current_precedes_old_failure(self):
        collector.STATE[self.day] = {'status': 'INCOMPLETE', 'retry_after_epoch': 0}
        collector.ACTIVE = self.day
        self.assertEqual(self.get('/status/' + self.day),
                         (200, {'status': 'COLLECTING', 'data_date': self.day}))

    def test_status_terminal_and_expired_states_are_readonly(self):
        for status in ('READY', 'WAIT_DATA', 'INCOMPLETE'):
            with self.subTest(status=status):
                collector.STATE[self.day] = {'status': status, 'data_date': self.day,
                                            'retry_after_epoch': 0, 'credentials_exported': False}
                code, value = self.get('/status/' + self.day)
                self.assertEqual(code, 200)
                self.assertEqual(value['status'], status)
                self.assertNotIn('retry_after_epoch', value)

    def test_status_invalid_and_old_dates(self):
        self.assertEqual(self.get('/status/2026-02-30'), (400, {'status': 'DATE_WINDOW'}))
        self.assertEqual(self.get('/status/2000-01-01'), (400, {'status': 'DATE_WINDOW'}))
        self.assertEqual(self.get('/status/' + self.day + '?x=1'), (404, {'status': 'NOT_FOUND'}))

    def test_health_unchanged(self):
        self.assertEqual(self.get('/health'), (200, {'status': 'UP'}))

    def test_old_collector_is_unchanged(self):
        self.assertEqual(hashlib.sha256(OLD.read_bytes()).hexdigest(),
                         'ce06ffcf09ffce8629734fe84e537c164644e2e01b51bae4d98845c418840a07')

    def publish_synthetic(self, missing_required=False):
        prior_path = list(sys.path)
        try:
            sys.path.insert(0, str(UPSTREAM / 'app'))
            spec = importlib.util.spec_from_file_location('normalizer_for_cache_test', UPSTREAM / 'app/fetch_market_history.py')
            original = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(original)
            rows = []
            for code in list(original.SPECS) + original.FREIGHT:
                unit = original.SPECS[code][2] if code in original.SPECS else 'synthetic-unit'
                rows.append({'code_name': code, 'value_date': DAY, 'close': '100',
                             'unit': unit, 'item': 'synthetic'})
            rows.append({'code_name': 'OPTIONAL_EXTRA', 'value_date': DAY, 'close': '--',
                         'unit': 'synthetic-unit', 'item': 'synthetic-extra'})
            if missing_required:
                rows[0]['close'] = '--'
            payload = {'errcode': 0, 'data': rows}
            with tempfile.TemporaryDirectory() as folder:
                engine = Path(folder) / 'engine'
                app = engine / 'app'
                sources = app / 'sources'
                sources.mkdir(parents=True)
                for name in ('fetch_market_history.py', 'fetch_energy_data.py'):
                    (app / name).write_bytes((UPSTREAM / 'app' / name).read_bytes())
                name = 'jkx_new_' + DAY + '.json'
                (sources / name).write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                feed = Path(folder) / 'feed'
                feed.mkdir()
                entry = types.SimpleNamespace(read=lambda path, limit: path.read_bytes())
                with mock.patch.object(collector, 'FEED', feed):
                    collector.publish_cache(engine, KEY, entry, DAY)
                if missing_required:
                    self.assertFalse((feed / name).exists())
                else:
                    published = json.loads((feed / name).read_bytes())
                    self.assertEqual(original.normalize(payload, DAY), original.normalize(published, DAY))
                    self.assertEqual(len(published['data']), 15)
        finally:
            sys.path[:] = prior_path

    def test_optional_missing_row_does_not_hide_original_valid_14_series(self):
        self.publish_synthetic()

    def test_required_missing_quote_still_not_published(self):
        self.publish_synthetic(missing_required=True)


if __name__ == '__main__':
    unittest.main()
