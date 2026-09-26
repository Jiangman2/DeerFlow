"""v2.1 #5401 owner resolution without changing read authorization semantics."""

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio

from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import get_current_user, reset_current_user, set_current_user


@pytest_asyncio.fixture(params=["none", "memory", "sqlite"])
async def owner_store(request, tmp_path):
    if request.param != "sqlite":
        yield None if request.param == "none" else MemoryRunStore()
        return

    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
    from deerflow.persistence.run import RunRepository

    try:
        await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'owners.db'}", sqlite_dir=str(tmp_path))
        yield RunRepository(get_session_factory())
    finally:
        await close_engine()


@pytest.fixture(params=["create", "create_or_reject"])
def admission(request):
    return request.param


@pytest.mark.asyncio
async def test_omitted_owner_matches_memory_store_and_scoped_history(owner_store, admission):
    manager = RunManager(store=owner_store)
    user = SimpleNamespace(id=UUID("9121b36d-bcef-42b1-89e1-c501dab730a6"))
    token = set_current_user(user)
    try:
        record = await getattr(manager, admission)("thread")
        assert record.user_id == str(user.id)
        assert get_current_user() is user
        records = await manager.get_many_by_thread("thread", {record.run_id})
        assert records[record.run_id] is record
        assert await manager.get_many_by_thread("thread", {record.run_id}, user_id="other-user") == {}
        assert await manager.get_many_by_thread("other-thread", {record.run_id}) == {}
        if owner_store is not None:
            row = await owner_store.get(record.run_id, user_id=str(user.id))
            assert row["user_id"] == record.user_id
            peer = RunManager(store=owner_store)
            peer_record = await peer.get(record.run_id, user_id=str(user.id))
            assert peer_record.user_id == record.user_id
            assert peer_record.store_only is True
            assert await peer.get(record.run_id, user_id="other-user") is None
            assert record.run_id not in peer._runs
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_explicit_trusted_owner_is_not_replaced_by_ambient_user(owner_store, admission):
    manager = RunManager(store=owner_store)
    ambient_user = get_current_user()
    record = await getattr(manager, admission)("thread", user_id="scheduled-owner")
    assert record.user_id == "scheduled-owner"
    assert get_current_user() is ambient_user
    assert await manager.get_many_by_thread("thread", {record.run_id}) == {}
    assert (await manager.get_many_by_thread("thread", {record.run_id}, user_id="scheduled-owner"))[record.run_id] is record
    if owner_store is not None:
        assert (await owner_store.get(record.run_id, user_id="scheduled-owner"))["user_id"] == "scheduled-owner"


@pytest.mark.no_auto_user
@pytest.mark.asyncio
async def test_no_ambient_owner_does_not_invent_default_bucket(owner_store, admission):
    manager = RunManager(store=owner_store)
    assert get_current_user() is None
    if owner_store is not None and not isinstance(owner_store, MemoryRunStore):
        with pytest.raises(RuntimeError, match="no user context"):
            await getattr(manager, admission)("thread")
        assert manager._runs == {}
        assert await owner_store.list_by_thread("thread", user_id=None) == []
    else:
        record = await getattr(manager, admission)("thread")
        assert record.user_id is None
        if owner_store is not None:
            assert (await owner_store.get(record.run_id))["user_id"] is None
    assert get_current_user() is None


@pytest.mark.asyncio
async def test_parallel_admissions_capture_their_own_user(owner_store, admission):
    manager = RunManager(store=owner_store)
    parent = get_current_user()
    ready = asyncio.Event()
    arrived = 0

    async def admit(user_id):
        nonlocal arrived
        user = SimpleNamespace(id=user_id)
        token = set_current_user(user)
        try:
            arrived += 1
            if arrived == 2:
                ready.set()
            await ready.wait()
            record = await getattr(manager, admission)("thread-" + user_id)
            assert record.user_id == user_id
            assert get_current_user() is user
            return record
        finally:
            reset_current_user(token)

    first, second = await asyncio.wait_for(asyncio.gather(admit("user-a"), admit("user-b")), timeout=5)
    assert first.user_id != second.user_id
    assert get_current_user() is parent
    assert await manager.get_many_by_thread(first.thread_id, {first.run_id}, user_id=second.user_id) == {}
