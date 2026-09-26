"""Delivery backfill after lease CAS, with the enterprise captured-owner boundary."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_run_event_singletons import anyio_backend as _anyio_backend
from test_run_event_singletons import stores as _stores

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import get_current_user, reset_current_user, set_current_user

anyio_backend = _anyio_backend
stores = _stores


async def seed(store, *, owner="alice", kind="run", run_id="orphan", thread_id="thread"):
    old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await store.put(run_id, thread_id=thread_id, user_id=owner, status="running", operation_kind=kind, owner_worker_id="dead", lease_expires_at=old, created_at=old)


@pytest.mark.anyio
async def test_recovery_preserves_first_receipt(stores):
    events, peer = stores
    runs = MemoryRunStore()
    await seed(runs)
    original, _ = await events.put_if_absent(
        thread_id="thread",
        run_id="orphan",
        event_type="run.delivery",
        category="outputs",
        content={"presented": 1, "paths": ["/mnt/user-data/outputs/报告.pdf"], "by_tool": {"present_files": ["/mnt/user-data/outputs/报告.pdf"]}},
    )
    managers = [RunManager(store=runs, event_store=event_store) for event_store in (events, peer)]
    result = await asyncio.gather(*(m.reconcile_orphaned_inflight_runs(error="dead worker") for m in managers))
    assert sum(map(len, result)) == 1
    assert await events.list_events("thread", "orphan") == [original]
    assert (await runs.get("orphan"))["status"] == "error"


@pytest.mark.anyio
async def test_recovery_uses_captured_owner_not_requester(stores):
    events, _ = stores
    runs = MemoryRunStore()
    await seed(runs)
    token = set_current_user(SimpleNamespace(id="bob"))
    try:
        recovered = await RunManager(store=runs, event_store=events).reconcile_orphaned_inflight_runs(error="dead")
        assert len(recovered) == 1
        assert get_current_user().id == "bob"
        if hasattr(events, "_sf"):
            assert await events.list_events("thread", "orphan") == []
    finally:
        reset_current_user(token)
    facts = await events.list_events("thread", "orphan")  # fixture's Alice context
    assert len(facts) == 1
    assert facts[0]["content"] == {"presented": 0, "paths": [], "by_tool": {}}
    if hasattr(events, "_sf"):
        assert facts[0]["user_id"] == "alice"


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["renew", "complete", "delete"])
async def test_stale_recovery_scan_cannot_seed_live_or_deleted_receipt(change, monkeypatch):
    runs, events = MemoryRunStore(), MemoryRunEventStore()
    await seed(runs)
    scan = runs.list_inflight_with_expired_lease

    async def stale_scan(**kwargs):
        result = [dict(row) for row in await scan(**kwargs)]
        if change == "renew":
            await runs.update_lease("orphan", owner_worker_id="dead", lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
        elif change == "complete":
            await runs.update_status("orphan", "success")
        else:
            await runs.delete("orphan")
        return result

    monkeypatch.setattr(runs, "list_inflight_with_expired_lease", stale_scan)
    assert await RunManager(store=runs, event_store=events).reconcile_orphaned_inflight_runs(error="dead") == []
    assert await events.list_events("thread", "orphan") == []


@pytest.mark.anyio
async def test_internal_checkpoint_recovery_never_emits_user_delivery():
    runs, events = MemoryRunStore(), MemoryRunEventStore()
    await seed(runs, kind="checkpoint_write")
    assert await RunManager(store=runs, event_store=events).reconcile_orphaned_inflight_runs(error="dead") == []
    assert (await runs.get("orphan"))["status"] == "error"
    assert await events.list_events("thread", "orphan") == []


@pytest.mark.anyio
async def test_backfill_outage_does_not_undo_claim_or_suppress_terminalization():
    class UnavailableEvents(MemoryRunEventStore):
        async def put_if_absent(self, **kwargs):
            raise RuntimeError("temporary outage")

    runs = MemoryRunStore()
    await seed(runs)
    recovered = await RunManager(store=runs, event_store=UnavailableEvents()).reconcile_orphaned_inflight_runs(error="dead")
    assert len(recovered) == 1
    assert (await runs.get("orphan"))["status"] == "error"


@pytest.mark.anyio
async def test_legacy_null_owner_does_not_inherit_recovery_callers_identity():
    owners = []

    class InspectingEvents(MemoryRunEventStore):
        async def put_if_absent(self, **kwargs):
            owners.append(get_current_user())
            return await super().put_if_absent(**kwargs)

    runs = MemoryRunStore()
    await seed(runs, owner=None)
    token = set_current_user(SimpleNamespace(id="bob"))
    try:
        await RunManager(store=runs, event_store=InspectingEvents()).reconcile_orphaned_inflight_runs(error="dead")
        assert get_current_user().id == "bob"
    finally:
        reset_current_user(token)
    assert owners == [None]
