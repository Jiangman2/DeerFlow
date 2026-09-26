"""Official #5191/#5112 cleanup boundaries with enterprise context resets."""

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs import RunContext, RunManager, RunStatus, run_agent
from deerflow.runtime.weknora_context import get_current_weknora_access_token, get_current_weknora_scope


class TerminalInterrupt(BaseException):
    pass


class OkAgent:
    def __init__(self, journals):
        self.journals = journals

    async def astream(self, graph_input, config=None, **kwargs):
        self.journals.append(config["configurable"]["__pregel_runtime"].context["__run_journal"])
        yield {"messages": []}


def bridge():
    return SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())


@pytest.mark.anyio
@pytest.mark.parametrize("interruption", [asyncio.CancelledError, TerminalInterrupt])
async def test_hook_interrupt_finishes_terminal_signaling_before_propagating(interruption):
    manager = RunManager()
    record = await manager.create("hook-interrupt")
    stream = bridge()
    journals = []

    async def hook(_record):
        assert _record.finalizing
        raise interruption("hook interrupted")

    with pytest.raises(interruption, match="hook interrupted"):
        await run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore(), on_run_completed=hook), agent_factory=lambda **kw: OkAgent(journals), graph_input={}, config={})
    await asyncio.sleep(0)

    assert record.status == RunStatus.success
    assert not record.finalizing
    stream.publish_end.assert_awaited_once_with(record.run_id)
    stream.cleanup.assert_awaited_once_with(record.run_id, delay=60)
    assert journals[0]._closed and journals[0]._store is None


@pytest.mark.anyio
async def test_host_cancellation_in_hook_is_deferred_and_not_swallowed():
    manager = RunManager()
    record = await manager.create("hook-host-cancel")
    stream = bridge()
    journals = []
    entered = asyncio.Event()

    async def hook(_record):
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore(), on_run_completed=hook), agent_factory=lambda **kw: OkAgent(journals), graph_input={}, config={}))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel("host shutdown")
        with pytest.raises(asyncio.CancelledError, match="host shutdown"):
            await asyncio.wait_for(task, 2)
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.sleep(0)
    assert task.cancelling() == 0
    assert not record.finalizing
    stream.publish_end.assert_awaited_once()
    stream.cleanup.assert_awaited_once()
    assert journals[0]._closed


@pytest.mark.anyio
async def test_lease_cleanup_interrupt_resets_enterprise_context_in_owning_task(monkeypatch):
    manager = RunManager()
    record = await manager.create("lease-cleanup-interrupt")
    stream = bridge()
    journals = []
    hook = AsyncMock()
    observations = []

    async def release(context):
        observations.append(get_current_weknora_access_token())
        raise asyncio.CancelledError("lease cleanup already drained")

    monkeypatch.setattr("deerflow.runtime.runs.worker.release_sandbox_execution_lease_async", release)
    original_token = get_current_weknora_access_token()
    original_scope = get_current_weknora_scope()
    with pytest.raises(asyncio.CancelledError, match="lease cleanup already drained"):
        await run_agent(
            stream,
            manager,
            record,
            ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore(), on_run_completed=hook),
            agent_factory=lambda **kw: OkAgent(journals),
            graph_input={},
            config={"context": {"weknora_access_token": "synthetic-user-token", "weknora_knowledge_base_ids": ["synthetic-kb"]}},
        )
    await asyncio.sleep(0)
    assert observations == ["synthetic-user-token"]
    assert get_current_weknora_access_token() == original_token
    assert get_current_weknora_scope() == original_scope
    assert not record.finalizing
    hook.assert_awaited_once()
    stream.publish_end.assert_awaited_once()
    stream.cleanup.assert_awaited_once()
    assert journals[0]._closed


