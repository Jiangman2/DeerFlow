import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cleanup', Path(__file__).with_name('inspect-datax01-cleanup.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def report(self, value, mismatch=False):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'REPORT.json'
            raw = json.dumps(value).encode()
            path.write_bytes(raw)
            with patch.object(module, 'REPORT', path), patch.object(module, 'REPORT_BYTES', len(raw)), patch.object(module, 'REPORT_SHA', '0' * 64 if mismatch else hashlib.sha256(raw).hexdigest()):
                return module.report_observation()

    def test_mismatch_returns_actual_metadata_without_raw_content(self):
        row = self.report({'private': 'not-for-output'}, mismatch=True)
        self.assertEqual(row['error_code'], 'REPORT_MISMATCH')
        self.assertEqual(row['phase'], 'report_read')
        self.assertIn('observed_sha256', row)
        self.assertNotIn('not-for-output', json.dumps(row))

    def test_state_failure_is_identified(self):
        row = self.report({'operation': 'wrong', 'image_import_requested': False})
        self.assertEqual(row['error_code'], 'REPORT_STATE_CHANGED')
        self.assertFalse(row['operation_matches'])

    def test_valid_report_projects_only_storage(self):
        row = self.report({'operation': module.OPERATION, 'image_import_requested': False,
            'storage_checks': [{'devices': [{'available_bytes': 100, 'required_bytes': 200, 'private': 'omitted'}]}]})
        self.assertTrue(row['verified'])
        self.assertEqual(row['storage_checks'][0]['devices'][0]['shortfall_bytes'], 100)
        self.assertNotIn('omitted', json.dumps(row))

    def test_directory_timeout_is_nonfatal(self):
        with patch.object(module.shutil, 'which', return_value='/usr/bin/du'), patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('du', 30)):
            self.assertEqual(module.sizes(Path('/var/lib'))['error_code'], 'DU_TIMEOUT')

    def test_du_arguments_stay_readonly_and_on_one_filesystem(self):
        with patch.object(module.shutil, 'which', return_value='/usr/bin/du'), patch.object(subprocess, 'run', return_value=SimpleNamespace(stdout=b'10\t/var/lib\n', stderr=b'', returncode=0)) as run:
            self.assertEqual(module.sizes(Path('/var/lib'))['rows'][0]['bytes'], 10)
            self.assertEqual(run.call_args[0][0], ['/usr/bin/du', '-x', '-B1', '--max-depth=1', '--', '/var/lib'])

    def test_file_scan_does_not_follow_symlink_or_modify(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory)
            (root / 'outside').symlink_to(other, target_is_directory=True)
            file = root / 'old.oci.tar'
            with file.open('wb') as f:
                f.truncate(40 * 1024 * 1024)
            before = file.stat()
            row = module.large_files(root)
            self.assertEqual([r['path'] for r in row['files']], [str(file)])
            self.assertEqual(file.stat().st_mtime_ns, before.st_mtime_ns)

    def test_wrong_host_stops_before_report_read(self):
        with patch.object(module.os, 'uname', return_value=SimpleNamespace(nodename='different')), patch.object(module, 'report_observation', side_effect=AssertionError('read')), patch('sys.argv', ['inspect', '--inspect-readonly']), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(), 1)


if __name__ == '__main__':
    unittest.main()
