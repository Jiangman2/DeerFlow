import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('inspector', Path(__file__).with_name('inspect-oa-compat-datax01-storage.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def test_only_resource_fields_are_exported(self):
        result = module.summarize({'storage_checks': [{'phase': 'preflight', 'pass': False,
            'devices': [{'paths': ['/var/lib'], 'available_bytes': 100, 'required_bytes': 150,
                         'secret': 'must-not-export', 'original_cost_rows': ['private']}]}]})
        self.assertEqual(result[0]['devices'][0]['shortfall_bytes'], 50)
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('secret', json.dumps(result))

    def test_pass_has_zero_shortfall(self):
        row = module.summarize({'storage_checks': [{'devices': [{'available_bytes': 200, 'required_bytes': 150}]}]})
        self.assertEqual(row[0]['devices'][0]['shortfall_bytes'], 0)

    def test_report_digest_and_no_import_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            raw = json.dumps({'operation': module.OPERATION, 'image_import_requested': False}).encode()
            path.write_bytes(raw)
            with patch.object(module, 'REPORT', path), patch.object(module, 'REPORT_BYTES', len(raw)), patch.object(module, 'REPORT_SHA', hashlib.sha256(raw).hexdigest()):
                self.assertFalse(module.read_report()['image_import_requested'])
                path.write_bytes(raw + b' ')
                with self.assertRaises(AssertionError):
                    module.read_report()

    def test_symlink_filesystem_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / 'link'
            link.symlink_to(directory, target_is_directory=True)
            self.assertEqual(module.filesystem(link)['status'], 'NOT_DIRECT_DIRECTORY')


if __name__ == '__main__':
    unittest.main()