@pytest.mark.anyio
async def test_durable_finalization_failure_still_detaches_journal(monkeypatch):
    manager = RunManager()
    record = await manager.create("finalization-failure")
    stream = bridge()
    journals = []
    monkeypatch.setattr(manager, "update_finalizing_progress", AsyncMock(side_effect=RuntimeError("test finalization failed")))
    with pytest.raises(RuntimeError, match="test finalization failed"):
        await run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()), agent_factory=lambda **kw: OkAgent(journals), graph_input={}, config={})
    await asyncio.sleep(0)
    assert journals[0]._closed and journals[0]._store is None
    stream.cleanup.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_end_failure_keeps_local_cleanup(failure):
    manager = RunManager()
    record = await manager.create("end-failure")
    stream = bridge()
    stream.publish_end.side_effect = failure("test END failed")
    journals = []
    with pytest.raises(failure, match="test END failed"):
        await run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()), agent_factory=lambda **kw: OkAgent(journals), graph_input={}, config={})
    await asyncio.sleep(0)
    assert journals[0]._closed
    stream.cleanup.assert_awaited_once()


@pytest.mark.anyio
async def test_delayed_eviction_does_not_inherit_request_context():
    credential = ContextVar("synthetic_request_credential", default=None)
    token = credential.set("synthetic-do-not-retain")
    manager = RunManager()
    record = await manager.create("contextless-cleanup")
    stream = bridge()
    observed = []

    async def cleanup(*args, **kwargs):
        observed.append(credential.get())

    stream.cleanup.side_effect = cleanup
    try:
        await run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()), agent_factory=lambda **kw: OkAgent([]), graph_input={}, config={})
        await asyncio.sleep(0)
        assert observed == [None]
        assert credential.get() == "synthetic-do-not-retain"
    finally:
        credential.reset(token)


@pytest.mark.anyio
async def test_cancelled_journal_close_discards_without_restarting_writes(monkeypatch):
    manager = RunManager()
    record = await manager.create("cancelled-close")
    stream = bridge()
    journals = []
    modes = []
    original_close = RunJournal.close

    async def close(self, *, flush=True):
        modes.append(flush)
        if flush:
            raise asyncio.CancelledError("interrupted flush")
        await original_close(self, flush=False)

    monkeypatch.setattr(RunJournal, "close", close)
    with pytest.raises(asyncio.CancelledError, match="interrupted flush"):
        await run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()), agent_factory=lambda **kw: OkAgent(journals), graph_input={}, config={})
    await asyncio.sleep(0)
    assert modes == [True, False]
    assert journals[0]._closed and journals[0]._store is None
    assert not record.finalizing
    stream.publish_end.assert_awaited_once()
    stream.cleanup.assert_awaited_once()


@pytest.mark.anyio
async def test_lease_cleanup_cancellation_does_not_bypass_ownership_fence(monkeypatch):
    manager = RunManager()
    record = await manager.create("fenced-cleanup")
    stream = bridge()
    journals = []
    events = MemoryRunEventStore()
    hook = AsyncMock()
    progress = AsyncMock()
    monkeypatch.setattr(manager, "update_finalizing_progress", progress)

    async def release(context):
        await manager._mark_ownership_lost(record, reason="synthetic expired lease")
        raise asyncio.CancelledError("release interrupted after lease loss")

    monkeypatch.setattr("deerflow.runtime.runs.worker.release_sandbox_execution_lease_async", release)
    with pytest.raises(asyncio.CancelledError, match="release interrupted"):
        await run_agent(stream, manager, record, ctx=RunContext(checkpointer=None, event_store=events, on_run_completed=hook), agent_factory=lambda **kw: OkAgent(journals), graph_input={}, config={})
    await asyncio.sleep(0)
    hook.assert_not_awaited()
    progress.assert_not_awaited()
    assert journals[0]._closed and journals[0]._store is None
    assert not record.finalizing
    assert not await events.list_events(record.thread_id, record.run_id)
    stream.publish_end.assert_awaited_once()
    stream.cleanup.assert_awaited_once()
