"""Enterprise clarification-race wrapper must own its child tasks to completion."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deerflow.runtime.runs import RunContext, RunManager, RunStatus, run_agent


@pytest.mark.anyio
@pytest.mark.parametrize("stream_modes", [["values"], ["values", "custom"]])
@pytest.mark.parametrize("lost_owner", [False, True])
@pytest.mark.parametrize("second_cancel", [False, True])
async def test_parent_cancellation_drains_execution_before_sandbox_release(monkeypatch, stream_modes, lost_owner, second_cancel):
    manager = RunManager()
    record = await manager.create("stream-teardown")
    stream = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    entered = asyncio.Event()
    closing = asyncio.Event()
    release_close = asyncio.Event()
    closed = asyncio.Event()
    children = []
    order = []

    class Agent:
        async def astream(self, *args, **kwargs):
            children.append(asyncio.current_task())
            try:
                entered.set()
                await asyncio.Event().wait()
                yield {}
            finally:
                closing.set()
                await release_close.wait()
                order.append("stream_closed")
                closed.set()

    async def release(context):
        order.append("sandbox_released")

    cleanup = AsyncMock(side_effect=release)
    monkeypatch.setattr("deerflow.runtime.runs.worker.release_sandbox_execution_lease_async", cleanup)
    record.task = asyncio.create_task(run_agent(stream, manager, record, ctx=RunContext(checkpointer=None), agent_factory=lambda **kw: Agent(), graph_input={}, config={}, stream_modes=stream_modes))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        if lost_owner:
            await manager._mark_ownership_lost(record, reason="synthetic lost owner")
        else:
            record.task.cancel("first host cancel")
        await asyncio.wait_for(closing.wait(), 2)
        cleanup.assert_not_awaited()
        assert not record.task.done()
        if second_cancel:
            record.task.cancel("repeated shutdown")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert not record.task.done()
            assert not children[0].done()
            cleanup.assert_not_awaited()
        release_close.set()
        await asyncio.wait_for(record.task, 2)
        assert closed.is_set()
        assert order == ["stream_closed", "sandbox_released"]
        assert record.status == (RunStatus.error if lost_owner else RunStatus.interrupted)
        stream.publish_end.assert_awaited_once()
        assert all(task.done() for task in children)
    finally:
        release_close.set()
        for task in [record.task, *children]:
            if not task.done():
                task.cancel()
        await asyncio.gather(record.task, *children, return_exceptions=True)


@pytest.mark.anyio
async def test_clarification_timeout_drains_stream_before_success_and_end(monkeypatch):
    manager = RunManager()
    record = await manager.create("clarification-teardown")
    stream = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    closing = asyncio.Event()
    release_close = asyncio.Event()
    closed = asyncio.Event()
    cleanup = AsyncMock()
    monkeypatch.setattr("deerflow.runtime.runs.worker.release_sandbox_execution_lease_async", cleanup)

    class Agent:
        async def astream(self, *args, config=None, **kwargs):
            try:
                config["configurable"]["__pregel_runtime"].context["__clarification_event"].set()
                await asyncio.Event().wait()
                yield {}
            finally:
                closing.set()
                await release_close.wait()
                closed.set()

    record.task = asyncio.create_task(run_agent(stream, manager, record, ctx=RunContext(checkpointer=None), agent_factory=lambda **kw: Agent(), graph_input={}, config={}))
    try:
        await asyncio.wait_for(closing.wait(), 3)
        assert not record.task.done()
        cleanup.assert_not_awaited()
        stream.publish_end.assert_not_awaited()
        release_close.set()
        await asyncio.wait_for(record.task, 2)
        assert closed.is_set()
        assert record.status == RunStatus.success
        assert record.stop_reason == "awaiting_human_input"
        cleanup.assert_awaited_once()
        stream.publish_end.assert_awaited_once()
    finally:
        release_close.set()
        if not record.task.done():
            record.task.cancel()
        await asyncio.gather(record.task, return_exceptions=True)


@pytest.mark.anyio
async def test_stream_error_is_not_hidden_by_child_task_drain(monkeypatch):
    manager = RunManager()
    record = await manager.create("error-teardown")
    stream = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    closed = []

    class Agent:
        async def astream(self, *args, **kwargs):
            try:
                raise RuntimeError("synthetic tool failure")
                yield {}
            finally:
                closed.append(True)

    await run_agent(stream, manager, record, ctx=RunContext(checkpointer=None), agent_factory=lambda **kw: Agent(), graph_input={}, config={})
    assert closed == [True]
    assert record.status == RunStatus.error
    assert record.error == "synthetic tool failure"
    stream.publish_end.assert_awaited_once()
