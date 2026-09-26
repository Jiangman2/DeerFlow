"""Enterprise HTTP/checkpoint admission using the real shared store boundary."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from test_thread_operation_store import anyio_backend as _anyio_backend
from test_thread_operation_store import store as _store
from test_threads_router import _build_thread_app

from app.gateway.routers import threads
from deerflow.config.run_ownership_config import RunOwnershipConfig
from deerflow.runtime import ConflictError, RunManager, RunStatus, ThreadOperationKind
from deerflow.runtime.user_context import reset_current_user, set_current_user

anyio_backend = _anyio_backend
store = _store


def manager(store, worker):
    return RunManager(store=store, worker_id=worker, run_ownership_config=RunOwnershipConfig(heartbeat_enabled=True))


@pytest.mark.anyio
async def test_http_writers_reject_peer_run_without_touching_checkpoint(store, monkeypatch):
    app, _, checkpointer = _build_thread_app()
    owner = manager(store, "owner")
    app.state.run_manager = manager(store, "writer")
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        response = await client.put("/api/threads/shared/goal", json={"objective": "原目标"})
        assert response.status_code == 200, response.text
        before = await checkpointer.aget_tuple({"configurable": {"thread_id": "shared"}})
        active = await owner.create_or_reject("shared", user_id="alice")
        await owner.set_status(active.run_id, RunStatus.running)
        compactor = AsyncMock(side_effect=AssertionError("must not compact during peer run"))
        monkeypatch.setattr(threads, "compact_thread_context", compactor)
        for method, path, payload in (
            ("PUT", "goal", {"objective": "不得覆盖"}),
            ("DELETE", "goal", None),
            ("POST", "compact", {"force": True}),
            ("POST", "state", {"values": {"title": "不得覆盖"}}),
        ):
            result = await client.request(method, "/api/threads/shared/" + path, json=payload)
            assert result.status_code == 409, result.text
            assert "run in flight" in result.json()["detail"]
        after = await checkpointer.aget_tuple({"configurable": {"thread_id": "shared"}})
        assert after == before
        compactor.assert_not_awaited()
        assert (await store.get(active.run_id, user_id="alice"))["status"] == "running"


@pytest.mark.anyio
async def test_state_read_merge_write_holds_reservation_until_title_synced(store, monkeypatch):
    app, _, checkpointer = _build_thread_app()
    writer = manager(store, "writer")
    peer = manager(store, "peer")
    app.state.run_manager = writer
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        assert (await client.put("/api/threads/shared/goal", json={"objective": "保留"})).status_code == 200
        read = checkpointer.aget_tuple
        sync_title = app.state.thread_store.update_display_name
        boundaries = []

        async def reject_peer(boundary):
            boundaries.append(boundary)
            for strategy in ("reject", "interrupt", "rollback"):
                with pytest.raises(ConflictError):
                    await peer.create_or_reject("shared", user_id="alice", multitask_strategy=strategy)

        async def protected_read(*args, **kwargs):
            await reject_peer("read")
            return await read(*args, **kwargs)

        async def protected_sync(*args, **kwargs):
            await reject_peer("title-sync")
            return await sync_title(*args, **kwargs)

        monkeypatch.setattr(checkpointer, "aget_tuple", protected_read)
        monkeypatch.setattr(app.state.thread_store, "update_display_name", protected_sync)
        result = await client.post("/api/threads/shared/state", json={"values": {"title": "新标题"}})
        assert result.status_code == 200, result.text
        assert result.json()["values"]["goal"]["objective"] == "保留"
        assert result.json()["values"]["title"] == "新标题"
        assert boundaries == ["read", "title-sync"]
        assert await store.list_inflight() == []
        assert (await peer.create_or_reject("shared", user_id="alice")).status == RunStatus.pending


@pytest.mark.anyio
async def test_manager_internal_operation_hidden_and_release_keeps_captured_owner(store):
    writer = manager(store, "writer")
    async with writer.reserve_thread_operation("shared", kind=ThreadOperationKind.checkpoint_write, user_id="alice"):
        record = next(iter(writer._runs.values()))
        assert record.operation_kind == ThreadOperationKind.checkpoint_write
        assert not await writer.has_inflight("shared")
        assert await writer.list_by_thread("shared", user_id="alice") == []
        assert await writer.get_many_by_thread("shared", {record.run_id}, user_id="alice") == {}
        assert await writer.list_successful_regenerate_sources("shared", user_id="alice") == set()
        token = set_current_user(SimpleNamespace(id="bob"))
    try:
        assert await store.get(record.run_id, user_id="alice") is None
        assert writer._runs == {} and writer._runs_by_thread == {}
    finally:
        reset_current_user(token)


@pytest.mark.anyio
async def test_cancelled_writer_releases_then_peer_can_start(store):
    writer, peer = manager(store, "writer"), manager(store, "peer")
    entered = asyncio.Event()

    async def operation():
        async with writer.reserve_thread_operation("shared", kind=ThreadOperationKind.checkpoint_write, user_id="alice"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(operation())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await store.list_inflight() == []
    assert (await peer.create_or_reject("shared", user_id="alice")).status == RunStatus.pending
