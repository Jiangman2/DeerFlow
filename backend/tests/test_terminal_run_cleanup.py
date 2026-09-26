"""Official #5112 eviction must retain enterprise finalization/recovery fences."""

import asyncio
from contextvars import Context, ContextVar
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_thread_operation_store import anyio_backend as _anyio_backend
from test_thread_operation_store import store as _store

from deerflow.runtime.runs import RunContext, RunManager, RunStatus, worker
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import get_current_user

anyio_backend = _anyio_backend
store = _store


@pytest.mark.anyio
@pytest.mark.parametrize("guard", ["pending", "running", "finalizing", "terminal_status_pending", "task"])
async def test_cleanup_retains_live_or_unconfirmed_record(guard):
    manager = RunManager(store=MemoryRunStore())
    record = await manager.create("protected")
    await manager.set_status(record.run_id, RunStatus.success)
    if guard in ("pending", "running"):
        record.status = RunStatus(guard)
    elif guard == "task":
        record.task = asyncio.current_task()
    else:
        setattr(record, guard, True)
    await manager.cleanup(record.run_id, delay=0)
    assert manager._runs[record.run_id] is record
    assert record.run_id in manager._runs_by_thread[record.thread_id]


@pytest.mark.anyio
@pytest.mark.parametrize("problem", ["missing", "failure", "pending", "running", "unknown", "thread", "owner", "run", "kind"])
async def test_cleanup_requires_recoverable_matching_terminal_row(problem):
    backing = MemoryRunStore()
    manager = RunManager(store=backing)
    record = await manager.create("protected", user_id="alice")
    await manager.set_status(record.run_id, RunStatus.success)
    row = dict(await backing.get(record.run_id, user_id="alice"))
    if problem == "missing":
        row = None
    elif problem in ("pending", "running", "unknown"):
        row["status"] = problem
    elif problem in ("thread", "owner", "run", "kind"):
        row[{"thread": "thread_id", "owner": "user_id", "run": "run_id", "kind": "operation_kind"}[problem]] = "other"
    backing.get = AsyncMock(return_value=row)
    if problem == "failure":
        backing.get.side_effect = RuntimeError("synthetic unavailable store")
    await manager.cleanup(record.run_id, delay=0)
    assert manager._runs[record.run_id] is record
    backing.get.assert_awaited_once_with(record.run_id, user_id="alice")


@pytest.mark.anyio
@pytest.mark.parametrize("race", ["replacement", "finalizing", "terminal_status_pending", "running", "task"])
async def test_cleanup_rechecks_identity_and_liveness_after_unlocked_store_read(race):
    backing = MemoryRunStore()
    manager = RunManager(store=backing)
    record = await manager.create("race")
    await manager.set_status(record.run_id, RunStatus.success)
    original_get = backing.get
    read = False

    async def get(run_id, *, user_id):
        nonlocal read
        read = True
        # Taking the manager lock here proves store I/O doesn't hold admission.
        async with manager._lock:
            if race == "replacement":
                manager._runs[run_id] = replace(record)
            elif race == "running":
                record.status = RunStatus.running
            elif race == "task":
                record.task = asyncio.current_task()
            else:
                setattr(record, race, True)
        return await original_get(run_id, user_id=user_id)

    backing.get = get
    await asyncio.wait_for(manager.cleanup(record.run_id, delay=0), timeout=2)
    assert read
    assert record.run_id in manager._runs
    assert record.run_id in manager._runs_by_thread[record.thread_id]


@pytest.mark.anyio
@pytest.mark.no_auto_user
@pytest.mark.parametrize("owner", ["alice", None])
@pytest.mark.parametrize("status", [RunStatus.success, RunStatus.error, RunStatus.timeout, RunStatus.interrupted])
async def test_cleanup_in_blank_context_retains_durable_history(store, owner, status):
    manager = RunManager(store=store)
    # Include legacy NULL owners without going through request admission.
    await store.put("historical-run", thread_id="history", user_id=owner, status=status.value)
    record = await manager.get("historical-run", user_id=owner)
    await asyncio.create_task(manager.cleanup(record.run_id, delay=0), context=Context())
    assert record.run_id not in manager._runs
    assert record.thread_id not in manager._runs_by_thread
    row = await store.get(record.run_id, user_id=owner)
    assert row["status"] == status
    assert row["user_id"] == owner
    hydrated = await manager.get(record.run_id, user_id=owner)
    assert hydrated.status == status
    assert hydrated.user_id == owner


@pytest.mark.anyio
async def test_cleanup_preserves_peer_terminal_winner():
    backing = MemoryRunStore()
    manager = RunManager(store=backing)
    record = await manager.create("peer")
    await manager.set_status(record.run_id, RunStatus.success)
    backing._runs[record.run_id]["status"] = "interrupted"
    await manager.cleanup(record.run_id, delay=0)
    assert record.run_id not in manager._runs
    assert (await manager.get(record.run_id, user_id=record.user_id)).status == RunStatus.interrupted


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, "hook", "end"])
async def test_worker_schedules_delayed_manager_eviction_without_request_context(failure):
    marker = ContextVar("synthetic_request_secret", default=None)
    observed = []

    class Manager(RunManager):
        async def cleanup(self, run_id, *, delay=300):
            observed.append((run_id, delay, marker.get(), get_current_user()))

    class Agent:
        async def astream(self, *args, **kwargs):
            yield {"messages": []}

    manager = Manager()
    record = await manager.create("worker-cleanup")
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    hook = AsyncMock()
    if failure == "hook":
        hook.side_effect = asyncio.CancelledError("hook cancel")
    if failure == "end":
        bridge.publish_end.side_effect = RuntimeError("END failed")
    token = marker.set("do-not-retain")
    try:
        try:
            await worker.run_agent(bridge, manager, record, ctx=RunContext(checkpointer=None, on_run_completed=hook), agent_factory=lambda **kw: Agent(), graph_input={}, config={})
        except (RuntimeError, asyncio.CancelledError):
            assert failure is not None
        await asyncio.sleep(0)
    finally:
        marker.reset(token)
    assert observed == [(record.run_id, 300, None, None)]


@pytest.mark.anyio
async def test_cleanup_delay_happens_before_eligibility_check():
    manager = RunManager(store=MemoryRunStore())
    record = await manager.create("delayed")
    cleanup = asyncio.create_task(manager.cleanup(record.run_id, delay=0.02))
    await manager.set_status(record.run_id, RunStatus.success)
    await cleanup
    assert record.run_id not in manager._runs
