"""Real local PostgreSQL: ORM, Alembic, employees and LangGraph use one schema.

Only a disposable loopback test database is accepted. No production URL, mounted
volume or public-schema drop is supported. The caller removes its test container.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import DropSchema

from deerflow.config.checkpointer_config import CheckpointerConfig
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.agents import make_agent_store
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import _engines, _engines_lock
from deerflow.persistence.bootstrap import _get_alembic_config, _upgrade
from deerflow.persistence.engine import close_engine, get_engine, init_engine_from_config
from deerflow.runtime.checkpointer.async_provider import make_checkpointer
from deerflow.runtime.checkpointer.provider import _resolve_checkpointer_config, _sync_checkpointer_cm
from deerflow.runtime.store.async_provider import make_store
from deerflow.runtime.store.provider import _resolve_store_config, _sync_store_cm

POSTGRES_URL = os.getenv("TEST_POSTGRES_URI")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="requires disposable loopback TEST_POSTGRES_URI")


@pytest.fixture
def anyio_backend():
    # psycopg cannot use Windows' default Proactor event loop.
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


def database(schema):
    url = make_url(POSTGRES_URL)
    assert url.host == "127.0.0.1" and url.database == "deerflow_v21_test", "disposable local DB required"
    return DatabaseConfig(backend="postgres", postgres_url=POSTGRES_URL, postgres_schema=schema)


def sync_roundtrip(app, key, checkpoint_config):
    with _sync_checkpointer_cm(_resolve_checkpointer_config(app)) as saver:
        assert saver.get(checkpoint_config)["channel_values"] == {"answer": "中文检查点"}
        saver.put(checkpoint_config, empty_checkpoint(), {"source": "update", "step": 1, "parents": {}}, {})
    with _sync_store_cm(_resolve_store_config(app)) as store:
        assert store.get((key,), "record").value == {"source": "async"}
        store.put((key,), "record", {"source": "sync"})


def employee_roundtrip(app, key):
    store = make_agent_store(app)
    store.create(key, {"description": "原有员工", "future_extension": {"keep": True}}, "中文职责", user_id="owner_a")
    store.create(key, {"description": "另一个用户"}, "独立职责", user_id="owner_b")
    assert store.get(key, user_id="owner_a", allow_shared=False).description == "原有员工"
    assert store.get_soul(key, user_id="owner_b", allow_shared=False) == "独立职责"
    with pytest.raises(FileNotFoundError):
        store.get(key, user_id="outsider", allow_shared=False)


async def cleanup(config):
    await close_engine()
    with _engines_lock:
        engine = _engines.pop(config.app_sync_sqlalchemy_url, None)
    if engine is not None:
        await asyncio.to_thread(engine.dispose)
    if config.postgres_schema:
        assert config.postgres_schema.startswith("v21_test_")
        admin = create_async_engine(config.app_sqlalchemy_url)
        try:
            async with admin.begin() as conn:
                await conn.execute(DropSchema(config.postgres_schema, cascade=True, if_exists=True))
        finally:
            await admin.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("custom", [False, True], ids=["existing-public", "custom-schema"])
@pytest.mark.parametrize("legacy", [False, True], ids=["unified", "legacy-checkpointer"])
async def test_full_persistence_roundtrip_preserves_existing_records(custom, legacy):
    schema = "v21_test_" + uuid.uuid4().hex if custom else ""
    db = database(schema)
    key = "employee_" + uuid.uuid4().hex
    app = SimpleNamespace(database=db, checkpointer=None, agent_storage=SimpleNamespace(backend="db"))
    if legacy:
        app.checkpointer = CheckpointerConfig(type="postgres", connection_string=db.postgres_url, postgres_schema=schema)
    expected_schema = schema or "public"
    try:
        await init_engine_from_config(db)
        engine = get_engine()
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT current_schema()"))).scalar() == expected_schema
            assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() == "0021_thread_operation_kind"
        await asyncio.to_thread(employee_roundtrip, app, key)
        async with make_checkpointer(app) as saver, make_store(app) as store:
            checkpoint = empty_checkpoint()
            checkpoint["channel_values"] = {"answer": "中文检查点"}
            checkpoint["channel_versions"] = {"answer": "1"}
            saved = await saver.aput({"configurable": {"thread_id": key, "checkpoint_ns": ""}}, checkpoint, {"source": "input", "step": 0, "parents": {}}, {"answer": "1"})
            await store.aput((key,), "record", {"source": "async"})
            await asyncio.to_thread(sync_roundtrip, app, key, saved)
            assert (await store.aget((key,), "record")).value == {"source": "sync"}

        async with engine.connect() as conn:
            before = (await conn.execute(select(AgentRow.__table__).where(AgentRow.name == key).order_by(AgentRow.user_id))).mappings().all()
            assert len(before) == 2
            assert before[0]["config"]["future_extension"] == {"keep": True}
        await close_engine()
        # A restart uses versioned bootstrap and its independently-created
        # Alembic connection. Neither legacy public data nor custom data moves.
        await init_engine_from_config(db)
        async with get_engine().connect() as conn:
            after = (await conn.execute(select(AgentRow.__table__).where(AgentRow.name == key).order_by(AgentRow.user_id))).mappings().all()
            assert after == before
            tables = set((await conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema=:s"), {"s": expected_schema})).scalars())
            assert {"alembic_version", "agents", "runs", "threads_meta", "checkpoints", "store"} <= tables
            if custom and (await conn.execute(text("SELECT to_regclass('public.agents')"))).scalar() is not None:
                assert (await conn.execute(text("SELECT count(*) FROM public.agents WHERE name=:key"), {"key": key})).scalar() == 0
    finally:
        await cleanup(db)


@pytest.mark.anyio
async def test_custom_schema_real_upgrade_from_enterprise_0019_preserves_employee():
    from alembic import command

    db = database("v21_test_" + uuid.uuid4().hex)
    app = SimpleNamespace(database=db, checkpointer=None, agent_storage=SimpleNamespace(backend="db"))
    key = "employee_" + uuid.uuid4().hex
    try:
        await init_engine_from_config(db)
        engine = get_engine()
        cfg = _get_alembic_config(engine, postgres_schema=db.postgres_schema)
        # Throwaway schema ONLY, to simulate the real pre-cancellation baseline.
        await asyncio.to_thread(command.downgrade, cfg, "0019_digital_employee_agents")
        await asyncio.to_thread(employee_roundtrip, app, key)
        async with engine.connect() as conn:
            before = (await conn.execute(select(AgentRow.__table__).where(AgentRow.name == key))).mappings().all()
        await asyncio.to_thread(_upgrade, cfg, "head")
        await asyncio.to_thread(_upgrade, cfg, "head")
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() == "0021_thread_operation_kind"
            after = (await conn.execute(select(AgentRow.__table__).where(AgentRow.name == key))).mappings().all()
            assert after == before
            columns = set((await conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema=:s AND table_name='runs'"), {"s": db.postgres_schema})).scalars())
            assert {"cancel_requested_at", "cancel_action", "operation_kind"} <= columns
    finally:
        await cleanup(db)


@pytest.mark.anyio
async def test_schema_permission_denied_never_falls_back_to_public():
    from sqlalchemy.exc import DBAPIError

    role = "v21_role_" + uuid.uuid4().hex
    db = database("v21_test_" + uuid.uuid4().hex)
    admin = create_async_engine(db.app_sqlalchemy_url)
    try:
        # A disposable LOGIN with no database/schema creation rights. Do not
        # grant application users broader privileges as a schema-error fallback.
        async with admin.begin() as conn:
            await conn.execute(text(f"CREATE ROLE {role} LOGIN PASSWORD 'local-test-only' NOSUPERUSER NOCREATEDB NOCREATEROLE"))
        restricted = db.model_copy(update={"postgres_url": make_url(db.postgres_url).set(username=role, password="local-test-only").render_as_string(hide_password=False)})
        with patch("deerflow.persistence.engine._auto_create_postgres_db", new_callable=AsyncMock) as create_db:
            with pytest.raises(DBAPIError, match="permission denied"):
                await init_engine_from_config(restricted)
            create_db.assert_not_awaited()
        async with admin.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM pg_namespace WHERE nspname=:s"), {"s": db.postgres_schema})).scalar() == 0
    finally:
        await close_engine()
        async with admin.begin() as conn:
            await conn.execute(text(f"DROP ROLE IF EXISTS {role}"))
        await admin.dispose()


@pytest.mark.anyio
async def test_real_postgres_timeout_and_recycle_keep_schema_on_reconnect():
    db = database("v21_test_" + uuid.uuid4().hex)
    try:
        await init_engine_from_config(db)
        await close_engine()
        # A single idle slot makes PID reuse deterministic; the default pool
        # may correctly alternate between several existing healthy connections.
        bounded = db.model_copy(update={"command_timeout": 0.2, "pool_recycle": 2, "pool_size": 1})
        await init_engine_from_config(bounded)
        async with get_engine().connect() as conn:
            started = monotonic()
            with pytest.raises(TimeoutError):
                await conn.execute(text("SELECT pg_sleep(2)"))
            assert monotonic() - started < 1.5
            await conn.rollback()
            assert (await conn.execute(text("SELECT current_schema()"))).scalar() == db.postgres_schema
            first_pid = (await conn.execute(text("SELECT pg_backend_pid()"))).scalar()
        async with get_engine().connect() as conn:
            # A timeout-discarded socket alone is not evidence of age recycling.
            assert (await conn.execute(text("SELECT pg_backend_pid()"))).scalar() == first_pid
        await asyncio.sleep(2.1)
        async with get_engine().connect() as conn:
            assert (await conn.execute(text("SELECT pg_backend_pid()"))).scalar() != first_pid
            assert (await conn.execute(text("SELECT current_schema()"))).scalar() == db.postgres_schema
        await close_engine()
        unbounded = db.model_copy(update={"command_timeout": None})
        await init_engine_from_config(unbounded)
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT pg_sleep(0.3)"))
            assert (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar() == "0021_thread_operation_kind"
    finally:
        await cleanup(db)
