"""Current source and exact predecessor both remain hash-bound."""
import json
import subprocess
import unittest
from pathlib import Path

import procurement_stream_source_20261123 as source

ROOT = Path(__file__).resolve().parents[2]


class StreamSourceTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_bytes()

    def test_exact_parent_and_new_runtime_inventory(self):
        read, result = source.historical_view(self.read)
        self.assertEqual(len(result['added_gateway_runtime']), 2)
        for path, row in json.loads(self.read(source.MANIFEST))['changes'].items():
            if row['base_sha256'] is None:
                with self.assertRaises(KeyError):
                    read(path)
            else:
                expected = subprocess.check_output(['git', 'show', source.BASE_COMMIT + ':' + path], cwd=ROOT)
                self.assertEqual(read(path), expected)
        self.assertEqual(source.historical_view(read), (read, None))

    def test_tampering_current_parent_and_manifest(self):
        paths = [source.MANIFEST]
        for path, row in json.loads(self.read(source.MANIFEST))['changes'].items():
            paths.append(path)
            if row['base_sha256']:
                paths.append(source.ARCHIVE + 'baseline/' + path)
        for path in paths:
            with self.subTest(path=path), self.assertRaises(AssertionError):
                source.historical_view(lambda name: self.read(name) + (b'\n' if name == path else b''))

    def test_missing_manifest_rejected(self):
        with self.assertRaisesRegex(AssertionError, 'MANIFEST_REQUIRED'):
            source.historical_view({source.MARKER: b''}.__getitem__)


if __name__ == '__main__':
    unittest.main()
