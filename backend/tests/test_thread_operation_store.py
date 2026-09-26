"""Upstream #4437 storage prerequisite, retaining enterprise cancellation/owners."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from deerflow.persistence.run.model import RunRow
from deerflow.persistence.run.sql import RunRepository
from deerflow.runtime.runs.manager import ConflictError
from deerflow.runtime.runs.schemas import ThreadOperationKind
from deerflow.runtime.runs.store.base import RunStore
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import reset_current_user, set_current_user


@pytest.fixture
def anyio_backend():
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture(params=["memory", "sqlite", "postgres"])
async def store(request, tmp_path, anyio_backend):
    if request.param == "memory":
        yield MemoryRunStore()
        return
    schema = None
    admin = None
    if request.param == "postgres":
        uri = os.getenv("TEST_POSTGRES_URI")
        if not uri:
            pytest.skip("requires disposable loopback TEST_POSTGRES_URI")
        url = make_url(uri)
        assert url.host == "127.0.0.1" and url.database == "deerflow_v21_test"
        schema = "v21_ops_" + uuid.uuid4().hex
        admin = create_async_engine(uri)
        async with admin.begin() as conn:
            await conn.execute(CreateSchema(schema))
        engine = create_async_engine(uri, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "operations.db"))
    try:
        async with engine.begin() as conn:
            await conn.run_sync(RunRow.__table__.create)
        yield RunRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
        if admin is not None:
            try:
                assert schema.startswith("v21_ops_")
                async with admin.begin() as conn:
                    await conn.execute(DropSchema(schema, cascade=True))
            finally:
                await admin.dispose()


async def admit(store, run_id, *, kind="run", owner="worker-a", strategy="reject", lease=None):
    return await store.create_thread_operation_atomic(
        run_id,
        thread_id="thread",
        owner_worker_id=owner,
        lease_expires_at=lease,
        operation_kind=kind,
        multitask_strategy=strategy,
        user_id="alice",
    )


@pytest.mark.anyio
@pytest.mark.parametrize("first_kind", ["run", "checkpoint_write"])
async def test_run_and_checkpoint_share_atomic_uniqueness(store, first_kind):
    await admit(store, "first", kind=first_kind)
    other = "run" if first_kind == "checkpoint_write" else "checkpoint_write"
    with pytest.raises((ConflictError, IntegrityError)):
        await admit(store, "second", kind=other, owner="worker-b")
    assert (await store.get("first", user_id="alice"))["operation_kind"] == first_kind
    assert await store.get("second", user_id="alice") is None


@pytest.mark.anyio
@pytest.mark.parametrize("owner", ["worker-a", "worker-b"])
@pytest.mark.parametrize("strategy", ["interrupt", "rollback"])
@pytest.mark.parametrize("live_lease", [False, True])
async def test_active_checkpoint_cannot_be_displaced(store, owner, strategy, live_lease):
    lease = (datetime.now(UTC) + timedelta(minutes=5)).isoformat() if live_lease else None
    await admit(store, "write", kind="checkpoint_write", lease=lease)
    with pytest.raises(ConflictError):
        await admit(store, "new", owner=owner, strategy=strategy)
    assert (await store.get("write", user_id="alice"))["status"] == "pending"
    assert await store.get("new", user_id="alice") is None


@pytest.mark.anyio
async def test_expired_checkpoint_is_reclaimable_without_deleting_evidence(store):
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await admit(store, "write", kind="checkpoint_write", lease=past)
    run, claimed = await admit(store, "new", owner="worker-b", strategy="interrupt")
    assert run["operation_kind"] == "run"
    assert [row["run_id"] for row in claimed] == ["write"]
    old = await store.get("write", user_id="alice")
    assert old["status"] == "interrupted" and old["operation_kind"] == "checkpoint_write"


@pytest.mark.anyio
async def test_internal_rows_are_hidden_from_history_tokens_regeneration_and_pending(store):
    await store.put("write", thread_id="thread", user_id="alice", operation_kind="checkpoint_write", status="success", metadata={"regenerate_from_run_id": "private"})
    await store.put("run", thread_id="thread", user_id="alice", status="success", metadata={"regenerate_from_run_id": "original"})
    await store.put("bob", thread_id="thread", user_id="bob", status="success")
    await store.put("pending", thread_id="thread", user_id="alice", operation_kind="checkpoint_write")
    await store.update_run_completion("write", status="success", total_tokens=999)
    await store.update_run_completion("run", status="success", total_tokens=7)
    await store.update_run_completion("bob", status="success", total_tokens=321)
    assert [row["run_id"] for row in await store.list_by_thread("thread", user_id="alice")] == ["run"]
    assert await store.list_successful_regenerate_sources("thread", user_id="alice") == {"original"}
    assert set(await store.get_many_by_thread("thread", {"run", "write", "bob"}, user_id="alice")) == {"run"}
    totals = await store.aggregate_tokens_by_thread("thread", include_active=True, user_id="alice")
    assert totals["total_runs"] == 1 and totals["total_tokens"] == 7
    assert await store.list_pending() == []
    assert [row["run_id"] for row in await store.list_inflight()] == ["pending"]


@pytest.mark.anyio
async def test_release_is_scoped_to_captured_owner_and_internal_kind(store):
    await admit(store, "write", kind="checkpoint_write")
    await store.delete_thread_operation("write", user_id="bob")
    await store.delete_thread_operation("write", user_id=None)
    assert await store.get("write", user_id="alice") is not None
    token = set_current_user(SimpleNamespace(id="bob"))
    try:
        await store.delete_thread_operation("write", user_id="alice")
    finally:
        reset_current_user(token)
    assert await store.get("write", user_id="alice") is None
    await admit(store, "normal")
    await store.delete_thread_operation("normal", user_id="alice")
    assert await store.get("normal", user_id="alice") is not None


@pytest.mark.anyio
async def test_competing_admissions_produce_one_winner(store):
    results = await asyncio.gather(admit(store, "run"), admit(store, "write", kind="checkpoint_write", owner="worker-b"), return_exceptions=True)
    assert sum(isinstance(result, tuple) for result in results) == 1
    assert sum(isinstance(result, (ConflictError, IntegrityError)) for result in results) == 1
    assert len(await store.list_inflight()) == 1


@pytest.mark.anyio
async def test_legacy_store_interface_is_normal_run_only():
    class LegacyStore(MemoryRunStore):
        create_thread_operation_atomic = RunStore.create_thread_operation_atomic

        async def create_run_atomic(self, *args, **kwargs):
            return {"run_id": args[0]}, []

    store = LegacyStore()
    row, _ = await admit(store, "legacy")
    assert row["run_id"] == "legacy"
    with pytest.raises(NotImplementedError, match="cannot create non-run"):
        await admit(store, "write", kind=ThreadOperationKind.checkpoint_write)
