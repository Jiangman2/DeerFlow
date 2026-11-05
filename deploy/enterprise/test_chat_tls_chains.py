"""Real local TLS fixtures; external networking is unnecessary."""
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

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parent
NS = {'__name__': 'chain_fixture'}
exec(compile('\n'.join((ROOT/n).read_text() for n in
    ('inspect-chat-tls-child.py', 'inspect-chat-tls-followup-child.py', 'inspect-chat-tls-chain-child.py')), '<chains>', 'exec'), NS)


def root_certificate(name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=10))
        .not_valid_after(now+datetime.timedelta(days=10))
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),True).sign(key,hashes.SHA256()))
    return key,cert


class ChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key,cls.root = root_certificate('Offline Public Test Root')
        _,cls.other_root = root_certificate('Different Offline Test Root')

    def fixture(self, trusted=True, hostname='api.tavily.com', expired=False, proxy_status=200):
        now = datetime.datetime.now(datetime.timezone.utc)
        leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,hostname)]))
            .issuer_name(self.root.subject).public_key(self.key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(days=2))
            .not_valid_after(now+datetime.timedelta(days=-1 if expired else 1))
            .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]),False).sign(self.key,hashes.SHA256()))
        received = []
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); ca=p/'ca.pem'; cert=p/'leaf.pem'; key=p/'fixture.key'
            ca.write_bytes((self.root if trusted else self.other_root).public_bytes(serialization.Encoding.PEM))
            cert.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
            key.write_bytes(self.key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
            server=socket.socket();server.bind(('127.0.0.1',0));server.listen(1);server.settimeout(6)
            port=server.getsockname()[1]
            def serve():
                conn=None
                try:
                    conn,_=server.accept();conn.settimeout(6); header=b''
                    while not header.endswith(b'\r\n\r\n'):
                        part=conn.recv(1)
                        if not part:break
                        header+=part
                    received.append(header)
                    conn.sendall(('HTTP/1.1 %d Test\r\n\r\n'%proxy_status).encode())
                    if proxy_status==200:
                        with context.wrap_socket(conn,server_side=True) as secured:
                            received.append(secured.recv(1))
                except ssl.SSLError:
                    received.append(b'TLS_CLIENT_REJECTED')
                finally:
                    if conn:conn.close()
                    server.close()
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            with patch.dict(NS, {'SOURCE_ROOT':ROOT.parent.parent/'backend/packages/harness/deerflow',
                    'qualified_proxy':lambda host:'http://fixture:private-fixture-password@127.0.0.1:%d'%port}), \
                    patch.dict(os.environ, {'TAVILY_TLS_COMPAT':'true','REQUESTS_CA_BUNDLE':str(ca)},clear=True):
                result=NS['chain_handshake']('api.tavily.com','configured_authenticated_proxy',set())
            thread.join(8)
            self.assertFalse(thread.is_alive())
        self.assertIn(b'Proxy-Authorization: Basic '+base64.b64encode(b'fixture:private-fixture-password'),received[0])
        self.assertNotIn('private-fixture-password',json.dumps(result))
        self.assertEqual(result['origin_http_requests'],0)
        self.assertTrue(result['certificate_verification'])
        self.assertTrue(result['hostname_verification'])
        return result,received

    def test_trusted_tls_chain_without_origin_http(self):
        result,received=self.fixture()
        self.assertEqual(result['status'],'TLS_VERIFIED')
        self.assertEqual(received[1],b'')
        self.assertEqual(result['peer_chain']['certificates'][0]['issuer_common_name'],'Offline Public Test Root')

    def test_missing_issuer_stays_failed_but_peer_chain_is_collected(self):
        result,_=self.fixture(trusted=False)
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['exception_chain'][0]['verify_code'],20)
        self.assertEqual(result['peer_chain']['status'],'PRESENTED_CHAIN_CAPTURED')

    def test_hostname_mismatch_is_not_bypassed(self):
        result,_=self.fixture(hostname='wrong.example')
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['exception_chain'][0]['verify_code'],62)

    def test_expired_certificate_is_not_bypassed(self):
        result,_=self.fixture(expired=True)
        self.assertEqual(result['status'],'FAILED')
        self.assertEqual(result['exception_chain'][0]['verify_code'],10)

    def test_proxy_auth_failure_prevents_tls(self):
        result,_=self.fixture(proxy_status=407)
        self.assertEqual(result['status'],'FAILED')
        self.assertFalse(result['tls_attempted'])

    def test_direct_private_dns_refused_before_socket(self):
        with patch.object(socket,'getaddrinfo',return_value=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]), \
                patch.object(socket,'socket') as connect:
            with self.assertRaisesRegex(ValueError,'NONPUBLIC'):
                NS['connect_direct_public']('www.baidu.com')
            connect.assert_not_called()

    def test_proxy_endpoint_change_refused_before_socket(self):
        with patch.dict(os.environ,{'HTTPS_PROXY':'http://u:private@other.example:8080'},clear=True), \
                patch.object(socket,'create_connection') as connect:
            with self.assertRaisesRegex(ValueError,'ENDPOINT_CHANGED'):
                NS['connect_proxy_tunnel']('api.tavily.com')
            connect.assert_not_called()

    def test_fixed_sampling_limits(self):
        raw=self.root.public_bytes(serialization.Encoding.PEM)
        for mode in ('control','failed_node'):
            with patch.dict(NS,{'bounded_public_file':lambda p:(None,raw),'EXPECTED_CA_SHA':hashlib.sha256(raw).hexdigest(),
                    'chain_handshake':lambda host,route,trusted:{'target':host,'route':route}}):
                result=NS['main'](mode)
            self.assertEqual(len(result['samples']),4)
            self.assertEqual(sum(r['route']=='direct_public_dns' for r in result['samples']),2 if mode=='control' else 0)

    def test_changed_ca_causes_no_connections(self):
        with patch.dict(NS,{'bounded_public_file':lambda p:(None,b'changed')}), patch.object(socket,'create_connection') as connect:
            result=NS['main']('control')
        self.assertEqual(result['status'],'INCOMPLETE')
        self.assertEqual(result['samples'],[])
        connect.assert_not_called()

    def test_unsafe_context_refused_before_connect(self):
        class Session:
            adapters={'https://':object()}
            def close(self):pass
        with patch.dict(NS,{'make_tavily_session':lambda:Session()}), patch.object(socket,'create_connection') as connect:
            result=NS['chain_handshake']('api.tavily.com','configured_authenticated_proxy',set())
        self.assertEqual(result['status'],'FAILED')
        self.assertFalse(result['connection_attempted'])
        connect.assert_not_called()


if __name__=='__main__':
    unittest.main(verbosity=2)
