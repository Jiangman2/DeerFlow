"""Rejection tests for financial provenance; fixtures never execute release tools."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "financial_verifier", ROOT / "deploy/enterprise/verify-research-fund-production-source.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
source = verifier.successor


class FinancialSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Exercise the immutable earlier verifier against its verified parent view.
        current = [p.relative_to(ROOT).as_posix() for p in (ROOT / source.SKILL).rglob("*") if p.is_file() and "__pycache__" not in p.parts]
        _, cls.parent_projection = verifier.later.qualify(lambda path: (ROOT / path).read_bytes(), current)
        cls.manifest = json.loads((ROOT / source.MANIFEST).read_bytes())
        cls.paths = [p.relative_to(ROOT).as_posix()
                     for p in (ROOT / source.SKILL).rglob("*") if p.is_file()]

    def reader(self, overrides):
        def read(path):
            if path in overrides:
                if overrides[path] is None:
                    raise KeyError(path)
                return overrides[path]
            if path == verifier.later.MANIFEST:
                raise KeyError(path)
            if self.parent_projection and path in self.parent_projection:
                return self.parent_projection[path]
            return (ROOT / path).read_bytes()
        return read

    def qualify(self, overrides=None, paths=None):
        return source.qualify(self.reader(overrides or {}), self.paths if paths is None else paths)

    def resealed(self, changes, manifest_change=None):
        # Move past the cryptographic seal only to exercise downstream rejection.
        manifest = copy.deepcopy(self.manifest)
        for path, raw in changes.items():
            for key in ("evidence_files", "support_files"):
                if path in manifest[key]:
                    manifest[key][path] = {"bytes": len(raw), "sha256": source.sha(raw)}
        if manifest_change:
            manifest_change(manifest)
        raw = json.dumps(manifest).encode()
        overrides = {**changes, source.MANIFEST: raw}
        with patch.object(source, "MANIFEST_SHA256", source.sha(raw)):
            return self.qualify(overrides)

    def test_current_source_and_unchanged_historical_archive(self):
        result = verifier.verify(self.reader({}), self.paths)
        self.assertEqual(result["skill_files"], 30)
        self.assertEqual(result["historical_skill_files"], 29)
        self.assertEqual(result["tree_sha256"], "6c393b11a0023a50de29a1958088322324f759e85c6f3080816aed023c61e951")
        self.assertTrue(result["business_acceptance_pending"])
        self.assertFalse(result["production_commands_executed"])

    def test_original_october_source_still_verifies_without_successor(self):
        lock = json.loads((ROOT / source.LOCK).read_bytes())
        original = source.historical_files(self.reader({}), lock)
        result = verifier.verify(self.reader({**original, source.MANIFEST: None}), list(original))
        self.assertEqual(result["skill_files"], 29)
        self.assertEqual(result["tree_sha256"], lock["deployed_tree_sha256"])

    def test_latest_files_cannot_hide_the_successor_manifest(self):
        with self.assertRaisesRegex(AssertionError, "SKILL_FILE_SET_CHANGED"):
            verifier.verify(self.reader({source.MANIFEST: None}), self.paths)

    def test_source_changes_rejected(self):
        path = source.SKILL + "/scripts/report_model.py"
        with self.assertRaisesRegex(ValueError, "FINANCE_SOURCE_CHANGED"):
            self.qualify({path: (ROOT / path).read_bytes() + b"\n# changed\n"})

    def test_extra_file_rejected(self):
        with self.assertRaisesRegex(ValueError, "FINANCE_SKILL_FILE_SET_CHANGED"):
            self.qualify(paths=self.paths + [source.SKILL + "/unexpected.py"])

    def test_missing_file_rejected(self):
        with self.assertRaisesRegex(ValueError, "FINANCE_SKILL_FILE_SET_CHANGED"):
            self.qualify(paths=self.paths[1:])

    def test_unchanged_evidence_bytes_required(self):
        for path in (source.NOV04 + "production-update-20261104.json",
                     source.NOV04 + "production-cleanup-20261104.json",
                     source.NOV04 + "old-bundle.json",
                     source.NOV05 + "candidate-manifest.json",
                     source.NOV05 + "production-update-20261105.json",
                     source.NOV05 + "production-update-operator-output-20261105.json"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "FINANCE_EVIDENCE_CHANGED"):
                self.qualify({path: (ROOT / path).read_bytes() + b" "})

    def test_support_files_are_sealed(self):
        for path in source.SUPPORT_PATHS:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "FINANCE_EVIDENCE_CHANGED"):
                self.qualify({path: b"changed"})

    def test_manifest_cannot_be_rewritten(self):
        with self.assertRaisesRegex(ValueError, "FINANCE_SOURCE_SEAL_CHANGED"):
            self.qualify({source.MANIFEST: (ROOT / source.MANIFEST).read_bytes() + b" "})

    def test_parent_lock_cannot_be_rewritten(self):
        with self.assertRaisesRegex(ValueError, "FINANCE_PARENT_LOCK_CHANGED"):
            self.qualify({source.LOCK: (ROOT / source.LOCK).read_bytes() + b" "})

    def test_business_acceptance_overclaim_rejected(self):
        with self.assertRaisesRegex(ValueError, "FINANCE_ACCEPTANCE_OVERCLAIM"):
            self.resealed({}, lambda m: m.update(business_acceptance_pending=False))

    def test_production_execution_overclaim_rejected(self):
        with self.assertRaisesRegex(ValueError, "FINANCE_ACCEPTANCE_OVERCLAIM"):
            self.resealed({}, lambda m: m.update(production_commands_executed_by_verifier=True))

    def test_missing_support_registration_rejected(self):
        with self.assertRaisesRegex(ValueError, "FINANCE_SUPPORT_SET_CHANGED"):
            self.resealed({}, lambda m: m["support_files"].pop("scripts/review_research_fund_templates.py"))

    def test_evidence_cannot_expand_into_unrelated_runtime(self):
        def mutate(manifest):
            manifest["evidence_files"]["frontend/src/core/settings/store.ts"] = {"bytes": 0, "sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "FINANCE_EVIDENCE_SCOPE"):
            self.resealed({}, mutate)

    def test_candidate_manifest_tree_must_reconcile(self):
        path = source.NOV05 + "candidate-manifest.json"
        candidate = json.loads((ROOT / path).read_bytes())
        candidate["tree_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "FINANCE_TREE_CHANGED"):
            self.resealed({path: json.dumps(candidate).encode()})

    def test_candidate_manifest_rejects_traversal(self):
        path = source.NOV05 + "candidate-manifest.json"
        candidate = json.loads((ROOT / path).read_bytes())
        candidate["files"][0]["path"] = "../outside"
        with self.assertRaisesRegex(ValueError, "FINANCE_UNSAFE_PATH"):
            self.resealed({path: json.dumps(candidate).encode()})

    def test_receipt_cannot_claim_database_or_business_acceptance(self):
        path = source.NOV05 + "production-update-20261105.json"
        for field, value in (("production_database_access", True), ("browser_business_acceptance_pending", False)):
            receipt = json.loads((ROOT / path).read_bytes())
            receipt[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "FINANCE_RECEIPT_SCOPE_CHANGED"):
                self.resealed({path: json.dumps(receipt).encode()})

    def test_november4_original_failure_is_not_rewritten_as_success(self):
        path = source.NOV04 + "production-update-20261104.json"
        receipt = json.loads((ROOT / path).read_bytes())
        receipt.update(status="SUCCESS", exitcode=0)
        with self.assertRaisesRegex(ValueError, "FINANCE_NOV04_RECEIPT_REWRITTEN"):
            self.resealed({path: json.dumps(receipt).encode()})

    def test_november5_tree_cannot_be_bound_to_another_baseline(self):
        path = source.NOV05 + "candidate-delta.json"
        delta = json.loads((ROOT / path).read_bytes())
        delta["baseline_tree"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "FINANCE_DELTA_PARENT_CHANGED"):
            self.resealed({path: json.dumps(delta).encode()})

    def test_delta_cannot_omit_a_changed_file(self):
        path = source.NOV05 + "candidate-delta.json"
        delta = json.loads((ROOT / path).read_bytes())
        delta["files"].pop()
        with self.assertRaisesRegex(ValueError, "FINANCE_DELTA_CHANGED"):
            self.resealed({path: json.dumps(delta).encode()})

    def test_original_archive_digest_is_required(self):
        path = source.NOV04 + "old-bundle.json"
        bundle = json.loads((ROOT / path).read_bytes())
        bundle["archive_b64"] = "dGFtcGVyZWQ="
        with self.assertRaisesRegex(ValueError, "FINANCE_HISTORICAL_ARCHIVE_CHANGED"):
            self.resealed({path: json.dumps(bundle).encode()})

    def test_original_historical_evidence_is_still_checked(self):
        lock = json.loads((ROOT / source.LOCK).read_bytes())
        path = lock["production_report"]
        with self.assertRaises(AssertionError):
            verifier.verify(self.reader({path: b"{}"}), self.paths)


if __name__ == "__main__":
    unittest.main()
