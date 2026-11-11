"""Routing behaviour of ``ChannelManager`` under the V2 session pointer.

These tests replace only the network edges (LangGraph client, the run itself)
and keep the real router, the real pointer CAS and the real event inbox, so
they lock down the guarantees the design document requires:

* one pointer per (owner, chat) and a brand-new thread per ``/new``;
* a reply to this bot's own message restores that thread **without** moving
  the pointer, and a plain message right after still answers in the pointer
  thread (E34/E09);
* a reference that cannot be verified never runs the agent and never silently
  degrades to the current conversation (E35/E36/E37/E40);
* a duplicate platform delivery is claimed once (E42-adjacent idempotency) and
  a failed turn releases the claim so a redelivery can still be handled.
"""

from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.channels.manager import ChannelManager
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus
from app.channels.store import ChannelStore
from deerflow.persistence.im_routing import MessageLink, SessionPointer

pytestmark = pytest.mark.anyio

CHAT = "chat-group"
CONN = "conn-1"
ALICE = "alice"
BOB = "bob"
BOT = "ou_bot"


# ---------------------------------------------------------------------------
# fakes: durable store + LangGraph client
# ---------------------------------------------------------------------------


class FakeRoutingRepo:
    """In-memory twin of :class:`ImRoutingRepository` (same semantics)."""

    def __init__(self) -> None:
        self.pointers: dict[tuple, SessionPointer] = {}
        self.links: dict[str, MessageLink] = {}
        self.events: dict[str, str] = {}
        self.recorded_links: list[MessageLink] = []

    @staticmethod
    def _key(provider: str, connection_id: str, owner: str, channel_type: str, chat: str) -> tuple:
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

    async def cas_switch_pointer(self, *, provider, owner_user_id, chat_id, thread_id, expected_generation, channel_type="group", connection_id=""):
        key = self._key(provider, connection_id, owner_user_id, channel_type, chat_id)
        current = self.pointers.get(key)
        if current is None or current.generation != expected_generation:
            return False
        self.pointers[key] = SessionPointer(thread_id=thread_id, generation=expected_generation + 1)
        return True

    async def get_link(self, *, provider, message_id):
        return self.links.get(f"{provider}:{message_id}")

    async def record_link(self, *, provider, message_id, chat_id, owner_user_id, thread_id, direction, message_kind="", connection_id="", sender_external_id="", root_message_id="", parent_message_id="", task_id=""):
        key = f"{provider}:{message_id}"
        if key in self.links:
            return
        self.links[key] = MessageLink(
            provider=provider,
            message_id=message_id,
            chat_id=chat_id,
            owner_user_id=owner_user_id,
            thread_id=thread_id,
            direction=direction,
            message_kind=message_kind,
            connection_id=connection_id,
            sender_external_id=sender_external_id,
        )

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
    owner: str = ALICE,
    chat_type: str = "group",
    reply: dict[str, Any] | None = None,
    quoted_text: str | None = None,
    msg_type: InboundMessageType = InboundMessageType.CHAT,
) -> InboundMessage:
    metadata: dict[str, Any] = {"message_id": message_id, "chat_type": chat_type, "user_id": "ou_alice"}
    metadata.update(reply or {})
    if quoted_text is not None:
        metadata["quoted_text"] = quoted_text
    return InboundMessage(
        channel_name="feishu",
        chat_id=CHAT,
        user_id="ou_alice",
        text=text,
        msg_type=msg_type,
        connection_id=CONN,
        owner_user_id=owner,
        metadata=metadata,
    )


def _reply_metadata(*, message_id: str = "om-bot-1", sender_type: str = "app", from_self_bot: bool | None = True) -> dict[str, Any]:
    return {
        "reply_message_id": message_id,
        "reply_sender_type": sender_type,
        "reply_sender_id": BOT if sender_type == "app" else "ou_bob",
        "reply_from_self_bot": from_self_bot,
    }


@pytest.fixture
def harness(tmp_path):
    bus = MessageBus()
    outbound: list = []

    async def collect(message) -> None:
        outbound.append(message)

    bus.subscribe_outbound(collect)
    repo = FakeRoutingRepo()
    manager = ChannelManager(bus=bus, store=ChannelStore(tmp_path / "store.json"), routing_repo=repo, route_v2_enabled=True)
    manager._semaphore = asyncio.Semaphore(5)  # normally created by start()
    runs: list[dict[str, Any]] = []
    failures: list[BaseException] = []

    async def fake_run(client, msg, thread_id, **kwargs):
        if failures:
            raise failures.pop(0)
        runs.append({"thread_id": thread_id, "text": msg.text, **kwargs})

    client = FakeClient()
    manager._get_client = lambda: client
    manager._run_chat_on_thread = fake_run
    return SimpleNamespace(manager=manager, repo=repo, client=client, runs=runs, outbound=outbound, failures=failures, store=manager.store)


