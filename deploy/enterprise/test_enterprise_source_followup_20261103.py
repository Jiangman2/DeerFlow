"""Reject drift in current sources, parent snapshots and the successor seal."""

from pathlib import Path
import subprocess
import unittest

from released_source_20261102 import followup, load

ROOT = Path(__file__).resolve().parents[2]


class EnterpriseFollowupTests(unittest.TestCase):
    def read(self, path):
        return (ROOT / path).read_bytes()

    def changed(self, target):
        return lambda path: self.read(path) + (b"\n# drift\n" if path == target else b"")

    def test_current_successor_and_historical_release_both_verified(self):
        manifest = followup.qualify(self.read)
        self.assertEqual(len(manifest["changes"]), 18)
        self.assertEqual(followup.summary(manifest)["runtime_sources_verified"], 7)
        self.assertFalse(followup.summary(manifest)["production_acceptance_claimed"])
        self.assertEqual(len(load(self.read)[1]["sources"]), 41)

    def test_each_current_source_and_test_is_enforced(self):
        for path in followup.qualify(self.read)["changes"]:
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "ENTERPRISE_FOLLOWUP_CURRENT_CHANGED|DASHBOARD_NAVIGATION_CURRENT_CHANGED"):
                load(self.changed(path))

    def test_each_parent_snapshot_is_enforced(self):
        for row in followup.qualify(self.read)["changes"].values():
            if row["baseline_path"]:
                with self.subTest(path=row["baseline_path"]), self.assertRaisesRegex(AssertionError, "ENTERPRISE_FOLLOWUP_BASELINE_CHANGED"):
                    load(self.changed(row["baseline_path"]))

    def test_manifest_tampering_is_rejected(self):
        with self.assertRaisesRegex(AssertionError, "ENTERPRISE_FOLLOWUP_SEAL_CHANGED"):
            load(self.changed(followup.MANIFEST))

    def test_formatting_seal_and_snapshots_are_enforced(self):
        import json
        with self.assertRaisesRegex(AssertionError, "ENTERPRISE_FORMATTING_SEAL_CHANGED"):
            load(self.changed(followup.CI_FORMATTING))
        for row in json.loads(self.read(followup.CI_FORMATTING))["changes"].values():
            with self.subTest(path=row["baseline_path"]), self.assertRaisesRegex(AssertionError, "ENTERPRISE_FORMATTING_BASE_CHANGED"):
                load(self.changed(row["baseline_path"]))

    def test_formatting_fingerprint_accepts_layout_and_import_splitting(self):
        before = b"from a import c, b\nimport json\nx = (1 + 2)\n"
        after = b"import json\nfrom a import b\nfrom a import c\nx=1+2\n"
        self.assertEqual(followup.formatting_fingerprint(before, "example.py"), followup.formatting_fingerprint(after, "example.py"))

    def test_formatting_fingerprint_rejects_executable_and_binding_changes(self):
        baseline = followup.formatting_fingerprint(b"import json\nx = 3\n", "example.py")
        for raw in (b"import json\nx = 4\n", b"import json as something\nx=3\n", b"x=3\nimport json\n"):
            with self.subTest(raw=raw):
                self.assertNotEqual(baseline, followup.formatting_fingerprint(raw, "example.py"))

    def test_unused_import_exception_is_limited_to_two_tests(self):
        before, after = b"import json\nx=3\n", b"x=3\n"
        self.assertNotEqual(followup.formatting_fingerprint(before, "runtime.py"), followup.formatting_fingerprint(after, "runtime.py"))
        path = "backend/tests/test_weknora_relay_budget.py"
        self.assertEqual(followup.formatting_fingerprint(before, path), followup.formatting_fingerprint(after, path))

    def test_missing_registered_current_source_is_rejected(self):
        path = "backend/packages/harness/deerflow/models/llm_performance.py"
        def read(name):
            if name == path:
                raise FileNotFoundError(name)
            return self.read(name)
        with self.assertRaises(FileNotFoundError):
            load(read)

    def test_unlisted_paths_are_not_replaced(self):
        path = "backend/app/gateway/routers/model_monitor.py"
        with self.assertRaisesRegex(AssertionError, "NOV02_RUNTIME_LOGIC_CHANGED|NOV02_CURRENT_SOURCE_CHANGED"):
            load(self.changed(path))

    def test_reader_is_idempotent_and_added_files_are_not_old_evidence(self):
        reader = followup.historical_read(self.read)
        self.assertIs(followup.historical_read(reader), reader)
        with self.assertRaises(KeyError):
            reader("backend/packages/harness/deerflow/models/llm_performance.py")

    def test_snapshot_and_hashes_match_the_fixed_git_commits(self):
        manifest = followup.qualify(self.read)
        for path, row in manifest["changes"].items():
            with self.subTest(path=path):
                raw = subprocess.check_output(["git", "show", followup.SOURCE_COMMIT + ":" + path], cwd=ROOT)
                self.assertEqual(followup.digest(raw), row["current_sha256"])
                if row["baseline_path"]:
                    original = subprocess.check_output(["git", "show", followup.BASE_COMMIT + ":" + path], cwd=ROOT)
                    self.assertEqual(followup.digest(original), row["base_sha256"])
                    self.assertEqual(followup.digest(self.read(row["baseline_path"])), followup.digest(original))


if __name__ == "__main__":
    unittest.main()
