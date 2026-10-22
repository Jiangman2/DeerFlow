import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

import transfer_base as t


class TrustTests(unittest.TestCase):
    def test_correct_fingerprint_and_alias(self):
        key = b'fixture-public-key-not-a-real-host-key'
        encoded = base64.b64encode(key).decode()
        fp = 'SHA256:'+base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip('=')
        with patch.object(t, 'FINGERPRINT', fp):
            result = t.known_host(('192.168.111.18 ecdsa-sha2-nistp256 '+encoded+'\n').encode())
        self.assertEqual(result, ('datax-03 ecdsa-sha2-nistp256 '+encoded+'\n').encode())

    def test_wrong_key_is_rejected_not_auto_trusted(self):
        with self.assertRaises(t.verify.CheckFailure):
            t.known_host(b'192.168.111.18 ecdsa-sha2-nistp256 '+base64.b64encode(b'x'*32)+b'\n')

    def test_empty_keyscan_rejected(self):
        with self.assertRaises(t.verify.CheckFailure): t.known_host(b'')

    def test_command_has_only_exact_remote_read_and_strict_trust(self):
        cmd = t.ssh_command()
        self.assertIn('StrictHostKeyChecking=yes', cmd)
        self.assertIn('GlobalKnownHostsFile=/dev/null', cmd)
        self.assertIn('HostKeyAlias=datax-03', cmd)
        self.assertIn('NumberOfPasswordPrompts=1', cmd)
        self.assertIn('-T', cmd)
        self.assertNotIn('-tt', cmd)
        self.assertEqual(cmd[-2], 'cnooc@192.168.111.18')
        self.assertTrue(cmd[-1].startswith("sudo -S -p '' -- python3 -I -B -c "))
        for word in ('chmod', 'chown', 'kubectl', 'systemctl', 'API_KEY', '/proc/', 'os.kill'):
            self.assertNotIn(word, t.remote_source())


class ProtocolTests(unittest.TestCase):
    def example(self):
        archive = b'bounded synthetic archive'*1000
        digest = hashlib.sha256(archive).hexdigest()
        report = json.dumps({'weknora_base_export': 'PASS', 'bytes': len(archive),
                             'archive_sha256': digest, 'config_digest': t.base.CONFIG,
                             'manifest_digest': t.MANIFEST}).encode()
        return archive, report, digest

    def receiver(self):
        parts, reports = [], []
        return t.Receiver(parts.append, reports.append), parts, reports

    def context(self, archive, report, digest):
        return patch.multiple(t, ARCHIVE_BYTES=len(archive), ARCHIVE_SHA=digest,
                              REPORT_SHA=hashlib.sha256(report).hexdigest())

    def test_fragmented_and_combined_streams(self):
        archive, report, digest = self.example()
        wire = t.MAGIC+struct.pack('>I', len(report))+report+archive
        for size in (1, 7, 1024, 65536):
            with self.subTest(chunk=size), self.context(archive, report, digest):
                receiver, parts, reports = self.receiver()
                for start in range(0, len(wire), size): receiver.feed(wire[start:start+size])
                receiver.finish()
                self.assertEqual(b''.join(parts), archive)
                self.assertEqual(reports, [report])

    def test_wrong_report_never_writes_archive(self):
        receiver, parts, reports = self.receiver()
        with self.assertRaisesRegex(t.verify.CheckFailure, 'source_receipt_hash_mismatch'):
            receiver.feed(t.MAGIC+struct.pack('>I', 2)+b'{}'+b'archive')
        self.assertEqual(parts, []); self.assertEqual(reports, [])

    def test_protocol_banner_or_oversized_header_rejected(self):
        for wire in (b'LOGIN BANNER'+t.MAGIC+bytes(4), t.MAGIC+struct.pack('>I', 1048577)):
            with self.subTest(wire=wire):
                receiver, parts, reports = self.receiver()
                with self.assertRaises(t.verify.CheckFailure): receiver.feed(wire)
                self.assertFalse(parts); self.assertFalse(reports)

    def test_truncated_extra_and_corrupt_content_rejected(self):
        archive, report, digest = self.example()
        header = t.MAGIC+struct.pack('>I', len(report))+report
        for data in (archive[:-1], archive+b'extra', b'x'+archive[1:]):
            with self.subTest(size=len(data)), self.context(archive, report, digest):
                receiver, _, _ = self.receiver()
                with self.assertRaises(t.verify.CheckFailure):
                    receiver.feed(header+data); receiver.finish()

    def test_source_receipt_field_mismatch_even_if_hash_pinned(self):
        archive, report, digest = self.example()
        obj = json.loads(report); obj['weknora_base_export'] = 'FAIL'; report = json.dumps(obj).encode()
        with self.context(archive, report, digest):
            receiver, parts, reports = self.receiver()
            with self.assertRaisesRegex(t.verify.CheckFailure, 'source_receipt_identity_mismatch'):
                receiver.feed(t.MAGIC+struct.pack('>I', len(report))+report)
            self.assertFalse(parts); self.assertFalse(reports)

    def test_eof_before_header_is_failure(self):
        receiver, _, _ = self.receiver()
        with self.assertRaises(t.verify.CheckFailure): receiver.finish()


class SafetyTests(unittest.TestCase):
    def test_py37_and_remote_syntax(self):
        source = Path(t.__file__).read_text(encoding='utf-8')
        ast.parse(source, feature_version=(3, 7))
        ast.parse(t.remote_source(), feature_version=(3, 7))
        self.assertNotIn("open('/dev/tty', 'r+')", source)
        self.assertIn("open('/dev/tty', 'w', encoding='utf-8')", source)

    def test_wrong_host_performs_no_network_or_password_prompt(self):
        with patch.object(t.socket, 'gethostname', return_value='datax-03'), \
             patch.object(t, 'receive') as receive, patch.object(t.getpass, 'getpass') as password, \
             patch.object(t, 'emit'):
            self.assertEqual(t.main(['--pull-once']), 1)
            receive.assert_not_called(); password.assert_not_called()

    def test_exact_evidence_lock_matches_operator_receipt(self):
        lock = json.loads((Path(t.__file__).parent/'BUILD-INPUT-LOCK.json').read_text())['runtime_base']
        self.assertEqual(lock['archive_sha256'], t.ARCHIVE_SHA)
        self.assertEqual(lock['archive_bytes'], t.ARCHIVE_BYTES)
        self.assertEqual(lock['export_report_sha256'], t.REPORT_SHA)
        self.assertEqual(lock['manifest_digest'], t.MANIFEST)


if __name__ == '__main__': unittest.main()
