"""Feishu adapter contract under V2 routing.

The adapter's job stops at *describing* a quote: which message, from whom, and
is it ours. It must not rewrite the prompt (that would double-quote, because the
manager restores the thread or injects the text) and it must record outbound
cards so a later reply can be resolved back to its thread.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.feishu import (
    QUOTED_TEXT_METADATA_KEY,
    REPLY_FROM_SELF_BOT_METADATA_KEY,
    REPLY_MESSAGE_ID_METADATA_KEY,
    REPLY_SENDER_ID_METADATA_KEY,
    REPLY_SENDER_TYPE_METADATA_KEY,
    FeishuChannel,
)
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus, OutboundMessage
from app.channels.store import ChannelStore

BOT = "ou_bot_self"
ALICE = "ou_alice"
CHAT = "chat_group_1"

pytestmark = pytest.mark.anyio


class RecordingRepo:
    def __init__(self) -> None:
        self.links: list[dict] = []

    async def record_link(self, **kwargs) -> None:
        self.links.append(kwargs)


def _channel(tmp_path, repo: RecordingRepo | None = None) -> FeishuChannel:
    config = {
        "app_id": "test",
        "app_secret": "test",
        "channel_store": ChannelStore(tmp_path / "store.json"),
        "route_v2_enabled": True,
    }
    if repo is not None:
        config["routing_repo"] = repo
    channel = FeishuChannel(MessageBus(), config)
    channel._bot_open_id = BOT
    return channel


def _inbound(*, text: str = "and then?", parent_id: str = "om_bot_1", chat_type: str = "group") -> InboundMessage:
    metadata = {"message_id": "om_in_1", "chat_type": chat_type, "user_id": ALICE}
    if parent_id:
        metadata["parent_id"] = parent_id
        # What ``_on_message`` derives from the event payload.
        metadata[REPLY_MESSAGE_ID_METADATA_KEY] = parent_id
    return InboundMessage(channel_name="feishu", chat_id=CHAT, user_id=ALICE, text=text, msg_type=InboundMessageType.CHAT, metadata=metadata)


def _event(*, text: str = "hello", message_id: str = "om_in_1", parent_id: str | None = None, root_id: str | None = None) -> MagicMock:
    event = MagicMock()
    message = event.event.message
    message.chat_id = CHAT
    message.message_id = message_id
    message.root_id = root_id
    message.parent_id = parent_id
    message.thread_id = None
    message.chat_type = "group"
    mention = MagicMock()
    mention.key = "@_user_1"
    mention.name = "DeerFlow"
    mention.id.open_id = BOT
    message.mentions = [mention]
    message.content = json.dumps({"text": text})
    event.event.sender.sender_id.open_id = ALICE
    return event


# ---------------------------------------------------------------------------
# reply provenance
# ---------------------------------------------------------------------------


async def test_own_bot_reply_is_reported_without_touching_the_prompt(tmp_path, monkeypatch):
    channel = _channel(tmp_path)
    monkeypatch.setattr(channel, "_fetch_quoted_message_details", AsyncMock(return_value=("上季度收入 12 亿", BOT, "app")))
    inbound = _inbound()

    await channel._prepend_quoted_context(inbound)

    assert inbound.text == "and then?"  # no inline [引用] prefix under V2
    metadata = inbound.metadata
    assert metadata[REPLY_MESSAGE_ID_METADATA_KEY] == "om_bot_1"
    assert metadata[REPLY_SENDER_TYPE_METADATA_KEY] == "app"
    assert metadata[REPLY_SENDER_ID_METADATA_KEY] == BOT
    assert metadata[REPLY_FROM_SELF_BOT_METADATA_KEY] is True
    # The public text is available for a colleague who cannot share my thread.
    assert metadata[QUOTED_TEXT_METADATA_KEY] == "上季度收入 12 亿"


async def test_another_bot_is_reported_as_not_ours(tmp_path, monkeypatch):
    channel = _channel(tmp_path)
    monkeypatch.setattr(channel, "_fetch_quoted_message_details", AsyncMock(return_value=("another bot says", "ou_other_bot", "app")))

    inbound = _inbound()
    await channel._prepend_quoted_context(inbound)

    assert inbound.metadata[REPLY_FROM_SELF_BOT_METADATA_KEY] is False


async def test_unknown_bot_identity_fails_closed(tmp_path, monkeypatch):
    channel = _channel(tmp_path)
    channel._bot_open_id = None
    monkeypatch.setattr(channel, "_fetch_quoted_message_details", AsyncMock(return_value=("text", "ou_some_bot", "app")))
    refresh = MagicMock()
    monkeypatch.setattr(channel, "_schedule_bot_open_id_refresh", refresh)

    inbound = _inbound()
    await channel._prepend_quoted_context(inbound)

    assert inbound.metadata[REPLY_FROM_SELF_BOT_METADATA_KEY] is None
    refresh.assert_called_once()


async def test_unreadable_quote_is_reported_as_unknown(tmp_path, monkeypatch):
    channel = _channel(tmp_path)
    monkeypatch.setattr(channel, "_fetch_quoted_message_details", AsyncMock(return_value=(None, "", "")))

    inbound = _inbound()
    await channel._prepend_quoted_context(inbound)

    assert inbound.metadata[REPLY_SENDER_TYPE_METADATA_KEY] == "unknown"
    assert REPLY_FROM_SELF_BOT_METADATA_KEY not in inbound.metadata
    assert QUOTED_TEXT_METADATA_KEY not in inbound.metadata


async def test_human_quote_is_reported_as_a_user(tmp_path, monkeypatch):
    channel = _channel(tmp_path)
    monkeypatch.setattr(channel, "_fetch_quoted_message_details", AsyncMock(return_value=("明天十点开会", "ou_bob", "user")))

    inbound = _inbound()
    await channel._prepend_quoted_context(inbound)

    assert inbound.metadata[REPLY_SENDER_TYPE_METADATA_KEY] == "user"
    assert QUOTED_TEXT_METADATA_KEY in inbound.metadata


async def test_message_without_a_reply_records_no_provenance(tmp_path):
    channel = _channel(tmp_path)
    inbound = _inbound(parent_id="")

    await channel._prepend_quoted_context(inbound)

    assert REPLY_MESSAGE_ID_METADATA_KEY not in inbound.metadata


# ---------------------------------------------------------------------------
# picking the reply target out of a Feishu event
# ---------------------------------------------------------------------------


def _deliver(channel: FeishuChannel, event) -> InboundMessage | None:
    captured: dict[str, InboundMessage] = {}

    def capture(msg_id, inbound, *, source_message_ids=None):
        captured["inbound"] = inbound

    with pytest.MonkeyPatch.context() as m:
        m.setattr(channel, "_schedule_prepare_inbound", capture)
        channel._on_message(event)
    return captured.get("inbound")


def test_only_parent_id_is_treated_as_a_reply(tmp_path):
    channel = _channel(tmp_path)

    reply = _deliver(channel, _event(message_id="om_r1", parent_id="om_quoted", root_id="om_topic_root"))
    assert reply is not None
    assert reply.metadata[REPLY_MESSAGE_ID_METADATA_KEY] == "om_quoted"

    # root_id only means "same Feishu topic", never "replying to that message".
    topic_member = _deliver(channel, _event(message_id="om_r2", root_id="om_topic_root"))
    assert topic_member is not None
    assert topic_member.metadata.get(REPLY_MESSAGE_ID_METADATA_KEY) is None


def test_a_message_quoting_itself_is_not_a_reply(tmp_path):
    channel = _channel(tmp_path)

    inbound = _deliver(channel, _event(message_id="om_same", parent_id="om_same"))

    assert inbound is not None
    assert inbound.metadata[REPLY_MESSAGE_ID_METADATA_KEY] is None


# ---------------------------------------------------------------------------
# outbound links
# ---------------------------------------------------------------------------


def _outbound(*, thread_id: str = "thread-1", owner: str | None = ALICE, metadata: dict | None = None) -> OutboundMessage:
    return OutboundMessage(
        channel_name="feishu",
        chat_id=CHAT,
        thread_id=thread_id,
        text="answer",
        owner_user_id=owner,
        metadata={"message_id": "om_in_1", "chat_type": "group", **(metadata or {})},
    )


async def test_outbound_card_is_linked_to_its_thread(tmp_path):
    repo = RecordingRepo()
    channel = _channel(tmp_path, repo)

    await channel._remember_outbound_message(_outbound(), "om_card_1", "om_card_2")

    assert [link["message_id"] for link in repo.links] == ["om_card_1", "om_card_2"]
    first = repo.links[0]
    assert (first["direction"], first["message_kind"], first["thread_id"], first["owner_user_id"]) == ("outbound", "card", "thread-1", ALICE)
    assert first["sender_external_id"] == BOT
    assert first["chat_id"] == CHAT


async def test_outbound_link_requires_an_owner(tmp_path):
    repo = RecordingRepo()
    channel = _channel(tmp_path, repo)

    await channel._remember_outbound_message(_outbound(owner=None), "om_card_1")

    assert repo.links == []


def test_json_pointer_is_not_written_under_v2(tmp_path):
    channel = _channel(tmp_path)
    store = channel.config["channel_store"]
    message = _outbound(metadata={"topic_id": f"{ALICE}:om_in_1"})

    channel._remember_thread_mapping(message, "om_in_1", "om_card_1")

    assert store.get_thread_id(channel.name, CHAT, topic_id=f"{ALICE}:om_in_1") is None
    assert store.get_thread_id(channel.name, CHAT, topic_id=f"{ALICE}:om_card_1") is None


# ---------------------------------------------------------------------------
# ADR-02: only a real mention entity opens the group gate
# ---------------------------------------------------------------------------


def _without_mentions(event: MagicMock) -> MagicMock:
    event.event.message.mentions = []
    return event


def test_reply_without_a_mention_is_dropped(tmp_path):
    """E31: tapping "reply" alone must not restore anything."""
    channel = _channel(tmp_path)

    assert _deliver(channel, _without_mentions(_event(message_id="om_r1", parent_id="om_bot_1"))) is None


def test_new_command_without_a_mention_is_dropped(tmp_path):
    """E32: a bare ``/new`` in a group must not create a thread."""
    channel = _channel(tmp_path)

    assert _deliver(channel, _without_mentions(_event(text="/new", message_id="om_r2"))) is None


def test_plain_text_at_bot_is_dropped(tmp_path):
    """E33: "@DeerFlow" typed as text is not a platform mention."""
    channel = _channel(tmp_path)

    assert _deliver(channel, _without_mentions(_event(text="@DeerFlow hello", message_id="om_r3"))) is None
