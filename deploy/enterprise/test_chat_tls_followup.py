"""Offline tests, including a local authenticated CONNECT/TLS fixture."""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from unittest.mock import patch

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent
NS = {'__name__': 'test_followup'}
exec(compile((ROOT / 'inspect-chat-tls-child.py').read_text() + '\n' + (ROOT / 'inspect-chat-tls-followup-child.py').read_text(), '<followup>', 'exec'), NS)


class FollowupTests(unittest.TestCase):
    def event(self, value):
        return {'seq': 1, 'created_at': '2026-10-25T09:39:00Z', 'event_type': 'llm.tool.result', 'content': json.dumps(value)}

    def test_wrapped_missing_name_and_unicode(self):
        data = NS['event_summary'](self.event({'type': 'tool', 'data': {'content': '证书错误 SSLError 用户问题SECRET', 'status': 'error'}}))
        self.assertEqual(data['wrapper_shape'], ['json', 'data'])
        self.assertIn('证书', data['keyword_mentions'])
        self.assertIn('SSLError', data['error_codes'])
        self.assertNotIn('SECRET', json.dumps(data, ensure_ascii=False))

    def test_double_json_shape(self):
        data = NS['event_summary'](self.event(json.dumps({'type': 'tool', 'name': 'web_search', 'content': 'ProxyError'})))
        self.assertEqual(data['wrapper_shape'], ['json', 'json'])
        self.assertEqual(data['tool_name'], 'web_search')

    def test_plain_text_tool_error_does_not_disappear(self):
        row = self.event({})
        row['content'] = 'Error: certificate verify failed private user text'
        data = NS['event_summary'](row)
        self.assertEqual(data['top_level_type'], 'str')
        self.assertIn('certificate', data['keyword_mentions'])
        self.assertNotIn('private user text', json.dumps(data))

    def test_browser_helpers_and_children_excluded(self):
        for args in (['/opt/chrome_crashpad_handler', '--no-periodic-tasks'],
                     ['/opt/chrome', '--type=zygote'], ['/opt/chrome --type=renderer --private-url=secret']):
            self.assertIsNone(NS['browser_main_args'](args))
        self.assertIsNotNone(NS['browser_main_args'](['/opt/chrome', '--headless', '--ignore-certificate-errors-spki-list=abc']))

    def test_proxy_endpoint_and_shape_bound(self):
        for value in ('http://u:secret@different:8080', 'http://u:secret@10.68.211.1:8081',
                      'http://u:secret@10.68.211.1:8080/private', 'http://10.68.211.1:8080'):
            with patch.dict(os.environ, {'HTTPS_PROXY': value}, clear=True):
                with self.assertRaises(ValueError):
                    NS['qualified_proxy']('api.tavily.com')
        value = 'http://u:secret@10.68.211.1:8080'
        with patch.dict(os.environ, {'HTTPS_PROXY': value}, clear=True):
            self.assertEqual(NS['qualified_proxy']('api.tavily.com'), value)

    def test_ca_change_prevents_requests(self):
        with patch.dict(NS, {'bounded_public_file': lambda p: (None, b'changed')}), patch.object(requests.Session, 'send') as send:
            with self.assertRaisesRegex(ValueError, 'CA_CHANGED'):
                NS['proxy_probes']()
            send.assert_not_called()

    def test_proxy_exception_redacts_credential(self):
        source = ROOT.parent.parent / 'backend/packages/harness/deerflow'
        with patch.dict(NS, {'SOURCE_ROOT': source, 'EXPECTED_CA_SHA': hashlib.sha256(b'test').hexdigest(),
                             'bounded_public_file': lambda p: (None, b'test'),
                             'qualified_proxy': lambda host: 'http://u:PRIVATE_PASSWORD@10.68.211.1:8080'}), \
                patch.dict(os.environ, {'TAVILY_TLS_COMPAT': 'true'}, clear=True), \
                patch.object(requests.Session, 'send', side_effect=requests.exceptions.ProxyError('http://u:PRIVATE_PASSWORD@10.68.211.1:8080')):
            data = NS['proxy_probes']()
        self.assertEqual(len(data['requests']), 1)
        self.assertEqual(data['requests'][0]['status'], 'FAILED')
        self.assertNotIn('PRIVATE_PASSWORD', json.dumps(data))

    def test_matrix_exactly_six_explicit_head_requests(self):
        sent = []
        class Response:
            status_code = 405
            raw = object()
            def __enter__(self): return self
            def __exit__(self, *args): pass
        def send(session, request, **kwargs):
            sent.append((request.method, request.url, dict(request.headers), kwargs))
            return Response()
        with patch.dict(NS, {'SOURCE_ROOT': ROOT.parent.parent / 'backend/packages/harness/deerflow',
                'EXPECTED_CA_SHA': hashlib.sha256(b'test').hexdigest(), 'bounded_public_file': lambda p: (None, b'test'),
                'qualified_proxy': lambda host: 'http://u:PRIVATE_PASSWORD@10.68.211.1:8080'}), \
                patch.dict(os.environ, {'TAVILY_TLS_COMPAT': 'true'}, clear=True), patch.object(requests.Session, 'send', send):
            data = NS['proxy_probes'](True)
        self.assertEqual(len(sent), 6)
        self.assertEqual(len(data['requests']), 6)
        self.assertNotIn('PRIVATE_PASSWORD', json.dumps(data))
        for method, url, headers, options in sent:
            self.assertEqual(method, 'HEAD')
            self.assertEqual(options['verify'], '/etc/deerflow-ca/ca-bundle.crt')
            self.assertFalse(options['allow_redirects'])
            self.assertNotIn('Authorization', headers)
            self.assertNotIn('Proxy-Authorization', headers)
            self.assertNotIn('?', url)

    def test_real_local_connect_auth_tls_hostname_and_origin_isolation(self):
        # No external network. Proxy authenticates CONNECT, then the same socket
        # serves TLS for api.tavily.com with an ephemeral test CA and certificate.
        now = datetime.datetime.now(datetime.timezone.utc)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        root = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Offline Test Root')])
        ca = (x509.CertificateBuilder().subject_name(root).issuer_name(root).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1))
            .not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'api.tavily.com')]))
            .issuer_name(root).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('api.tavily.com')]),False).sign(key,hashes.SHA256()))
        received, failures = [], []
        with tempfile.TemporaryDirectory() as d:
            ca_path, leaf_path, key_path = [Path(d)/x for x in ('ca.pem','leaf.pem','test.key')]
            ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
            leaf_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
            key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(leaf_path),str(key_path))
            server = socket.socket()
            server.bind(('127.0.0.1',0)); server.listen(1); server.settimeout(8)
            def headers(sock):
                result=b''
                while not result.endswith(b'\r\n\r\n') and len(result)<8192:
                    b=sock.recv(1)
                    if not b: break
                    result+=b
                return result
            def serve():
                try:
                    connection,_=server.accept()
                    connection.settimeout(8)
                    received.append(headers(connection))
                    connection.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                    with ctx.wrap_socket(connection,server_side=True) as secured:
                        received.append(headers(secured))
                        secured.sendall(b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
                except Exception as exc:
                    failures.append(type(exc).__name__)
                finally:
                    server.close()
            port=server.getsockname()[1]
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            with patch.dict(NS, {'CA_PATH':str(ca_path),'EXPECTED_CA_SHA':hashlib.sha256(ca_path.read_bytes()).hexdigest(),
                'SOURCE_ROOT':ROOT.parent.parent/'backend/packages/harness/deerflow',
                'qualified_proxy':lambda host:'http://fixture:local-secret@127.0.0.1:%d'%port}), \
                patch.dict(os.environ,{'TAVILY_TLS_COMPAT':'true','REQUESTS_CA_BUNDLE':str(ca_path)},clear=True):
                data=NS['proxy_probes']()
            thread.join(10)
        self.assertFalse(failures)
        self.assertFalse(thread.is_alive())
        self.assertEqual(data['requests'][0]['status'],'HTTP_RESPONSE_TLS_ACCEPTED')
        self.assertEqual(data['requests'][0]['http_status'],204)
        self.assertTrue(received[0].startswith(b'CONNECT api.tavily.com:443'))
        self.assertIn(b'Proxy-Authorization: Basic '+base64.b64encode(b'fixture:local-secret'),received[0])
        self.assertTrue(received[1].startswith(b'HEAD / HTTP'))
        self.assertNotIn(b'Authorization',received[1])
        self.assertNotIn('local-secret',json.dumps(data))


if __name__ == '__main__':
    unittest.main(verbosity=2)
