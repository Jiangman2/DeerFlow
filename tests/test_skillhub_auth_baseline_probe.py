import ast
import hashlib
import importlib.util
import io
import json
import pathlib
import random
import unittest
import zipfile
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
PATH = ROOT / 'deploy/enterprise/inspect-portal-skillhub-auth-baseline-20261105-r1.py'
spec = importlib.util.spec_from_file_location('skillhub_baseline', PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

class FakeJarReader:
    def __init__(self, data):
        self.data = data
        self.calls = []
    def execute(self, pod, container, argv, step, limit):
        self.calls.append(argv)
        if argv[0] == 'sh':
            return ('%d 100 42\n%s  /app/app.jar\n' % (len(self.data), hashlib.sha256(self.data).hexdigest())).encode()
        assert argv[:3] == ['dd', 'if=/app/app.jar', 'bs=65536']
        start = int(argv[3].split('=')[1]) * 65536
        count = int(argv[4].split('=')[1]) * 65536
        result = self.data[start:start + count]
        assert len(result) <= limit
        return result

def jar_with_classes(include=True):
    stream = io.BytesIO()
    generator = random.Random(100)
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('unrelated-private.bin', bytes(generator.randrange(256) for _ in range(200000)))
        if include:
            for name in probe.CLASS_PATHS:
                archive.writestr(name, b'\xca\xfe\xba\xbe' + b' authorizationPolicies /api/web/skills ConditionalOnProperty ', compress_type=zipfile.ZIP_DEFLATED)
    return stream.getvalue()

class SkillHubBaselineTests(unittest.TestCase):
    def test_python37_syntax(self):
        ast.parse(PATH.read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_host_guard_never_reaches_kubernetes(self):
        with patch.object(probe.os, 'name', 'nt'), patch.object(probe.sys if hasattr(probe, 'sys') else __import__('sys'), 'argv', ['probe', '--inspect-readonly']), patch.object(probe.subprocess, 'run') as run, patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.assertEqual(probe.run(), 1)
        run.assert_not_called()
        out = json.loads(stdout.getvalue())
        self.assertEqual(out['error_code'], 'DATAX01_ROOT_REQUIRED')
        self.assertFalse(out['workload_modified'])

    def test_exact_class_reads_and_hashes_without_exporting_bytes(self):
        reader = FakeJarReader(jar_with_classes())
        out = probe.inspect_jar(reader, 'sample', 'server')
        self.assertTrue(out['stable_readback'])
        self.assertLess(out['bounded_bytes_transferred'], len(reader.data))
        for row in out['classes'].values():
            self.assertTrue(row['present'])
            self.assertTrue(row['markers']['authorizationPolicies'])
            self.assertFalse(row['markers']['publicLabelFilterChain'])
        self.assertNotIn('unrelated-private.bin', json.dumps(out))

    def test_missing_patch_class_is_evidence_not_exception(self):
        out = probe.inspect_jar(FakeJarReader(jar_with_classes(False)), 'sample', 'server')
        self.assertTrue(all(not row['present'] for row in out['classes'].values()))

    def test_corrupt_zip_rejected(self):
        with self.assertRaises((zipfile.BadZipFile, probe.Refused)):
            probe.inspect_jar(FakeJarReader(b'not a jar'), 'sample', 'server')

    def test_remote_reads_are_bounded_and_cached(self):
        reader = FakeJarReader(b'a' * 200000)
        remote = probe.RemoteJar(reader, 'sample', 'server', len(reader.data))
        remote.seek(65530)
        self.assertEqual(remote.read(20), b'a' * 20)
        self.assertEqual(len(reader.calls), 1)
        remote.seek(65530)
        self.assertEqual(remote.read(20), b'a' * 20)
        self.assertEqual(len(reader.calls), 1)
        with self.assertRaises(probe.Refused):
            remote.seek(-1)
        big = probe.RemoteJar(reader, 'sample', 'server', 20000000)
        with self.assertRaises(probe.Refused):
            big.read(4194305)

    def test_short_remote_read_rejected(self):
        reader = FakeJarReader(b'short')
        remote = probe.RemoteJar(reader, 'sample', 'server', 200000)
        with self.assertRaisesRegex(probe.Refused, 'JAR_SHORT_READ'):
            remote.read(200)

    def test_mutating_binary_during_inspection_rejected(self):
        reader = FakeJarReader(jar_with_classes())
        with patch.object(probe, 'jar_stat', side_effect=[{'bytes': len(reader.data), 'sha256': 'a'}, {'bytes': len(reader.data), 'sha256': 'b'}]):
            with self.assertRaisesRegex(probe.Refused, 'JAR_CHANGED_DURING_READ'):
                probe.inspect_jar(reader, 'sample', 'server')

    def test_real_tracked_nginx_configs_parse(self):
        for name in ('k8s/local-pvc/nginx.conf', 'docker/nginx/nginx.conf', 'docker/nginx/nginx.local.conf'):
            result = probe.summarize_config((ROOT / name).read_bytes())
            self.assertGreaterEqual(len(result['skillhub_routes']), 5)
            api = next(r for r in result['skillhub_routes'] if r['header'] == ['location', '^~', '/platform/skillhub/api/'])
            self.assertEqual(api['auth_request'], [['/_internal/skillhub/api-auth']])
            self.assertTrue(api['clears_forwarded'])
            self.assertTrue(api['clears_forwarded_port'])
            self.assertTrue(api['sets_forwarded_host'])

    def test_no_auth_is_recorded_and_sensitive_values_not_exported(self):
        result = probe.summarize_config(b'http { # comment\nserver { location /platform/skillhub/ { proxy_set_header X-Key "SENSITIVE"; proxy_pass http://upstream:80; } } }')
        self.assertEqual(result['skillhub_routes'][0]['auth_request'], [])
        self.assertNotIn('SENSITIVE', json.dumps(result))
        self.assertNotIn('http://upstream', json.dumps(result))

    def test_nested_conditional_records_auth_and_return(self):
        result = probe.summarize_config(b'http { server { location = /_internal/skillhub/api-auth { internal; if ($bearer) { return 204; } proxy_pass http://gateway; } } }')
        self.assertTrue(result['skillhub_routes'][0]['internal'])
        self.assertEqual(result['skillhub_routes'][0]['return_statuses'], ['204'])

    def test_bad_config_is_not_silently_accepted(self):
        for source in ('http {', 'http { x "unclosed; }', '}', 'a; trailing', 'http { ; }'):
            with self.assertRaises(probe.Refused):
                probe.summarize_config(source.encode())

    def test_env_values_never_expose_credentials(self):
        self.assertEqual(probe.flag([{'env': [{'name': 'OTHER_SECRET', 'value': 'SENSITIVE'}]}]), 'not_explicit')
        self.assertEqual(probe.flag([{'env': [{'name': 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED', 'value': 'TRUE'}]}]), 'true')
        self.assertEqual(probe.flag([{'env': [{'name': 'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED', 'valueFrom': {'secretKeyRef': {'name': 'secret'}}}]}]), 'indirect_or_nonboolean')

    def test_resource_name_rejects_shell_and_argument_injection(self):
        for value in ('--all', 'pod;echo', '../etc', 'a b'):
            with self.assertRaises(probe.Refused):
                probe.safe_name(value)

    def test_resource_identity_does_not_export_spec_values(self):
        result = probe.identity({'metadata': {'name': 'sample', 'uid': 'uid'}, 'spec': {'secret': 'SENSITIVE'}})
        self.assertNotIn('SENSITIVE', json.dumps(result))
        self.assertEqual(len(result['spec_sha256']), 64)

if __name__ == '__main__':
    unittest.main()