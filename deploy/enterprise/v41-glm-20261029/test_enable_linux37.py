"""Stdlib-only Linux host qualification; no kubeconfig, network, or host writes."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent


def load(path):
    spec = importlib.util.spec_from_file_location('enable_qualification', str(path))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


class LinuxHost(unittest.TestCase):
    def setUp(self):
        self.host = load(ROOT / 'activation.packaged.py')
        self.lib = types.ModuleType('reader_test')
        exec(compile(self.host.LIBRARY, '<reader>', 'exec'), self.lib.__dict__)

    def test_all_sources_compile_on_python37(self):
        compile(self.host.CHILD, '<child>', 'exec')
        compile((ROOT / 'enable_host.py').read_bytes(), '<host>', 'exec')

    def test_wrong_host_no_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(self.host, 'CONTROL', tmp + '/never-created'), patch.object(self.host.socket, 'gethostname', return_value='local-test'):
                with patch.object(sys, 'argv', ['tool', '--enable-v41-optional-once']), contextlib.redirect_stdout(io.StringIO()) as capture:
                    self.assertEqual(self.host.main(), 1)
            self.assertEqual(list(Path(tmp).iterdir()), [])
            self.assertEqual(json.loads(capture.getvalue())['error_code'], 'WRONG_HOST_OR_UID')

    def test_real_child_pipe_output_is_bounded(self):
        reader = self.lib.Reader()
        with self.assertRaises(self.lib.Refused):
            reader.read([sys.executable, '-c', 'print("x" * 200000)'], cap=1024, timeout=5)
        self.assertEqual(reader.observations[-1]['error_code'], 'STDOUT_LIMIT_EXCEEDED')

    def test_real_child_timeout(self):
        reader = self.lib.Reader()
        with self.assertRaises(self.lib.Refused):
            reader.read([sys.executable, '-c', 'import time; time.sleep(20)'], timeout=0.1)
        self.assertEqual(reader.observations[-1]['error_code'], 'COMMAND_TIMEOUT')

    def test_private_journal_fsync_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(self.host, 'CONTROL', tmp):
            self.host.private_write('test.private.json', b'{"synthetic":true}')
            self.host.save({'once_claimed': True, 'status': 'TEST_ONLY'})
            self.assertEqual((Path(tmp) / 'test.private.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((Path(tmp) / 'REPORT.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((Path(tmp) / 'REPORT.json').read_bytes())['status'], 'TEST_ONLY')
            with self.assertRaises(FileExistsError): self.host.private_write('test.private.json', b'no overwrite')

    def test_receipt_symlink_refused_before_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target'; target.write_bytes(b'synthetic')
            link = Path(tmp) / 'link'; link.symlink_to(target)
            with patch.object(self.host, 'RECEIPT', str(link)), self.assertRaises(OSError):
                self.host.read_receipt(self.lib)

    def test_receipt_digest_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target'; target.write_bytes(b' ' * 7445); target.chmod(0o600)
            with patch.object(self.host, 'RECEIPT', str(target)), self.assertRaisesRegex(self.host.Refused, 'PROVIDER_RECEIPT_CHANGED'):
                self.host.read_receipt(self.lib)


if __name__ == '__main__': unittest.main()
