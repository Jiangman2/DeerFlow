"""Contract tests for upstream-style per-key sandbox acquire serialization."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.sandbox.acquire_serialization import AcquireSerializer
from deerflow.trace_context import get_current_trace_id, request_trace_context


def test_same_key_never_overlaps_and_table_is_reclaimed():
    serializer = AcquireSerializer()
    active = 0
    maximum = 0
    counter_lock = threading.Lock()

    def worker():
        nonlocal active, maximum
        with serializer.hold("scope"):
            with counter_lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with counter_lock:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert maximum == 1
    assert not serializer._table
    serializer.close()


def test_different_keys_proceed_concurrently():
    serializer = AcquireSerializer()
    barrier = threading.Barrier(2, timeout=2)

    def worker(key: str):
        with serializer.hold(key):
            barrier.wait()

    threads = [threading.Thread(target=worker, args=(key,)) for key in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    serializer.close()


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_leak_or_block_successor():
    serializer = AcquireSerializer()
    hold = serializer.hold("scope")
    hold.__enter__()
    cancelled = asyncio.create_task(_hold_once(serializer, "scope"))
    await asyncio.sleep(0.05)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    successor = asyncio.create_task(_hold_once(serializer, "scope"))
    hold.__exit__(None, None, None)
    await asyncio.wait_for(successor, timeout=2)
    await _wait_for_table_empty(serializer)
    serializer.close()


@pytest.mark.asyncio
async def test_async_wait_stays_off_default_executor():
    serializer = AcquireSerializer()
    loop = asyncio.get_running_loop()

    class ExplodingExecutor(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            raise AssertionError("serializer used the default executor")

    previous = loop._default_executor
    loop.set_default_executor(ExplodingExecutor())
    try:
        entered = asyncio.Event()

        async def holder():
            async with serializer.hold_async("scope"):
                entered.set()
                await asyncio.sleep(0.15)

        holder_task = asyncio.create_task(holder())
        await entered.wait()
        waiter = asyncio.create_task(_hold_once(serializer, "scope"))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        await holder_task
        await asyncio.wait_for(waiter, timeout=2)
    finally:
        serializer.close()
        if previous is not None:
            loop.set_default_executor(previous)


@pytest.mark.asyncio
async def test_run_on_executor_preserves_request_context():
    serializer = AcquireSerializer()
    try:
        with request_trace_context("trace-acquire-serializer"):
            seen = await serializer.run_on_executor(get_current_trace_id)
        assert seen == "trace-acquire-serializer"
    finally:
        serializer.close()


@pytest.mark.asyncio
async def test_close_is_idempotent_and_rejects_new_holders():
    serializer = AcquireSerializer()
    async with serializer.hold_async("active"):
        serializer.close()
        serializer.close()
    with pytest.raises(RuntimeError, match="closed"):
        async with serializer.hold_async("new"):
            pass


async def _hold_once(serializer: AcquireSerializer[str], key: str) -> None:
    async with serializer.hold_async(key):
        pass


async def _wait_for_table_empty(serializer: AcquireSerializer[str], timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not serializer._table:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("serializer lock table did not drain")
