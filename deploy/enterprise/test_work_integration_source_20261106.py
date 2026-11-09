"""Only sealed Work changes can be projected to the latest 0716 parent."""

import json
import unittest
from unittest.mock import patch

import work_integration_source_20261106 as work

MODIFIED = "backend/app/gateway/services.py"
ADDED = "backend/app/integrations/work/file_search.py"


class WorkIntegrationSourceTests(unittest.TestCase):
    @staticmethod
    def read(path):
        return (work.ROOT / path).read_bytes()

    def test_declared_current_and_exact_git_parent_are_qualified(self):
        projected, result = work.historical_view(self.read)
        manifest = json.loads(self.read(work.MANIFEST))
        self.assertEqual(result["base_commit"], work.BASE_COMMIT)
        self.assertFalse(result["production_acceptance_claimed"])
        self.assertEqual(result["changed_paths_verified"], len(manifest["changes"]))
        self.assertEqual(work.digest(projected(MODIFIED)), manifest["changes"][MODIFIED]["base_sha256"])
        self.assertNotEqual(projected(MODIFIED), self.read(MODIFIED))
        self.assertIn(ADDED, result["added_gateway_runtime"])
        with self.assertRaises(KeyError):
            projected(ADDED)
        self.assertEqual(projected("backend/app/gateway/routers/model_monitor.py"), self.read("backend/app/gateway/routers/model_monitor.py"))

    def test_rejects_changed_runtime_or_manifest(self):
        for target, error in ((MODIFIED, "WORK_FOLLOWUP_CURRENT_CHANGED"), (ADDED, "WORK_FOLLOWUP_CURRENT_CHANGED"), (work.MANIFEST, "WORK_FOLLOWUP_SEAL_CHANGED")):
            with self.subTest(target=target), self.assertRaisesRegex(AssertionError, error):
                work.historical_view(lambda path: self.read(path) + (b"\n" if path == target else b""))

    def test_rejects_altered_git_parent(self):
        parents = work._parent_sources(json.loads(self.read(work.MANIFEST))["changes"])
        parents[MODIFIED] += b"\n"
        with patch.object(work, "_parent_sources", return_value=parents), self.assertRaisesRegex(AssertionError, "WORK_FOLLOWUP_PARENT_CHANGED"):
            work.historical_view(self.read)

    def test_missing_manifest_cannot_hide_candidate(self):
        def without_manifest(path):
            if path == work.MANIFEST:
                raise KeyError(path)
            return self.read(path)

        with self.assertRaisesRegex(AssertionError, "WORK_FOLLOWUP_MANIFEST_REQUIRED"):
            work.historical_view(without_manifest)
        historical = {}.__getitem__
        read, result = work.historical_view(historical)
        self.assertIs(read, historical)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
