"""Regression test for GitHub issue #3682.

End-to-end shape:

1. Hand-build a SQLite DB that mirrors a real pre-#3658 deployment -- the
   ``runs`` table is missing the ``token_usage_by_model`` column, mirroring
   what every existing user's DB looked like after the upgrade that triggered
   the issue.
2. Run ``init_engine`` (the entry point used by the FastAPI Gateway
   lifespan), which now routes through ``bootstrap_schema``.
3. Confirm a real ``SELECT`` against the column succeeds, demonstrating the
   500 from the original issue is gone.

The pre-fix codepath would have raised
``sqlalchemy.exc.OperationalError: no such column: runs.token_usage_by_model``
on step 3.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

import deerflow.persistence.models  # noqa: F401  -- registers ORM models
from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
from deerflow.persistence.run import RunRepository

pytestmark = pytest.mark.asyncio


async def _seed_historical_database(db_path: Path, *, drop_token_column: bool) -> None:
    """Build a DB that looks like a pre-PR-#3658 deployment.

    Use the last pre-confirmation migration's real DDL so today's ORM cannot
    accidentally pre-create the new confirmation tables in a historical DB.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    seed_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        await asyncio.to_thread(_upgrade, _get_alembic_config(seed_engine), "0019_digital_employee_agents")
        async with seed_engine.begin() as conn:
            tables = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert not {"ppt_confirmation_tasks", "ppt_confirmation_gates"} & tables
            await conn.execute(sa.text("DROP TABLE alembic_version"))
            if drop_token_column:
                await conn.execute(sa.text("ALTER TABLE runs DROP COLUMN token_usage_by_model"))
    finally:
        await seed_engine.dispose()


async def test_legacy_database_recovers_token_usage_column(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    await _seed_historical_database(db_path, drop_token_column=True)

    # Sanity: confirm we did indeed land in the buggy pre-fix shape before
    # init_engine touches the file.
    with sqlite3.connect(db_path) as raw:
        cols = {row[1] for row in raw.execute("PRAGMA table_info(runs)").fetchall()}
        assert "run_id" in cols
        assert "token_usage_by_model" not in cols
        version_table_count = raw.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='alembic_version'").fetchone()[0]
        assert version_table_count == 0

    # Run the same init_engine path FastAPI lifespan uses on startup.
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    await init_engine(backend="sqlite", url=url, sqlite_dir=str(tmp_path))

    try:
        # The column must now be present.
        with sqlite3.connect(db_path) as raw:
            cols = {row[1] for row in raw.execute("PRAGMA table_info(runs)").fetchall()}
            assert "token_usage_by_model" in cols
            version_row = raw.execute("SELECT version_num FROM alembic_version").fetchone()
            assert version_row[0] == "0022_work_integration_state"
            tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"ppt_confirmation_tasks", "ppt_confirmation_gates"} <= tables

        # And the read path that originally 500'd must now succeed.
        sf = get_session_factory()
        assert sf is not None
        repo = RunRepository(sf)
        # No rows yet -- the point is just that the SELECT does not raise
        # ``no such column: runs.token_usage_by_model``.
        result = await repo.aggregate_tokens_by_thread(thread_id=str(uuid4()))
        assert result["total_tokens"] == 0
        assert result["by_model"] == {}
    finally:
        await close_engine()


async def test_legacy_database_with_manual_alter_still_bootstraps(tmp_path: Path) -> None:
    """User-side workaround scenario: someone already applied the manual
    ``ALTER TABLE runs ADD COLUMN token_usage_by_model JSON`` from the issue
    write-up. The hybrid bootstrap must just stamp head, not double-add the
    column, and not error.
    """
    db_path = tmp_path / "manual_altered.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Keep the historical token column, modelling the already-applied workaround.
    await _seed_historical_database(db_path, drop_token_column=False)

    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    await init_engine(backend="sqlite", url=url, sqlite_dir=str(tmp_path))
    try:
        with sqlite3.connect(db_path) as raw:
            cols = [row[1] for row in raw.execute("PRAGMA table_info(runs)").fetchall()]
            # No duplicate column -- list, not set, to catch dupes.
            assert cols.count("token_usage_by_model") == 1
            version_row = raw.execute("SELECT version_num FROM alembic_version").fetchone()
            assert version_row[0] == "0022_work_integration_state"
            tables = {row[0] for row in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"ppt_confirmation_tasks", "ppt_confirmation_gates"} <= tables
    finally:
        await close_engine()
