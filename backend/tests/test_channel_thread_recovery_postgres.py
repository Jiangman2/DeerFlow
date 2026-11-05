"""Optional real PostgreSQL checks against an isolated, disposable test DB."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.channel_connections import ChannelConnectionRepository, ChannelConnectionRow, ChannelConversationRow
from deerflow.persistence.channel_connections.recovery import ChannelRecoveryRefused, ChannelThreadBinding, ChannelThreadRecovery
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow
from deerflow.persistence.thread_meta.sql import ThreadMetaRepository

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


@pytest.fixture
async def pg(tmp_path):
    url = os.environ.get("CHANNEL_RECOVERY_TEST_POSTGRES")
    if not url:
        pytest.skip("isolated PostgreSQL test database not requested")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost", "portal-incident-pg"}
    assert parsed.database == "incident_test", "never use an application database"
    schema = "recovery_test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    sf = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            tables = [m.__table__ for m in (ChannelConnectionRow, ChannelConversationRow, ThreadMetaRow, ThreadCreationRow, RunRow)]
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
            await conn.execute(text("CREATE TABLE checkpoints (thread_id TEXT PRIMARY KEY, checkpoint BYTEA)"))
        repo = ChannelConnectionRepository(sf)
        connection = await repo.upsert_connection(owner_user_id="alice", provider="feishu", external_account_id="user-a", workspace_id="workspace-a")
        binding = ChannelThreadBinding(connection["id"], "alice", "feishu", "user-a", "workspace-a", "chat-a", "topic-a")
        await repo.set_thread_id(connection_id=binding.connection_id, owner_user_id="alice", provider="feishu", external_conversation_id="chat-a", external_topic_id="topic-a", thread_id="old")
        yield SimpleNamespace(url=url, schema=schema, sf=sf, binding=binding, recovery=ChannelThreadRecovery(sf, binding), meta=ThreadMetaRepository(sf), repo=repo, tmp_path=tmp_path)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def current(pg):
    return await pg.repo.get_thread_id(pg.binding.connection_id, "chat-a", "topic-a")


async def test_postgres_two_processes_publish_once(pg):
    for candidate in ("process-a", "process-b"):
        await pg.meta.create(candidate, user_id="alice")
    worker = r"""
import asyncio, json, pathlib, sys
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from deerflow.persistence.channel_connections.recovery import ChannelThreadBinding, ChannelThreadRecovery
async def main():
    url, schema, connection_id, candidate, root = sys.argv[1:]
    root = pathlib.Path(root)
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    sf = async_sessionmaker(engine, expire_on_commit=False)
    binding = ChannelThreadBinding(connection_id,'alice','feishu','user-a','workspace-a','chat-a','topic-a')
    async def create():
        (root / (candidate + '.ready')).touch()
        async with asyncio.timeout(15):
            while not all((root / (name + '.ready')).exists() for name in ('process-a','process-b')):
                await asyncio.sleep(.02)
        return candidate
    try:
        print(json.dumps(await ChannelThreadRecovery(sf,binding).recover(create)))
    finally:
        await engine.dispose()
asyncio.run(main())
"""
    processes = []
    try:
        for candidate in ("process-a", "process-b"):
            processes.append(await asyncio.create_subprocess_exec(sys.executable, "-c", worker, pg.url, pg.schema, pg.binding.connection_id, candidate, str(pg.tmp_path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE))
        results = await asyncio.wait_for(asyncio.gather(*(p.communicate() for p in processes)), 45)
        assert [p.returncode for p in processes] == [0, 0], [err.decode() for _, err in results]
        values = [json.loads(out) for out, _ in results]
        assert values[0][0] == values[1][0] == await current(pg)
        assert sorted(created for _, created in values) == [False, True]
    finally:
        for process in processes:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def test_postgres_manual_new_not_overwritten(pg):
    async def create():
        await pg.meta.create("candidate", user_id="alice")
        await pg.meta.create("manual", user_id="alice")
        await pg.repo.set_thread_id(connection_id=pg.binding.connection_id, owner_user_id="alice", provider="feishu", external_conversation_id="chat-a", external_topic_id="topic-a", thread_id="manual")
        return "candidate"

    assert await pg.recovery.recover(create) == ("manual", False)
    assert await current(pg) == "manual"


async def test_postgres_revocation_during_create_blocks_publish(pg):
    async def create():
        await pg.meta.create("candidate", user_id="alice")
        async with pg.sf() as session:
            await session.execute(update(ChannelConnectionRow).values(status="revoked"))
            await session.commit()
        return "candidate"

    with pytest.raises(ChannelRecoveryRefused):
        await pg.recovery.recover(create)
    assert await current(pg) == "old"


async def test_postgres_busy_binding_lock_is_bounded(pg):
    from sqlalchemy.exc import DBAPIError

    await pg.meta.create("candidate", user_id="alice")

    async def create():
        return "candidate"

    async with pg.sf() as blocker:
        await blocker.execute(select(ChannelConnectionRow.id).with_for_update())
        started = time.monotonic()
        with pytest.raises(DBAPIError):
            await pg.recovery.recover(create)
        assert time.monotonic() - started < 8
        await blocker.rollback()
    assert await current(pg) == "old"
