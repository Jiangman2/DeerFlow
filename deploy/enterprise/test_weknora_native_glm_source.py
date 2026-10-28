"""Failure cases for the production source/receipt binding; no production access."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location(
    "provenance", Path(__file__).with_name("verify-weknora-native-glm-source.py"))
P = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(P)


class SourceLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        read = P.Reader(P.ROOT)
        cls.lock = json.loads(read(P.LOCK))
        cls.paths = read.source_paths(cls.lock["vendor_prefix"])
        names = set(cls.lock["evidence_files"]) | set(cls.lock["historical_recipe_files"]) | set(cls.lock["shared_builder_files"])
        names |= {cls.lock["vendor_prefix"] + p for p in cls.paths}
        cls.data = {p: read(p) for p in names}

    def check(self, changed=None, lock=None, paths=None):
        changed = changed or {}
        return P.verify(lambda p: changed[p] if p in changed else self.data[p],
                        lock or self.lock, self.paths if paths is None else paths)

    def test_complete_historical_inventory(self):
        self.assertEqual(self.check()["manifest_files"], 1999)

    def test_missing_factory_rejected(self):
        name = "internal/models/vlm/vlm.go"
        with self.assertRaisesRegex(ValueError, "INVENTORY_CHANGED"):
            self.check(paths=self.paths - {name})

    def test_added_runtime_source_rejected(self):
        with self.assertRaisesRegex(ValueError, "INVENTORY_CHANGED"):
            self.check(paths=self.paths | {"internal/models/vlm/unarchived.go"})

    def test_changed_glm_source_rejected(self):
        path = self.lock["vendor_prefix"] + "internal/models/vlm/enterprise_glm.go"
        with self.assertRaisesRegex(ValueError, "BUILD_SOURCE_CHANGED"):
            self.check({path: self.data[path] + b"\n// unintended candidate\n"})

    def test_manifest_cannot_be_regenerated_to_hide_drift(self):
        path = self.lock["archive_prefix"] + "SOURCE.SHA256"
        raw = self.data[path] + b"0" * 64 + b"  unintended.go\n"
        lock = copy.deepcopy(self.lock)
        lock["source_manifest_sha256"] = P.digest(raw)
        with self.assertRaisesRegex(ValueError, "HISTORICAL_MANIFEST_CHANGED"):
            self.check({path: raw}, lock)

    def test_dependency_crate_replacement_rejected(self):
        with self.assertRaisesRegex(ValueError, "ANYDOC_CRATE_CHANGED"):
            P.patched_anydoc(b"replacement", self.lock["anydoc"])

    def test_changed_jieba_recipe_rejected(self):
        path = "release/mineru-provider-switch-20261014/glm-native/Dockerfile.runtime-finish"
        with self.assertRaisesRegex(ValueError, "ARCHIVE_INPUT_CHANGED"):
            self.check({path: self.data[path] + b"\nENV JIEBA_DICT_DIR=/wrong\n"})

    def test_receipt_must_bind_the_actual_program(self):
        path = self.lock["archive_prefix"] + "operator-build-receipt-20261026.json"
        receipt = json.loads(self.data[path])
        receipt["live_binary_sha256"] = "0" * 64
        raw = json.dumps(receipt).encode()
        lock = copy.deepcopy(self.lock)
        lock["evidence_files"][path] = P.digest(raw)
        with self.assertRaisesRegex(ValueError, "LIVE_BINARY_MISMATCH"):
            self.check({path: raw}, lock)

    def test_manifest_duplicate_and_traversal_rejected(self):
        for raw in (b"0" * 64 + b"  ../outside\n", (b"0" * 64 + b"  duplicate.go\n") * 2):
            with self.assertRaises(ValueError):
                P.parse_manifest(raw)


if __name__ == "__main__":
    unittest.main()
