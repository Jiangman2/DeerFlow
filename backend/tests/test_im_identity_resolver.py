"""S08 identity resolution: user-level bindings and the unowned-thread guard.

The Feishu ``/connect`` handshake used to write a chat-scoped
``channel_connections`` row only, so an owner was resolvable in exactly the one
chat where they connected. These tests lock down the corrected order
(binding -> chat connection -> workspace-less connection -> none) and the
manager-level admission boundary that keeps unbound senders from creating
threads nobody owns.
"""

from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.channels.identity_resolver import resolve_im_identity
from app.channels.manager import (
    IDENTITY_UNBOUND_MESSAGE,
    ChannelManager,
)
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus
from app.channels.store import ChannelStore
from deerflow.persistence.im_routing import SessionPointer

pytestmark = pytest.mark.anyio

TENANT = "tenant-1"
BOT = "cli_app_1"
ALICE_OPEN_ID = "ou_alice"
BOB_OPEN_ID = "ou_bob"
OWNER_A = "u_alice"
OWNER_B = "u_bob"
GROUP_X = "oc_group_x"
PRIVATE_A = "oc_dm_alice_bot"


class FakeBindingRepo:
    """In-memory twin of :class:`ImUserBindingRepository`."""

    def __init__(self, active: dict[tuple[str, str, str, str], str] | None = None) -> None:
        self.active = dict(active or {})

    async def find_active(self, *, feishu_user_id, tenant_key="", bot_app_id="", feishu_id_type="open_id"):
        exact = self.active.get((tenant_key, bot_app_id, feishu_id_type, feishu_user_id))
        if exact:
            return exact
        loose = {owner for (tenant, app, kind, user), owner in self.active.items() if (app, kind, user) == (bot_app_id, feishu_id_type, feishu_user_id) and tenant in ("", tenant_key)}
        if len(loose) == 1:
            return next(iter(loose))
        return None


