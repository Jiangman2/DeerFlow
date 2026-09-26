"""Official #5214 Journal loop ownership prerequisite, without enabling new guards."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


def record(journal):
    journal.record_middleware("tool_progress", name="ToolProgressMiddleware", hook="wrap_tool_call", action="warn", changes={"to_phase": "warned"})


@pytest.mark.anyio
async def test_worker_thread_callback_runs_on_journal_owner_loop():
    store = MemoryRunEventStore()
    journal = RunJournal("r", "t", store, flush_threshold=1)
    threads = []
    original = journal._put

    def put(**kwargs):
        threads.append(threading.get_ident())
        original(**kwargs)

    journal._put = put
    await asyncio.to_thread(record, journal)
    await journal.flush()
    assert threads == [threading.get_ident()]
    assert len(await store.list_events("t", "r")) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("cross_thread", [False, True])
async def test_threshold_append_cannot_race_explicit_flush(cross_thread):
    class BlockingStore(MemoryRunEventStore):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.active = 0
            self.maximum = 0

        async def put_batch(self, events):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                if not self.started.is_set():
                    self.started.set()
                    await self.release.wait()
                return await super().put_batch(events)
            finally:
                self.active -= 1

    store = BlockingStore()
    journal = RunJournal("r", "t", store, flush_threshold=1)
    journal._buffer.append(journal._make_event(event_type="middleware:first", category="middleware"))
    flushing = asyncio.create_task(journal.flush())
    try:
        await asyncio.wait_for(store.started.wait(), 2)
        if cross_thread:
            await asyncio.to_thread(record, journal)
        else:
            record(journal)
        await asyncio.sleep(0)
    finally:
        store.release.set()
        await flushing
    assert store.maximum == 1
    assert [e["event_type"] for e in await store.list_events("t", "r")] == ["middleware:first", "middleware:tool_progress"]


def test_sync_journal_without_owner_loop_keeps_compatibility():
    store = MemoryRunEventStore()
    journal = RunJournal("r", "t", store)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(record, journal).result(timeout=2)
    asyncio.run(journal.flush())
    assert len(asyncio.run(store.list_events("t", "r"))) == 1


def test_owner_loop_identity_survives_thread_move():
    loop = asyncio.new_event_loop()

    async def build():
        return RunJournal("r", "t", MemoryRunEventStore())

    async def on_owner(journal):
        record(journal)
        assert len(journal._buffer) == 1
        await journal.close()

    try:
        journal = loop.run_until_complete(build())
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(loop.run_until_complete, on_owner(journal)).result(timeout=2)
    finally:
        loop.close()


@pytest.mark.parametrize("closed", [False, True])
def test_stopped_owner_loop_does_not_accept_late_cross_thread_events(closed):
    loop = asyncio.new_event_loop()

    async def build():
        return RunJournal("r", "t", MemoryRunEventStore())

    journal = loop.run_until_complete(build())
    try:
        if closed:
            loop.close()
        record(journal)
        assert journal._buffer == []
    finally:
        if not loop.is_closed():
            loop.run_until_complete(journal.close(flush=False))
            loop.close()


@pytest.mark.anyio
async def test_late_scheduled_event_is_discarded_after_ownership_loss():
    store = MemoryRunEventStore()
    journal = RunJournal("r", "t", store)
    # Block the owner loop briefly until the worker has queued, not executed,
    # the callback, then fence the run before yielding to queued events.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(record, journal).result(timeout=2)
    await journal.close(flush=False)
    await asyncio.sleep(0)
    assert journal._buffer == []
    assert await store.list_events("t", "r") == []


@pytest.mark.anyio
async def test_flush_failure_releases_auto_flush_suppression():
    class FailOnce(MemoryRunEventStore):
        failed = False

        async def put_batch(self, events):
            if not self.failed:
                self.failed = True
                raise RuntimeError("failure")
            return await super().put_batch(events)

    store = FailOnce()
    journal = RunJournal("r", "t", store)
    record(journal)
    with pytest.raises(RuntimeError, match="failure"):
        await journal.flush()
    assert not journal._explicit_flush_in_progress
    await journal.close()
    assert len(await store.list_events("t", "r")) == 1
