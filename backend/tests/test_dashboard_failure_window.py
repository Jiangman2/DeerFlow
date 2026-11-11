"""Execute the real statement builder against a small isolated SQL schema."""

import ast
import unittest
from pathlib import Path

from sqlalchemy import Column, MetaData, String, Table, create_engine
from sqlalchemy.orm import registry


class FailureWindowTests(unittest.TestCase):
    def setUp(self):
        metadata = MetaData()
        fields = {
            "RunRow": ("runs", ["run_id", "thread_id", "user_id", "status", "error", "assistant_id", "model_name", "created_at", "updated_at"]),
            "UserRow": ("users", ["id", "email", "display_name"]),
            "ThreadMetaRow": ("threads_meta", ["thread_id", "user_id"]),
            "RunEventRow": ("run_events", ["id", "thread_id", "category"]),
            "OperationDispositionRow": ("operation_dispositions", ["id", "run_id", "status", "action", "created_at"]),
        }
        self.tables = {key: Table(name, metadata, *[Column(c, String, primary_key=(i == 0)) for i, c in enumerate(cols)]) for key, (name, cols) in fields.items()}
        mappings = registry()
        self.addCleanup(mappings.dispose)
        scope = {}
        for key, table in self.tables.items():
            scope[key] = type(key, (), {})
            mappings.map_imperatively(scope[key], table)
        path = Path(__file__).resolve().parents[1] / "app/gateway/dashboard_failures.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [n for n in tree.body if not (isinstance(n, ast.ImportFrom) and (n.module or "").startswith("deerflow."))]
        exec(compile(tree, str(path), "exec"), scope)
        self.statements = scope["failed_run_statements"]
        self.engine = create_engine("sqlite://")
        metadata.create_all(self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as conn:
            conn.execute(self.tables["UserRow"].insert(), [dict(id="new", email="new@example", display_name="新"), dict(id="old", email="old@example", display_name="旧")])
            conn.execute(self.tables["ThreadMetaRow"].insert(), [dict(thread_id="thread", user_id="old")])
            conn.execute(
                self.tables["RunRow"].insert(),
                [dict(run_id=f"r{i:04}", thread_id="thread", user_id="old" if i < 100 else "new", status="error", error="x" * 5000, assistant_id="a", model_name="m", created_at=f"{i:04}", updated_at=f"{i:04}") for i in range(1100)],
            )

    def query(self, q="", limit=5, offset=0):
        count, rows = self.statements(q, limit, offset)
        with self.engine.connect() as conn:
            return conn.scalar(count), conn.execute(rows).mappings().all()

    def test_count_and_page_are_bounded_to_latest_thousand(self):
        total, rows = self.query()
        self.assertEqual(total, 1000)
        self.assertEqual([r["run_id"] for r in rows], [f"r{i}" for i in range(1099, 1094, -1)])
        self.assertTrue(all(len(r["error"]) == 4096 and r["error_truncated"] for r in rows))
        self.assertEqual([r["run_id"] for r in self.query(offset=995)[1]], [f"r{i:04}" for i in range(104, 99, -1)])
        self.assertEqual(self.query(offset=1000), (1000, []))

    def test_identity_filter_cannot_expand_window(self):
        self.assertEqual(self.query("old@"), (0, []))
        self.assertEqual(self.query("NEW@")[0], 1000)
        with self.engine.begin() as conn:
            conn.execute(self.tables["RunRow"].update().where(self.tables["RunRow"].c.run_id == "r1099").values(user_id=None))
        total, rows = self.query("old@")
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["run_id"], "r1099")
        self.assertEqual(rows[0]["user_id"], "old")

    def test_successes_do_not_consume_failure_window_and_ties_are_stable(self):
        runs = self.tables["RunRow"]
        with self.engine.begin() as conn:
            conn.execute(runs.update().where(runs.c.run_id.in_(["r1099", "r1098"])).values(updated_at="9999", created_at="9999"))
            conn.execute(runs.insert(), dict(run_id="success", thread_id="thread", user_id="new", status="success", error=None, created_at="zzzz", updated_at="zzzz"))
        self.assertEqual([r["run_id"] for r in self.query()[1]][:2], ["r1099", "r1098"])
        self.assertEqual(self.query()[0], 1000)

    def test_cte_selects_only_narrow_identity_columns(self):
        count, _ = self.statements("", 5, 0)
        sql = str(count.compile(compile_kwargs={"literal_binds": True})).lower()
        self.assertIn("limit 1000", sql)
        self.assertNotIn("length(", sql)
        self.assertNotIn("substr(", sql)
        self.assertNotIn(" join ", sql)


if __name__ == "__main__":
    unittest.main()
