"""Production Work upgrades existing databases without changing channel bindings."""

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade, bootstrap_schema


@pytest.mark.asyncio
async def test_work_migration_from_previous_release_and_idempotent_startup(tmp_path):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "upgrade.db")
    engine = create_async_engine(url)
    config = _get_alembic_config(engine)
    import asyncio

    await asyncio.to_thread(_upgrade, config, "0019_digital_employee_agents")
    try:
        await bootstrap_schema(engine, backend="sqlite")
        await bootstrap_schema(engine, backend="sqlite")
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
            assert "work_integration_records" in names and "channel_connections" in names
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0020_work_integration_state"
    finally:
        await engine.dispose()
