import asyncio
import weakref

import pytest

from deerflow.runtime.events.store.db import DbRunEventStore


class _PausedDeleteSession:
    def __init__(self, scalar_started: asyncio.Event, allow_scalar: asyncio.Event) -> None:
        self._scalar_started = scalar_started
        self._allow_scalar = allow_scalar

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def get_bind(self):
        return None

    def begin(self):
        return self

    async def scalar(self, _stmt):
        self._scalar_started.set()
        await self._allow_scalar.wait()
        return 1

    async def execute(self, _stmt, _params=None):
        return None

    async def commit(self) -> None:
        return None


@pytest.mark.anyio
async def test_delete_waiter_handoff_keeps_one_write_lock_generation():
    scalar_started = asyncio.Event()
    allow_scalar = asyncio.Event()
    session = _PausedDeleteSession(scalar_started, allow_scalar)
    store = DbRunEventStore(lambda: session)

    old_lock = store._get_write_lock("t1")
    old_lock_ref = weakref.ref(old_lock)
    await old_lock.acquire()

    waiter_resolved = asyncio.Event()
    waiter_entered = asyncio.Event()
    release_waiter = asyncio.Event()

    async def queued_writer() -> None:
        lock = store._get_write_lock("t1")
        waiter_resolved.set()
        async with lock:
            waiter_entered.set()
            await release_waiter.wait()

    delete_task = asyncio.create_task(store.delete_by_thread("t1", user_id=None))
    await asyncio.sleep(0)
    assert not scalar_started.is_set()
    assert not delete_task.done()

    waiter_task = asyncio.create_task(queued_writer())
    await waiter_resolved.wait()
    await asyncio.sleep(0)
    assert not waiter_entered.is_set()

    # Delete queues behind the holder and ahead of the new writer. Keep the
    # same generation during both handoffs rather than splitting the registry.
    old_lock.release()
    del old_lock
    await asyncio.wait_for(scalar_started.wait(), 3)
    assert not waiter_entered.is_set()
    allow_scalar.set()
    await delete_task
    await waiter_entered.wait()

    try:
        # The waiter still owns the old generation. Any later writer must
        # resolve that exact lock rather than creating a concurrent generation.
        assert store._get_write_lock("t1") is old_lock_ref()
    finally:
        release_waiter.set()
        await waiter_task