class FakeConnectionRepo:
    """Minimal ``ChannelConnectionRepository`` twin keyed by (user, workspace)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])

    async def find_connection_by_external_identity(self, *, provider, external_account_id, workspace_id):
        for row in self.rows:
            if row["provider"] != provider or row["external_account_id"] != external_account_id:
                continue
            if (row.get("workspace_id") or None) == (workspace_id or None):
                return row
        return None


def _connection(owner: str, *, user: str = ALICE_OPEN_ID, workspace_id: str | None = GROUP_X, connection_id: str = "conn-1") -> dict[str, Any]:
    return {
        "id": connection_id,
        "provider": "feishu",
        "external_account_id": user,
        "owner_user_id": owner,
        "workspace_id": workspace_id,
    }


class FakeRoutingRepo:
    def __init__(self) -> None:
        self.pointers: dict[tuple, SessionPointer] = {}
        self.events: dict[str, str] = {}

    @staticmethod
    def _key(provider, connection_id, owner, channel_type, chat):
        return (provider, connection_id, owner, channel_type, chat)

    async def get_pointer(self, *, provider, owner_user_id, chat_id, channel_type="group", connection_id=""):
        return self.pointers.get(self._key(provider, connection_id, owner_user_id, channel_type, chat_id))

    async def insert_pointer_if_absent(self, *, provider, owner_user_id, chat_id, thread_id, channel_type="group", connection_id=""):
        key = self._key(provider, connection_id, owner_user_id, channel_type, chat_id)
        if key in self.pointers:
            return self.pointers[key]
        pointer = SessionPointer(thread_id=thread_id, generation=1)
        self.pointers[key] = pointer
        return pointer

    async def cas_switch_pointer(self, **kwargs):
        return False

    async def get_link(self, *, provider, message_id):
        return None

    async def record_link(self, *, provider, message_id, chat_id, owner_user_id, thread_id, direction, message_kind="", connection_id="", sender_external_id="", root_message_id="", parent_message_id="", task_id=""):
        return None

    async def claim_event(self, *, provider, event_key, chat_id, owner_user_id, message_id, connection_id, route_reason):
        if event_key in self.events:
            return False
        self.events[event_key] = "PROCESSING"
        return True

    async def finish_event(self, *, provider, event_key, status, thread_id="", route_reason=""):
        self.events[event_key] = status

    async def release_event(self, *, provider, event_key):
        self.events.pop(event_key, None)


class FakeClient:
    def __init__(self, *, existing: set[str] | None = None) -> None:
        self.created: list[str] = []
        self.existing = set(existing or ())
        self._ids = itertools.count(1)
        self.threads = SimpleNamespace(create=self._create, get=self._get, update=AsyncMock())
        self.runs = SimpleNamespace(wait=AsyncMock(), stream=AsyncMock())

    async def _create(self, **kwargs):
        thread_id = f"thread-{next(self._ids)}"
        self.created.append(thread_id)
        return {"thread_id": thread_id}

    async def _get(self, thread_id, **kwargs):
        if thread_id in self.existing:
            return {"thread_id": thread_id}
        raise RuntimeError(f"thread {thread_id} not found")


def _msg(
    text: str = "hello",
    *,
    message_id: str = "om-in-1",
    chat_id: str = GROUP_X,
    owner: str | None = None,
    connection_id: str = "",
    user_id: str = ALICE_OPEN_ID,
    chat_type: str = "group",
    msg_type: InboundMessageType = InboundMessageType.CHAT,
) -> InboundMessage:
    return InboundMessage(
        channel_name="feishu",
        chat_id=chat_id,
        user_id=user_id,
        text=text,
        msg_type=msg_type,
        connection_id=connection_id,
        owner_user_id=owner or "",
        metadata={
            "message_id": message_id,
            "chat_type": chat_type,
            "user_id": user_id,
            "tenant_key": TENANT,
            "bot_app_id": BOT,
        },
    )


def _harness(tmp_path, *, bindings=None, connections=None, s08_enabled=True):
    bus = MessageBus()
    outbound: list = []

    async def collect(message) -> None:
        outbound.append(message)

    bus.subscribe_outbound(collect)
    routing_repo = FakeRoutingRepo()
    binding_repo = FakeBindingRepo(bindings)
    connection_repo = FakeConnectionRepo(connections)
    manager = ChannelManager(
        bus=bus,
        store=ChannelStore(tmp_path / "store.json"),
        connection_repo=connection_repo,
        routing_repo=routing_repo,
        route_v2_enabled=True,
        binding_repo=binding_repo,
        s08_enabled=s08_enabled,
    )
    manager._semaphore = asyncio.Semaphore(5)
    client = FakeClient()
    runs: list[dict[str, Any]] = []
    manager._get_client = lambda: client

    async def fake_run(client_, msg, thread_id, **kwargs):
        runs.append({"thread_id": thread_id, "text": msg.text})

    manager._run_chat_on_thread = fake_run
    return SimpleNamespace(
        manager=manager,
        routing_repo=routing_repo,
        binding_repo=binding_repo,
        connection_repo=connection_repo,
        client=client,
        runs=runs,
        outbound=outbound,
    )


# ---------------------------------------------------------------------------
# resolver order
# ---------------------------------------------------------------------------


async def test_binding_wins_over_a_chat_scoped_connection():
    binding_repo = FakeBindingRepo({(TENANT, BOT, "open_id", ALICE_OPEN_ID): OWNER_A})
    connection_repo = FakeConnectionRepo([_connection(OWNER_B, workspace_id=GROUP_X, connection_id="conn-old")])

    resolution = await resolve_im_identity(
        binding_repo=binding_repo,
        connection_repo=connection_repo,
        provider="feishu",
        external_user_id=ALICE_OPEN_ID,
        chat_id=GROUP_X,
        tenant_key=TENANT,
        bot_app_id=BOT,
    )

    assert resolution.owner_user_id == OWNER_A
    assert resolution.source == "binding"
    # The chat-scoped connection id is still reported for adapter bookkeeping.
    assert resolution.connection_id == "conn-old"


async def test_q1_binding_resolves_owner_in_a_group_they_did_not_connect_in():
    """Q1 regression: the connection row belongs to a different chat."""
    binding_repo = FakeBindingRepo({(TENANT, BOT, "open_id", ALICE_OPEN_ID): OWNER_A})
    connection_repo = FakeConnectionRepo([_connection(OWNER_A, workspace_id=PRIVATE_A, connection_id="conn-dm")])

    resolution = await resolve_im_identity(
        binding_repo=binding_repo,
        connection_repo=connection_repo,
        provider="feishu",
        external_user_id=ALICE_OPEN_ID,
        chat_id=GROUP_X,
        tenant_key=TENANT,
        bot_app_id=BOT,
    )

    assert resolution.owner_user_id == OWNER_A
    assert resolution.source == "binding"


async def test_chat_scoped_connection_is_the_first_fallback():
    resolution = await resolve_im_identity(
        binding_repo=FakeBindingRepo(),
        connection_repo=FakeConnectionRepo([_connection(OWNER_A)]),
        provider="feishu",
        external_user_id=ALICE_OPEN_ID,
        chat_id=GROUP_X,
        tenant_key=TENANT,
        bot_app_id=BOT,
    )

    assert resolution.owner_user_id == OWNER_A
    assert resolution.source == "connection_chat"


async def test_workspace_less_connection_is_the_last_fallback():
    resolution = await resolve_im_identity(
        binding_repo=FakeBindingRepo(),
        connection_repo=FakeConnectionRepo([_connection(OWNER_A, workspace_id=None)]),
        provider="feishu",
        external_user_id=ALICE_OPEN_ID,
        chat_id=GROUP_X,
        tenant_key=TENANT,
        bot_app_id=BOT,
    )

    assert resolution.owner_user_id == OWNER_A
    assert resolution.source == "connection_workspace"


async def test_unknown_identity_resolves_to_none_without_writing():
    binding_repo = FakeBindingRepo()
    connection_repo = FakeConnectionRepo()

    resolution = await resolve_im_identity(
        binding_repo=binding_repo,
        connection_repo=connection_repo,
        provider="feishu",
        external_user_id="ou_unknown",
        chat_id=GROUP_X,
        tenant_key=TENANT,
        bot_app_id=BOT,
    )

    assert resolution.owner_user_id is None
    assert resolution.source == "none"
    assert resolution.resolved is False
    assert binding_repo.active == {}
    assert connection_repo.rows == []


async def test_empty_external_user_id_never_resolves():
    resolution = await resolve_im_identity(
        binding_repo=FakeBindingRepo({(TENANT, BOT, "open_id", ""): OWNER_A}),
        connection_repo=FakeConnectionRepo([_connection(OWNER_A, user="")]),
        provider="feishu",
        external_user_id="",
        chat_id=GROUP_X,
        tenant_key=TENANT,
        bot_app_id=BOT,
    )

    assert resolution.owner_user_id is None
    assert resolution.source == "none"


# ---------------------------------------------------------------------------
# manager admission boundary: no unowned threads
# ---------------------------------------------------------------------------


async def test_unbound_sender_is_rejected_before_creating_a_thread(tmp_path):
    harness = _harness(tmp_path)

    await harness.manager._handle_message(_msg())

    assert harness.client.created == []
    assert harness.runs == []
    assert harness.outbound[-1].text == IDENTITY_UNBOUND_MESSAGE
    assert harness.routing_repo.pointers == {}


async def test_bound_sender_adopts_the_server_side_owner(tmp_path):
    harness = _harness(tmp_path, bindings={(TENANT, BOT, "open_id", ALICE_OPEN_ID): OWNER_A})
    msg = _msg(owner=None, connection_id="")

    await harness.manager._handle_message(msg)

    assert msg.owner_user_id == OWNER_A
    assert harness.client.created == ["thread-1"]
    assert harness.runs[0]["thread_id"] == "thread-1"
    key = ("feishu", "", OWNER_A, "group", GROUP_X)
    assert harness.routing_repo.pointers[key].thread_id == "thread-1"


async def test_s08_rejects_new_conversation_for_unbound_user(tmp_path):
    harness = _harness(tmp_path)
    msg = _msg("/new", msg_type=InboundMessageType.COMMAND)

    await harness.manager._handle_message(msg)

    assert harness.client.created == []
    assert harness.outbound[-1].text == IDENTITY_UNBOUND_MESSAGE


async def test_asserted_owner_mismatch_is_rejected_with_server_side_hints(tmp_path):
    harness = _harness(tmp_path, bindings={(TENANT, BOT, "open_id", ALICE_OPEN_ID): OWNER_A})
    msg = _msg(owner=OWNER_B, connection_id="conn-forged")
    harness.connection_repo.rows.append(_connection(OWNER_A, connection_id="conn-real"))

    await harness.manager._handle_message(msg)

    assert harness.client.created == []
    assert harness.runs == []
    assert harness.outbound[-1].owner_user_id == OWNER_A
    assert harness.outbound[-1].connection_id == "conn-real"


async def test_other_users_bindings_do_not_leak_across_tenants(tmp_path):
    harness = _harness(
        tmp_path,
        bindings={("tenant-2", BOT, "open_id", ALICE_OPEN_ID): OWNER_A},
    )

    await harness.manager._handle_message(_msg())

    assert harness.client.created == []
    assert harness.outbound[-1].text == IDENTITY_UNBOUND_MESSAGE


async def test_s08_disabled_keeps_the_legacy_path(tmp_path):
    harness = _harness(
        tmp_path,
        connections=[_connection(OWNER_A, connection_id="conn-legacy")],
        s08_enabled=False,
    )
    msg = _msg(owner=OWNER_A, connection_id="conn-legacy")

    await harness.manager._handle_message(msg)

    # No binding row is consulted, and the legacy connection is authoritative.
    assert harness.binding_repo.active == {}
    assert harness.client.created == ["thread-1"]
    assert harness.runs[0]["thread_id"] == "thread-1"


async def test_unbound_rejection_releases_the_event_claim(tmp_path):
    """The dedupe claim must not swallow a later bound redelivery."""
    harness = _harness(tmp_path)
    await harness.manager._handle_message(_msg(message_id="om-x"))

    assert harness.outbound[-1].text == IDENTITY_UNBOUND_MESSAGE
    assert harness.routing_repo.events == {}  # rejection released the claim

    # Same platform delivery after the user binds must be processed, not dropped.
    harness.binding_repo.active[(TENANT, BOT, "open_id", ALICE_OPEN_ID)] = OWNER_A
    await harness.manager._handle_message(_msg(message_id="om-x"))

    assert harness.client.created == ["thread-1"]
# ---------------------------------------------------------------------------
# adapter wiring: /connect writes the user-level binding
# ---------------------------------------------------------------------------


async def test_connect_code_writes_a_binding_that_resolves_in_other_chats(tmp_path):
    from datetime import UTC, datetime, timedelta

    from app.channels.feishu import FeishuChannel
    from deerflow.persistence.channel_connections import ChannelConnectionRepository
    from deerflow.persistence.engine import get_session_factory, init_engine
    from deerflow.persistence.im_routing import ImUserBindingRepository

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'bind.db'}", sqlite_dir=str(tmp_path))
    session_factory = get_session_factory()
    connection_repo = ChannelConnectionRepository(session_factory)
    binding_repo = ImUserBindingRepository(session_factory)
    try:
        await connection_repo.create_oauth_state(
            owner_user_id=OWNER_A,
            provider="feishu",
            state="code-1",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        channel = FeishuChannel(
            bus=MessageBus(),
            config={
                "app_id": BOT,
                "app_secret": "secret",
                "connection_repo": connection_repo,
                "binding_repo": binding_repo,
            },
        )
        channel._reply_card = AsyncMock()
        channel._keep_running_card = AsyncMock()

        handled = await channel._bind_connection_from_connect_code(
            message_id="om-connect-1",
            chat_id=PRIVATE_A,
            user_id=ALICE_OPEN_ID,
            code="code-1",
            chat_type="p2p",
            tenant_key=TENANT,
        )

        assert handled is True
        assert await binding_repo.find_active(feishu_user_id=ALICE_OPEN_ID, tenant_key=TENANT, bot_app_id=BOT) == OWNER_A

        # Q1: the same person in a group they never connected in still resolves.
        resolution = await resolve_im_identity(
            binding_repo=binding_repo,
            connection_repo=connection_repo,
            provider="feishu",
            external_user_id=ALICE_OPEN_ID,
            chat_id=GROUP_X,
            tenant_key=TENANT,
            bot_app_id=BOT,
        )
        assert resolution.owner_user_id == OWNER_A
        assert resolution.source == "binding"

        # The adapter attaches that owner before the manager ever sees the message.
        inbound = _msg(chat_id=GROUP_X, owner=None)
        attached = await channel._attach_connection_identity(inbound)
        assert attached.owner_user_id == OWNER_A
    finally:
        await connection_repo.close()
