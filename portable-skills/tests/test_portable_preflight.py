import ast
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'deployment/inspect-energy-daily-portable-runtime-20261111-r2.py'
spec = importlib.util.spec_from_file_location('portable_preflight', SCRIPT)
pre = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pre)


class PreflightTests(unittest.TestCase):
    def test_host_python37_syntax(self):
        ast.parse(SCRIPT.read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_private_environment_values_not_accepted_as_key_lists(self):
        for value in ('KEY=private-value', 'https://user:private@example.com', 'key name', None):
            with self.subTest(value=value):
                with self.assertRaises(pre.Refused):
                    pre.key_list(value)

    def test_csv_keys_preserve_existing_order(self):
        self.assertEqual(pre.key_list(' FIRST_KEY,SECOND_KEY ,THIRD_KEY'), ['FIRST_KEY', 'SECOND_KEY', 'THIRD_KEY'])
        self.assertEqual(pre.key_list('http_proxy,no_proxy,HTTPS_PROXY'), ['http_proxy', 'no_proxy', 'HTTPS_PROXY'])

    def test_proxy_summary_redacts_authentication(self):
        result = pre.proxy_summary('http://user:fixture-private@proxy.example:8080')
        self.assertEqual(result['host'], 'proxy.example')
        self.assertTrue(result['authentication_present'])
        self.assertNotIn('fixture-private', json.dumps(result))

    def test_proxy_url_not_silently_rewritten(self):
        for value in ('file:///tmp/private', 'http://proxy.example/?private=1', 'http://proxy.example/#private'):
            with self.assertRaises(pre.Refused):
                pre.proxy_summary(value)

    def test_mutation_and_secret_commands_denied(self):
        for args in (['delete', 'pod', 'x'], ['get', 'secret', 'x', '-o', 'json'], ['exec', 'random', '--', 'env']):
            with self.subTest(args=args):
                with patch.object(pre.subprocess, 'Popen', side_effect=AssertionError('must reject first')):
                    with self.assertRaises(pre.Refused):
                        pre.run_query(args)

    def test_wrong_host_runs_no_queries(self):
        output = io.StringIO()
        with patch.object(pre.os, 'name', 'nt'), patch.object(pre, 'run_query', side_effect=AssertionError('must not query')):
            with contextlib.redirect_stdout(output):
                self.assertEqual(pre.main(['--inspect-readonly']), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result['error_code'], 'DATAX01_ROOT_REQUIRED')
        self.assertFalse(result['credentials_exported'])

    def test_child_only_compiles_environment_builder_not_application(self):
        self.assertIn("n.name=='_build_sandbox_env'", pre.CHILD)
        self.assertIn("body=functions", pre.CHILD)
        self.assertNotIn('load_incluster_config(', pre.CHILD)
        self.assertNotIn('import app', pre.CHILD)
        self.assertIn('signal.alarm(20)', pre.CHILD)


if __name__ == '__main__':
    unittest.main()
