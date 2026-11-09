import json
from pathlib import Path
import unittest

import artifact_delivery_source_20261109 as source

ROOT = Path(__file__).resolve().parents[2]


class ArtifactDeliverySourceTests(unittest.TestCase):
    def fixture(self):
        raw = (ROOT / source.MANIFEST).read_bytes()
        paths = list(json.loads(raw)["changes"])
        paths += [source.ARCHIVE + "baseline/" + p for p in source.PATHS]
        return {source.MANIFEST: raw, **{p: (ROOT / p).read_bytes() for p in paths}}

    def test_projects_verified_parent_only(self):
        data = self.fixture()
        data["unrelated"] = b"unchanged"
        projected = source.historical_read(data.__getitem__)
        for p in source.PATHS:
            self.assertEqual(projected(p), data[source.ARCHIVE + "baseline/" + p])
        self.assertEqual(projected("unrelated"), b"unchanged")

    def test_modified_manifest_current_and_parent_rejected(self):
        for path in self.fixture():
            with self.subTest(path=path):
                data = self.fixture()
                data[path] = b"modified"
                with self.assertRaises(AssertionError):
                    source.historical_read(data.__getitem__)

    def test_missing_current_and_parent_rejected(self):
        for path in self.fixture():
            if path == source.MANIFEST:
                continue
            with self.subTest(path=path):
                data = self.fixture()
                del data[path]
                with self.assertRaises(KeyError):
                    source.historical_read(data.__getitem__)

    def test_old_tree_without_manifest_uses_original_verifier(self):
        read = {}.__getitem__
        self.assertIs(source.historical_read(read), read)


if __name__ == "__main__":
    unittest.main()
