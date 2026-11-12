"""A qualified monitoring change must not bypass historical source checks."""

import json
import subprocess
import unittest
from pathlib import Path

import datalake_combined_source_20261112 as monitor

ROOT = Path(__file__).resolve().parents[2]


class DataLakeCombinedSourceTests(unittest.TestCase):
    @staticmethod
    def read(path):
        return (ROOT / path).read_bytes()

    def test_projection_matches_git_parent_and_is_idempotent(self):
        projected, result = monitor.historical_view(self.read)
        self.assertEqual(result["changed_paths_verified"], len(monitor.PATHS))
        self.assertFalse(result["production_acceptance_claimed"])
        for path, row in json.loads(self.read(monitor.MANIFEST))["changes"].items():
            if row["base_sha256"] is None:
                with self.assertRaises(KeyError):
                    projected(path)
            else:
                original = subprocess.check_output(["git", "show", f"{monitor.BASE_COMMIT}:{path}"], cwd=ROOT)
                self.assertEqual(projected(path), original)
        again, result = monitor.historical_view(projected)
        self.assertIs(again, projected)
        self.assertIsNone(result)

    def test_each_current_and_parent_hash_is_checked(self):
        for path, row in json.loads(self.read(monitor.MANIFEST))["changes"].items():
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "DATALAKE_COMBINED_CURRENT_CHANGED"):
                monitor.historical_view(lambda name: self.read(name) + (b"\n" if name == path else b""))
            if row["base_sha256"]:
                target = monitor.ARCHIVE + "baseline/" + path
                with self.subTest(parent=path), self.assertRaisesRegex(AssertionError, "DATALAKE_COMBINED_PARENT_CHANGED"):
                    monitor.historical_view(lambda name: self.read(name) + (b"\n" if name == target else b""))

    def test_seal_and_missing_manifest(self):
        with self.assertRaisesRegex(AssertionError, "DATALAKE_COMBINED_MANIFEST_CHANGED"):
            monitor.historical_view(lambda name: self.read(name) + (b"\n" if name == monitor.MANIFEST else b""))
        with self.assertRaisesRegex(AssertionError, "DATALAKE_COMBINED_MANIFEST_REQUIRED"):
            monitor.historical_view({monitor.MARKER: b""}.__getitem__)
        empty = {}.__getitem__
        self.assertEqual(monitor.historical_view(empty), (empty, None))


if __name__ == "__main__":
    unittest.main()
