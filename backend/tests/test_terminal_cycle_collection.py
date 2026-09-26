"""Official #5112 coalesced terminal GC, including failure/release boundaries."""

import asyncio
import logging
import threading
import time
import weakref
from contextvars import ContextVar
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs import RunContext, RunManager, worker


async def wait_finished(loop):
    async def wait():
        while loop in worker._terminal_cycle_collection_scheduled_loops:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=2)


@pytest.mark.anyio
async def test_collection_is_coalesced_contextless_off_loop_and_observable(monkeypatch, caplog):
    marker = ContextVar("terminal_gc_request", default=None)
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    seen = []

    def collect():
        seen.append((marker.get(), threading.get_ident()))
        return 7

    monkeypatch.setattr(worker, "_TERMINAL_CYCLE_COLLECTION_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(worker, "_TERMINAL_CYCLE_COLLECTION_INFO_THRESHOLD_SECONDS", 0.0)
    monkeypatch.setattr(worker, "_terminal_cycle_collection_last_at", 0.0)
    monkeypatch.setattr(worker, "gc", SimpleNamespace(collect=collect))
    caplog.set_level(logging.INFO, logger=worker.__name__)
    token = marker.set("request-credential")
    try:
        worker._schedule_terminal_cycle_collection()
        worker._schedule_terminal_cycle_collection()
        await wait_finished(loop)
    finally:
        marker.reset(token)
    assert len(seen) == 1
    assert seen[0][0] is None
    assert seen[0][1] != main_thread
    assert "Terminal cyclic GC collected 7 object(s)" in caplog.text


@pytest.mark.anyio
async def test_gc_failure_releases_singleflight_and_is_logged(monkeypatch, caplog):
    loop = asyncio.get_running_loop()
    calls = []

    def collect():
        calls.append(True)
        raise RuntimeError("synthetic collection failure")

    monkeypatch.setattr(worker, "_TERMINAL_CYCLE_COLLECTION_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(worker, "gc", SimpleNamespace(collect=collect))
    for _ in range(2):
        worker._schedule_terminal_cycle_collection()
        await wait_finished(loop)
    assert len(calls) == 2
    assert "synthetic collection failure" in caplog.text


@pytest.mark.anyio
async def test_timer_does_not_retain_request_and_respects_global_interval(monkeypatch):
    loop = asyncio.get_running_loop()
    marker = ContextVar("gc_timer_request", default=None)
    callbacks = []
    collected = []
    clock = SimpleNamespace(monotonic=lambda: 105.0, perf_counter=time.perf_counter)
    monkeypatch.setattr(worker, "time", clock)
    monkeypatch.setattr(worker, "_terminal_cycle_collection_last_at", 100.0)
    monkeypatch.setattr(worker, "gc", SimpleNamespace(collect=lambda: collected.append(True) or 0))

    def call_later(delay, callback, *, context):
        callbacks.append((delay, callback, context))

    class Payload:
        pass

    payload = Payload()
    ref = weakref.ref(payload)
    token = marker.set(payload)
    with monkeypatch.context() as patch:
        patch.setattr(loop, "call_later", call_later)
        try:
            worker._schedule_terminal_cycle_collection()
        finally:
            marker.reset(token)
    del token, payload
    assert ref() is None
    delay, callback, context = callbacks.pop()
    assert delay == 5.0
    assert context.get(marker) is None
    # Another loop may have collected since this timer was scheduled.
    monkeypatch.setattr(worker, "_terminal_cycle_collection_last_at", 104.0)
    context.run(callback)
    await wait_finished(loop)
    assert collected == []
    assert isinstance(worker._terminal_cycle_collection_scheduled_loops, weakref.WeakSet)


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, "hook", "end"])
async def test_worker_schedules_gc_only_after_runtime_detachment(monkeypatch, failure):
    manager = RunManager()
    record = await manager.create("gc-terminal")
    config = {"callbacks": []}
    seen = []
    hook = AsyncMock()
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())

    class Agent:
        async def astream(self, *args, **kwargs):
            yield {"messages": []}

    def schedule():
        assert "__pregel_runtime" not in config["configurable"]
        assert config["callbacks"] == []
        seen.append(True)

    monkeypatch.setattr(worker, "_schedule_terminal_cycle_collection", schedule)
    if failure == "hook":
        hook.side_effect = asyncio.CancelledError("hook cancel")
    if failure == "end":
        bridge.publish_end.side_effect = RuntimeError("END failed")
    try:
        await worker.run_agent(bridge, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore(), on_run_completed=hook), agent_factory=lambda **kw: Agent(), graph_input={}, config=config)
    except (RuntimeError, asyncio.CancelledError):
        assert failure is not None
    assert seen == [True]
