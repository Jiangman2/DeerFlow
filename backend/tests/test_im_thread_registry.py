"""SQL contract of the S08 ``im_thread_registry`` repository.

These tests run against a real SQLite database so the primary key, the guarded
status transitions and the keyset pagination are exercised as SQL. The registry
is the single source of truth for "which threads belong to which owner and chat
type", so every isolation assertion here is a security boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from deerflow.persistence.im_routing import ImGroupCatalogRepository, ImThreadRegistryRepository
from deerflow.persistence.im_routing.registry_model import ImThreadRegistryRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

pytestmark = pytest.mark.anyio

OWNER_A = "u_alice"
OWNER_B = "u_bob"
PRIVATE_A = "oc_dm_alice_bot"
GROUP_X = "oc_group_x"
GROUP_Y = "oc_group_y"


@pytest.fixture
async def env(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 's08.db'}", sqlite_dir=str(tmp_path))
    factory = get_session_factory()
    try:
        yield ImThreadRegistryRepository(factory), ImGroupCatalogRepository(factory), factory
    finally:
        await close_engine()


async def _seed_thread_meta(factory, thread_id: str, *, display_name: str | None = None, updated_at: datetime | None = None) -> None:
    now = updated_at or datetime.now(UTC)
    async with factory() as session:
        session.add(
            ThreadMetaRow(
                thread_id=thread_id,
                assistant_id="assistant",
                user_id=OWNER_A,
                display_name=display_name,
                created_at=now,
                updated_at=now,
            ),
        )
        await session.commit()


async def _count(factory) -> int:
    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(ImThreadRegistryRow))).scalar_one())


def _at(minutes_ago: int = 0) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes_ago)


async def test_register_is_idempotent(env):
    repo, _, factory = env
    await repo.register(
        thread_id="t1",
        owner_user_id=OWNER_A,
        channel_type="private",
        external_conversation_id=PRIVATE_A,
        last_active_at=_at(2),
    )
    await repo.register(
        thread_id="t1",
        owner_user_id=OWNER_B,
        channel_type="group",
        external_conversation_id=GROUP_X,
    )

    assert await _count(factory) == 1
    stored = await repo.get("t1")
    assert stored is not None
    # The first registration is the ownership fact; a later register never wins.
    assert (stored.owner_user_id, stored.channel_type, stored.external_conversation_id) == (OWNER_A, "private", PRIVATE_A)


async def test_register_rejects_duplicate_thread_id_without_error(env):
    repo, _, factory = env
    await repo.register(thread_id="dup", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X)
    # Must not raise IntegrityError out of the repository.
    await repo.register(thread_id="dup", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_Y)
    assert await _count(factory) == 1
    assert (await repo.get("dup")).external_conversation_id == GROUP_X


async def test_status_transition_matrix(env):
    repo, _, _ = env
    await repo.register(thread_id="t", owner_user_id=OWNER_A, channel_type="private", external_conversation_id=PRIVATE_A)

    assert await repo.mark_status("t", "archived") is True
    assert await repo.mark_status("t", "deleted") is True
    # Terminal states never come back.
    assert await repo.mark_status("t", "active") is False
    assert await repo.mark_status("t", "archived") is False
    assert (await repo.get("t")).status == "deleted"

    # orphaned is only reachable from active; it is terminal as well.
    await repo.register(thread_id="o1", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X)
    assert await repo.mark_status("o1", "orphaned") is True
    assert await repo.mark_status("o1", "active") is False
    assert (await repo.get("o1")).status == "orphaned"

    await repo.register(thread_id="o2", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X)
    assert await repo.mark_status("o2", "archived") is True
    assert await repo.mark_status("o2", "orphaned") is False

    assert await repo.mark_status("missing", "deleted") is False


async def test_touch_only_advances_when_newer(env):
    repo, _, _ = env
    created = _at(10)
    await repo.register(thread_id="t", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=created)

    # Older than the stored value: ignored.
    await repo.touch("t", at=_at(30))
    assert (await repo.get("t")).last_active_at == created

    newer = _at(0)
    await repo.touch("t", at=newer)
    assert (await repo.get("t")).last_active_at >= newer - timedelta(seconds=1)

    # min_interval suppresses a second bump that happens too soon.
    after_first = (await repo.get("t")).last_active_at
    await repo.touch("t", at=after_first + timedelta(seconds=10), min_interval=timedelta(seconds=60))
    assert (await repo.get("t")).last_active_at == after_first
    await repo.touch("t", at=after_first + timedelta(seconds=120), min_interval=timedelta(seconds=60))
    assert (await repo.get("t")).last_active_at >= after_first + timedelta(seconds=119)


async def test_list_private_keyset_pagination(env):
    repo, _, _ = env
    for index in range(5):
        await repo.register(
            thread_id=f"p{index}",
            owner_user_id=OWNER_A,
            channel_type="private",
            external_conversation_id=PRIVATE_A,
            last_active_at=_at(10 - index),
        )
    # A group thread must never leak into the private section.
    await repo.register(thread_id="g1", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(0))

    first = await repo.list_private(owner_user_id=OWNER_A, limit=2, cursor=None)
    assert [thread.thread_id for thread in first] == ["p4", "p3"]

    cursor = (first[-1].last_active_at, first[-1].thread_id)
    second = await repo.list_private(owner_user_id=OWNER_A, limit=2, cursor=cursor)
    assert [thread.thread_id for thread in second] == ["p2", "p1"]

    cursor = (second[-1].last_active_at, second[-1].thread_id)
    third = await repo.list_private(owner_user_id=OWNER_A, limit=2, cursor=cursor)
    assert [thread.thread_id for thread in third] == ["p0"]

    assert await repo.list_private(owner_user_id=OWNER_B, limit=10, cursor=None) == []


async def test_aggregate_groups_order_and_count(env):
    repo, _, _ = env
    await repo.register(thread_id="x1", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(5))
    await repo.register(thread_id="x2", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(1))
    await repo.register(thread_id="y1", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_Y, last_active_at=_at(2))
    # Archived rows still count: the group directory lists history too.
    await repo.register(thread_id="y2", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_Y, last_active_at=_at(9))
    await repo.mark_status("y2", "archived")
    # Deleted and other owners never count.
    await repo.register(thread_id="x3", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(0))
    await repo.mark_status("x3", "deleted")
    await repo.register(thread_id="bx", owner_user_id=OWNER_B, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(0))

    groups = await repo.aggregate_groups(owner_user_id=OWNER_A, limit=10)
    assert [(group.external_conversation_id, group.thread_count) for group in groups] == [(GROUP_X, 2), (GROUP_Y, 2)]
    # Newest group first: X's newest active thread (_at(1)) beats Y's (_at(2)).
    assert groups[0].last_active_at >= groups[1].last_active_at

    assert await repo.aggregate_groups(owner_user_id=OWNER_A, limit=1) == groups[:1]
    assert await repo.aggregate_groups(owner_user_id=OWNER_A, limit=1, offset=1) == groups[1:]


async def test_first_threads_per_group_respects_partition(env):
    repo, _, _ = env
    await repo.register(thread_id="x1", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(5))
    await repo.register(thread_id="x2", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(1))
    await repo.register(thread_id="y1", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_Y, last_active_at=_at(3))
    await repo.register(thread_id="y2", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_Y, last_active_at=_at(4))

    result = await repo.first_threads_per_group(owner_user_id=OWNER_A, chat_ids=[GROUP_X, GROUP_Y], per_group=1)
    assert {chat: [thread.thread_id for thread in threads] for chat, threads in result.items()} == {
        GROUP_X: ["x2"],
        GROUP_Y: ["y1"],
    }

    all_threads = await repo.first_threads_per_group(owner_user_id=OWNER_A, chat_ids=[GROUP_X, GROUP_Y], per_group=5)
    assert [thread.thread_id for thread in all_threads[GROUP_X]] == ["x2", "x1"]

    # Unknown chats are simply absent, and another owner sees nothing.
    assert await repo.first_threads_per_group(owner_user_id=OWNER_A, chat_ids=["oc_unknown"], per_group=1) == {}
    assert await repo.first_threads_per_group(owner_user_id=OWNER_B, chat_ids=[GROUP_X], per_group=1) == {}


async def test_search_matches_thread_title_and_group_name(env):
    repo, catalog, factory = env
    await _seed_thread_meta(factory, "t_title", display_name="Release Notes")
    await _seed_thread_meta(factory, "t_group", display_name=None)
    await repo.register(thread_id="t_title", owner_user_id=OWNER_A, channel_type="private", external_conversation_id=PRIVATE_A, last_active_at=_at(1))
    await repo.register(thread_id="t_group", owner_user_id=OWNER_A, channel_type="group", external_conversation_id=GROUP_X, last_active_at=_at(0))
    await catalog.ensure(chat_id=GROUP_X)
    await catalog.update_meta(chat_id=GROUP_X, display_name="Alpha Team", name_source="CHAT_INFO")

    by_title = await repo.search(owner_user_id=OWNER_A, query="release", limit=10, offset=0)
    assert [thread.thread_id for thread in by_title] == ["t_title"]
    assert by_title[0].title == "Release Notes"
    assert by_title[0].thread_exists is True

    by_group = await repo.search(owner_user_id=OWNER_A, query="alpha", limit=10, offset=0)
    assert [thread.thread_id for thread in by_group] == ["t_group"]

    assert await repo.search(owner_user_id=OWNER_A, query="   ", limit=10, offset=0) == []
    assert await repo.search(owner_user_id=OWNER_A, query="nothing-here", limit=10, offset=0) == []


async def test_search_scoped_to_owner(env):
    repo, _, factory = env
    await _seed_thread_meta(factory, "mine", display_name="Shared name")
    await _seed_thread_meta(factory, "theirs", display_name="Shared name")
    await repo.register(thread_id="mine", owner_user_id=OWNER_A, channel_type="private", external_conversation_id=PRIVATE_A)
    await repo.register(thread_id="theirs", owner_user_id=OWNER_B, channel_type="private", external_conversation_id="oc_dm_bob_bot")

    assert [thread.thread_id for thread in await repo.search(owner_user_id=OWNER_A, query="shared", limit=10, offset=0)] == ["mine"]
    assert [thread.thread_id for thread in await repo.search(owner_user_id=OWNER_B, query="shared", limit=10, offset=0)] == ["theirs"]


async def test_list_stale_detects_missing_threads_meta(env):
    repo, _, factory = env
    await _seed_thread_meta(factory, "alive")
    await repo.register(thread_id="alive", owner_user_id=OWNER_A, channel_type="private", external_conversation_id=PRIVATE_A)
    await repo.register(thread_id="th_gone", owner_user_id=OWNER_A, channel_type="private", external_conversation_id=PRIVATE_A)

    assert await repo.list_stale(OWNER_A) == ["th_gone"]
    assert await repo.list_stale(OWNER_B) == []
