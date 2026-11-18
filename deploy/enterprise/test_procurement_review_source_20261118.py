"""A qualified procurement change must not bypass historical source checks."""

import json
import subprocess
import unittest
from pathlib import Path

import procurement_review_source_20261118 as monitor

ROOT = Path(__file__).resolve().parents[2]


class ProcurementSourceTests(unittest.TestCase):
    @staticmethod
    def read(path):
        return (ROOT / path).read_bytes()

    def test_projection_matches_git_parent_and_is_idempotent(self):
        projected, result = monitor.historical_view(self.read)
        self.assertEqual(result["changed_paths_verified"], len(monitor.PATHS))
        self.assertFalse(result["production_acceptance_claimed"])
        self.assertEqual(len(result["added_gateway_runtime"]), 10)
        self.assertTrue(
            all(path.endswith(".py") for path in result["added_gateway_runtime"])
        )
        for path, row in json.loads(self.read(monitor.MANIFEST))["changes"].items():
            if row["base_sha256"] is None:
                with self.assertRaises(KeyError):
                    projected(path)
            else:
                original = subprocess.check_output(
                    ["git", "show", f"{monitor.BASE_COMMIT}:{path}"], cwd=ROOT
                )
                self.assertEqual(projected(path), original)
        again, result = monitor.historical_view(projected)
        self.assertIs(again, projected)
        self.assertIsNone(result)

    def test_each_current_and_parent_hash_is_checked(self):
        for path, row in json.loads(self.read(monitor.MANIFEST))["changes"].items():
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(AssertionError, "PROCUREMENT_CURRENT_CHANGED"),
            ):
                monitor.historical_view(
                    lambda name: self.read(name) + (b"\n" if name == path else b"")
                )
            if row["base_sha256"]:
                target = monitor.ARCHIVE + "baseline/" + path
                with (
                    self.subTest(parent=path),
                    self.assertRaisesRegex(
                        AssertionError, "PROCUREMENT_BASELINE_CHANGED"
                    ),
                ):
                    monitor.historical_view(
                        lambda name: (
                            self.read(name) + (b"\n" if name == target else b"")
                        )
                    )

    def test_seal_and_missing_manifest(self):
        with self.assertRaisesRegex(AssertionError, "PROCUREMENT_MANIFEST_CHANGED"):
            monitor.historical_view(
                lambda name: (
                    self.read(name) + (b"\n" if name == monitor.MANIFEST else b"")
                )
            )
        with self.assertRaisesRegex(AssertionError, "PROCUREMENT_MANIFEST_REQUIRED"):
            monitor.historical_view({monitor.MARKER: b""}.__getitem__)
        empty = {}.__getitem__
        self.assertEqual(monitor.historical_view(empty), (empty, None))


if __name__ == "__main__":
    unittest.main()
