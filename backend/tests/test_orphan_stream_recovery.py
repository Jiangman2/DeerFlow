"""Official #4420 durable fallback through enterprise owner-scoped stores."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_thread_operation_store import anyio_backend as _anyio_backend
from test_thread_operation_store import store as _store

from app.gateway.deps import _terminalize_recovered_runs
from app.gateway.services import sse_consumer, wait_for_run_completion
from deerflow.runtime import ORPHAN_RECOVERY_STOP_REASON, MemoryStreamBridge, RunManager
from deerflow.runtime.user_context import reset_current_user, set_current_user

anyio_backend = _anyio_backend
store = _store


class BrokenEndBridge(MemoryStreamBridge):
    def __init__(self):
        super().__init__()
        self.subscribed = asyncio.Event()
        self.end_attempts = []

    def subscribe(self, run_id, *, last_event_id=None, heartbeat_interval=15):
        self.subscribed.set()
        return super().subscribe(run_id, last_event_id=last_event_id, heartbeat_interval=0.01)

    async def publish_end(self, run_id):
        self.end_attempts.append(run_id)
        raise ConnectionError("test bridge END unavailable")


class ConnectedRequest:
    headers = {}

    async def is_disconnected(self):
        return False


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["sse", "wait"])
async def test_durable_orphan_claim_ends_wait_even_when_end_publish_fails(store, mode):
    old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await store.put("orphan", thread_id="thread", user_id="alice", status="running", owner_worker_id="dead", lease_expires_at=old, created_at=old)
    viewer = RunManager(store=store, worker_id="viewer")
    record = await viewer.get("orphan", user_id="alice")
    assert record is not None and record.store_only
    bridge = BrokenEndBridge()
    await bridge.publish(record.run_id, "values", {"step": 1})

    async def terminalize(records):
        await _terminalize_recovered_runs(bridge, records, cleanup_delay=60)

    reaper = RunManager(store=store, worker_id="reaper", on_orphans_recovered=terminalize)

    async def consume():
        # A changed ambient identity must not override the already authorized
        # record owner used by the heartbeat refresh.
        token = set_current_user(SimpleNamespace(id="bob", role="user"))
        try:
            if mode == "wait":
                return await wait_for_run_completion(bridge, record, ConnectedRequest(), viewer)
            return [frame async for frame in sse_consumer(bridge, record, ConnectedRequest(), viewer)]
        finally:
            reset_current_user(token)

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(bridge.subscribed.wait(), timeout=2)
        await reaper._reconcile_orphans_periodic()
        result = await asyncio.wait_for(consumer, timeout=2)
    finally:
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
    assert bridge.end_attempts == ["orphan"]
    if mode == "wait":
        assert result is True
    else:
        assert result[-1] == "event: end\ndata: null\n\n"
    row = await store.get("orphan", user_id="alice")
    assert row["status"] == "error"
    assert row["stop_reason"] == ORPHAN_RECOVERY_STOP_REASON
    assert await store.get("orphan", user_id="bob") is None


@pytest.mark.anyio
async def test_expired_internal_reservation_never_emits_user_stream_end(store):
    old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await store.put("write", thread_id="thread", user_id="alice", operation_kind="checkpoint_write", status="pending", owner_worker_id="dead", lease_expires_at=old, created_at=old)
    callbacks = []

    async def recovered(records):
        callbacks.extend(records)

    await RunManager(store=store, on_orphans_recovered=recovered)._reconcile_orphans_periodic()
    row = await store.get("write", user_id="alice")
    assert row["status"] == "error" and row["stop_reason"] == ORPHAN_RECOVERY_STOP_REASON
    assert callbacks == []
