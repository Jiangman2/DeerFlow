#!/usr/bin/env python3
from __future__ import print_function

import importlib.util
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "restore-agent-categories.py"
SPEC = importlib.util.spec_from_file_location("restore_agent_categories", str(SCRIPT))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RestoreAgentCategoriesTests(unittest.TestCase):
    def test_build_snapshot_preserves_absence_null_and_value(self):
        rows = [
            types.SimpleNamespace(id="b", config={"category_id": None}),
            types.SimpleNamespace(id="a", config={"keep": 1}),
            types.SimpleNamespace(id="c", config={"category_id": "finance-capital"}),
        ]
        snapshot = MODULE.build_snapshot(rows)
        self.assertEqual([item["id"] for item in snapshot["rows"]], ["a", "b", "c"])
        self.assertEqual(snapshot["rows"][0], {"id": "a", "has_category": False})
        self.assertIsNone(snapshot["rows"][1]["category"])
        self.assertEqual(snapshot["rows"][2]["category"], "finance-capital")

    def test_restore_preserves_unrelated_fields_and_extra_rows(self):
        snapshot = {
            "version": 1,
            "rows": [
                {"id": "a", "has_category": False},
                {"id": "b", "has_category": True, "category": None},
                {"id": "c", "has_category": True, "category": "finance-capital"},
            ],
        }
        current = {
            "a": {"category_id": "new", "keep": 1},
            "b": {"category_id": "new", "keep": 2},
            "c": {"category_id": "new", "keep": 3},
            "new-row": {"category_id": "new", "keep": 4},
        }
        restored = MODULE.restore_configurations(current, snapshot)
        self.assertNotIn("category_id", restored["a"])
        self.assertIsNone(restored["b"]["category_id"])
        self.assertEqual(restored["c"]["category_id"], "finance-capital")
        self.assertEqual([restored[key]["keep"] for key in ("a", "b", "c")], [1, 2, 3])
        self.assertEqual(restored["new-row"], current["new-row"])

    def test_missing_snapshotted_row_fails_closed(self):
        snapshot = {"version": 1, "rows": [{"id": "missing", "has_category": False}]}
        with self.assertRaisesRegex(ValueError, "missing"):
            MODULE.restore_configurations({}, snapshot)

    def test_duplicate_snapshot_id_is_rejected(self):
        snapshot = {
            "version": 1,
            "rows": [
                {"id": "a", "has_category": False},
                {"id": "a", "has_category": False},
            ],
        }
        with self.assertRaisesRegex(ValueError, "duplicate"):
            MODULE.validate_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
