"""S08 end-to-end scenarios at the manager/DB boundary.

These tests keep the real SQL repositories (routing pointers, message links,
thread registry, group catalog) and replace only the network edges (LangGraph
client, the run itself, the Feishu chat metadata call). Every assertion about
ownership, classification and the current pointer is therefore read back from
SQLite, not inferred from an API response.

Covered: S08-01 .. S08-08, S08-10/11/12, S08-15, X1 (pointer race), X2
(registry-less directory) and the flag-off no-op guarantee.
"""

from __future__ import annotations

import asyncio
import itertools
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select

from app.channels.manager import (
    IDENTITY_UNBOUND_MESSAGE,
    NEW_THREAD_SWITCH_FAILED_MESSAGE,
    ChannelManager,
)
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus
from app.channels.store import ChannelStore
from deerflow.persistence.im_routing import (
    ImGroupCatalogRepository,
    ImRoutingRepository,
    ImThreadRegistryRepository,
    SessionPointer,
)
from deerflow.persistence.im_routing.registry_model import ImThreadRegistryRow

pytestmark = pytest.mark.anyio

TENANT = "tenant-1"
BOT = "cli_app_1"
OWNER_A = "u_alice"
OWNER_B = "u_bob"
PRIVATE_A = "oc_dm_alice_bot"
GROUP_X = "oc_group_x"
GROUP_Y = "oc_group_y"
GROUP_Z = "oc_group_z"


class FakeConnectionRepo:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])

    async def find_connection_by_external_identity(self, *, provider, external_account_id, workspace_id):
        for row in self.rows:
            if row["provider"] != provider or row["external_account_id"] != external_account_id:
                continue
            if (row.get("workspace_id") or None) == (workspace_id or None):
                return row
        return None


class FakeClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.existing: set[str] = set()
        self._ids = itertools.count(1)
        self.threads = SimpleNamespace(create=self._create, get=self._get)
        self.runs = SimpleNamespace()

    async def _create(self, **kwargs):
        thread_id = f"thread-{next(self._ids)}"
        self.created.append(thread_id)
        self.existing.add(thread_id)
        return {"thread_id": thread_id}

    async def _get(self, thread_id, **kwargs):
        if thread_id in self.existing:
            return {"thread_id": thread_id}
        raise RuntimeError(f"thread {thread_id} not found")


def _msg(
    *,
    message_id: str,
    chat_id: str = GROUP_X,
    owner: str = OWNER_A,
    chat_type: str = "group",
    reply: dict[str, Any] | None = None,
    text: str = "hello",
) -> InboundMessage:
    metadata: dict[str, Any] = {
        "message_id": message_id,
        "chat_type": chat_type,
        "user_id": "ou_alice",
        "tenant_key": TENANT,
        "bot_app_id": BOT,
    }
    metadata.update(reply or {})
    return InboundMessage(
        channel_name="feishu",
        chat_id=chat_id,
        user_id="ou_alice" if owner == OWNER_A else "ou_bob",
        text=text,
        msg_type=InboundMessageType.CHAT,
        connection_id="conn-1",
        owner_user_id=owner,
        metadata=metadata,
    )


def _bot_reply(message_id: str) -> dict[str, Any]:
    return {
        "reply_message_id": message_id,
        "reply_sender_type": "app",
        "reply_sender_id": "ou_bot",
        "reply_from_self_bot": True,
    }


@pytest.fixture
async def env(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 's08_scenarios.db'}", sqlite_dir=str(tmp_path))
    factory = get_session_factory()
    try:
        yield SimpleNamespace(
            factory=factory,
            routing=ImRoutingRepository(factory),
            registry=ImThreadRegistryRepository(factory),
            catalog=ImGroupCatalogRepository(factory),
        )
    finally:
        await close_engine()