# ---------------------------------------------------------------------------
# current pointer
# ---------------------------------------------------------------------------


async def test_first_message_creates_one_thread_and_records_the_link(harness):
    msg = _msg()
    await harness.manager._handle_routed_chat(msg)

    assert harness.client.created == ["thread-1"]
    assert harness.runs[0]["thread_id"] == "thread-1"
    assert (await harness.repo.get_pointer(provider="feishu", owner_user_id=ALICE, chat_id=CHAT, connection_id=CONN)).thread_id == "thread-1"
    link = await harness.repo.get_link(provider="feishu", message_id="om-in-1")
    assert (link.thread_id, link.direction, link.owner_user_id) == ("thread-1", "inbound", ALICE)


async def test_next_message_reuses_the_pointer_without_creating_a_thread(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-1"))
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-2"))

    assert harness.client.created == ["thread-1"]
    assert [run["thread_id"] for run in harness.runs] == ["thread-1", "thread-1"]


async def test_each_owner_gets_their_own_thread_in_the_same_group(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-a", owner=ALICE))
    await harness.manager._handle_routed_chat(_msg(message_id="om-b", owner=BOB))

    assert harness.client.created == ["thread-1", "thread-2"]
    assert harness.runs[0]["thread_id"] != harness.runs[1]["thread_id"]


# ---------------------------------------------------------------------------
# reply-based history restore
# ---------------------------------------------------------------------------


async def test_reply_to_own_bot_answer_restores_that_thread_and_keeps_the_pointer(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-1"))
    await harness.repo.record_link(provider="feishu", message_id="om-bot-old", chat_id=CHAT, owner_user_id=ALICE, thread_id="thread-old", direction="outbound")
    harness.client.existing.add("thread-old")

    await harness.manager._handle_routed_chat(_msg("and then?", message_id="om-in-2", reply=_reply_metadata(message_id="om-bot-old")))

    assert harness.runs[-1]["thread_id"] == "thread-old"
    pointer = await harness.repo.get_pointer(provider="feishu", owner_user_id=ALICE, chat_id=CHAT, connection_id=CONN)
    assert pointer.thread_id == "thread-1"
    assert pointer.generation == 1

    # E34/E09: the next plain message still answers in the pointer thread.
    await harness.manager._handle_routed_chat(_msg("back to normal", message_id="om-in-3"))
    assert harness.runs[-1]["thread_id"] == "thread-1"


# ---------------------------------------------------------------------------
# fail closed / reject: no agent run, explicit error
# ---------------------------------------------------------------------------


async def test_restore_of_a_deleted_thread_fails_closed(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-1"))
    await harness.repo.record_link(provider="feishu", message_id="om-bot-old", chat_id=CHAT, owner_user_id=ALICE, thread_id="thread-gone", direction="outbound")

    await harness.manager._handle_routed_chat(_msg(message_id="om-in-2", reply=_reply_metadata(message_id="om-bot-old")))

    assert len(harness.runs) == 1  # only the first turn ran
    assert harness.outbound[-1].text.startswith("无法恢复原会话")
    assert harness.outbound[-1].thread_id == ""
    pointer = await harness.repo.get_pointer(provider="feishu", owner_user_id=ALICE, chat_id=CHAT, connection_id=CONN)
    assert pointer.thread_id == "thread-1"


async def test_reply_to_own_bot_message_without_a_link_fails_closed(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-1"))

    await harness.manager._handle_routed_chat(_msg(message_id="om-in-2", reply=_reply_metadata(message_id="om-bot-unknown")))

    assert len(harness.runs) == 1
    assert "没有找到对应的会话记录" in harness.outbound[-1].text


async def test_reply_to_another_bot_is_rejected_without_running(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-1"))

    await harness.manager._handle_routed_chat(_msg(message_id="om-in-2", reply=_reply_metadata(message_id="om-other-bot", from_self_bot=False)))

    assert len(harness.runs) == 1
    assert "无法确认被引用的消息属于本机器人" in harness.outbound[-1].text


async def test_cross_owner_reply_quotes_into_the_senders_current_thread(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-1", owner=ALICE))
    await harness.repo.record_link(provider="feishu", message_id="om-bot-alice", chat_id=CHAT, owner_user_id=ALICE, thread_id="thread-alice", direction="outbound")

    await harness.manager._handle_routed_chat(
        _msg(
            "what about this quarter?",
            message_id="om-in-bob",
            owner=BOB,
            reply=_reply_metadata(message_id="om-bot-alice"),
            quoted_text="上季度收入 12 亿",
        )
    )

    assert harness.runs[-1]["thread_id"] == "thread-2"  # Bob's own thread
    assert harness.runs[-1]["text"] == "[引用] 群里另一位同事的消息：上季度收入 12 亿\n\nwhat about this quarter?"


# ---------------------------------------------------------------------------
# /new
# ---------------------------------------------------------------------------


async def test_new_starts_a_fresh_thread_and_moves_the_pointer(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-in-1"))

    assert await harness.manager._handle_new_conversation_v2(_msg(message_id="om-new-1", msg_type=InboundMessageType.COMMAND)) == "New conversation started."
    pointer = await harness.repo.get_pointer(provider="feishu", owner_user_id=ALICE, chat_id=CHAT, connection_id=CONN)
    assert pointer.thread_id == "thread-2"
    assert pointer.generation == 2

    await harness.manager._handle_new_conversation_v2(_msg(message_id="om-new-2", msg_type=InboundMessageType.COMMAND))
    pointer = await harness.repo.get_pointer(provider="feishu", owner_user_id=ALICE, chat_id=CHAT, connection_id=CONN)
    assert pointer.thread_id == "thread-3"
    assert pointer.generation == 3

    await harness.manager._handle_routed_chat(_msg(message_id="om-in-2"))
    assert harness.runs[-1]["thread_id"] == "thread-3"


async def test_new_does_not_steal_another_owners_pointer(harness):
    await harness.manager._handle_routed_chat(_msg(message_id="om-a", owner=ALICE))
    await harness.manager._handle_new_conversation_v2(_msg(message_id="om-new-b", owner=BOB, msg_type=InboundMessageType.COMMAND))

    assert (await harness.repo.get_pointer(provider="feishu", owner_user_id=ALICE, chat_id=CHAT, connection_id=CONN)).thread_id == "thread-1"
    assert (await harness.repo.get_pointer(provider="feishu", owner_user_id=BOB, chat_id=CHAT, connection_id=CONN)).thread_id != "thread-1"


# ---------------------------------------------------------------------------
# durable event inbox: one delivery, one run
# ---------------------------------------------------------------------------


async def test_duplicate_platform_delivery_runs_once(harness):
    msg = _msg(message_id="om-dup")
    await harness.manager._handle_message(msg)
    await harness.manager._handle_message(_msg(message_id="om-dup"))

    assert len(harness.runs) == 1
    assert harness.repo.events["feishu:chat-group:om-dup"] == "COMPLETED"


async def test_duplicate_new_command_creates_one_thread(harness):
    await harness.manager._handle_message(_msg("/new", message_id="om-new", msg_type=InboundMessageType.COMMAND))
    await harness.manager._handle_message(_msg("/new", message_id="om-new", msg_type=InboundMessageType.COMMAND))

    assert harness.client.created == ["thread-1"]


async def test_failed_turn_releases_the_claim_for_redelivery(harness):
    harness.failures.append(RuntimeError("gateway down"))
    await harness.manager._handle_message(_msg(message_id="om-retry"))
    assert "feishu:chat-group:om-retry" not in harness.repo.events

    await harness.manager._handle_message(_msg(message_id="om-retry"))
    assert len(harness.runs) == 1
    assert harness.repo.events["feishu:chat-group:om-retry"] == "COMPLETED"


async def test_v2_is_off_without_a_repository(tmp_path):
    manager = ChannelManager(bus=MessageBus(), store=ChannelStore(tmp_path / "store.json"), route_v2_enabled=True)
    assert manager._routing_v2_active(_msg()) is False

    disabled = ChannelManager(bus=MessageBus(), store=ChannelStore(tmp_path / "store2.json"), routing_repo=FakeRoutingRepo(), route_v2_enabled=False)
    assert disabled._routing_v2_active(_msg()) is False
    # A channel without reply provenance stays on the legacy store.
    other = InboundMessage(channel_name="slack", chat_id=CHAT, user_id="ou_alice", text="hi")
    assert disabled._routing_v2_active(other) is False
    assert manager._routing_v2_active(other) is False
