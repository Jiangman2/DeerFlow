"""Worker-level regression tests for the terminal run.delivery event (#4272 slice 1)."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from deerflow.config.run_ownership_config import RunOwnershipConfig
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import CancelOutcome, RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.runs.worker import RunContext, run_agent


def _make_bridge():
    return SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())


async def _delivery_events(store: MemoryRunEventStore, thread_id: str, run_id: str) -> list[dict]:
    events = await store.list_events(thread_id, run_id)
    return [e for e in events if e["event_type"] == "run.delivery"]


@pytest.mark.anyio
async def test_delivery_event_records_present_files_paths_on_success():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    store = MemoryRunEventStore()

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            journal = config["context"]["__run_journal"]
            ai = AIMessage(content="", tool_calls=[{"id": "call_1", "name": "present_files", "args": {}}])
            journal._remember_current_run_tool_calls(ai, caller="lead_agent")
            journal.on_tool_end(
                Command(
                    update={
                        "artifacts": ["/mnt/user-data/outputs/report.md"],
                        "messages": [ToolMessage("Successfully presented files", tool_call_id="call_1")],
                    }
                ),
                run_id=uuid4(),
            )
            yield {"messages": []}

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=store),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    delivery = await _delivery_events(store, "thread-1", record.run_id)
    assert len(delivery) == 1
    assert delivery[0]["content"]["presented"] == 1
    assert delivery[0]["content"]["paths"] == ["/mnt/user-data/outputs/report.md"]
    assert delivery[0]["content"]["by_tool"] == {"present_files": ["/mnt/user-data/outputs/report.md"]}
    fetched = await run_manager.get(record.run_id)
    assert fetched.status == RunStatus.success


@pytest.mark.anyio
async def test_delivery_event_presented_zero_without_artifact_production():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    store = MemoryRunEventStore()

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": []}

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=store),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    delivery = await _delivery_events(store, "thread-1", record.run_id)
    assert len(delivery) == 1
    assert delivery[0]["content"] == {"presented": 0, "paths": [], "by_tool": {}}
    fetched = await run_manager.get(record.run_id)
    assert fetched.status == RunStatus.success


@pytest.mark.anyio
async def test_delivery_event_is_singleton_across_goal_continuations(monkeypatch):
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    store = MemoryRunEventStore()
    stream_calls = 0
    continuation_calls = 0

    class ContinuingAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            nonlocal stream_calls
            stream_calls += 1
            journal = config["context"]["__run_journal"]
            tool_call_id = f"call_{stream_calls}"
            journal._remember_current_run_tool_calls(
                AIMessage(content="", tool_calls=[{"id": tool_call_id, "name": "present_files", "args": {}}]),
                caller="lead_agent",
            )
            artifacts = ["/mnt/user-data/outputs/report.md"]
            if stream_calls == 2:
                artifacts.append("/mnt/user-data/outputs/appendix.md")
            journal.on_tool_end(
                Command(
                    update={
                        "artifacts": artifacts,
                        "messages": [ToolMessage("Successfully presented files", tool_call_id=tool_call_id)],
                    }
                ),
                run_id=uuid4(),
            )
            yield {"messages": []}

    async def prepare_continuation(**kwargs):
        nonlocal continuation_calls
        continuation_calls += 1
        if continuation_calls == 1:
            return {"messages": []}
        return None

    monkeypatch.setattr("deerflow.runtime.runs.worker._prepare_goal_continuation_input", prepare_continuation)

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=store),
        agent_factory=lambda *, config: ContinuingAgent(),
        graph_input={},
        config={},
    )

    delivery = await _delivery_events(store, "thread-1", record.run_id)
    assert stream_calls == 2
    assert len(delivery) == 1
    assert delivery[0]["content"] == {
        "presented": 2,
        "paths": [
            "/mnt/user-data/outputs/report.md",
            "/mnt/user-data/outputs/appendix.md",
        ],
        "by_tool": {
            "present_files": [
                "/mnt/user-data/outputs/report.md",
                "/mnt/user-data/outputs/appendix.md",
            ]
        },
    }


@pytest.mark.anyio
async def test_delivery_event_emitted_exactly_once_on_error_path():
    run_manager = RunManager()
    record = await run_manager.create("thread-1")
    store = MemoryRunEventStore()

    class FailingAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            raise RuntimeError("boom")
            yield  # pragma: no cover - make this an async generator

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=store),
        agent_factory=lambda *, config: FailingAgent(),
        graph_input={},
        config={},
    )
    await asyncio.sleep(0)

    delivery = await _delivery_events(store, "thread-1", record.run_id)
    assert len(delivery) == 1
    assert delivery[0]["content"]["presented"] == 0
    fetched = await run_manager.get(record.run_id)
    assert fetched.status == RunStatus.error


@pytest.mark.anyio
async def test_delivery_is_durable_before_terminal_run_status():
    events = MemoryRunEventStore()
    terminal_receipts = []

    class OrderingRunStore(MemoryRunStore):
        async def update_status(self, run_id, status, *, error=None, stop_reason=None):
            if status not in {"pending", "running"}:
                receipt = await events.list_events("thread-1", run_id, event_types=["run.delivery"])
                terminal_receipts.append(len(receipt))
            return await super().update_status(run_id, status, error=error, stop_reason=stop_reason)

    run_store = OrderingRunStore()
    run_manager = RunManager(store=run_store)
    record = await run_manager.create("thread-1")

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": []}

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=events),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )

    assert (await run_store.get(record.run_id))["status"] == "success"
    assert terminal_receipts and all(count == 1 for count in terminal_receipts)


@pytest.mark.anyio
async def test_delivery_write_retries_before_persisting_success():
    class FlakyReceiptStore(MemoryRunEventStore):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def put_if_absent(self, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("transient event store outage")
            return await super().put_if_absent(**kwargs)

    event_store = FlakyReceiptStore()
    run_store = MemoryRunStore()
    run_manager = RunManager(store=run_store)
    record = await run_manager.create("thread-1")

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": []}

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=event_store),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )

    assert event_store.attempts == 2
    assert len(await _delivery_events(event_store, "thread-1", record.run_id)) == 1
    assert (await run_store.get(record.run_id))["status"] == "success"


@pytest.mark.anyio
async def test_delivery_write_failure_preserves_real_durable_terminal_status():
    class FailingReceiptStore(MemoryRunEventStore):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def put_if_absent(self, **kwargs):
            self.attempts += 1
            raise RuntimeError("event store unavailable")

    run_store = MemoryRunStore()
    run_manager = RunManager(store=run_store)
    record = await run_manager.create("thread-1")
    event_store = FailingReceiptStore()

    class DummyAgent:
        async def astream(self, graph_input, config=None, stream_mode=None, subgraphs=False):
            yield {"messages": []}

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=event_store),
        agent_factory=lambda *, config: DummyAgent(),
        graph_input={},
        config={},
    )

    # A receipt outage must not let lease recovery rewrite a genuine success
    # as an error. After bounded retries, preserve the worker's real outcome.
    assert event_store.attempts > 1
    assert record.status == RunStatus.success
    assert (await run_store.get(record.run_id))["status"] == "success"


@pytest.mark.anyio
async def test_delivery_event_emitted_when_startup_preflight_fails(monkeypatch):
    run_manager = RunManager()
    run_manager.update_run_completion = AsyncMock(wraps=run_manager.update_run_completion)
    record = await run_manager.create("thread-1")
    store = MemoryRunEventStore()
    compatibility_check = AsyncMock(side_effect=RuntimeError("incompatible checkpoint"))
    monkeypatch.setattr(run_manager, "try_start", compatibility_check)

    def unexpected_agent_factory(**kwargs):
        raise AssertionError("agent must not be built after preflight failure")

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=object(), event_store=store),
        agent_factory=unexpected_agent_factory,
        graph_input={},
        config={},
    )

    delivery = await _delivery_events(store, "thread-1", record.run_id)
    assert len(delivery) == 1
    assert delivery[0]["content"] == {"presented": 0, "paths": [], "by_tool": {}}
    fetched = await run_manager.get(record.run_id)
    assert fetched.status == RunStatus.error
    run_manager.update_run_completion.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("heartbeat", [False, True])
@pytest.mark.parametrize("remote", [False, True])
async def test_cancel_during_receipt_write_wins(heartbeat, remote):
    if remote and not heartbeat:
        return  # Cross-worker cancellation requires durable ownership.
    entered = asyncio.Event()
    release = asyncio.Event()

    class SlowReceiptStore(MemoryRunEventStore):
        async def put_if_absent(self, **kwargs):
            entered.set()
            await release.wait()
            return await super().put_if_absent(**kwargs)

    store = MemoryRunStore()
    ownership = RunOwnershipConfig(heartbeat_enabled=heartbeat, lease_seconds=30, grace_seconds=10)
    owner = RunManager(store=store, worker_id="owner", run_ownership_config=ownership)
    peer = RunManager(store=store, worker_id="peer", run_ownership_config=ownership)
    record = await owner.create("thread-receipt-cancel")
    events = SlowReceiptStore()

    class Agent:
        async def astream(self, *_args, **_kwargs):
            yield {"messages": []}

    task = asyncio.create_task(
        run_agent(
            _make_bridge(),
            owner,
            record,
            ctx=RunContext(checkpointer=None, event_store=events),
            agent_factory=lambda **_kwargs: Agent(),
            graph_input={},
            config={},
        )
    )
    record.task = task
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert (await store.get(record.run_id))["status"] == "running"
        result = await (peer if remote else owner).cancel(record.run_id, action="rollback")
        assert result in {CancelOutcome.requested, CancelOutcome.cancelled}
    finally:
        release.set()
        await asyncio.wait_for(task, timeout=2)
    assert (await store.get(record.run_id))["error"] == "Rolled back by user"
    assert len(await _delivery_events(events, record.thread_id, record.run_id)) == 1


@pytest.mark.anyio
async def test_staged_terminal_receipt_renews_lease_until_persisted():
    store = MemoryRunStore()
    manager = RunManager(store=store, worker_id="owner", run_ownership_config=RunOwnershipConfig(heartbeat_enabled=True))
    record = await manager.create("thread-receipt-lease")
    await manager.set_status(record.run_id, RunStatus.running)
    await manager.set_status(record.run_id, RunStatus.success, persist=False)
    store.renew_lease = AsyncMock(wraps=store.renew_lease)
    await manager._renew_leases()
    store.renew_lease.assert_awaited_once()
    assert (await store.get(record.run_id))["status"] == "running"
    await manager.persist_current_status(record.run_id)
    await manager._renew_leases()
    assert store.renew_lease.await_count == 1


@pytest.mark.anyio
async def test_staged_terminal_is_fenced_after_confirmed_deadline():
    store = MemoryRunStore()
    manager = RunManager(store=store, worker_id="owner", run_ownership_config=RunOwnershipConfig(heartbeat_enabled=True))
    record = await manager.create("thread-receipt-expired")
    await manager.set_status(record.run_id, RunStatus.success, persist=False)
    record.lease_expires_at = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    await manager._renew_leases()
    assert record.ownership_lost
    assert not await manager.persist_current_status(record.run_id)
    assert (await store.get(record.run_id))["status"] == "pending"


@pytest.mark.anyio
@pytest.mark.parametrize("fence_stage", ["journal_flush", "receipt"])
async def test_lease_loss_during_finalization_stops_writes_and_ends_stream(fence_stage):
    entered = asyncio.Event()
    captured_journals = []

    class BlockingEvents(MemoryRunEventStore):
        async def put_batch(self, events):
            if fence_stage == "journal_flush":
                entered.set()
                await asyncio.Event().wait()
            return await super().put_batch(events)

        async def put_if_absent(self, **kwargs):
            entered.set()
            await asyncio.Event().wait()
            return await super().put_if_absent(**kwargs)

    store = MemoryRunStore()
    manager = RunManager(store=store, worker_id="owner", run_ownership_config=RunOwnershipConfig(heartbeat_enabled=True))
    record = await manager.create("thread-fence-finalizing")
    events = BlockingEvents()
    bridge = _make_bridge()
    completed = AsyncMock()

    class Agent:
        async def astream(self, _input, config=None, **_kwargs):
            journal = config["context"]["__run_journal"]
            captured_journals.append(journal)
            journal.record_middleware("test", name="test", hook="after", action="record", changes={})
            yield {"messages": []}

    task = asyncio.create_task(
        run_agent(
            bridge,
            manager,
            record,
            ctx=RunContext(checkpointer=None, event_store=events, on_run_completed=completed),
            agent_factory=lambda **_kwargs: Agent(),
            graph_input={},
            config={},
        )
    )
    record.task = task
    await asyncio.wait_for(entered.wait(), timeout=2)
    await manager._mark_ownership_lost(record, reason="test finalization lease expired", require_active=False)
    await asyncio.wait_for(task, timeout=2)
    assert record.ownership_lost
    assert (await store.get(record.run_id))["status"] == "running"
    assert await _delivery_events(events, record.thread_id, record.run_id) == []
    bridge.publish_end.assert_awaited_once()
    completed.assert_not_awaited()
    assert captured_journals[0]._closed
    assert captured_journals[0]._store is None


@pytest.mark.anyio
async def test_durable_run_stays_active_through_final_checkpoint_write(monkeypatch):
    store = MemoryRunStore()
    events = MemoryRunEventStore()
    manager = RunManager(store=store)
    record = await manager.create("thread-duration-admission")
    observations = []

    class Agent:
        async def astream(self, *_args, **_kwargs):
            yield {"messages": []}

    async def persist_duration(**_kwargs):
        observations.append((await store.get(record.run_id))["status"])
        observations.append(len(await _delivery_events(events, record.thread_id, record.run_id)))

    monkeypatch.setattr("deerflow.runtime.runs.worker._persist_run_duration", persist_duration)
    await run_agent(_make_bridge(), manager, record, ctx=RunContext(checkpointer=InMemorySaver(), event_store=events), agent_factory=lambda **_kwargs: Agent(), graph_input={}, config={})
    assert observations == ["running", 1]
    assert (await store.get(record.run_id))["status"] == "success"


@pytest.mark.anyio
async def test_delivery_event_emitted_when_cancelled_waiting_for_prior_finalization(monkeypatch):
    run_manager = RunManager()
    run_manager.update_run_completion = AsyncMock(wraps=run_manager.update_run_completion)
    record = await run_manager.create("thread-1")
    store = MemoryRunEventStore()
    monkeypatch.setattr(
        run_manager,
        "wait_for_prior_finalizing",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )

    def unexpected_agent_factory(**kwargs):
        raise AssertionError("agent must not be built after preflight cancellation")

    await run_agent(
        _make_bridge(),
        run_manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=store),
        agent_factory=unexpected_agent_factory,
        graph_input={},
        config={},
    )

    delivery = await _delivery_events(store, "thread-1", record.run_id)
    assert len(delivery) == 1
    assert delivery[0]["content"] == {"presented": 0, "paths": [], "by_tool": {}}
    fetched = await run_manager.get(record.run_id)
    assert fetched.status == RunStatus.interrupted
    run_manager.update_run_completion.assert_not_awaited()