def _manager(
    env,
    *,
    s08_enabled: bool = True,
    routing=None,
    connections: list[dict[str, Any]] | None = None,
) -> SimpleNamespace:
    bus = MessageBus()
    outbound: list = []

    async def collect(message) -> None:
        outbound.append(message)

    bus.subscribe_outbound(collect)
    manager = ChannelManager(
        bus=bus,
        store=ChannelStore(),
        routing_repo=routing or env.routing,
        route_v2_enabled=True,
        connection_repo=FakeConnectionRepo(connections),
        binding_repo=None,
        s08_enabled=s08_enabled,
        registry_repo=env.registry,
        catalog_repo=env.catalog,
    )
    manager._semaphore = asyncio.Semaphore(5)
    client = FakeClient()
    runs: list[dict[str, Any]] = []

    async def fake_run(_client, msg, thread_id, **kwargs):
        runs.append({"thread_id": thread_id, "text": msg.text, **kwargs})

    manager._get_client = lambda: client
    manager._run_chat_on_thread = fake_run

    async def fake_meta(_channel_name: str, chat_id: str):
        names = {GROUP_X: "群聊 X", GROUP_Y: "群聊 Y"}
        return {"name": names.get(chat_id, ""), "chat_status": "normal", "member_count": 3}

    manager._fetch_chat_meta = fake_meta
    return SimpleNamespace(manager=manager, client=client, runs=runs, outbound=outbound)


async def _rows(env, owner: str | None = None) -> list:
    """Read registry rows straight from SQL (listing APIs filter by type)."""
    stmt = select(ImThreadRegistryRow)
    if owner is not None:
        stmt = stmt.where(ImThreadRegistryRow.owner_user_id == owner)
    async with env.factory() as session:
        return list((await session.execute(stmt)).scalars())


async def _count(env) -> int:
    async with env.factory() as session:
        return int((await session.execute(select(func.count()).select_from(ImThreadRegistryRow))).scalar_one())


async def _pointer(env, *, chat_id: str, owner: str, channel_type: str = "group"):
    return await env.routing.get_pointer(
        provider="feishu",
        owner_user_id=owner,
        chat_id=chat_id,
        channel_type=channel_type,
        connection_id="conn-1",
    )


# ---------------------------------------------------------------------------
# S08-01..S08-04: private thread lifecycle
# ---------------------------------------------------------------------------


async def test_private_first_message_registers_a_private_owned_thread(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))

    rows = await _rows(env)
    assert [(r.thread_id, r.owner_user_id, r.channel_type, r.external_conversation_id, r.status) for r in rows] == [
        ("thread-1", OWNER_A, "private", PRIVATE_A, "active")
    ]
    pointer = await _pointer(env, chat_id=PRIVATE_A, owner=OWNER_A, channel_type="private")
    assert pointer.thread_id == "thread-1"


async def test_continue_message_reuses_the_thread_and_advances_last_active(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))
    first = (await _rows(env))[0]
    h.manager._thread_touch_cache.clear()  # bypass the 60s touch throttle
    await h.manager._handle_routed_chat(_msg(message_id="om-p2", chat_id=PRIVATE_A, chat_type="p2p"))

    rows = await _rows(env)
    assert len(rows) == 1
    assert rows[0].thread_id == "thread-1"
    assert rows[0].last_active_at >= first.last_active_at
    assert h.client.created == ["thread-1"]


async def test_new_conversation_moves_the_pointer_and_keeps_the_old_row(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))
    reply = await h.manager._handle_new_conversation_v2(_msg(message_id="om-new", chat_id=PRIVATE_A, chat_type="p2p"))

    assert reply == "New conversation started."
    pointer = await _pointer(env, chat_id=PRIVATE_A, owner=OWNER_A, channel_type="private")
    assert pointer.thread_id == "thread-2"
    # /new never touches the registry: both threads stay active and listed.
    assert {r.thread_id for r in await _rows(env)} == {"thread-1", "thread-2"}


async def test_reply_to_an_old_answer_restores_it_without_moving_the_pointer(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))
    await h.manager._handle_new_conversation_v2(_msg(message_id="om-new", chat_id=PRIVATE_A, chat_type="p2p"))
    await env.routing.record_link(
        provider="feishu",
        message_id="om-bot-old",
        chat_id=PRIVATE_A,
        owner_user_id=OWNER_A,
        thread_id="thread-1",
        direction="outbound",
    )

    await h.manager._handle_routed_chat(
        _msg(message_id="om-p3", chat_id=PRIVATE_A, chat_type="p2p", reply=_bot_reply("om-bot-old"))
    )

    assert h.runs[-1]["thread_id"] == "thread-1"
    pointer = await _pointer(env, chat_id=PRIVATE_A, owner=OWNER_A, channel_type="private")
    assert pointer.thread_id == "thread-2"


