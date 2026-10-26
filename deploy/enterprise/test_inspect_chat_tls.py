"""Offline safety and evidence tests. No production/network access."""
import asyncio
import base64
import datetime
import importlib.util
import json
import os
from pathlib import Path
import ssl
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, str(ROOT / filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


C = load('tls_child', 'inspect-chat-tls-child.py')
H = load('tls_host', 'inspect-chat-tls-host.py')
try:
    import requests
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    HAS_CRYPTO_HTTP = True
except ImportError:
    HAS_CRYPTO_HTTP = False


class EvidenceTests(unittest.TestCase):
    def test_proxy_credentials_redacted(self):
        data = C.proxy_metadata('https://user:secret@proxy.example:8443/private?token=hidden')
        self.assertTrue(data['authentication_present'])
        self.assertTrue(data['path_or_query_present'])
        for value in ('secret', 'user', 'private', 'hidden'):
            self.assertNotIn(value, json.dumps(data))

    def test_invalid_proxy_redacted(self):
        self.assertEqual(C.proxy_metadata('http://u:secret@host:bad')['status'], 'INVALID_REDACTED')

    def test_paths(self):
        for p in ('/tmp/private.key', '/tmp/a.pfx', '/var/run/secrets/token', '../foo', '/tmp/../foo', '/tmp/a?token=x'):
            self.assertIsNone(C.safe_path(p))
        self.assertEqual(C.safe_path('/etc/deerflow-ca/ca-bundle.crt'), '/etc/deerflow-ca/ca-bundle.crt')

    def test_private_key_not_reported(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'ca.crt'
            p.write_bytes(b'-----BEGIN PRIVATE KEY-----\nFAKE_PRIVATE_BYTES\n')
            result = C.inspect_bundle(str(p))
            self.assertEqual(result['error_code'], 'PRIVATE_KEY_MARKER_IN_CA_FILE')
            self.assertNotIn('sha256', result)
            self.assertNotIn('FAKE_PRIVATE_BYTES', json.dumps(result))

    def test_error_chain_redacts_urls_and_secret(self):
        inner = RuntimeError('CERTIFICATE_VERIFY_FAILED https://user:secret@host/?private=prompt')
        outer = ConnectionError('user data')
        outer.__cause__ = inner
        result = C.error_info(outer)
        self.assertIn('CERTIFICATE_UNTRUSTED', result['categories'])
        self.assertEqual(len(result['exception_chain']), 2)
        for value in ('secret', 'prompt', 'user data', 'https://'):
            self.assertNotIn(value, json.dumps(result))

    def test_error_cycle_bounded(self):
        exc = RuntimeError('WRONG_VERSION_NUMBER')
        exc.__cause__ = exc
        self.assertEqual(len(C.error_info(exc)['exception_chain']), 1)

    def event(self, event_type='llm.tool.result', name='web_search', content='SSLError: certificate verify failed SECRET_QUERY'):
        return {'seq': 7, 'run_id': 'test-run-id', 'created_at': '2026-10-25T09:39:00Z',
                'event_type': event_type, 'content': json.dumps({'type': 'tool', 'name': name, 'status': 'error', 'content': content})}

    def test_incident_tool_evidence_redacted(self):
        data = C.incident_event(self.event())
        self.assertEqual(data['tool_name'], 'web_search')
        self.assertIn('CERTIFICATE_UNTRUSTED', data['classification'])
        self.assertNotIn('SECRET_QUERY', json.dumps(data))
        self.assertNotIn('test-run-id', json.dumps(data))

    def test_model_narration_is_not_error_evidence(self):
        self.assertIsNone(C.incident_event(self.event(event_type='llm.ai.response')))
        self.assertIsNone(C.incident_event(self.event(content='The model said SSL problem')))
        self.assertIsNone(C.incident_event(self.event(name='read_file')))

    def test_scoped_database_readonly_and_bounded(self):
        calls = []
        record = self.event()
        record['original_bytes'] = len(record['content'])
        class Transaction:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        class Connection:
            def transaction(self, **kwargs):
                self_kwargs = {'readonly': True, 'isolation': 'repeatable_read'}
                if kwargs != self_kwargs:
                    raise AssertionError(kwargs)
                calls.append(('transaction', kwargs))
                return Transaction()
            async def fetchval(self, sql):
                if sql != 'SHOW transaction_read_only':
                    raise AssertionError(sql)
                return 'on'
            async def fetch(self, sql, *args):
                calls.append((sql, args))
                if 'FROM runs ' in sql:
                    return []
                return [{'event_type': 'llm.tool.result', 'count': 1}]
            async def cursor(self, sql, *args, **kwargs):
                calls.append((sql, args))
                if kwargs != {'prefetch': 20}:
                    raise AssertionError(kwargs)
                yield record
            async def close(self):
                calls.append(('close',))
        async def connect(*args, **kwargs):
            return Connection()
        config = types.ModuleType('deerflow.config')
        config.get_app_config = lambda: types.SimpleNamespace(database=types.SimpleNamespace(backend='postgres', postgres_url='postgresql://fake-secret'))
        pg = types.ModuleType('asyncpg')
        pg.connect = connect
        with patch.dict(sys.modules, {'asyncpg': pg, 'deerflow': types.ModuleType('deerflow'), 'deerflow.config': config}):
            data = asyncio.run(C.incident_async())
        self.assertEqual(len(data['matches']), 1)
        self.assertTrue(data['database_readonly'])
        self.assertEqual(calls[-1], ('close',))
        for sql, args in [x for x in calls if len(x) == 2 and x[0].startswith('SELECT')]:
            self.assertIn('thread_id=$1', sql)
            self.assertEqual(args[0], C.INCIDENT_THREAD)
        self.assertNotIn('fake-secret', json.dumps(data))

    def test_readonly_command_guard(self):
        with patch.object(H.subprocess, 'run') as run:
            for args in (['get', 'secrets'], ['patch', 'deployments'], ['delete', 'pods'], ['get', 'configmaps']):
                with self.assertRaises(H.Refused):
                    H.query(args)
            run.assert_not_called()

    def test_runtime_exec_uses_isolation_and_no_shell_interpolation(self):
        p = {'metadata': {'name': 'gateway-test', 'uid': 'u'}, 'spec': {'nodeName': 'n'}, 'status': {}}
        with patch.object(H, 'query', return_value={'status': 'OBSERVED'}) as query:
            H.runtime_exec('def main(mode): return {}\nimport json', p, 'gateway', 'inventory')
            argv, body = query.call_args[0]
            self.assertEqual(argv[:6], ['exec', '-i', 'gateway-test', '-c', 'gateway', '--'])
            self.assertIn('-I -B -', argv[-1])
            self.assertIn(b"main('inventory')", body)

    def test_runtime_group_keeps_cert_changes(self):
        def row(sha, stamp, processes):
            return {'identity': {'node': 'n'}, 'workload': 'gateway', 'observation': {
                'sampled_at_utc': stamp, 'bundles': {'/ca': {'sha256': sha, 'file_mtime_utc': stamp, 'resolved_path': '/ca/..' + stamp + '/ca.crt'}},
                'live_browser_flags': {'browser_processes': processes}, 'status': 'OBSERVED'}}
        groups, refs = H.group_runtime([row('same', 't1', 1), row('same', 't2', 2), row('changed', 't2', 1)])
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['count'], 2)
        self.assertEqual(refs[1]['live_browser_flags']['browser_processes'], 2)

    def test_template_excludes_unrelated_secrets(self):
        d = {'metadata': {'name': 'gateway', 'uid': 'u'}, 'spec': {'template': {'spec': {
            'containers': [{'name': 'gateway', 'image': 'image', 'env': [
                {'name': 'API_KEY', 'value': 'PRIVATEKEY'}, {'name': 'HTTPS_PROXY', 'value': 'http://u:pwd@proxy:80'},
                {'name': 'SSL_CERT_FILE', 'valueFrom': {'secretKeyRef': {'name': 'ca', 'key': 'bundle'}}}]}]}}}}
        data = H.template_inventory(d, C.__dict__)
        self.assertNotIn('PRIVATEKEY', json.dumps(data))
        self.assertNotIn('pwd', json.dumps(data))
        self.assertEqual(len(data['containers'][0]['tls_environment']), 2)

    def test_sealed_distribution_matches_source(self):
        for path in ROOT.glob('inspect-portal-chat-tls-*-r1.py'):
            ns = {'__name__': 'qualification'}
            exec(compile(path.read_text(), str(path), 'exec'), ns)
            self.assertEqual(zlib.decompress(base64.b64decode(ns['CHILD'])), (ROOT / 'inspect-chat-tls-child.py').read_bytes())

    def test_whole_inspection_coverage_and_one_probe_per_node(self):
        deployment = {'metadata': {'name': 'gateway', 'uid': 'dep', 'generation': 387},
            'spec': {'replicas': 3, 'template': {'spec': {'containers': [{'name': 'gateway', 'image': 'fixture'}]}}},
            'status': {'readyReplicas': 3, 'updatedReplicas': 3, 'observedGeneration': 387}}
        rs = {'metadata': {'uid': 'rs', 'ownerReferences': [{'uid': 'dep', 'controller': True}]}}
        pods = [{'metadata': {'name': 'gateway-' + str(i), 'uid': 'p' + str(i), 'ownerReferences': [{'uid': 'rs', 'controller': True}]},
            'spec': {'nodeName': 'n' + str(i % 2), 'containers': [{'name': 'gateway', 'image': 'fixture'}]},
            'status': {'phase': 'Running', 'containerStatuses': [{'name': 'gateway', 'ready': True, 'containerID': str(i), 'restartCount': 0}]}} for i in range(3)]
        resources = {'deployments': [deployment], 'replicasets': [rs], 'pods': pods, 'nodes': [], 'ingresses': []}
        calls = []
        def query(argv, *args, **kwargs):
            self.assertEqual(argv[0], 'get')
            return {'items': resources[argv[1]]}
        def runtime(source, pod, container, mode):
            calls.append(mode)
            statuses = {'inventory': 'OBSERVED', 'probe': 'PROBES_OBSERVED', 'incident': 'SCOPED_INCIDENT_EVIDENCE_OBSERVED'}
            return {'identity': H.pod_identity(pod, container), 'observation': {'status': statuses[mode], 'probes': []}}
        source = (ROOT / 'inspect-chat-tls-child.py').read_text() + '\ndef main(mode): return {"status":"TEST_HOST_OBSERVED"}\n'
        report = {}
        with patch.object(H, 'query', side_effect=query), patch.object(H, 'runtime_exec', side_effect=runtime):
            H.inspect(source, report, lambda: None)
        self.assertEqual(calls.count('inventory'), 3)
        self.assertEqual(calls.count('probe'), 2)
        self.assertEqual(calls.count('incident'), 1)
        self.assertEqual(report['maximum_head_requests'], 6)
        self.assertTrue(report['gateway_inventory_complete'])
        self.assertTrue(report['snapshot_stable'])
        self.assertEqual(report['exitcode'], 0)


@unittest.skipUnless(HAS_CRYPTO_HTTP, 'cryptography/requests are needed for runtime qualification')
class RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def cert(self, ca=True, expired=False):
        now = datetime.datetime.now(datetime.timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Offline Diagnostic Test CA' if ca else 'test.invalid')])
        return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(self.key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=2))
                .not_valid_after(now + datetime.timedelta(days=-1 if expired else 1))
                .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True).sign(self.key, hashes.SHA256()))

    def test_ca_leaf_expiry_distinguished(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        ca = C.cert_metadata(self.cert().public_bytes(serialization.Encoding.DER), now)
        leaf = C.cert_metadata(self.cert(ca=False, expired=True).public_bytes(serialization.Encoding.DER), now)
        self.assertEqual(ca['classification'], 'ROOT_CANDIDATE')
        self.assertEqual(leaf['classification'], 'LEAF_OR_UNSPECIFIED')
        self.assertEqual(leaf['time_status'], 'EXPIRED')

    def test_pem_bundle_load_and_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'bundle.crt'
            p.write_bytes(self.cert().public_bytes(serialization.Encoding.PEM))
            result = C.inspect_bundle(str(p))
        self.assertEqual(result['openssl_load'], 'PASS')
        self.assertEqual(result['counts']['ROOT_CANDIDATE'], 1)
        self.assertNotIn('BEGIN CERTIFICATE', json.dumps(result))

    def test_der_browser_export_identified(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'browser.cer'
            p.write_bytes(self.cert(ca=False).public_bytes(serialization.Encoding.DER))
            result = C.inspect_bundle(str(p))
        self.assertEqual(result['encoding'], 'DER_NOT_PEM')
        self.assertEqual(result['counts']['LEAF_OR_UNSPECIFIED'], 1)

    def test_real_tavily_adapter_keeps_validation(self):
        source = ROOT.parent.parent / 'backend/packages/harness/deerflow'
        with patch.object(C, 'SOURCE_ROOT', source), patch.dict(os.environ, {'TAVILY_TLS_COMPAT': 'true'}, clear=True):
            session = C.make_tavily_session()
            ctx = session.adapters['https://']._tavily_ssl_context
            self.assertTrue(ctx.check_hostname)
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
            self.assertEqual(session.adapters['https://'].max_retries.total, 0)
            session.close()

    def test_changed_tavily_source_no_request(self):
        with patch.object(C, 'SOURCE_ROOT', ROOT.parent.parent / 'backend/packages/harness/deerflow'), patch.object(C, 'TAVILY_SOURCE_SHA', 'changed'):
            with self.assertRaisesRegex(ValueError, 'NOT_QUALIFIED'):
                C.make_tavily_session()

    def test_head_no_auth_redirects_or_retry_and_metadata_failure(self):
        sends = []
        class Socket:
            def getpeercert(self, **kwargs):
                raise ImportError('metadata dependency missing')
        class Response:
            status_code = 405
            raw = types.SimpleNamespace(_connection=types.SimpleNamespace(sock=Socket()))
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        real_session = requests.Session
        def fake_session():
            s = real_session()
            def send(req, **kwargs):
                sends.append((req, kwargs))
                return Response()
            s.send = send
            return s
        with patch.dict(os.environ, {}, clear=True), patch.object(requests, 'Session', side_effect=fake_session), patch.object(C, 'make_tavily_session', side_effect=fake_session):
            data = C.probes()
        self.assertEqual(len(sends), 3)
        for req, kwargs in sends:
            self.assertEqual(req.method, 'HEAD')
            self.assertTrue(kwargs['verify'])
            self.assertFalse(kwargs['allow_redirects'])
            self.assertNotIn('Authorization', req.headers)
            self.assertNotIn('?', req.url)
        self.assertTrue(all(r['status'] == 'HTTP_RESPONSE_OBSERVED_TLS_ACCEPTED' for r in data['probes']))
        self.assertEqual(data['probes'][0]['peer_certificate_status'], 'METADATA_UNAVAILABLE')

    def test_authenticated_proxy_never_contacted(self):
        with patch.dict(os.environ, {'HTTPS_PROXY': 'http://u:password@proxy.invalid:8080'}, clear=True), patch.object(requests, 'Session') as session:
            data = C.probes()
            session.assert_not_called()
        self.assertTrue(all(r['requests_attempted'] == 0 for r in data['probes']))
        self.assertNotIn('password', json.dumps(data))


if __name__ == '__main__':
    unittest.main(verbosity=2)
