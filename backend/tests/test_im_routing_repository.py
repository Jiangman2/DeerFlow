"""SQL contract of the V2 routing repository.

These tests run against a real SQLite database so the unique constraints and
the generation CAS are exercised as SQL, not as Python bookkeeping. They are
the concurrency evidence for "one pointer per (connection, owner, chat)" and
for "``/new`` switches the pointer exactly once per duplicate delivery".
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select

from deerflow.persistence.im_routing import ImRoutingRepository
from deerflow.persistence.im_routing.model import ImMessageLinkRow, ImSessionPointerRow

pytestmark = pytest.mark.anyio

PROVIDER = "feishu"
CONN = "conn-1"
CHAT = "chat-a"
OWNER = "alice"


@pytest.fixture
async def repo(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'routing.db'}", sqlite_dir=str(tmp_path))
    try:
        yield ImRoutingRepository(get_session_factory()), get_session_factory()
    finally:
        await close_engine()


async def _pointer(repo: ImRoutingRepository, *, owner: str = OWNER, chat: str = CHAT):
    return await repo.get_pointer(provider=PROVIDER, owner_user_id=owner, chat_id=chat, channel_type="group", connection_id=CONN)


async def test_pointer_is_created_once_and_reported_to_the_loser(repo):
    repository, _ = repo
    first = await repository.insert_pointer_if_absent(
        provider=PROVIDER,
        owner_user_id=OWNER,
        chat_id=CHAT,
        channel_type="group",
        connection_id=CONN,
        thread_id="thread-1",
    )
    second = await repository.insert_pointer_if_absent(
        provider=PROVIDER,
        owner_user_id=OWNER,
        chat_id=CHAT,
        channel_type="group",
        connection_id=CONN,
        thread_id="thread-2",
    )

    assert (first.thread_id, first.generation) == ("thread-1", 1)
    # A second creator must adopt the winner instead of silently overwriting.
    assert (second.thread_id, second.generation) == ("thread-1", 1)


async def test_pointer_scope_is_per_owner_and_per_chat(repo):
    repository, _ = repo
    await repository.insert_pointer_if_absent(provider=PROVIDER, owner_user_id=OWNER, chat_id=CHAT, connection_id=CONN, thread_id="alice-a")
    await repository.insert_pointer_if_absent(provider=PROVIDER, owner_user_id="bob", chat_id=CHAT, connection_id=CONN, thread_id="bob-a")
    await repository.insert_pointer_if_absent(provider=PROVIDER, owner_user_id=OWNER, chat_id="chat-b", connection_id=CONN, thread_id="alice-b")

    assert (await _pointer(repository)).thread_id == "alice-a"
    assert (await _pointer(repository, owner="bob")).thread_id == "bob-a"
    assert (await _pointer(repository, chat="chat-b")).thread_id == "alice-b"


async def test_cas_switch_rejects_a_stale_generation(repo):
    repository, _ = repo
    created = await repository.insert_pointer_if_absent(provider=PROVIDER, owner_user_id=OWNER, chat_id=CHAT, connection_id=CONN, thread_id="thread-1")

    assert await repository.cas_switch_pointer(
        provider=PROVIDER,
        owner_user_id=OWNER,
        chat_id=CHAT,
        channel_type="group",
        connection_id=CONN,
        expected_generation=created.generation,
        thread_id="thread-2",
    )
    # A duplicate delivery of the same /new still carries the old generation
    # and must lose, so it cannot create a second conversation.
    assert not await repository.cas_switch_pointer(
        provider=PROVIDER,
        owner_user_id=OWNER,
        chat_id=CHAT,
        channel_type="group",
        connection_id=CONN,
        expected_generation=created.generation,
        thread_id="thread-3",
    )

    current = await _pointer(repository)
    assert (current.thread_id, current.generation) == ("thread-2", 2)


async def test_concurrent_new_switches_advance_generation_once_per_winner(repo):
    repository, sf = repo
    await repository.insert_pointer_if_absent(provider=PROVIDER, owner_user_id=OWNER, chat_id=CHAT, connection_id=CONN, thread_id="thread-0")

    results = await asyncio.gather(
        *[
            repository.cas_switch_pointer(
                provider=PROVIDER,
                owner_user_id=OWNER,
                chat_id=CHAT,
                channel_type="group",
                connection_id=CONN,
                expected_generation=1,
                thread_id=f"thread-{index + 1}",
            )
            for index in range(5)
        ]
    )

    assert results.count(True) == 1
    assert (await _pointer(repository)).generation == 2


async def test_record_link_is_idempotent(repo):
    repository, sf = repo
    kwargs = dict(
        provider=PROVIDER,
        message_id="om_card_1",
        chat_id=CHAT,
        owner_user_id=OWNER,
        thread_id="thread-1",
        direction="outbound",
        message_kind="card",
        connection_id=CONN,
        sender_external_id="ou_bot",
    )
    await repository.record_link(**kwargs)
    await repository.record_link(**kwargs)

    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(ImMessageLinkRow)) == 1

    link = await repository.get_link(provider=PROVIDER, message_id="om_card_1")
    assert link is not None
    assert (link.chat_id, link.owner_user_id, link.thread_id) == (CHAT, OWNER, "thread-1")
    assert await repository.get_link(provider=PROVIDER, message_id="om_missing") is None


async def test_event_claim_is_exclusive_and_releasable(repo):
    repository, sf = repo
    claim = dict(
        provider=PROVIDER,
        event_key="feishu:chat-a:om_in_1",
        chat_id=CHAT,
        owner_user_id=OWNER,
        message_id="om_in_1",
        connection_id=CONN,
        route_reason="NORMAL",
    )

    assert await repository.claim_event(**claim) is True
    assert await repository.claim_event(**claim) is False

    # A failed attempt must be retryable rather than swallowed forever.
    await repository.release_event(provider=PROVIDER, event_key=claim["event_key"])
    assert await repository.claim_event(**claim) is True

    await repository.finish_event(provider=PROVIDER, event_key=claim["event_key"], status="COMPLETED", thread_id="thread-1", route_reason="NORMAL")

    async with sf() as session:
        from deerflow.persistence.im_routing.model import ImEventInboxRow

        row = await session.scalar(select(ImEventInboxRow).where(ImEventInboxRow.event_key == claim["event_key"]))
        assert (row.status, row.thread_id) == ("COMPLETED", "thread-1")


async def test_pointer_scope_ignores_feishu_topics(repo):
    """Every topic inside one group resolves to the same conversation."""
    repository, sf = repo
    await repository.insert_pointer_if_absent(provider=PROVIDER, owner_user_id=OWNER, chat_id=CHAT, connection_id=CONN, thread_id="a")

    assert (await _pointer(repository)).thread_id == "a"
    async with sf() as session:
        row = await session.scalar(select(ImSessionPointerRow))
        assert row.external_topic_id == ""