# ---------------------------------------------------------------------------
# S08-05..S08-09: group classification and isolation
# ---------------------------------------------------------------------------


async def test_group_first_message_registers_group_thread_and_catalog_row(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-x1", chat_id=GROUP_X))

    rows = await _rows(env)
    assert [(r.channel_type, r.external_conversation_id) for r in rows] == [("group", GROUP_X)]
    meta = (await env.catalog.get_many([GROUP_X]))[GROUP_X]
    assert (meta.display_name, meta.chat_status) == ("群聊 X", "normal")


async def test_two_owners_in_one_group_get_separate_threads_and_rows(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-a", chat_id=GROUP_X, owner=OWNER_A))
    await h.manager._handle_routed_chat(_msg(message_id="om-b", chat_id=GROUP_X, owner=OWNER_B))

    a_rows = await _rows(env, OWNER_A)
    b_rows = await _rows(env, OWNER_B)
    assert len(a_rows) == 1 and len(b_rows) == 1
    assert a_rows[0].thread_id != b_rows[0].thread_id
    assert a_rows[0].external_conversation_id == b_rows[0].external_conversation_id == GROUP_X


async def test_same_owner_two_groups_get_separate_threads(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-x1", chat_id=GROUP_X))
    await h.manager._handle_routed_chat(_msg(message_id="om-y1", chat_id=GROUP_Y))

    rows = {r.thread_id: r.external_conversation_id for r in await _rows(env)}
    assert rows == {"thread-1": GROUP_X, "thread-2": GROUP_Y}


async def test_private_and_group_threads_are_isolated(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))
    await h.manager._handle_routed_chat(_msg(message_id="om-x1", chat_id=GROUP_X))

    rows = {r.thread_id: (r.channel_type, r.external_conversation_id) for r in await _rows(env)}
    assert rows == {"thread-1": ("private", PRIVATE_A), "thread-2": ("group", GROUP_X)}


async def test_catalog_row_without_registry_rows_is_not_a_directory(env):
    # GROUP_Z was cached (e.g. a rename event) but no thread was ever created.
    await env.catalog.ensure(chat_id=GROUP_Z, provider="feishu", tenant_key=TENANT, bot_app_id=BOT, chat_type="group")
    groups = await env.registry.aggregate_groups(owner_user_id=OWNER_A, limit=50)
    assert [g.external_conversation_id for g in groups] == []


# ---------------------------------------------------------------------------
# S08-10..S08-12: ownership guard, /new failure, pointer race
# ---------------------------------------------------------------------------


async def test_registry_owner_mismatch_fails_closed_without_running(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-a", chat_id=GROUP_X, owner=OWNER_A))
    await env.routing.record_link(
        provider="feishu",
        message_id="om-bot-a",
        chat_id=GROUP_X,
        owner_user_id=OWNER_A,
        thread_id="thread-1",
        direction="outbound",
    )
    # A second owner's link points at the first owner's thread (corrupt/legacy
    # data); the registry must still refuse the restore.
    await env.routing.record_link(
        provider="feishu",
        message_id="om-bot-b",
        chat_id=GROUP_X,
        owner_user_id=OWNER_B,
        thread_id="thread-1",
        direction="outbound",
    )

    before = len(h.runs)
    await h.manager._handle_routed_chat(
        _msg(message_id="om-b", chat_id=GROUP_X, owner=OWNER_B, reply=_bot_reply("om-bot-b"))
    )

    assert len(h.runs) == before
    assert h.outbound[-1].text == "无法恢复原会话：原会话不属于当前账号。请直接 @我 重新提问，或发送 /new 开始新会话。"


async def test_deleted_registry_thread_is_not_restored(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))
    await env.registry.mark_status("thread-1", "deleted")
    await env.routing.record_link(
        provider="feishu",
        message_id="om-bot-old",
        chat_id=PRIVATE_A,
        owner_user_id=OWNER_A,
        thread_id="thread-1",
        direction="outbound",
    )

    before = len(h.runs)
    await h.manager._handle_routed_chat(
        _msg(message_id="om-p2", chat_id=PRIVATE_A, chat_type="p2p", reply=_bot_reply("om-bot-old"))
    )
    assert len(h.runs) == before
    assert "原会话已被删除" in h.outbound[-1].text


class _ContendedRouting:
    """Routing repo whose pointer can never be switched (CAS always loses)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.switch_attempts = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def cas_switch_pointer(self, **kwargs):
        self.switch_attempts += 1
        return False


async def test_new_conversation_cas_exhaustion_reports_failure_and_orphans_the_thread(env):
    routed = _ContendedRouting(env.routing)
    h = _manager(env, routing=routed)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))

    reply = await h.manager._handle_new_conversation_v2(_msg(message_id="om-new", chat_id=PRIVATE_A, chat_type="p2p"))

    assert reply == NEW_THREAD_SWITCH_FAILED_MESSAGE
    assert routed.switch_attempts == 5
    pointer = await _pointer(env, chat_id=PRIVATE_A, owner=OWNER_A, channel_type="private")
    assert pointer.thread_id == "thread-1"
    statuses = {r.thread_id: r.status for r in await _rows(env)}
    assert statuses == {"thread-1": "active", "thread-2": "orphaned"}


class _AdoptingRouting:
    """Routing repo that always adopts another process's thread (race loser)."""

    def __init__(self, inner, winner: str) -> None:
        self.inner = inner
        self.winner = winner

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def insert_pointer_if_absent(self, **kwargs):
        return SessionPointer(thread_id=self.winner, generation=1)


async def test_pointer_creation_race_loser_is_marked_orphaned(env):
    routed = _AdoptingRouting(env.routing, winner="thread-winner")
    h = _manager(env, routing=routed)

    thread_id = await h.manager._ensure_pointer_thread(h.client, _msg(message_id="om-x1", chat_id=GROUP_X))

    assert thread_id == "thread-winner"
    statuses = {r.thread_id: r.status for r in await _rows(env)}
    assert statuses == {"thread-1": "orphaned"}


# ---------------------------------------------------------------------------
# flag off / unbound sender
# ---------------------------------------------------------------------------


async def test_flag_off_writes_no_archive_rows(env):
    h = _manager(env, s08_enabled=False)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))

    assert await _count(env) == 0
    assert (await env.catalog.get_many([PRIVATE_A])) == {}


