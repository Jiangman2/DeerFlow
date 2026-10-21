"""Exercise this revision's DDL in a disposable DB, not production migration."""

import importlib.util
import sys
import unittest
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages/harness"))

from deerflow.persistence.ppt_confirmation.model import ConfirmationGateRow, ConfirmationTaskRow

SOURCE = Path(__file__).resolve().parents[1] / "packages/harness/deerflow/persistence/migrations/versions/0020_ppt_confirmation.py"


class ConfirmationMigrationTests(unittest.TestCase):
    def load_revision(self):
        spec = importlib.util.spec_from_file_location("ppt_confirmation_revision_test", SOURCE)
        revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(revision)
        return revision

    def test_revision_shape_matches_orm_and_leaves_unrelated_tables_untouched(self):
        revision = self.load_revision()
        self.assertEqual(revision.down_revision, "0019_digital_employee_agents")
        self.assertEqual(revision.revision, "0020_ppt_confirmation")
        engine = sa.create_engine("sqlite://")
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("CREATE TABLE test_existing_runs (value TEXT)")
                conn.exec_driver_sql("INSERT INTO test_existing_runs VALUES ('preserved')")
                with Operations.context(MigrationContext.configure(conn)):
                    revision.upgrade()
                inspector = sa.inspect(conn)
                for model in (ConfirmationTaskRow, ConfirmationGateRow):
                    actual = {column["name"]: column for column in inspector.get_columns(model.__tablename__)}
                    self.assertEqual(set(actual), set(model.__table__.columns.keys()))
                    for column in model.__table__.columns:
                        self.assertEqual(actual[column.name]["nullable"], column.nullable)
                    indexes = {index["name"]: index["column_names"] for index in inspector.get_indexes(model.__tablename__)}
                    self.assertEqual(indexes, {index.name: [c.name for c in index.columns] for index in model.__table__.indexes})
                self.assertEqual(conn.exec_driver_sql("SELECT value FROM test_existing_runs").scalar_one(), "preserved")
                with Operations.context(MigrationContext.configure(conn)):
                    revision.downgrade()
                self.assertEqual(sa.inspect(conn).get_table_names(), ["test_existing_runs"])
        finally:
            engine.dispose()

    def test_registration_source_imports_both_models(self):
        source = SOURCE.parents[2] / "models/__init__.py"
        text = source.read_text(encoding="utf-8")
        self.assertIn("ConfirmationTaskRow", text)
        self.assertIn("ConfirmationGateRow", text)


if __name__ == "__main__":
    unittest.main()
