"""Sync checkpoint mutations cannot commit after their caller unwinds."""

import asyncio
import threading
from types import SimpleNamespace

import pytest

from deerflow.runtime.goal import _call_checkpointer_method


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["put", "put_writes", "delete_thread"])
async def test_cancel_waits_for_sync_checkpoint_mutation(method_name):
    started = threading.Event()
    finish = threading.Event()
    committed = threading.Event()

    def mutate():
        started.set()
        finish.wait(timeout=5)
        committed.set()

    checkpointer = SimpleNamespace(**{method_name: mutate})
    task = asyncio.create_task(_call_checkpointer_method(checkpointer, "a" + method_name, method_name))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert committed.is_set()
    finally:
        finish.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