async def test_unbound_sender_is_rejected_before_any_thread_exists(env):
    h = _manager(env)
    msg = _msg(message_id="om-unbound", chat_id=GROUP_X)
    msg.owner_user_id = ""
    rejection = await h.manager._get_bound_identity_rejection(msg)

    assert rejection is not None
    assert rejection.message == IDENTITY_UNBOUND_MESSAGE
    assert await _count(env) == 0


async def test_registry_listing_never_crosses_owners(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-a", chat_id=GROUP_X, owner=OWNER_A))
    await h.manager._handle_routed_chat(_msg(message_id="om-b", chat_id=GROUP_Y, owner=OWNER_B))

    assert [r.thread_id for r in await _rows(env, OWNER_A)] == ["thread-1"]
    assert [r.thread_id for r in await _rows(env, OWNER_B)] == ["thread-2"]
    assert (await env.registry.get("thread-1")).owner_user_id == OWNER_A
    assert (await env.registry.get("thread-2")).owner_user_id == OWNER_B

    # And the shared group listing only reports the caller's threads.
    x_groups = await env.registry.aggregate_groups(owner_user_id=OWNER_A, limit=50)
    assert [(g.external_conversation_id, g.thread_count) for g in x_groups] == [(GROUP_X, 1)]


async def test_registration_is_bound_to_the_message_tenant_and_bot(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-x1", chat_id=GROUP_X))
    row = await env.registry.get("thread-1")
    async with env.factory() as session:
        stored = (
            await session.execute(
                select(ImThreadRegistryRow).where(ImThreadRegistryRow.thread_id == "thread-1")
            )
        ).scalar_one()
    assert (stored.tenant_key, stored.bot_app_id) == (TENANT, BOT)
    assert row is not None


async def test_touch_is_throttled_for_a_busy_thread(env):
    h = _manager(env)
    await h.manager._handle_routed_chat(_msg(message_id="om-p1", chat_id=PRIVATE_A, chat_type="p2p"))
    first = datetime.now(UTC)
    h.manager._thread_touch_cache["thread-1"] = first
    await h.manager._touch_thread("thread-1")

    assert h.manager._thread_touch_cache["thread-1"] == first