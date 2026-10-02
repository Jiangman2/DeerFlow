from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.runtime.keyed_lock import AsyncKeyedLockTable


class _WeakKey:
    pass


@pytest.mark.anyio
async def test_late_arrival_cannot_bypass_queued_waiter() -> None:
    table = AsyncKeyedLockTable[str]()
    release_first = asyncio.Event()
    release_second = asyncio.Event()
    first_entered = asyncio.Event()
    second_started = asyncio.Event()
    second_entered = asyncio.Event()
    third_started = asyncio.Event()
    third_entered = asyncio.Event()
    active = 0
    max_active = 0
    order: list[str] = []

    async def participant(
        name: str,
        started: asyncio.Event | None,
        entered: asyncio.Event,
        release: asyncio.Event | None,
    ) -> None:
        nonlocal active, max_active
        if started is not None:
            started.set()
        async with table.hold("thread"):
            active += 1
            max_active = max(max_active, active)
            order.append(name)
            entered.set()
            try:
                if release is not None:
                    await release.wait()
            finally:
                active -= 1

    first = asyncio.create_task(participant("first", None, first_entered, release_first))
    await first_entered.wait()
    second = asyncio.create_task(participant("second", second_started, second_entered, release_second))
    await second_started.wait()

    release_first.set()
    await second_entered.wait()
    third = asyncio.create_task(participant("third", third_started, third_entered, None))
    await third_started.wait()

    assert not third_entered.is_set()
    assert max_active == 1

    release_second.set()
    await asyncio.gather(first, second, third)
    assert order == ["first", "second", "third"]
    assert max_active == 1
    assert not table._entries_by_loop


@pytest.mark.anyio
async def test_cancelled_waiter_releases_participation_and_key() -> None:
    table = AsyncKeyedLockTable[_WeakKey]()
    key = _WeakKey()
    key_ref = weakref.ref(key)
    release_holder = asyncio.Event()
    holder_entered = asyncio.Event()
    waiter_started = asyncio.Event()

    async def holder(lock_key: _WeakKey) -> None:
        async with table.hold(lock_key):
            holder_entered.set()
            await release_holder.wait()

    async def waiter(lock_key: _WeakKey) -> None:
        waiter_started.set()
        async with table.hold(lock_key):
            raise AssertionError("cancelled waiter entered")

    holder_task = asyncio.create_task(holder(key))
    await holder_entered.wait()
    waiter_task = asyncio.create_task(waiter(key))
    await waiter_started.wait()
    waiter_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter_task

    release_holder.set()
    await holder_task
    del holder_task, waiter_task, key
    gc.collect()
    assert key_ref() is None
    assert not table._entries_by_loop


def test_same_key_is_independent_across_event_loops() -> None:
    table = AsyncKeyedLockTable[str]()
    barrier = threading.Barrier(2, timeout=2)

    def run_loop() -> None:
        async def run() -> None:
            async with table.hold("thread"):
                await asyncio.to_thread(barrier.wait)

        asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run_loop) for _ in range(2)]
        for future in futures:
            future.result(timeout=3)
