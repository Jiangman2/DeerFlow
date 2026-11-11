"""Fresh-target acceptance with actual original fetch/normalizer, synthetic HTTP."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
import unittest
from unittest import mock

import requests
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'release/energy-market-released-20261110/proxy/repair'))
import fresh_target_probe as probe
from test_energy_market_collector import TrackedResponse, collector, UPSTREAM, DAY, KEY, PROXY, ENDPOINT


class FreshTargetTests(unittest.TestCase):
    def execute(self, missing=False, bad_date=False, cached=False, first_failure=False):
        with tempfile.TemporaryDirectory() as tmp:
            engine = Path(tmp) / 'engine'
            shutil.copytree(UPSTREAM, engine)
            app = engine / 'app'
            data = json.loads((app / 'sources/jkx_new_2026-11-06.json').read_text(encoding='utf-8'))
            for row in data['data']:
                row['value_date'] = '2026-11-06' if bad_date else DAY
                if missing and row['code_name'] == 'XA0942':
                    row['close'] = '--'
            target = app / 'sources' / ('jkx_new_' + DAY + '.json')
            if cached:
                target.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            before = hashlib.sha256((app / 'fetch_market_history.py').read_bytes()).hexdigest()
            spec = importlib.util.spec_from_file_location('fetch_market_history', app / 'fetch_market_history.py')
            module = importlib.util.module_from_spec(spec)
            old_path = list(sys.path)
            self.calls = []
            guard = []
            session = requests.Session()
            response = TrackedResponse(200, json.dumps(data, ensure_ascii=False).encode('utf-8'))
            def send(adapter, request, **kwargs):
                self.calls.append(kwargs)
                guard[0]('socket.connect', (None, ('10.68.211.1', 8080)))
                if first_failure and len(self.calls) == 1:
                    raise requests.ConnectionError('redact-' + KEY)
                response.request, response.url = request, request.url
                return response
            stream = io.StringIO()
            try:
                sys.path.insert(0, str(app))
                spec.loader.exec_module(module)
                with mock.patch.dict(sys.modules, {'fetch_market_history': module}), \
                     mock.patch.object(sys, 'argv', ['-c', str(app), DAY, '2']), \
                     mock.patch.object(sys, 'addaudithook', lambda hook: guard.append(hook)), \
                     mock.patch.dict(os.environ, {'ENERGY_MARKET_HTTP_PROXY': PROXY,
                                                  'JKX_API_PASSWORD': KEY, 'JKX_API_ENDPOINT': ENDPOINT}, clear=True), \
                     mock.patch.object(requests, 'Session', return_value=session), \
                     mock.patch.object(requests.adapters.HTTPAdapter, 'send', send), \
                     mock.patch.object(socket.socket, 'connect', side_effect=AssertionError('NO_REAL_NETWORK')), \
                     contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                    exec(compile(probe.SINGLE_DAY + collector.RUNNER, '<fresh-single>', 'exec'), {})
            finally:
                sys.path[:] = old_path
            self.assertEqual(before, hashlib.sha256((app / 'fetch_market_history.py').read_bytes()).hexdigest())
            output = stream.getvalue()
            self.assertNotIn(KEY, output)
            runner = json.loads(output)
            summary = json.loads((Path(tmp) / 'fresh-observation.json').read_text(encoding='utf-8'))
            return summary, runner

    def test_actual_original_fresh_complete(self):
        summary, runner = self.execute()
        accepted = probe.inspect_summary(summary, runner)
        self.assertTrue(accepted['all_required_quotes_available'])
        self.assertEqual(accepted['quote_counts']['available'], 14)
        self.assertEqual(accepted['http_requests'], 1)
        self.assertEqual(self.calls[0]['proxies'], {'http': PROXY})

    def test_partial_market_is_valid_fresh_response_not_ready(self):
        summary, runner = self.execute(missing=True)
        accepted = probe.inspect_summary(summary, runner)
        self.assertFalse(accepted['all_required_quotes_available'])
        self.assertEqual(accepted['unavailable_codes'], ['XA0942'])

    def test_wrong_date_is_not_accepted(self):
        summary, runner = self.execute(bad_date=True)
        with self.assertRaisesRegex(ValueError, 'FRESH_ORIGINAL_FETCH_FAILED'):
            probe.inspect_summary(summary, runner)

    def test_cache_success_does_not_count_as_fresh(self):
        summary, runner = self.execute(cached=True)
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(ValueError, 'FRESH_PROXY_CONNECTION_REQUIRED'):
            probe.inspect_summary(summary, runner)

    def test_original_two_attempt_retry_is_preserved(self):
        summary, runner = self.execute(first_failure=True)
        accepted = probe.inspect_summary(summary, runner)
        self.assertEqual(accepted['http_requests'], 2)
        self.assertEqual(accepted['proxy_connections'], 2)

    def test_unknown_status_rejected(self):
        summary, runner = self.execute()
        summary['quotes']['XA0942'] = 'estimated'
        with self.assertRaisesRegex(ValueError, 'QUOTE_STATUS_REQUIRED'):
            probe.inspect_summary(summary, runner)

    def test_wrong_expected_series_rejected(self):
        summary, runner = self.execute()
        summary['quotes']['UNKNOWN'] = summary['quotes'].pop('XA0942')
        with self.assertRaisesRegex(ValueError, 'EXPECTED_SERIES_REQUIRED'):
            probe.inspect_summary(summary, runner)

    def test_business_response_required_not_plain_http200(self):
        summary, runner = self.execute()
        del summary['original_normalizer_passed']
        with self.assertRaisesRegex(ValueError, 'ORIGINAL_NORMALIZER_REQUIRED'):
            probe.inspect_summary(summary, runner)

    def test_response_hash_required(self):
        summary, runner = self.execute()
        summary['decoded_response_sha256'] = ''
        with self.assertRaisesRegex(ValueError, 'RESPONSE_HASH_REQUIRED'):
            probe.inspect_summary(summary, runner)

    def test_main_redacts_unexpected_exception(self):
        stream = io.StringIO()
        with mock.patch.object(probe, 'probe', side_effect=RuntimeError('secret=' + KEY)), contextlib.redirect_stdout(stream):
            code = probe.main(['a' * 64, DAY])
        self.assertEqual(code, 1)
        self.assertNotIn(KEY, stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue())['status'], 'INCOMPLETE')


if __name__ == '__main__':
    unittest.main()
