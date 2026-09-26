"""Official #5112 Journal cleanup, kept separate from enterprise callbacks."""

import asyncio
import weakref

import pytest

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal


@pytest.mark.anyio
async def test_close_flushes_and_detaches_runtime_dependencies():
    class ProgressReporter:
        async def __call__(self, snapshot):
            del snapshot

    store = MemoryRunEventStore()
    reporter = ProgressReporter()
    store_ref = weakref.ref(store)
    reporter_ref = weakref.ref(reporter)
    journal = RunJournal(
        "r-close",
        "t-close",
        store,
        progress_reporter=reporter,
        flush_threshold=100,
    )
    journal.record_middleware("test", name="test", hook="after", action="record", changes={})

    await journal.close()

    assert journal._closed is True
    assert journal._store is None
    assert journal._progress_reporter is None
    assert journal._buffer == []
    assert journal._pending_flush_tasks == set()
    del store, reporter
    await asyncio.sleep(0)
    assert store_ref() is None
    assert reporter_ref() is None


@pytest.mark.anyio
async def test_close_preserves_buffer_and_dependencies_when_flush_fails():
    class FailOnceRunEventStore(MemoryRunEventStore):
        def __init__(self) -> None:
            super().__init__()
            self.put_batch_calls = 0

        async def put_batch(self, events):
            self.put_batch_calls += 1
            if self.put_batch_calls == 1:
                raise RuntimeError("transient store failure")
            return await super().put_batch(events)

    store = FailOnceRunEventStore()
    journal = RunJournal("r-close-retry", "t-close-retry", store, flush_threshold=100)
    journal.record_middleware("test", name="test", hook="after", action="record", changes={})

    with pytest.raises(RuntimeError, match="transient store failure"):
        await journal.close()

    assert journal._closed is False
    assert journal._store is store
    assert len(journal._buffer) == 1

    await journal.close()

    assert journal._closed is True
    assert journal._store is None
    assert journal._buffer == []
    events = await store.list_events("t-close-retry", "r-close-retry")
    assert [event["event_type"] for event in events] == ["middleware:test"]


@pytest.mark.anyio
async def test_close_without_flush_discards_buffer_and_detaches_runtime_dependencies():
    class TrackingRunEventStore(MemoryRunEventStore):
        def __init__(self) -> None:
            super().__init__()
            self.put_batch_calls = 0

        async def put_batch(self, events):
            self.put_batch_calls += 1
            return await super().put_batch(events)

    store = TrackingRunEventStore()
    journal = RunJournal("r-close-discard", "t-close-discard", store, flush_threshold=100)
    journal.record_middleware("test", name="test", hook="after", action="record", changes={})

    await journal.close(flush=False)

    assert store.put_batch_calls == 0
    assert journal._closed is True
    assert journal._store is None
    assert journal._buffer == []


@pytest.mark.anyio
async def test_close_without_flush_detaches_when_cancellation_interrupts_pending_task_cleanup():
    store = MemoryRunEventStore()
    journal = RunJournal("r-close-cancelled", "t-close-cancelled", store, flush_threshold=100)
    journal.record_middleware("test", name="test", hook="after", action="record", changes={})
    first_cancellation_seen = asyncio.Event()

    async def stubborn_pending_flush() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            first_cancellation_seen.set()
            await asyncio.Event().wait()

    pending_flush = asyncio.create_task(stubborn_pending_flush())
    journal._pending_flush_tasks.add(pending_flush)
    close_task = asyncio.create_task(journal.close(flush=False))
    await asyncio.wait_for(first_cancellation_seen.wait(), timeout=1)

    close_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close_task

    assert pending_flush.done()
    assert journal._closed is True
    assert journal._store is None
    assert journal._buffer == []
    assert journal._pending_flush_tasks == set()
