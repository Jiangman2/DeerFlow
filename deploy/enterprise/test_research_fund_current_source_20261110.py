"""Reject unsealed changes and overclaims in the deployed financial source chain."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('finance_current_verifier', ROOT/'deploy/enterprise/verify-research-fund-production-source.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
source = verifier.current


class CurrentSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = [p.relative_to(ROOT).as_posix() for p in (ROOT/source.SKILL).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts]

    def read(self, changes=None):
        changes = changes or {}
        def read(path):
            if path in changes:
                if changes[path] is None:
                    raise KeyError(path)
                return changes[path]
            return (ROOT/path).read_bytes()
        return read

    def test_current_and_all_historical_receipts_verify(self):
        result = verifier.verify(self.read(), self.paths)
        self.assertEqual(result['tree_sha256'], source.AFTER)
        self.assertEqual(result['skill_files'],31)
        self.assertEqual(result['historical_skill_files'],29)
        self.assertTrue(result['business_acceptance_pending'])
        self.assertFalse(result['production_commands_executed'])

    def test_changed_runtime_receipts_or_parent_fail(self):
        for path in (source.SKILL+'scripts/group_template.py', source.SKILL+'scripts/report_display.py',
                     source.NEW+'production-server-report-20261110.json',
                     source.OLD+'operator-update-report-reconstructed-20261106.json',
                     source.MANIFEST, source.prior.MANIFEST):
            with self.subTest(path=path), self.assertRaises(ValueError):
                verifier.verify(self.read({path:b'changed'}), self.paths)

    def test_extra_missing_file_or_hidden_successor_fail(self):
        for paths in (self.paths[1:],self.paths+[source.SKILL+'extra.py']):
            with self.assertRaisesRegex(ValueError,'CURRENT_FINANCE_FILE_SET_CHANGED'):
                verifier.verify(self.read(), paths)
        with self.assertRaises(ValueError):
            verifier.verify(self.read({source.MANIFEST:None}), self.paths)


if __name__ == '__main__':
    unittest.main()
