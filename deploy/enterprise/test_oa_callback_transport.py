"""Offline failure classification, request limits and diagnostic packaging checks."""
import errno
import importlib.util
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p = load('callback_probe', 'oa-callback-transport-probe.py')


class CallbackDiagnostic(unittest.TestCase):
    def config(self):
        return types.SimpleNamespace(callback_url='https://grc-web.apps.shdev.ocpx.cnooc/private-path', callback_ca_bundle=None)

    def test_certificate_cause_and_redaction(self):
        cert = ssl.SSLCertVerificationError(1, 'SECRET_PATH certificate verify failed')
        cert.verify_code = 20
        wrapped = RuntimeError('SECRET_URL')
        wrapped.__cause__ = cert
        result = p.failure(wrapped)
        self.assertEqual(result['category'], 'TLS_CERTIFICATE_VERIFICATION')
        self.assertEqual(result['exception_chain'][1]['certificate_verify_category'], 'ISSUER_NOT_FOUND')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_hostname_mismatch_distinct(self):
        exc = ssl.SSLCertVerificationError(1, 'private host')
        exc.verify_code = 62
        self.assertEqual(p.failure(exc)['exception_chain'][0]['certificate_verify_category'], 'HOSTNAME_MISMATCH')

    def test_network_categories(self):
        for exc, expected in ((socket.gaierror(-2, 'private'), 'DNS_RESOLUTION'),
                              (TimeoutError('private'), 'TIMEOUT'),
                              (ConnectionRefusedError(errno.ECONNREFUSED, 'private'), 'TCP_REFUSED'),
                              (OSError(errno.ENETUNREACH, 'private'), 'NETWORK_UNREACHABLE')):
            with self.subTest(expected=expected):
                self.assertEqual(p.failure(exc)['category'], expected)

    def test_cyclic_exception_chain_bounded(self):
        one, two = ValueError('one'), ValueError('two')
        one.__cause__, two.__cause__ = two, one
        self.assertEqual(len(p.failure(one)['exception_chain']), 2)

    def test_dns_deduplicates(self):
        raw = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('10.0.0.1', 443))] * 2
        with patch.object(p, 'deadline', lambda seconds: __import__('contextlib').nullcontext()), patch.object(p.socket, 'getaddrinfo', return_value=raw):
            self.assertEqual(len(p.resolve('fixed', 443)), 1)

    def test_target_changes_rejected_before_requests(self):
        for url in ('http://grc-web.apps.shdev.ocpx.cnooc/x', 'https://other.cnooc/x',
                    'https://u:p@grc-web.apps.shdev.ocpx.cnooc/x',
                    'https://grc-web.apps.shdev.ocpx.cnooc/x?secret=yes'):
            config = self.config()
            config.callback_url = url
            with patch.object(p, 'head') as head, self.assertRaises(ValueError):
                p.diagnose(config)
            head.assert_not_called()

    def diagnose_mocked(self, current, alternate=None, usable=None):
        row = {'ip': '10.0.0.1', 'family': socket.AF_INET, 'sockaddr': ('10.0.0.1', 443)}
        responses = [dict(current, head_requests=1)]
        if alternate:
            responses.append(dict(alternate, head_requests=1))
        with patch.object(p, 'ca_inventory', return_value=([], usable or [])), \
                patch.object(p, 'resolve', return_value=[row]), \
                patch.object(p, 'tcp_check', return_value={'status': 'PASS'}), \
                patch.object(p, 'head', side_effect=responses) as head:
            result = p.diagnose(self.config())
        return result, head

    def test_no_extra_head_after_normal_response(self):
        result, head = self.diagnose_mocked({'status': 'HTTP_RESPONSE_OBSERVED', 'http_status': 405})
        self.assertEqual(result['head_requests'], 1)
        self.assertEqual(head.call_count, 1)

    def test_alternate_ca_only_after_certificate_failure(self):
        context = object()
        result, head = self.diagnose_mocked({'category': 'TLS_CERTIFICATE_VERIFICATION'},
                                           {'status': 'HTTP_RESPONSE_OBSERVED', 'http_status': 401},
                                           [('SSL_CERT_FILE', context, 'a')])
        self.assertEqual(head.call_count, 2)
        self.assertIs(head.call_args_list[1][0][1], context)
        self.assertTrue(result['existing_ca_comparison']['diagnostic_only_not_applied'])
        self.assertEqual(result['head_requests'], 2)

    def test_tcp_failure_does_not_change_ca(self):
        result, head = self.diagnose_mocked({'category': 'TCP_REFUSED'}, usable=[('SSL_CERT_FILE', object(), 'a')])
        self.assertEqual(head.call_count, 1)

    def test_ca_metadata_does_not_export_path_or_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'private-file'
            path.write_text('FAKE-CERT-CONTENT', encoding='ascii')
            ctx = types.SimpleNamespace(cert_store_stats=lambda: {'x509_ca': 1, 'x509': 1})
            with patch.dict(p.os.environ, {'SSL_CERT_FILE': str(path)}, clear=True), \
                    patch.object(p.ssl, 'create_default_context', return_value=ctx):
                rows, usable = p.ca_inventory(self.config())
            text = json.dumps(rows)
            self.assertNotIn(str(path), text)
            self.assertNotIn('FAKE-CERT', text)
            self.assertEqual(usable[0][0], 'SSL_CERT_FILE')

    def test_ca_utf8_comments_use_native_file_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.pem'
            path.write_text('# 企业证书说明\nSYNTHETIC-CERT\n', encoding='utf-8')
            ctx = types.SimpleNamespace(cert_store_stats=lambda: {'x509_ca': 0, 'x509': 1})
            with patch.dict(p.os.environ, {'SSL_CERT_FILE': str(path)}, clear=True), \
                    patch.object(p.ssl, 'create_default_context', return_value=ctx) as loader:
                rows, usable = p.ca_inventory(self.config())
            loader.assert_called_once_with(cafile=str(path))
            self.assertEqual(rows[1]['status'], 'USABLE')
            self.assertEqual(rows[1]['loading_method'], 'OPENSSL_CAFILE')
            self.assertEqual(usable[0][1], ctx)

    def test_changed_ca_file_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.pem'
            path.write_bytes(b'BEFORE')
            def loader(**kwargs):
                path.write_bytes(b'AFTER')
                return object()
            with patch.dict(p.os.environ, {'SSL_CERT_FILE': str(path)}, clear=True), \
                    patch.object(p.ssl, 'create_default_context', side_effect=loader):
                rows, usable = p.ca_inventory(self.config())
            self.assertEqual(rows[1]['status'], 'LOAD_FAILED')
            self.assertEqual(usable, [])

    def test_openssl_rejection_does_not_create_alternate_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bundle.pem'
            path.write_bytes(b'INVALID')
            with patch.dict(p.os.environ, {'SSL_CERT_FILE': str(path)}, clear=True), \
                    patch.object(p.ssl, 'create_default_context', side_effect=ssl.SSLError('private')):
                rows, usable = p.ca_inventory(self.config())
            self.assertEqual(rows[1]['status'], 'LOAD_FAILED')
            self.assertEqual(rows[1]['error_class'], 'SSLError')
            self.assertEqual(usable, [])

    def test_head_has_no_credentials_body_proxy_or_redirects(self):
        calls = []
        class Response:
            status_code = 405
            def __enter__(self): return self
            def __exit__(self, *args): pass
        class Client(Response):
            def __init__(self, **kwargs): calls.append(kwargs)
            def stream(self, *args, **kwargs):
                calls.append((args, kwargs))
                return Response()
        with patch.dict(sys.modules, {'httpx': types.SimpleNamespace(Client=Client)}), \
                patch.object(p, 'deadline', lambda seconds: __import__('contextlib').nullcontext()):
            result = p.head('https://fixed/private', True, 'default')
        self.assertEqual(calls, [{'verify': True, 'trust_env': False, 'follow_redirects': False, 'timeout': 8.0},
                                 (('HEAD', 'https://fixed/private'), {})])
        self.assertEqual(result['http_status'], 405)
        self.assertFalse(result['credentials_sent'])

    def test_package_replaces_loopback_and_embedded_code_compiles(self):
        package = load('callback_package', 'package-oa-callback-transport.py')
        code = package.build()
        ns = {'__name__': '_host'}
        exec(compile(code, '<host>', 'exec'), ns)
        common = {'__name__': '_common'}
        exec(compile(ns['COMMON'], '<common>', 'exec'), common)
        child = common['CHILD']
        self.assertNotIn("result['local_route'] = local_probe(config)", child)
        self.assertIn("result['callback_diagnosis'] = diagnose(config)", child)
        compile(child, '<child>', 'exec')
        self.assertEqual(common['EXPECTED']['gateway']['generation'], 384)


if __name__ == '__main__':
    unittest.main()
