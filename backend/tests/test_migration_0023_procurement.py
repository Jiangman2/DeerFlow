"""Upgrade the current enterprise parent without changing existing tables."""

import asyncio

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade, bootstrap_schema


@pytest.mark.asyncio
async def test_procurement_upgrade_and_repeat_startup(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "upgrade.db"))
    try:
        await asyncio.to_thread(_upgrade, _get_alembic_config(engine), "0022_work_integration_state")
        async with engine.connect() as conn:
            before = await conn.run_sync(lambda c: {name: [column["name"] for column in sa.inspect(c).get_columns(name)] for name in sa.inspect(c).get_table_names()})
        await bootstrap_schema(engine, backend="sqlite")
        await bootstrap_schema(engine, backend="sqlite")
        async with engine.connect() as conn:
            after = await conn.run_sync(lambda c: {name: [column["name"] for column in sa.inspect(c).get_columns(name)] for name in sa.inspect(c).get_table_names()})
            assert set(after) - set(before) == {"procurement_review_jobs"}
            assert {key: after[key] for key in before} == before
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0023_procurement_review_jobs"
            indexes = await conn.run_sync(lambda c: {i["name"] for i in sa.inspect(c).get_indexes("procurement_review_jobs")})
            assert "ix_procurement_review_due" in indexes
    finally:
        await engine.dispose()
