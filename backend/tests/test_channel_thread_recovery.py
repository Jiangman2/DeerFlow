"""Real SQL and independent-manager races for stale channel thread recovery."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import delete, event, select, text, update

from app.channels.manager import ChannelManager
from app.channels.message_bus import InboundMessage, MessageBus
from app.channels.store import ChannelStore
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME
from deerflow.persistence.channel_connections import ChannelConnectionRepository, ChannelConnectionRow, ChannelConversationRow
from deerflow.persistence.channel_connections.recovery import ChannelRecoveryRefused, ChannelThreadBinding, ChannelThreadRecovery
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow
from deerflow.persistence.thread_meta.sql import ThreadMetaRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
async def fixture(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}", sqlite_dir=str(tmp_path))
    sf = get_session_factory()
    repo = ChannelConnectionRepository(sf)
    connection = await repo.upsert_connection(owner_user_id="alice", provider="feishu", external_account_id="user-a", workspace_id="workspace-a")
    binding = ChannelThreadBinding(connection["id"], "alice", "feishu", "user-a", "workspace-a", "chat-a", "topic-a")
    await repo.set_thread_id(connection_id=binding.connection_id, owner_user_id="alice", provider="feishu", external_conversation_id="chat-a", external_topic_id="topic-a", thread_id="old")
    # Only the indexed existence contract is required; no checkpoint content is
    # read and no checkpointer schema is owned by the recovery implementation.
    async with sf() as session:
        await session.execute(text("CREATE TABLE checkpoints (thread_id TEXT PRIMARY KEY, checkpoint BLOB)"))
        session.add(ThreadCreationRow(thread_id="old", user_id="alice"))
        session.add(RunRow(run_id="historical", thread_id="old", user_id="alice", status="success", last_ai_message="historical answer"))
        await session.commit()
    msg = InboundMessage(channel_name="feishu", chat_id="chat-a", user_id="user-a", text="hello", topic_id="topic-a", connection_id=binding.connection_id, owner_user_id="alice", workspace_id="workspace-a")
    data = SimpleNamespace(sf=sf, repo=repo, binding=binding, recovery=ChannelThreadRecovery(sf, binding), msg=msg, tmp_path=tmp_path, meta=ThreadMetaRepository(sf))
    try:
        yield data
    finally:
        await close_engine()


def manager(f):
    return ChannelManager(bus=MessageBus(), store=ChannelStore(path=f.tmp_path / "legacy.json"), connection_repo=f.repo)


def client(f, candidate="new", before_return=None):
    async def create(**kwargs):
        assert kwargs["headers"][INTERNAL_OWNER_USER_ID_HEADER_NAME] == "alice"
        assert "thread_id" not in kwargs
        await f.meta.create(candidate, user_id="alice")
        if before_return:
            await before_return()
        return {"thread_id": candidate}

    return SimpleNamespace(threads=SimpleNamespace(create=AsyncMock(side_effect=create), get=AsyncMock(), update=AsyncMock()), runs=SimpleNamespace(wait=AsyncMock(), stream=AsyncMock()))


async def current(f):
    return await f.repo.get_thread_id(f.binding.connection_id, "chat-a", "topic-a")


async def test_missing_thread_recovers_and_keeps_history(fixture):
    f = fixture
    c = client(f)
    assert await manager(f)._get_or_create_thread(c, f.msg) == ("new", True)
    assert await current(f) == "new"
    async with f.sf() as session:
        assert await session.scalar(select(RunRow.last_ai_message).where(RunRow.run_id == "historical")) == "historical answer"
        assert await session.scalar(select(ThreadCreationRow.user_id).where(ThreadCreationRow.thread_id == "old")) == "alice"
        assert await session.get(ThreadMetaRow, "old") is None
    c.threads.get.assert_not_awaited()
    c.runs.wait.assert_not_awaited()
    c.runs.stream.assert_not_awaited()
    assert await manager(f)._get_or_create_thread(c, f.msg) == ("new", False)
    c.threads.create.assert_awaited_once()


@pytest.mark.parametrize("owner", ["alice", "bob", None])
async def test_existing_thread_never_replaced_or_loaded(fixture, owner):
    f = fixture
    await f.meta.create("old", user_id=owner, metadata={"large": "x" * 1000000})
    c = client(f)
    queries = []
    engine = f.sf.kw["bind"].sync_engine

    def capture(_conn, _cursor, statement, _params, _context, _many):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert await manager(f)._get_or_create_thread(c, f.msg) == ("old", False)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    c.threads.create.assert_not_awaited()
    c.threads.get.assert_not_awaited()
    assert all("metadata_json" not in sql and "kwargs_json" not in sql and "checkpoint" not in sql for sql in queries)


@pytest.mark.parametrize("state", ["pending", "running", "checkpoint", "foreign_ledger"])
async def test_incomplete_or_busy_old_thread_is_preserved(fixture, state):
    f = fixture
    async with f.sf() as session:
        if state in ("pending", "running"):
            session.add(RunRow(run_id="active", thread_id="old", user_id="alice", status=state))
        elif state == "checkpoint":
            await session.execute(text("INSERT INTO checkpoints VALUES ('old', :data)"), {"data": b"x" * 1000000})
        else:
            await session.execute(update(ThreadCreationRow).where(ThreadCreationRow.thread_id == "old").values(user_id="bob"))
        await session.commit()
    c = client(f)
    with pytest.raises(ChannelRecoveryRefused, match="not_safe_to_replace"):
        await manager(f)._get_or_create_thread(c, f.msg)
    assert await current(f) == "old"
    c.threads.create.assert_not_awaited()


@pytest.mark.parametrize("change", ["revoked", "owner", "provider", "user", "workspace", "mapping_owner", "mapping_provider", "topic"])
async def test_binding_mismatch_fails_closed(fixture, change):
    f = fixture
    async with f.sf() as session:
        connection_values = {"revoked": {"status": "revoked"}, "owner": {"owner_user_id": "bob"}, "provider": {"provider": "slack"}, "user": {"external_account_id": "other"}, "workspace": {"workspace_id": "other"}}
        mapping_values = {"mapping_owner": {"owner_user_id": "bob"}, "mapping_provider": {"provider": "slack"}, "topic": {"external_topic_id": "other"}}
        if change in connection_values:
            await session.execute(update(ChannelConnectionRow).values(**connection_values[change]))
        else:
            await session.execute(update(ChannelConversationRow).values(**mapping_values[change]))
        await session.commit()
    with pytest.raises(ChannelRecoveryRefused, match="binding_or_mapping_changed"):
        await f.recovery.inspect()


async def test_two_managers_converge_using_shared_database(fixture):
    f = fixture
    arrived = 0
    both_created = asyncio.Event()

    async def rendezvous():
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_created.set()
        await asyncio.wait_for(both_created.wait(), 5)

    first, second = client(f, "candidate-a", rendezvous), client(f, "candidate-b", rendezvous)
    results = await asyncio.gather(manager(f)._get_or_create_thread(first, f.msg), manager(f)._get_or_create_thread(second, f.msg))
    assert results[0][0] == results[1][0] == await current(f)
    assert sorted(created for _, created in results) == [False, True]
    assert await current(f) in ("candidate-a", "candidate-b")
    first.runs.wait.assert_not_awaited()
    second.runs.wait.assert_not_awaited()
    # Losing candidates remain empty: do not delete anything or replay tasks.
    async with f.sf() as session:
        assert (await session.execute(select(RunRow.run_id))).scalars().all() == ["historical"]


async def test_same_manager_serializes_missing_recovery(fixture):
    f = fixture
    m = manager(f)
    c = client(f)
    results = await asyncio.gather(*(m._get_or_create_thread(c, f.msg) for _ in range(8)))
    assert {tid for tid, _ in results} == {"new"}
    assert sum(created for _, created in results) == 1
    c.threads.create.assert_awaited_once()


async def test_two_independent_processes_publish_one_mapping(fixture):
    f = fixture
    await f.meta.create("process-a", user_id="alice")
    await f.meta.create("process-b", user_id="alice")
    worker = r"""
