"""Cancellation must not release a JSONL thread lock before disk I/O settles."""

from __future__ import annotations

import asyncio
import threading

import pytest

from deerflow.runtime.events.store.jsonl import JsonlRunEventStore


def _event(*, thread_id: str = "t1", run_id: str = "r1", content: str = "message") -> dict:
    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "event_type": "message",
        "category": "message",
        "content": content,
    }


class _PausedIO:
    """Pause real filesystem work at a deterministic point."""

    def __init__(self, operation):
        self.operation = operation
        self.loop = asyncio.get_running_loop()
        self.entered = asyncio.Event()
        self.finished = asyncio.Event()
        self.release = threading.Event()

    def __call__(self, *args):
        self.loop.call_soon_threadsafe(self.entered.set)
        try:
            if not self.release.wait(10):
                raise TimeoutError("test did not release paused filesystem operation")
            return self.operation(*args)
        finally:
            self.loop.call_soon_threadsafe(self.finished.set)


async def _checkpoint() -> None:
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.anyio
@pytest.mark.parametrize("cancellations", [1, 3])
async def test_cancelled_write_drains_before_delete(tmp_path, monkeypatch, cancellations):
    store = JsonlRunEventStore(tmp_path)
    await store.put(**_event(content="baseline"))
    paused = _PausedIO(store._write_record)
    monkeypatch.setattr(store, "_write_record", paused)

    pending = asyncio.create_task(store.put(**_event(run_id="r2", content="pending")))
    deletion = None
    try:
        await asyncio.wait_for(paused.entered.wait(), 5)
        for _ in range(cancellations):
            pending.cancel()
            await _checkpoint()
        deletion = asyncio.create_task(store.delete_by_thread("t1"))
        await _checkpoint()
        assert not pending.done(), "cancellation returned while disk I/O still owned the thread"
        assert not deletion.done(), "deletion overtook the admitted write"

        paused.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert await deletion == 2
        assert await store.count_messages("t1") == 0
    finally:
        paused.release.set()
        await asyncio.gather(pending, *([deletion] if deletion is not None else []), return_exceptions=True)
        await asyncio.wait_for(paused.finished.wait(), 5)


@pytest.mark.anyio
@pytest.mark.parametrize("method,io_method", [("delete_by_thread", "_delete_thread_files"), ("delete_by_run", "_delete_run_file")])
async def test_cancelled_delete_drains_before_later_write(tmp_path, monkeypatch, method, io_method):
    store = JsonlRunEventStore(tmp_path)
    await store.put(**_event(content="baseline"))
    paused = _PausedIO(getattr(store, io_method))
    monkeypatch.setattr(store, io_method, paused)

    pending = asyncio.create_task(store.delete_by_thread("t1") if method == "delete_by_thread" else store.delete_by_run("t1", "r1"))
    writer = None
    try:
        await asyncio.wait_for(paused.entered.wait(), 5)
        pending.cancel()
        await _checkpoint()
        pending.cancel()
        await _checkpoint()
        writer = asyncio.create_task(store.put(**_event(content="later")))
        await _checkpoint()
        assert not pending.done()
        assert not writer.done()

        paused.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        saved = await writer
        assert saved["seq"] == (1 if method == "delete_by_thread" else 2)
        assert [row["content"] for row in await store.list_messages("t1")] == ["later"]
    finally:
        paused.release.set()
        await asyncio.gather(pending, *([writer] if writer is not None else []), return_exceptions=True)
        await asyncio.wait_for(paused.finished.wait(), 5)


@pytest.mark.anyio
async def test_cancellation_while_waiting_for_lock_never_starts_write(tmp_path):
    store = JsonlRunEventStore(tmp_path)
    async with store._get_write_lock("t1"):
        pending = asyncio.create_task(store.put(**_event()))
        await _checkpoint()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    assert await store.count_messages("t1") == 0
    assert not list(tmp_path.rglob("*.jsonl"))


@pytest.mark.anyio
async def test_cancelled_multithread_batch_stops_after_current_group(tmp_path, monkeypatch):
    store = JsonlRunEventStore(tmp_path)
    append = store._append_records
    paused = _PausedIO(append)

    def pause_first_thread(path, records):
        return paused(path, records) if records[0]["thread_id"] == "t1" else append(path, records)

    monkeypatch.setattr(store, "_append_records", pause_first_thread)
    pending = asyncio.create_task(
        store.put_batch(
            [
                _event(thread_id="t1", content="first"),
                _event(thread_id="t2", content="must-not-start"),
            ]
        )
    )
    try:
        await asyncio.wait_for(paused.entered.wait(), 5)
        pending.cancel()
        await _checkpoint()
        pending.cancel()
        await _checkpoint()
        assert not pending.done()
        assert "t2" not in store._seq_counters

        paused.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert [row["content"] for row in await store.list_messages("t1")] == ["first"]
        assert await store.list_messages("t2") == []
        assert "t2" not in store._seq_counters
    finally:
        paused.release.set()
        await asyncio.gather(pending, return_exceptions=True)
        await asyncio.wait_for(paused.finished.wait(), 5)
