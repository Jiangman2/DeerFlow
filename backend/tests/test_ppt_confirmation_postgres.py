"""Opt-in disposable Postgres parity for the complete consent repository suite.

Only a loopback port is accepted, with a fixed TEST-only role/database. Never
set DATABASE_URL here. Each case creates/drops its own random test schema.
"""

import importlib.util
import os
import re
import unittest
import uuid
from pathlib import Path

import test_ppt_confirmation_repository as repository_tests
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

_PORT = os.environ.get("PPT_CONFIRMATION_TEST_POSTGRES_PORT", "")


@unittest.skipUnless(_PORT, "requires a disposable loopback Postgres test container")
class ConfirmationPostgresTests(repository_tests.ConfirmationRepositoryTests):
    async def test_additive_revision_executes_on_postgres(self):
        source = Path(__file__).resolve().parents[1] / "packages/harness/deerflow/persistence/migrations/versions/0020_ppt_confirmation.py"
        spec = importlib.util.spec_from_file_location("ppt_confirmation_pg_revision_test", source)
        revision = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(revision)

        def check(connection):
            repository_tests.ConfirmationTaskRow.metadata.drop_all(connection, tables=[repository_tests.ConfirmationGateRow.__table__, repository_tests.ConfirmationTaskRow.__table__])
            connection.exec_driver_sql("CREATE TABLE test_existing_runs (value TEXT)")
            connection.exec_driver_sql("INSERT INTO test_existing_runs VALUES ('preserved')")
            with Operations.context(MigrationContext.configure(connection)):
                revision.upgrade()
                revision.downgrade()
            self.assertEqual(connection.exec_driver_sql("SELECT value FROM test_existing_runs").scalar_one(), "preserved")

        async with self.engine.begin() as conn:
            await conn.run_sync(check)

    async def asyncSetUp(self):
        if not re.fullmatch(r"[0-9]{4,5}", _PORT) or not 1024 <= int(_PORT) <= 65535:
            raise RuntimeError("invalid_test_port")
        self.schema = "ppt_test_" + uuid.uuid4().hex
        self.engine = create_async_engine(
            f"postgresql+asyncpg://ppt_test:ppt-local-disposable@127.0.0.1:{_PORT}/ppt_confirmation_test",
            connect_args={"server_settings": {"search_path": self.schema, "statement_timeout": "10000"}},
            pool_size=4,
            max_overflow=8,
        )
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
            await conn.run_sync(lambda db: repository_tests.ConfirmationTaskRow.metadata.create_all(db, tables=[repository_tests.ConfirmationTaskRow.__table__, repository_tests.ConfirmationGateRow.__table__]))
        self.now = 1000
        self.repo = repository_tests.ConfirmationRepository(self.factory, clock=lambda: self.now)
        self.scope = repository_tests.Scope("owner-1", "thread-1", "task-1")

    async def asyncTearDown(self):
        try:
            if not re.fullmatch(r"ppt_test_[a-f0-9]{32}", self.schema):
                raise RuntimeError("invalid_test_schema")
            async with self.engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        finally:
            await self.engine.dispose()


if __name__ == "__main__":
    unittest.main()
