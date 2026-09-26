"""Explicit, disposable PostgreSQL rehearsal; never autodetect a production DB.

Run only in the task-owned network-isolated PostgreSQL container namespace.
The exact sentinel URL/database is required and ordinary pytest skips it.
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event, insert, select
from sqlalchemy.exc import OperationalError

from deerflow.persistence.agents.drafts import DraftConflict
from deerflow.persistence.agents.inventory import capture_sql
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import SqlAgentStore
from deerflow.persistence.base import Base

URL = "postgresql+psycopg://postgres@127.0.0.1:5432/employee_release_test"
pytestmark = pytest.mark.skipif(os.getenv("EMPLOYEE_DISPOSABLE_PG_TEST") != "isolated-test-container", reason="requires disposable PostgreSQL test namespace")


@pytest.fixture
def postgres():
    engine = create_engine(URL, connect_args={"connect_timeout": 3})
    # An existing table is not ours: never drop/reuse it without its fixture.
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT to_regclass('public.agents')").scalar() is None
    Base.metadata.create_all(engine, tables=[AgentRow.__table__])
    try:
        with engine.begin() as conn:
            conn.execute(insert(AgentRow), {"id": "preserve-id", "user_id": "alice", "name": "employee", "config": {"future_binding": {"keep": [1, 2]}, "skills": []}, "soul": "original"})
        yield engine
    finally:
        Base.metadata.drop_all(engine, tables=[AgentRow.__table__])
        engine.dispose()


def test_postgres_inventory_is_read_only_and_has_bounded_lock_wait(postgres):
    statements = []
    event.listen(postgres, "before_cursor_execute", lambda conn, cur, stmt, params, context, many: statements.append(stmt))
    before = capture_sql(postgres)
    assert before["employee_count"] == 1
    assert "SET TRANSACTION READ ONLY" in statements
    assert "SET LOCAL statement_timeout = '10s'" in statements
    assert "SET LOCAL lock_timeout = '1s'" in statements
    assert all(s.startswith(("SELECT", "SET")) for s in statements)
    with postgres.connect() as blocker:
        transaction = blocker.begin()
        blocker.exec_driver_sql("LOCK TABLE agents IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        try:
            with pytest.raises(OperationalError):
                capture_sql(postgres)
            assert time.monotonic() - started < 5
        finally:
            transaction.rollback()
    assert capture_sql(postgres) == before


def test_postgres_draft_cas_preserves_row_and_unknown_fields(postgres):
    store = SqlAgentStore(URL)
    before = store.read_definition("employee", user_id="alice")

    def edit(text):
        try:
            store.patch_definition("employee", {"description": text}, text, user_id="alice", expected_revision=before.revision)
            return "saved"
        except DraftConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(edit, ("first", "second"))) == ["conflict", "saved"]
    with postgres.connect() as conn:
        row = conn.execute(select(AgentRow.__table__)).mappings().one()
    assert row["id"] == "preserve-id" and row["user_id"] == "alice"
    assert row["config"]["future_binding"] == {"keep": [1, 2]}
    assert row["config"]["description"] == row["soul"]
