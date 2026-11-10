"""Reject unqualified lease/attachment changes before historical verification."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

import lease_work_source_20261110 as lease
import work_integration_source_20261106 as work

ROOT = Path(__file__).resolve().parents[2]


class LeaseWorkSourceTests(unittest.TestCase):
    @staticmethod
    def read(path):
        return (ROOT / path).read_bytes()

    def test_backend_can_load_verifier_without_sibling_on_sys_path(self):
        script = """
import importlib.util
import sys
spec = importlib.util.spec_from_file_location("standalone_verifier", sys.argv[1])
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)
assert verifier.lease_work.SOURCE_TREE == sys.argv[2]
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(ROOT / "deploy/enterprise/verify-release-source-20261023.py"), lease.SOURCE_TREE],
            cwd=ROOT / "backend", capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_exact_parent_projection_and_added_runtime_inventory(self):
        projected, result = lease.historical_view(self.read)
        changes = json.loads(self.read(lease.MANIFEST))["changes"]
        self.assertEqual(result["changed_paths_verified"], 15)
        self.assertEqual(result["added_gateway_runtime"], sorted(lease.MARKERS))
        self.assertFalse(result["production_acceptance_claimed"])
        for path, row in changes.items():
            with self.subTest(path=path):
                if row["base_sha256"] is None:
                    with self.assertRaises(KeyError):
                        projected(path)
                else:
                    original = subprocess.check_output(["git", "show", f"{lease.BASE_COMMIT}:{path}"], cwd=ROOT)
                    self.assertEqual(projected(path), original)
        untouched = "backend/app/gateway/services.py"
        self.assertEqual(projected(untouched), self.read(untouched))
        again, metadata = lease.historical_view(projected)
        self.assertIs(again, projected)
        self.assertIsNone(metadata)

    def test_every_current_file_is_hash_bound(self):
        for target in json.loads(self.read(lease.MANIFEST))["changes"]:
            with self.subTest(path=target), self.assertRaisesRegex(AssertionError, "LEASE_WORK_CURRENT_CHANGED"):
                lease.historical_view(lambda path: self.read(path) + (b"\n" if path == target else b""))

    def test_every_parent_snapshot_is_hash_bound(self):
        for path, row in json.loads(self.read(lease.MANIFEST))["changes"].items():
            if row["base_sha256"] is None:
                continue
            target = lease.ARCHIVE + "baseline/" + path
            with self.subTest(path=path), self.assertRaisesRegex(AssertionError, "LEASE_WORK_PARENT_CHANGED"):
                lease.historical_view(lambda name: self.read(name) + (b"\n" if name == target else b""))

    def test_manifest_is_sealed(self):
        with self.assertRaisesRegex(AssertionError, "LEASE_WORK_MANIFEST_CHANGED"):
            lease.historical_view(lambda path: self.read(path) + (b"\n" if path == lease.MANIFEST else b""))

    def test_each_new_runtime_requires_manifest(self):
        for marker in lease.MARKERS:
            with self.subTest(marker=marker), self.assertRaisesRegex(AssertionError, "LEASE_WORK_MANIFEST_REQUIRED"):
                lease.historical_view({marker: self.read(marker)}.__getitem__)
        historical = {}.__getitem__
        reader, result = lease.historical_view(historical)
        self.assertIs(reader, historical)
        self.assertIsNone(result)

    def test_work_projection_preserves_old_inventory_and_qualifies_new_delta(self):
        projected, result = work.historical_view(self.read)
        self.assertEqual(result["lease_work_source"]["tested_source_tree"], lease.SOURCE_TREE)
        for path in lease.MARKERS:
            self.assertIn(path, result["added_gateway_runtime"])
            with self.assertRaises(KeyError):
                projected(path)
        target = "backend/app/channels/feishu.py"
        with self.assertRaisesRegex(AssertionError, "LEASE_WORK_CURRENT_CHANGED"):
            work.historical_view(lambda path: self.read(path) + (b"\n" if path == target else b""))


if __name__ == "__main__":
    unittest.main()
