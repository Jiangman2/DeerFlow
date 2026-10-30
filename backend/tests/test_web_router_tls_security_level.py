import ast
import os
from pathlib import Path
import ssl
import unittest
from unittest.mock import patch


class TransportPolicyTests(unittest.TestCase):
    def setUp(self):
        provider = Path(__file__).with_name('providers.py')
        if not provider.exists():
            provider = Path(__file__).resolve().parents[1] / 'packages/harness/deerflow/community/web_router/providers.py'
        tree = ast.parse(provider.read_text(encoding='utf-8'))
        tree.body = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
                     and n.name in {'ProviderFailure', '_transport_options'}]
        self.ns = dict(ssl=ssl, os=os)
        exec(compile(tree, 'providers.py', 'exec'), self.ns)
        self.options = self.ns['_transport_options']
        self.failure = self.ns['ProviderFailure']
        self.env = {k: v for k, v in os.environ.items() if k not in {
            'DEER_FLOW_WEB_ROUTER_TLS_SECURITY_LEVEL', 'DEER_FLOW_WEB_ROUTER_TRUST_ENV',
            'REQUESTS_CA_BUNDLE', 'SSL_CERT_FILE', 'CURL_CA_BUNDLE'}}

    def test_default_unchanged(self):
        with patch.dict(os.environ, self.env, clear=True):
            self.assertEqual(self.options(), dict(verify=True, trust_env=False, follow_redirects=False))

    def test_scoped_context_keeps_verification(self):
        context = ssl.create_default_context()
        unrelated = ssl.create_default_context()
        before = unrelated.security_level
        with patch.dict(os.environ, {**self.env, 'DEER_FLOW_WEB_ROUTER_TLS_SECURITY_LEVEL': '1',
            'DEER_FLOW_WEB_ROUTER_TRUST_ENV': 'true', 'REQUESTS_CA_BUNDLE': '/approved.pem'}, clear=True):
            with patch.object(ssl, 'create_default_context', return_value=context) as create:
                options = self.options()
        create.assert_called_once_with(cafile='/approved.pem')
        self.assertIs(options['verify'], context)
        self.assertEqual(context.security_level, 1)
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(options['trust_env'])
        self.assertFalse(options['follow_redirects'])
        self.assertEqual(unrelated.security_level, before)

    def test_invalid_levels_fail_closed(self):
        for level in ['0', '-1', '3', '', 'false']:
            with self.subTest(level=level), patch.dict(os.environ,
                {**self.env, 'DEER_FLOW_WEB_ROUTER_TLS_SECURITY_LEVEL': level}, clear=True):
                with self.assertRaises(self.failure): self.options()

    def test_level1_requires_proxy_and_explicit_ca(self):
        for extras in [{}, {'DEER_FLOW_WEB_ROUTER_TRUST_ENV': 'true'}, {'REQUESTS_CA_BUNDLE': '/approved.pem'}]:
            with self.subTest(extras=extras), patch.dict(os.environ,
                {**self.env, 'DEER_FLOW_WEB_ROUTER_TLS_SECURITY_LEVEL': '1', **extras}, clear=True):
                with self.assertRaises(self.failure): self.options()

    def test_bad_ca_never_falls_back(self):
        with patch.dict(os.environ, {**self.env, 'DEER_FLOW_WEB_ROUTER_TLS_SECURITY_LEVEL': '1',
            'DEER_FLOW_WEB_ROUTER_TRUST_ENV': 'true', 'REQUESTS_CA_BUNDLE': '/bad.pem'}, clear=True):
            with patch.object(ssl, 'create_default_context', side_effect=OSError('sensitive-canary')):
                with self.assertRaises(self.failure) as failure: self.options()
        self.assertEqual(str(failure.exception), 'CONFIG_ERROR')


if __name__ == '__main__': unittest.main()
