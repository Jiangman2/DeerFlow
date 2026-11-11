"""Migration ``0024_im_s08_archive`` is additive and reversible.

The migration creates the three S08 tables and one read-path index. It must
never touch ``im_session_pointers`` rows or columns: the routing pointer is the
live "current thread" fact, and a schema migration is not allowed to disturb it.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade

NEW_TABLES = {"im_user_bindings", "im_group_catalog", "im_thread_registry"}
POINTER_COLUMNS = {
    "id",
    "provider",
    "connection_id",
    "owner_user_id",
    "channel_type",
    "external_conversation_id",
    "external_topic_id",
    "thread_id",
    "generation",
    "created_at",
    "updated_at",
}
POINTER_INDEX = "ix_im_session_pointers_owner_scope"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_upgrade_creates_tables_and_indexes(tmp_path, backend):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "s08_upgrade.db") if backend == "sqlite" else os.getenv("S08_MIGRATION_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("isolated PostgreSQL migration database not configured")
    engine = create_async_engine(url)
    cfg = _get_alembic_config(engine)
    try:
        await asyncio.to_thread(_upgrade, cfg, "0023_im_session_routing")
        await asyncio.to_thread(_upgrade, cfg, "head")

        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert NEW_TABLES <= names
            indexes = await conn.run_sync(lambda c: {name: {index["name"] for index in sa.inspect(c).get_indexes(name)} for name in NEW_TABLES})
            assert indexes["im_thread_registry"] == {
                "ix_im_thread_registry_owner_scope",
                "ix_im_thread_registry_owner_active",
                "ix_im_thread_registry_scope",
            }
            assert "uq_im_group_catalog_scope" in (await conn.run_sync(lambda c: {uc["name"] for uc in sa.inspect(c).get_unique_constraints("im_group_catalog")}))
            binding_indexes = await conn.run_sync(lambda c: {index["name"]: index["unique"] for index in sa.inspect(c).get_indexes("im_user_bindings")})
            assert bool(binding_indexes["uq_im_user_binding_active"]) is True
            pointer_indexes = await conn.run_sync(lambda c: {index["name"] for index in sa.inspect(c).get_indexes("im_session_pointers")})
            assert POINTER_INDEX in pointer_indexes
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0024_im_s08_archive"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_upgrade_is_idempotent(tmp_path, backend):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "s08_idempotent.db") if backend == "sqlite" else os.getenv("S08_MIGRATION_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("isolated PostgreSQL migration database not configured")
    engine = create_async_engine(url)
    cfg = _get_alembic_config(engine)
    try:
        await asyncio.to_thread(_upgrade, cfg, "head")
        # Re-running both the whole chain and the single revision must be a no-op.
        await asyncio.to_thread(_upgrade, cfg, "head")
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert NEW_TABLES <= names
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0024_im_s08_archive"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_downgrade_drops_only_new_tables(tmp_path, backend):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "s08_downgrade.db") if backend == "sqlite" else os.getenv("S08_MIGRATION_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("isolated PostgreSQL migration database not configured")
    engine = create_async_engine(url)
    cfg = _get_alembic_config(engine)
    try:
        await asyncio.to_thread(_upgrade, cfg, "head")
        await asyncio.to_thread(alembic_command.downgrade, cfg, "-1")

        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert not (NEW_TABLES & names)
            # The routing tables and their data model stay exactly as they were.
            assert {"im_session_pointers", "im_message_links", "im_event_inbox"} <= names
            assert (await conn.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one() == "0023_im_session_routing"

        # A re-upgrade brings the S08 tables back.
        await asyncio.to_thread(_upgrade, cfg, "head")
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda c: set(sa.inspect(c).get_table_names()))
            assert NEW_TABLES <= names
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pointer_index_exists_and_pointer_rows_survive_downgrade(tmp_path, backend):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "s08_pointer.db") if backend == "sqlite" else os.getenv("S08_MIGRATION_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("isolated PostgreSQL migration database not configured")
    engine = create_async_engine(url)
    cfg = _get_alembic_config(engine)
    try:
        await asyncio.to_thread(_upgrade, cfg, "0023_im_session_routing")
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    """
                    INSERT INTO im_session_pointers
                        (id, provider, connection_id, owner_user_id, channel_type,
                         external_conversation_id, external_topic_id, thread_id, generation,
                         created_at, updated_at)
                    VALUES
                        ('ptr-1', 'feishu', 'conn-1', 'u_alice', 'group',
                         'oc_group_x', '', 'thread-1', 3,
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                ),
            )
            before_columns = await conn.run_sync(lambda c: {column["name"] for column in sa.inspect(c).get_columns("im_session_pointers")})

        await asyncio.to_thread(_upgrade, cfg, "head")
        await asyncio.to_thread(alembic_command.downgrade, cfg, "-1")

        async with engine.connect() as conn:
            after_columns = await conn.run_sync(lambda c: {column["name"] for column in sa.inspect(c).get_columns("im_session_pointers")})
            assert after_columns == before_columns == POINTER_COLUMNS
            row = (await conn.execute(sa.text("SELECT thread_id, generation FROM im_session_pointers WHERE id='ptr-1'"))).one()
            assert tuple(row) == ("thread-1", 3)
            pointer_indexes = await conn.run_sync(lambda c: {index["name"] for index in sa.inspect(c).get_indexes("im_session_pointers")})
            # The S08-owned read index is removed by downgrade; the pointer table
            # and its rows are not.
            assert POINTER_INDEX not in pointer_indexes
    finally:
        await engine.dispose()