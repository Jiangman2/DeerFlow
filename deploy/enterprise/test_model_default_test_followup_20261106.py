"""Reject source drift while retaining immutable pre-V4.1 test evidence."""
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import dashboard_navigation_source_20261103 as navigation
from released_source_20261102 import load
from work_integration_source_20261106 import historical_view

source = navigation.model_default_test
ROOT = Path(__file__).resolve().parents[2]


class ModelDefaultTestFollowupTests(unittest.TestCase):
    def setUp(self):
        self.read, _ = historical_view(lambda path: (ROOT / path).read_bytes())

    def replace(self, target, data):
        def read(path):
            if path == target:
                if data is None:
                    raise KeyError(path)
                return data
            return self.read(path)
        return read

    def changed(self, target):
        return self.replace(target, self.read(target) + b"\n// drift\n")

    def resealed(self, change):
        manifest = copy.deepcopy(source.qualify(self.read))
        change(manifest)
        raw = json.dumps(manifest).encode()
        with patch.object(source, "MANIFEST_SHA256", source.digest(raw)):
            return source.qualify(self.replace(source.MANIFEST, raw))

    def test_registration_is_single_test_only(self):
        result = source.qualify(self.read)
        self.assertEqual(set(result["changes"]), {source.TEST})
        self.assertFalse(result["runtime_modified"])
        self.assertFalse(result["fresh_production_acceptance_claimed"])

    def test_current_test_drift_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_CURRENT_CHANGED"):
            source.qualify(self.changed(source.TEST))

    def test_old_snapshot_drift_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_BASELINE_CHANGED"):
            source.qualify(self.changed(source.BASELINE))

    def test_manifest_drift_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_SEAL_CHANGED"):
            source.qualify(self.changed(source.MANIFEST))

    def test_manifest_removal_with_snapshot_remaining_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_MANIFEST_REQUIRED"):
            source.qualify(self.replace(source.MANIFEST, None))

    def test_runtime_addition_is_rejected_even_when_resealed(self):
        with self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_SCOPE"):
            self.resealed(lambda m: m["changes"].update({"frontend/src/extra.ts": {}}))

    def test_runtime_and_acceptance_overclaims_are_rejected(self):
        for key in ("runtime_modified", "fresh_production_acceptance_claimed"):
            with self.subTest(key=key), self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_OVERCLAIM"):
                self.resealed(lambda m: m.update({key: True}))

    def test_snapshot_is_exact_parent_commit_and_dashboard_successor(self):
        expected = subprocess.check_output(["git", "show", source.BASE_COMMIT + ":" + source.TEST], cwd=ROOT)
        baseline = self.read(source.BASELINE)
        self.assertEqual(source.digest(baseline), source.digest(expected))
        dashboard = json.loads(self.read(navigation.MANIFEST))
        self.assertEqual(source.digest(baseline), dashboard["changes"][source.TEST]["current_sha256"])
        self.assertEqual(source.historical_read(self.read)(source.TEST), baseline)

    def test_projection_is_revalidated_and_idempotent(self):
        values = {}
        def read(path):
            return values[path] if path in values else self.read(path)
        projected = source.historical_read(read)
        self.assertIs(source.historical_read(projected), projected)
        values[source.TEST] = self.read(source.TEST) + b"\n// drift\n"
        with self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_CURRENT_CHANGED"):
            source.historical_read(projected)

    def test_unrelated_runtime_is_unmodified(self):
        path = "frontend/src/components/workspace/sidecar/sidecar-panel.tsx"
        self.assertEqual(source.historical_read(self.read)(path), self.read(path))

    def test_historical_release_chain_accepts_registered_test(self):
        self.assertEqual(len(load(self.read)[1]["sources"]), 41)
        self.assertIn(source.TEST, navigation.qualify(self.read)["changes"])

    def test_historical_release_chain_rejects_changed_current_test(self):
        with self.assertRaisesRegex(AssertionError, "MODEL_DEFAULT_TEST_CURRENT_CHANGED"):
            load(self.changed(source.TEST))


if __name__ == "__main__":
    unittest.main()
