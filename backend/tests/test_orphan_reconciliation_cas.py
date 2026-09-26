"""Official #4434/#4427 stale-scan protection on memory, SQLite and PostgreSQL."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_thread_operation_store import anyio_backend as _anyio_backend
from test_thread_operation_store import store as _store

from deerflow.runtime.runs.manager import RunManager

anyio_backend = _anyio_backend
store = _store


async def seed(store):
    old = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    await store.put("orphan", thread_id="thread", user_id="alice", status="running", owner_worker_id="owner", lease_expires_at=old, created_at=old)


@pytest.mark.anyio
@pytest.mark.parametrize("change", ["renew", "complete", "delete"])
async def test_stale_scan_cannot_overwrite_newer_owner_state(store, monkeypatch, change):
    await seed(store)
    scan = store.list_inflight_with_expired_lease

    async def scan_then_change(**kwargs):
        rows = [dict(row) for row in await scan(**kwargs)]
        assert len(rows) == 1
        if change == "renew":
            assert await store.update_lease("orphan", owner_worker_id="owner", lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)).isoformat())
        elif change == "complete":
            assert await store.update_status("orphan", "success")
        elif hasattr(store, "_sf"):
            await store.delete("orphan", user_id="alice")
        else:
            await store.delete("orphan")
        return rows

    monkeypatch.setattr(store, "list_inflight_with_expired_lease", scan_then_change)
    recovered = await RunManager(store=store).reconcile_orphaned_inflight_runs(error="test orphan")
    assert recovered == []
    current = await store.get("orphan", user_id="alice")
    if change == "delete":
        assert current is None  # Never recreate a row deleted after the scan.
    else:
        assert current["status"] == ("running" if change == "renew" else "success")


@pytest.mark.anyio
async def test_two_reconcilers_only_report_one_durable_claim(store, monkeypatch):
    await seed(store)
    scan = store.list_inflight_with_expired_lease
    ready = asyncio.Event()
    scanned = 0

    async def synchronized_scan(**kwargs):
        nonlocal scanned
        rows = [dict(row) for row in await scan(**kwargs)]
        scanned += 1
        if scanned == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), timeout=5)
        return rows

    monkeypatch.setattr(store, "list_inflight_with_expired_lease", synchronized_scan)
    managers = [RunManager(store=store, worker_id="a"), RunManager(store=store, worker_id="b")]
    results = await asyncio.gather(*(manager.reconcile_orphaned_inflight_runs(error="test orphan") for manager in managers))
    assert sorted(len(result) for result in results) == [0, 1]
    assert (await store.get("orphan", user_id="alice"))["status"] == "error"
