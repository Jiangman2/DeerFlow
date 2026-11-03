"""The candidate cannot bypass historical provenance or drift outside its seal."""

from pathlib import Path
import subprocess
import unittest

import dashboard_navigation_source_20261103 as navigation

ROOT = Path(__file__).resolve().parents[2]


class NavigationSourceTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_bytes()

    def changed(self, target):
        return lambda path: self.read(path) + (b"\n// drift\n" if path == target else b"")

    def test_candidate_is_not_a_production_receipt(self):
        manifest = navigation.qualify(self.read)
        self.assertEqual(len(navigation.summary(manifest)["frontend_runtime_paths"]), 9)
        self.assertFalse(manifest["production_acceptance_claimed"])
        self.assertFalse(manifest["gateway_modified"])
        self.assertFalse(manifest["database_modified"])

    def test_every_runtime_and_test_file_is_bound(self):
        for path in navigation.qualify(self.read)["changes"]:
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "DASHBOARD_NAVIGATION_CURRENT_CHANGED"):
                navigation.qualify(self.changed(path))

    def test_every_baseline_is_bound(self):
        for row in navigation.qualify(self.read)["changes"].values():
            if row["baseline_path"]:
                with self.subTest(path=row["baseline_path"]), self.assertRaisesRegex(AssertionError, "DASHBOARD_NAVIGATION_BASELINE_CHANGED"):
                    navigation.qualify(self.changed(row["baseline_path"]))

    def test_seal_changes_are_rejected(self):
        with self.assertRaisesRegex(AssertionError, "DASHBOARD_NAVIGATION_SEAL_CHANGED"):
            navigation.qualify(self.changed(navigation.MANIFEST))

    def test_projection_matches_exact_enterprise_parent(self):
        manifest = navigation.qualify(self.read)
        reader = navigation.historical_read(self.read)
        self.assertIs(navigation.historical_read(reader), reader)
        for path, row in manifest["changes"].items():
            with self.subTest(path=path):
                result = subprocess.run(["git", "show", navigation.BASE_COMMIT + ":" + path], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                if row["baseline_path"] is None:
                    self.assertNotEqual(result.returncode, 0)
                    with self.assertRaises(KeyError):
                        reader(path)
                else:
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(reader(path), result.stdout)

    def test_other_sources_pass_through_unchanged(self):
        path = "backend/app/gateway/routers/model_monitor.py"
        self.assertEqual(navigation.historical_read(self.read)(path), self.read(path))


if __name__ == "__main__":
    unittest.main()
