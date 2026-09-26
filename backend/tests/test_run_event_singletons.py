"""Terminal-receipt storage primitives, with enterprise owner isolation.

Only the loopback disposable PostgreSQL test database is accepted. Memory and
JSONL intentionally provide no cross-process persistence guarantee.
"""

import asyncio
import os
import threading
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.runtime.events.store.db import DbRunEventStore
from deerflow.runtime.events.store.jsonl import JsonlRunEventStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.user_context import reset_current_user, set_current_user


@pytest.fixture
def anyio_backend():
    return "asyncio", {"loop_factory": asyncio.SelectorEventLoop}


@pytest.fixture(params=["memory", "jsonl", "sqlite", "postgres"])
async def stores(request, tmp_path, anyio_backend):
    token = set_current_user(SimpleNamespace(id="alice"))
    engine = admin = schema = None
    try:
        if request.param == "memory":
            store = MemoryRunEventStore()
            yield store, store
            return
        if request.param == "jsonl":
            store = JsonlRunEventStore(tmp_path)
            yield store, store
            return
        if request.param == "postgres":
            uri = os.getenv("TEST_POSTGRES_URI")
            if not uri:
                pytest.skip("requires disposable loopback TEST_POSTGRES_URI")
            url = make_url(uri)
            assert url.host == "127.0.0.1" and url.database == "deerflow_v21_test"
            schema = "v21_events_" + uuid.uuid4().hex
            admin = create_async_engine(uri)
            async with admin.begin() as conn:
                await conn.execute(CreateSchema(schema))
            engine = create_async_engine(uri, connect_args={"server_settings": {"search_path": schema}})
        else:
            engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "events.db"))
        async with engine.begin() as conn:
            await conn.run_sync(RunEventRow.__table__.create)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        store = DbRunEventStore(sf)
        # Independent lock registries exercise the actual PG transaction fence.
        yield store, DbRunEventStore(sf) if request.param == "postgres" else store
    finally:
        reset_current_user(token)
        if engine is not None:
            await engine.dispose()
        if admin is not None:
            try:
                assert schema.startswith("v21_events_")
                async with admin.begin() as conn:
                    await conn.execute(DropSchema(schema, cascade=True))
            finally:
                await admin.dispose()


def event(run_id="run", event_type="run.delivery", **kwargs):
    return {"thread_id": "thread", "run_id": run_id, "event_type": event_type, "category": "lifecycle", "content": {"status": "success", "artifacts": ["中文产物"]}, **kwargs}


@pytest.mark.anyio
async def test_singleton_keeps_original_receipt_and_does_not_consume_seq(stores):
    store, peer = stores
    original, created = await store.put_if_absent(**event(created_at="2026-01-01T00:00:00+00:00", metadata={"source": "worker"}))
    replay, duplicate = await peer.put_if_absent(**event(content={"status": "error"}, metadata={"source": "recovery"}))
    assert created and not duplicate and replay == original
    assert original["seq"] == 1 and original["metadata"]["source"] == "worker"
    assert original["created_at"] == "2026-01-01T00:00:00+00:00"
    assert (await store.put(**event(event_type="ordinary")))["seq"] == 2
    assert len(await store.list_events("thread", "run")) == 2
    assert await store.list_messages("thread") == []


@pytest.mark.anyio
async def test_singleton_key_is_thread_run_type_and_ordinary_writes_remain_append_only(stores):
    store, _ = stores
    for args in (event(), event(run_id="other"), event(event_type="run.other"), event(thread_id="other-thread")):
        assert (await store.put_if_absent(**args))[1] is True
    await store.put(**event())
    await store.put_batch([event(), event()])
    records = await store.list_events("thread", "run")
    assert len(records) == 5
    assert (await store.put_if_absent(**event()))[0] == records[0]


@pytest.mark.anyio
async def test_concurrent_singleton_and_ordinary_writers_share_seq_fence(stores):
    store, peer = stores
    outcomes = await asyncio.gather(
        *[(store if i % 2 else peer).put_if_absent(**event()) for i in range(20)],
        store.put_batch([event(run_id="ordinary", event_type="trace") for _ in range(10)]),
        peer.put(**event(run_id="ordinary", event_type="trace")),
    )
    assert sum(created for _, created in outcomes[:20]) == 1
    assert len({record["seq"] for record, _ in outcomes[:20]}) == 1
    receipts = await store.list_events("thread", "run")
    ordinary = await store.list_events("thread", "ordinary")
    assert len(receipts) == 1 and len(ordinary) == 11
    assert sorted(record["seq"] for record in receipts + ordinary) == list(range(1, 13))


