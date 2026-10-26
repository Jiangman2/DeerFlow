"""Production Work upgrades existing databases without changing channel bindings."""

import asyncio
import os

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade, bootstrap_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_work_migration_from_previous_release_and_idempotent_startup(tmp_path, backend):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "upgrade.db") if backend == "sqlite" else os.getenv("WORK_MIGRATION_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("isolated PostgreSQL migration database not configured")
    engine = create_async_engine(url)
    config = _get_alembic_config(engine)
    await asyncio.to_thread(_upgrade, config, "0021_oa_result_sets")
    try:
        async with engine.begin() as conn:
            before = await conn.run_sync(lambda c: {name: [column["name"] for column in sa.inspect(c).get_columns(name)] for name in sa.inspect(c).get_table_names()})
            await conn.execute(
                sa.text("""
                INSERT INTO users (id,email,password_hash,system_role,created_at,
                    oauth_provider,oauth_id,needs_setup,is_disabled,token_version)
                VALUES ('work-migration-synthetic','work-migration@example.test',NULL,
                    'user',CURRENT_TIMESTAMP,'cnooc','WORK-SYNTHETIC',false,false,0)
            """)
            )
        await bootstrap_schema(engine, backend=backend)
        await bootstrap_schema(engine, backend=backend)
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
            assert set(names) == set(before) | {"work_integration_records"}
            after = await conn.run_sync(lambda c: {name: [column["name"] for column in sa.inspect(c).get_columns(name)] for name in before})
            assert after == before
            assert (await conn.execute(sa.text("SELECT email FROM users WHERE id='work-migration-synthetic'"))).scalar_one() == "work-migration@example.test"
            indexes = await conn.run_sync(lambda c: {index["name"] for index in sa.inspect(c).get_indexes("work_integration_records")})
            assert indexes == {"ix_work_records_expiry", "ix_work_records_namespace_expiry"}
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0022_work_integration_state"
    finally:
        await engine.dispose()
