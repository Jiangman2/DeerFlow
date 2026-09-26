"""Official #5535 event deletion fence; no production database is accepted."""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from test_run_event_singletons import anyio_backend as _anyio_backend
from test_run_event_singletons import event
from test_run_event_singletons import stores as _stores

from deerflow.runtime.events.store.db import DbRunEventStore
from deerflow.runtime.user_context import reset_current_user, set_current_user

anyio_backend = _anyio_backend
stores = _stores


@pytest.mark.anyio
@pytest.mark.parametrize("delete_run", [False, True])
async def test_every_backend_accepts_explicit_delete_owner(stores, delete_run):
    store, _ = stores
    await store.put(**event())
    await store.put(**event(thread_id="other"))
    if delete_run:
        assert await store.delete_by_run("thread", "run", user_id="alice") == 1
    else:
        assert await store.delete_by_thread("thread", user_id="alice") == 1
    assert await store.list_events("thread", "run") == []
    assert len(await store.list_events("other", "run")) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("stores", ["sqlite", "postgres"], indirect=True)
@pytest.mark.parametrize("delete_run", [False, True])
async def test_db_delete_owner_auto_explicit_and_unscoped(stores, delete_run):
    store, _ = stores
    await store.put_batch([event(user_id="alice"), event(user_id="bob")])
    await store.put(**event(thread_id="other"))

    async def remove(**kwargs):
        if delete_run:
            return await store.delete_by_run("thread", "run", **kwargs)
        return await store.delete_by_thread("thread", **kwargs)

    assert await remove(user_id="bob") == 1
    assert len(await store.list_events("thread", "run", user_id="alice")) == 1
    assert await remove() == 1  # fixture's AUTO identity is alice
    assert await remove() == 0
    await store.put_batch([event(user_id="alice"), event(user_id="bob")])
    token = set_current_user(None)
    try:
        with pytest.raises(RuntimeError):
            await remove()
        assert await remove(user_id=None) == 2
    finally:
        reset_current_user(token)
    assert len(await store.list_events("other", "run")) == 1


class _FenceSession:
    def __init__(self, *, postgres=True, fail=False):
        self.calls = []
        self.postgres = postgres
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.calls.append("rollback" if exc_type else "exit")

    def begin(self):
        self.calls.append("begin")
        return self

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name="postgresql" if self.postgres else "sqlite"))

    async def execute(self, stmt, params=None):
        if "pg_advisory_xact_lock" in str(stmt):
            assert params == {"thread_id": "thread"}
            self.calls.append("fence")
        else:
            self.calls.append("delete")
            if self.fail:
                raise OSError("synthetic delete failure")

    async def scalar(self, stmt):
        self.calls.append("count")
        return 1

    async def commit(self):
        self.calls.append("explicit commit")


@pytest.mark.anyio
@pytest.mark.parametrize("delete_run", [False, True])
@pytest.mark.parametrize("postgres", [False, True])
async def test_db_delete_queues_before_count_and_fences_inside_transaction(delete_run, postgres):
    session = _FenceSession(postgres=postgres)
    store = DbRunEventStore(lambda: session)
    lock = store._get_write_lock("thread")
    await lock.acquire()
    coroutine = store.delete_by_run("thread", "run", user_id=None) if delete_run else store.delete_by_thread("thread", user_id=None)
    task = asyncio.create_task(coroutine)
    try:
        await asyncio.sleep(0)
        assert not session.calls and not task.done()
    finally:
        lock.release()
        await asyncio.wait_for(task, 3)
    assert task.result() == 1
    expected = ["begin", "fence", "count", "delete"] if postgres else ["begin", "count", "delete"]
    assert session.calls[: len(expected)] == expected
    assert "explicit commit" not in session.calls
    assert store._get_write_lock("thread") is lock


@pytest.mark.anyio
async def test_failed_delete_rolls_back_and_keeps_lock_generation():
    session = _FenceSession(fail=True)
    store = DbRunEventStore(lambda: session)
    lock = store._get_write_lock("thread")
    with pytest.raises(OSError, match="synthetic delete failure"):
        await store.delete_by_thread("thread", user_id=None)
    assert "rollback" in session.calls
    assert store._write_lock_pins["thread"] is lock
    assert not lock.locked()


@pytest.mark.anyio
@pytest.mark.parametrize("inside_transaction", [False, True])
@pytest.mark.parametrize("delete_run", [False, True])
async def test_cancelled_delete_releases_fence_without_retiring_live_generation(inside_transaction, delete_run):
    entered = asyncio.Event()

    class PausedSession(_FenceSession):
        async def scalar(self, stmt):
            entered.set()
            await asyncio.Event().wait()

    session = PausedSession()
    store = DbRunEventStore(lambda: session)
    lock = store._get_write_lock("thread")
    if not inside_transaction:
        await lock.acquire()
    coroutine = store.delete_by_run("thread", "run", user_id=None) if delete_run else store.delete_by_thread("thread", user_id=None)
    task = asyncio.create_task(coroutine)
    try:
        if inside_transaction:
            await asyncio.wait_for(entered.wait(), 3)
        else:
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        if inside_transaction:
            assert "rollback" in session.calls and not lock.locked()
        else:
            assert not session.calls and lock.locked()
        assert store._write_lock_pins["thread"] is lock
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if lock.locked():
            lock.release()


@pytest.mark.anyio
@pytest.mark.parametrize("stores", ["postgres"], indirect=True)
@pytest.mark.parametrize("delete_run", [False, True])
async def test_postgres_delete_waits_for_other_process_transaction(stores, delete_run, monkeypatch):
    store, peer = stores
    await store.put(**event())
    entered = asyncio.Event()
    pid = None
    original = peer._acquire_thread_mutation_fence

    async def observe_fence(session, thread_id):
        nonlocal pid
        pid = await session.scalar(text("SELECT pg_backend_pid()"))
        entered.set()
        await original(session, thread_id)

    monkeypatch.setattr(peer, "_acquire_thread_mutation_fence", observe_fence)
    task = None
    try:
        async with store._sf() as holder:
            async with holder.begin():
                await store._acquire_thread_mutation_fence(holder, "thread")
                coroutine = peer.delete_by_run("thread", "run", user_id="alice") if delete_run else peer.delete_by_thread("thread", user_id="alice")
                task = asyncio.create_task(coroutine)
                await asyncio.wait_for(entered.wait(), 3)

                async def observe_blocked_lock():
                    while True:
                        pending = await holder.scalar(text("SELECT count(*) FROM pg_locks WHERE pid=:pid AND locktype='advisory' AND NOT granted"), {"pid": pid})
                        if pending:
                            return
                        assert not task.done(), "deletion bypassed the held transaction fence"
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(observe_blocked_lock(), 3)
                assert not task.done()
        assert await asyncio.wait_for(task, 3) == 1
        assert await store.list_events("thread", "run") == []
        # Fence retirement is not a tombstone: a newly admitted lifecycle may
        # write again. The router must separately hold its deletion reservation.
        assert (await peer.put(**event()))["seq"] == 1
    finally:
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