@pytest.mark.anyio
async def test_singleton_delete_releases_key_without_affecting_other_run(stores):
    store, _ = stores
    await store.put_if_absent(**event())
    other, _ = await store.put_if_absent(**event(run_id="other"))
    assert await store.delete_by_run("thread", "run") == 1
    replacement, created = await store.put_if_absent(**event())
    assert created and replacement["seq"] > other["seq"]
    assert await store.list_events("thread", "other") == [other]


@pytest.mark.anyio
@pytest.mark.parametrize("stores", ["sqlite", "postgres"], indirect=True)
async def test_db_singleton_cannot_expose_another_owners_receipt(stores):
    store, _ = stores
    original, _ = await store.put_if_absent(**event())
    token = set_current_user(SimpleNamespace(id="bob"))
    try:
        with pytest.raises(PermissionError, match="another owner"):
            await store.put_if_absent(**event(content="not mine"))
        assert await store.list_events("thread", "run") == []
    finally:
        reset_current_user(token)
    assert await store.list_events("thread", "run") == [original]
    token = set_current_user(None)
    try:
        recovered, created = await store.put_if_absent(**event())
        assert not created and recovered == original and recovered["user_id"] == "alice"
    finally:
        reset_current_user(token)


@pytest.mark.anyio
async def test_jsonl_restart_preserves_singleton_and_run_files(tmp_path):
    store = JsonlRunEventStore(tmp_path)
    await store.put_batch([event(run_id="a"), event(run_id="b"), event(run_id="a", event_type="trace")])
    assert [e["run_id"] for e in await store.list_events("thread", "a")] == ["a", "a"]
    assert [e["run_id"] for e in await store.list_events("thread", "b")] == ["b"]
    restarted = JsonlRunEventStore(tmp_path)
    record, created = await restarted.put_if_absent(**event(run_id="b"))
    assert not created and record["seq"] == 2
    assert (await restarted.put(**event(run_id="c")))["seq"] == 4
    assert await restarted.delete_by_run("thread", "a") == 2
    assert await restarted.list_events("thread", "b") == [record]


@pytest.mark.anyio
@pytest.mark.parametrize("second_file_exists", [False, True])
async def test_jsonl_partial_group_failure_restores_existing_bytes_before_retry(tmp_path, monkeypatch, second_file_exists):
    store = JsonlRunEventStore(tmp_path)
    await store.put(**event(run_id="a", event_type="original"))
    if second_file_exists:
        await store.put(**event(run_id="b", event_type="original"))
    originals = {run_id: await store.list_events("thread", run_id) for run_id in ("a", "b")}
    original_append = store._append_records

    def fail_second_after_partial_write(path, records):
        original_append(path, records[:1])
        if path.stem == "b":
            raise OSError("synthetic partial append")

    monkeypatch.setattr(store, "_append_records", fail_second_after_partial_write)
    batch = [event(run_id="a"), event(run_id="b"), event(run_id="b", event_type="trace")]
    with pytest.raises(OSError, match="partial append"):
        await store.put_batch(batch)
    for run_id in ("a", "b"):
        assert await store.list_events("thread", run_id) == originals[run_id]
    if not second_file_exists:
        assert not store._run_file("thread", "b").exists()
    monkeypatch.setattr(store, "_append_records", original_append)
    await store.put_batch(batch)
    assert len(await store.list_events("thread", "a")) == len(originals["a"]) + 1
    assert len(await store.list_events("thread", "b")) == len(originals["b"]) + 2


@pytest.mark.anyio
async def test_jsonl_cancelled_singleton_drains_io_before_retry(tmp_path, monkeypatch):
    store = JsonlRunEventStore(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original_write = store._write_record

    def blocked_write(record):
        entered.set()
        assert release.wait(5), "test did not release filesystem writer"
        original_write(record)

    monkeypatch.setattr(store, "_write_record", blocked_write)
    first = asyncio.create_task(store.put_if_absent(**event()))
    retry = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        first.cancel()
        await asyncio.sleep(0)
        first.cancel()
        retry = asyncio.create_task(store.put_if_absent(**event(content="retry")))
        await asyncio.sleep(0)
        assert not first.done() and not retry.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 3)
        record, created = await asyncio.wait_for(retry, 3)
        assert not created and record["content"] == event()["content"]
        assert len(await store.list_events("thread", "run")) == 1
    finally:
        release.set()
        await asyncio.gather(*[task for task in (first, retry) if task is not None], return_exceptions=True)