import asyncio, json, pathlib, sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from deerflow.persistence.channel_connections.recovery import ChannelThreadBinding, ChannelThreadRecovery
async def main():
    root, connection_id, candidate = sys.argv[1:]
    root = pathlib.Path(root)
    engine = create_async_engine('sqlite+aiosqlite:///' + str(root / 'recovery.db'))
    sf = async_sessionmaker(engine, expire_on_commit=False)
    binding = ChannelThreadBinding(connection_id, 'alice', 'feishu', 'user-a', 'workspace-a', 'chat-a', 'topic-a')
    async def create():
        (root / (candidate + '.ready')).touch()
        async with asyncio.timeout(20):
            while not all((root / (name + '.ready')).exists() for name in ('process-a','process-b')):
                await asyncio.sleep(.02)
        return candidate
    try:
        print(json.dumps(await ChannelThreadRecovery(sf, binding).recover(create)))
    finally:
        await engine.dispose()
asyncio.run(main())
"""
    processes = []
    try:
        for name in ("process-a", "process-b"):
            processes.append(await asyncio.create_subprocess_exec(sys.executable, "-c", worker, str(f.tmp_path), f.binding.connection_id, name, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE))
        output = await asyncio.wait_for(asyncio.gather(*(p.communicate() for p in processes)), 60)
        assert [p.returncode for p in processes] == [0, 0], [stderr.decode() for _, stderr in output]
        results = [json.loads(stdout) for stdout, _ in output]
        assert results[0][0] == results[1][0] == await current(f)
        assert sorted(created for _, created in results) == [False, True]
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def test_explicit_new_wins_over_inflight_recovery(fixture):
    f = fixture

    async def new_command():
        # Exercise the actual /new creation primitive, including its mapping.
        await manager(f)._create_thread(client(f, "manual-new"), f.msg)

    c = client(f, before_return=new_command)
    assert await manager(f)._get_or_create_thread(c, f.msg) == ("manual-new", False)
    assert await current(f) == "manual-new"


@pytest.mark.parametrize("change", ["revoked", "rebound", "running", "restored", "removed", "candidate_owner"])
async def test_changes_during_create_do_not_publish_candidate(fixture, change):
    f = fixture

    async def mutate():
        async with f.sf() as session:
            if change == "revoked":
                await session.execute(update(ChannelConnectionRow).values(status="revoked"))
            elif change == "rebound":
                await session.execute(update(ChannelConnectionRow).values(owner_user_id="bob"))
            elif change == "running":
                session.add(RunRow(run_id="active", thread_id="old", status="running", user_id="alice"))
            elif change == "restored":
                session.add(ThreadMetaRow(thread_id="old", user_id="alice"))
            elif change == "removed":
                await session.execute(delete(ChannelConversationRow))
            elif change == "candidate_owner":
                await session.execute(update(ThreadMetaRow).where(ThreadMetaRow.thread_id == "new").values(user_id="bob"))
            await session.commit()

    c = client(f, before_return=mutate)
    with pytest.raises(ChannelRecoveryRefused):
        await manager(f)._get_or_create_thread(c, f.msg)
    assert await current(f) == (None if change == "removed" else "old")
    c.runs.wait.assert_not_awaited()


@pytest.mark.parametrize("error", [RuntimeError("network failed"), asyncio.CancelledError()])
async def test_create_failure_or_cancel_keeps_mapping(fixture, error):
    f = fixture
    c = client(f)
    c.threads.create.side_effect = error
    with pytest.raises(type(error)):
        await manager(f)._get_or_create_thread(c, f.msg)
    assert await current(f) == "old"
    c.threads.create.assert_awaited_once()


async def test_database_failure_does_not_create_thread(fixture):
    f = fixture
    async with f.sf() as session:
        await session.execute(text("DROP TABLE checkpoints"))
        await session.commit()
    c = client(f)
    from sqlalchemy.exc import OperationalError

    with pytest.raises(OperationalError):
        await manager(f)._get_or_create_thread(c, f.msg)
    c.threads.create.assert_not_awaited()
    assert await current(f) == "old"


async def test_preferred_webhook_and_legacy_paths_unchanged(fixture):
    f = fixture
    c = client(f)
    m = manager(f)
    assert await m._get_or_create_thread(c, replace(f.msg, metadata={"preferred_thread_id": "old"})) == ("old", False)
    legacy_msg = replace(f.msg, connection_id=None)
    m.store.set_thread_id("feishu", "chat-a", "legacy", topic_id="topic-a", user_id="user-a")
    assert await m._get_or_create_thread(c, legacy_msg) == ("legacy", False)
    c.threads.create.assert_not_awaited()
