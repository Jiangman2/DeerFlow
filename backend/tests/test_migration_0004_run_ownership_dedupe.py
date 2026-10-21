"""Regression test for migration ``0004_run_ownership`` dedupe pass.

End-to-end shape:

1. Build a SQLite DB through the historical migrations to mirror a deployment that ran
   ``GATEWAY_WORKERS>1`` before this PR and accumulated duplicate active rows
   per thread (the exact dirty state the multi-worker ownership fix targets).
2. Upgrade it to ``0003_scheduled_tasks`` so ``bootstrap_schema`` takes the
   versioned branch and runs ``alembic upgrade head``.
3. Insert two+ pending/running rows for the same ``thread_id`` (only possible
   because the partial unique index does not exist yet).
4. Run ``init_engine`` (the FastAPI lifespan entry point), which routes
   through ``bootstrap_schema`` → ``upgrade head`` → ``0004.upgrade()``.
5. Verify the migration cancelled the superseded duplicates (set them to
   ``error`` with an explanatory message), kept the newest active row, and
   successfully built the ``uq_runs_thread_active`` partial unique index.

Pre-fix codepath would have raised ``UNIQUE constraint failed`` (SQLite) /
``could not create unique index`` (Postgres) on step 5, aborting the alembic
upgrade and blocking gateway startup.
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config
from deerflow.persistence.engine import close_engine, init_engine

pytestmark = pytest.mark.asyncio


def _seed_pre_0004_with_duplicates(db_path: Path) -> None:
    """Build a DB at revision 0003 with duplicate active rows per thread.

    Build the actual historical schema through Alembic, rather than creating
    today's ORM tables and stamping an older revision. The latter incorrectly
    pre-created the 0020 confirmation tables before replaying their migration.
    Reflect the historical runs table so current ORM columns/defaults cannot
    silently change this pre-0004 fixture.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = _get_alembic_config(create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}"))
    command.upgrade(config, "0003_scheduled_tasks")
    sync_engine = sa.create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        inspector = sa.inspect(sync_engine)
        assert {"ppt_confirmation_tasks", "ppt_confirmation_gates"}.isdisjoint(inspector.get_table_names())
        assert {"owner_worker_id", "lease_expires_at"}.isdisjoint(column["name"] for column in inspector.get_columns("runs"))
        assert "uq_runs_thread_active" not in {index["name"] for index in inspector.get_indexes("runs")}
        runs = sa.Table("runs", sa.MetaData(), autoload_with=sync_engine)
        base = datetime.now(UTC)
        with sync_engine.begin() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0003_scheduled_tasks"
            conn.execute(
                runs.insert(),
                [
                    {
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "status": status,
                        "multitask_strategy": "reject",
                        "metadata_json": {},
                        "kwargs_json": {},
                        "message_count": 0,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                        "llm_call_count": 0,
                        "lead_agent_tokens": 0,
                        "subagent_tokens": 0,
                        "middleware_tokens": 0,
                        "token_usage_by_model": {},
                        "created_at": base + timedelta(seconds=seconds),
                        "updated_at": base + timedelta(seconds=seconds),
                    }
                    for run_id, thread_id, status, seconds in (
                        ("run-old-a", "thread-dup", "pending", 0),
                        ("run-old-b", "thread-dup", "running", 10),
                        ("run-newest", "thread-dup", "pending", 60),
                        ("run-solo", "thread-solo", "running", 0),
                        ("run-success", "thread-done", "success", 0),
                    )
                ],
            )
            assert conn.execute(sa.text("SELECT COUNT(*) FROM runs WHERE thread_id = 'thread-dup' AND status IN ('pending', 'running')")).scalar_one() == 3
    finally:
        sync_engine.dispose()


def _fetch_runs(db_path: Path) -> dict[str, tuple[str, str | None]]:
    """Map run_id -> (status, error) for assertions."""
    with sqlite3.connect(db_path) as raw:
        rows = raw.execute("SELECT run_id, status, error FROM runs").fetchall()
    return {run_id: (status, error) for run_id, status, error in rows}


def _index_exists(db_path: Path, index_name: str) -> bool:
    with sqlite3.connect(db_path) as raw:
        row = raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
            (index_name,),
        ).fetchone()
    return row is not None


async def test_migration_dedupes_duplicate_active_rows_before_unique_index(tmp_path: Path) -> None:
    db_path = tmp_path / "dirty.db"
    await asyncio.to_thread(_seed_pre_0004_with_duplicates, db_path)

    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    await init_engine(backend="sqlite", url=url, sqlite_dir=str(tmp_path))

    try:
        runs = _fetch_runs(db_path)

        # Newest active row on the duplicated thread survives unchanged.
        assert runs["run-newest"] == ("pending", None)

        # Older duplicate active rows are cancelled with an explanatory error.
        assert runs["run-old-a"][0] == "error"
        assert "uq_runs_thread_active" in (runs["run-old-a"][1] or "")
        assert runs["run-old-b"][0] == "error"
        assert "uq_runs_thread_active" in (runs["run-old-b"][1] or "")

        # Untouched threads: single active row stays active, terminal rows stay terminal.
        assert runs["run-solo"] == ("running", None)
        assert runs["run-success"] == ("success", None)

        # The partial unique index was successfully created — the upgrade did
        # not abort with ``UNIQUE constraint failed``.
        assert _index_exists(db_path, "uq_runs_thread_active")
        assert _index_exists(db_path, "ix_runs_lease")

        with sqlite3.connect(db_path) as raw:
            version_row = raw.execute("SELECT version_num FROM alembic_version").fetchone()
        assert version_row[0] == "0020_ppt_confirmation"

        # Sanity: the invariant the index enforces is now true — at most one
        # active row per thread.
        with sqlite3.connect(db_path) as raw:
            dupes = raw.execute("SELECT thread_id, COUNT(*) FROM runs WHERE status IN ('pending', 'running') GROUP BY thread_id HAVING COUNT(*) > 1").fetchall()
        assert dupes == []
    finally:
        await close_engine()
