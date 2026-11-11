"""Only the sealed IM S08 change set can be projected to the 0716 parent."""

import json
import unittest
from unittest.mock import patch

import im_s08_source_20261111 as im_s08
import work_integration_source_20261106 as work

MODIFIED = "backend/app/channels/feishu.py"
ADDED = "backend/app/gateway/routers/im_conversations.py"
UNTOUCHED = "backend/app/gateway/routers/model_monitor.py"


class ImS08SourceTests(unittest.TestCase):
    @staticmethod
    def read(path):
        return (im_s08.ROOT / path).read_bytes()

    def manifest(self):
        return json.loads(self.read(im_s08.MANIFEST))

    def test_declared_current_and_exact_git_parent_are_qualified(self):
        projected, result = im_s08.historical_view(self.read)
        manifest = self.manifest()
        self.assertEqual(result["base_commit"], im_s08.BASE_COMMIT)
        self.assertFalse(result["production_acceptance_claimed"])
        self.assertTrue(result["schema_migration_requested"])
        self.assertEqual(result["changed_paths_verified"], sum(len(group) for group in manifest["changes"].values()))
        row = manifest["changes"]["gateway"][MODIFIED]
        self.assertEqual(im_s08.digest(projected(MODIFIED)), row["base_sha256"])
        self.assertNotEqual(projected(MODIFIED), self.read(MODIFIED))
        self.assertIn(ADDED, result["added_gateway_runtime"])
        with self.assertRaises(KeyError):
            projected(ADDED)
        self.assertEqual(projected(UNTOUCHED), self.read(UNTOUCHED))

    def test_rejects_changed_runtime_or_manifest(self):
        for target, error in (
            (MODIFIED, "IM_S08_CURRENT_CHANGED"),
            (ADDED, "IM_S08_CURRENT_CHANGED"),
            (im_s08.MANIFEST, "IM_S08_SEAL_CHANGED"),
        ):
            with self.subTest(target=target), self.assertRaisesRegex(AssertionError, error):
                im_s08.historical_view(lambda path: self.read(path) + (b"\n" if path == target else b""))

    def test_rejects_altered_git_parent(self):
        parents = im_s08._parent_sources(
            [path for group in self.manifest()["changes"].values() for path in group]
        )
        parents[MODIFIED] += b"\n"
        with patch.object(im_s08, "_parent_sources", return_value=parents), self.assertRaisesRegex(
            AssertionError, "IM_S08_PARENT_CHANGED"
        ):
            im_s08.historical_view(self.read)

    def test_missing_manifest_cannot_hide_candidate(self):
        def without_manifest(path):
            if path == im_s08.MANIFEST:
                raise KeyError(path)
            return self.read(path)

        with self.assertRaisesRegex(AssertionError, "IM_S08_MANIFEST_REQUIRED"):
            im_s08.historical_view(without_manifest)
        historical = {}.__getitem__
        read, result = im_s08.historical_view(historical)
        self.assertIs(read, historical)
        self.assertIsNone(result)

    def test_overrides_supersede_the_previous_layers(self):
        lock = json.loads(self.read(im_s08.LOCK))
        manifest = self.manifest()
        prior = {path: "0" * 64 for path in manifest["changes"]["gateway"]}
        overrides = im_s08.overrides(
            lock, prior, manifest, im_s08.digest(self.read(im_s08.LOCK)), manifest["parent_candidate_sha256"]
        )
        self.assertEqual(set(overrides), {"gateway", "frontend"})
        self.assertEqual(overrides["gateway"][MODIFIED], manifest["changes"]["gateway"][MODIFIED]["candidate_sha256"])
        with self.assertRaisesRegex(AssertionError, "IM_S08_LOCK_CHANGED"):
            im_s08.overrides(lock, prior, manifest, "f" * 64, manifest["parent_candidate_sha256"])
        with self.assertRaisesRegex(AssertionError, "IM_S08_PARENT_SEAL_CHANGED"):
            im_s08.overrides(lock, prior, manifest, im_s08.digest(self.read(im_s08.LOCK)), "f" * 64)

    def test_overrides_reject_unregistered_new_source(self):
        lock = json.loads(self.read(im_s08.LOCK))
        manifest = self.manifest()
        lock = json.loads(json.dumps(lock))
        lock["source_sets"]["gateway"][ADDED] = "0" * 64
        with self.assertRaisesRegex(AssertionError, "IM_S08_UNREGISTERED_BASE"):
            im_s08.overrides(
                lock, {}, manifest, im_s08.digest(self.read(im_s08.LOCK)), manifest["parent_candidate_sha256"]
            )

    def test_work_integration_exposes_the_sealed_parent(self):
        _, result = work.historical_view(self.read)
        self.assertIsNotNone(result["im_s08_parent"])
        self.assertEqual(result["im_s08_parent"]["base_commit"], im_s08.BASE_COMMIT)


if __name__ == "__main__":
    unittest.main()