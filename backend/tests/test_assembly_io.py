"""Unit tests for the dedicated agent/tool assembly pool."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import deerflow.utils.assembly_io as assembly_io
from deerflow.trace_context import get_current_trace_id, request_trace_context


def test_pending_count_returns_to_zero_and_context_is_preserved() -> None:
    async def main() -> str | None:
        with request_trace_context("assembly-trace"):
            return await assembly_io.run_assembly(get_current_trace_id)

    assert asyncio.run(main()) == "assembly-trace"
    assert assembly_io._pending_assemblies == 0


def test_abandoned_loop_does_not_wedge_the_counter() -> None:
    worker_started = threading.Event()
    worker_release = threading.Event()

    def parked() -> str:
        worker_started.set()
        worker_release.wait(timeout=10)
        return "done"

    loop = asyncio.new_event_loop()

    def run() -> None:
        try:
            loop.run_until_complete(assembly_io.run_assembly(parked))
        except BaseException:
            pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert worker_started.wait(timeout=5)
    loop.call_soon_threadsafe(loop.stop)
    worker_release.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    loop.close()

    deadline = time.monotonic() + 5
    while assembly_io._pending_assemblies and time.monotonic() < deadline:
        time.sleep(0.01)
    assert assembly_io._pending_assemblies == 0


def test_queued_cancellation_releases_pending_count() -> None:
    worker_started = threading.Event()
    worker_release = threading.Event()

    def parked() -> str:
        worker_started.set()
        worker_release.wait(timeout=10)
        return "done"

    pool = ThreadPoolExecutor(max_workers=1)
    original_executor = assembly_io._ASSEMBLY_EXECUTOR
    assembly_io._ASSEMBLY_EXECUTOR = pool
    try:

        async def main() -> None:
            loop = asyncio.get_running_loop()
            first = asyncio.create_task(assembly_io.run_assembly(parked))
            assert await loop.run_in_executor(None, worker_started.wait, 5.0)
            second = asyncio.create_task(assembly_io.run_assembly(lambda: "queued"))
            deadline = time.monotonic() + 5
            while assembly_io._pending_assemblies != 2 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert assembly_io._pending_assemblies == 2

            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            assert assembly_io._pending_assemblies == 1

            worker_release.set()
            assert await first == "done"
            assert assembly_io._pending_assemblies == 0

        asyncio.run(main())
    finally:
        assembly_io._ASSEMBLY_EXECUTOR = original_executor
        pool.shutdown(wait=True)
