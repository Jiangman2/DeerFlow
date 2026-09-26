"""Official #4431 with enterprise cancellation and sandbox lease cleanup."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_thread_operation_store import anyio_backend as _anyio_backend
from test_thread_operation_store import store as _store

import deerflow.runtime.runs.worker as worker
from deerflow.config.run_ownership_config import RunOwnershipConfig
from deerflow.runtime import RunContext, RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore

anyio_backend = _anyio_backend
store = _store


def owner(store):
    return RunManager(store=store, worker_id="owner", run_ownership_config=RunOwnershipConfig(heartbeat_enabled=True, lease_seconds=30, grace_seconds=0))


@pytest.mark.anyio
async def test_completion_never_replaces_peer_terminal_outcome(store):
    await store.put("run", thread_id="thread", user_id="alice", status="running")
    await store.update_status("run", "error", error="peer takeover", stop_reason="orphan_recovered")
    assert await store.update_run_completion("run", status="success", total_tokens=9) is False
    row = await store.get("run", user_id="alice")
    assert row["status"] == "error" and row["error"] == "peer takeover"
    assert row["stop_reason"] == "orphan_recovered" and row.get("total_tokens", 0) == 0


@pytest.mark.anyio
async def test_arbitration_outage_fences_success_and_prevents_later_write(store, monkeypatch):
    manager = owner(store)
    record = await manager.create_or_reject("thread", user_id="alice")
    await manager.set_status(record.run_id, RunStatus.running)
    finalize = AsyncMock(side_effect=OSError("test storage outage"))
    monkeypatch.setattr(store, "finalize_if_not_cancelled", finalize)
    await manager.set_status_if_not_cancelled(record.run_id, RunStatus.success)
    assert record.ownership_lost and record.status == RunStatus.error
    await manager.set_status_if_not_cancelled(record.run_id, RunStatus.success)
    await manager.update_run_completion(record.run_id, status="success", total_tokens=9)
    await manager.update_run_progress(record.run_id, total_tokens=9)
    assert finalize.await_count == 1
    row = await store.get(record.run_id, user_id="alice")
    assert row["status"] == "running" and row.get("total_tokens", 0) == 0


def bridge():
    return SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())


@pytest.mark.anyio
async def test_fenced_worker_never_starts_or_projects_terminal_status():
    manager = owner(MemoryRunStore())
    record = await manager.create_or_reject("thread")
    await manager._mark_ownership_lost(record, reason="test fence")
    stream = bridge()
    threads = SimpleNamespace(update_display_name=AsyncMock(), update_status=AsyncMock())
    completed = AsyncMock()
    factory = MagicMock(side_effect=AssertionError("fenced worker started agent"))
    await worker.run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, thread_store=threads, on_run_completed=completed), agent_factory=factory, graph_input={}, config={})
    factory.assert_not_called()
    threads.update_status.assert_not_awaited()
    threads.update_display_name.assert_not_awaited()
    completed.assert_not_awaited()
    stream.publish_end.assert_awaited_once_with(record.run_id)


@pytest.mark.anyio
async def test_live_fence_skips_rollback_but_releases_sandbox_execution_lease(monkeypatch):
    manager = owner(MemoryRunStore())
    record = await manager.create_or_reject("thread")
    stream = bridge()
    threads = SimpleNamespace(update_display_name=AsyncMock(), update_status=AsyncMock())
    completed = AsyncMock()
    rollback = AsyncMock()
    release = AsyncMock()
    monkeypatch.setattr(worker, "_rollback_to_pre_run_checkpoint", rollback)
    monkeypatch.setattr(worker, "release_sandbox_execution_lease_async", release)
    started = asyncio.Event()

    class WaitingAgent:
        async def astream(self, *args, **kwargs):
            started.set()
            await asyncio.Event().wait()
            yield {}

    record.task = asyncio.create_task(worker.run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, thread_store=threads, on_run_completed=completed), agent_factory=lambda **kwargs: WaitingAgent(), graph_input={}, config={}))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        threads.update_status.reset_mock()
        record.abort_action = "rollback"
        await manager._mark_ownership_lost(record, reason="test peer takeover")
        await asyncio.wait_for(record.task, timeout=2)
    finally:
        if not record.task.done():
            record.task.cancel()
        await asyncio.gather(record.task, return_exceptions=True)
    assert record.status == RunStatus.error
    rollback.assert_not_awaited()
    release.assert_awaited_once()
    threads.update_display_name.assert_not_awaited()
    threads.update_status.assert_not_awaited()
    completed.assert_not_awaited()
    stream.publish_end.assert_awaited_once_with(record.run_id)
