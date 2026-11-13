"""Candidate source checks never imply deployment or rewrite previous receipts."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('financial_verifier', ROOT/'deploy/enterprise/verify-research-fund-production-source.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
source = verifier.current.adjusted


class AdjustedBudgetSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.paths = [p.relative_to(ROOT).as_posix() for p in (ROOT/source.SKILL).rglob('*')
                     if p.is_file() and '__pycache__' not in p.parts]

    def reader(self, changes=None):
        changes = changes or {}
        def read(path):
            if path in changes:
                if changes[path] is None:
                    raise KeyError(path)
                return changes[path]
            return (ROOT/path).read_bytes()
        return read

    def test_candidate_is_not_reported_as_deployed(self):
        result = verifier.verify(self.reader(), self.paths)
        self.assertEqual(result['status'], 'ADJUSTED_BUDGET_CANDIDATE_VERIFIED_NOT_DEPLOYED')
        self.assertNotEqual(result['candidate_tree_sha256'], result['deployed_tree_sha256'])
        self.assertEqual(result['deployed_tree_sha256'], verifier.current.AFTER)
        self.assertFalse(result['production_acceptance_claimed'])

    def test_missing_seal_cannot_disguise_changed_runtime_as_old_release(self):
        with self.assertRaises(ValueError):
            verifier.verify(self.reader({source.MANIFEST: None}), self.paths)

    def test_tampered_candidate_tests_or_parent_snapshot_are_rejected(self):
        for path in (source.MANIFEST, source.SKILL+'scripts/group_template.py',
                     'tests/skills/test_research_fund_adjusted_budget.py',
                     source.ARCHIVE+'parent/'+source.SKILL+'scripts/group_template.py'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                verifier.verify(self.reader({path:b'changed'}), self.paths)

    def test_extra_skill_file_is_rejected(self):
        with self.assertRaises(ValueError):
            verifier.verify(self.reader(), self.paths+[source.SKILL+'unexpected.py'])


if __name__ == '__main__':
    unittest.main()
