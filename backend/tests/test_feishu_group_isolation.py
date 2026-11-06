"""Group-chat behaviour of the Feishu adapter.

Two properties are locked down here:

* the @-mention gate opens **only** for this bot (``@all`` and other people's
  mentions stay closed), and it also recognises the ``{"tag": "at"}`` element
  of rich-text bodies;
* a Feishu group thread is shared by everybody who posts in it, so the
  DeerFlow topic is namespaced per sender. Alice and Bob replying in the same
  Feishu topic must end up in two separate conversations.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.feishu import FeishuChannel
from app.channels.message_bus import InboundMessage, MessageBus, OutboundMessage
from app.channels.store import ChannelStore

BOT = "ou_bot_self"
ALICE = "ou_alice"
BOB = "ou_bob"
CHAT = "chat_group_1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _channel(tmp_path) -> tuple[FeishuChannel, ChannelStore]:
    store = ChannelStore(tmp_path / "store.json")
    channel = FeishuChannel(
        MessageBus(),
        {"app_id": "test", "app_secret": "test", "channel_store": store},
    )
    channel._bot_open_id = BOT
    return channel, store


def _mention(open_id: str | None, *, union_id: str | None = None, key: str | None = None) -> MagicMock:
    mention = MagicMock()
    mention.key = key or f"@_user_{(open_id or 'anon')[-4:]}"
    mention.name = "someone"
    mention.id.open_id = open_id
    mention.id.user_id = None
    mention.id.union_id = union_id
    return mention


def _event(
    text: str = "hello",
    *,
    mentions: list | None = None,
    content: dict | None = None,
    chat_id: str = CHAT,
    message_id: str = "om_msg",
    sender: str = ALICE,
    chat_type: str = "group",
    root_id: str | None = None,
    parent_id: str | None = None,
    thread_id: str | None = None,
) -> MagicMock:
    event = MagicMock()
    message = event.event.message
    message.chat_id = chat_id
    message.message_id = message_id
    message.root_id = root_id
    message.parent_id = parent_id
    message.thread_id = thread_id
    message.chat_type = chat_type
    message.mentions = mentions or []
    message.content = json.dumps(content if content is not None else {"text": text})
    event.event.sender.sender_id.open_id = sender
    return event


def _deliver(channel: FeishuChannel, event) -> InboundMessage | None:
    """Run one inbound event and return the message handed to the manager."""
    captured: dict[str, InboundMessage] = {}

    def capture(msg_id, inbound, *, source_message_ids=None):
        captured["inbound"] = inbound

    with pytest.MonkeyPatch.context() as m:
        m.setattr(channel, "_schedule_prepare_inbound", capture)
        channel._on_message(event)
    return captured.get("inbound")


def _at_bot(**kwargs) -> MagicMock:
    return _event(mentions=[_mention(BOT, key="@_user_1")], **kwargs)


def _bot_answered(
    channel: FeishuChannel,
    *,
    thread_id: str,
    user_id: str,
    message_id: str,
    topic_id: str | None,
    card_id: str,
    extra: dict | None = None,
) -> None:
    """Emulate the outbound reply that records the thread mapping."""
    metadata = {
        "message_id": message_id,
        "topic_id": topic_id,
        "user_id": user_id,
        "chat_type": "group",
        **(extra or {}),
    }
    channel._remember_thread_mapping(
        OutboundMessage(
            channel_name=channel.name,
            chat_id=CHAT,
            thread_id=thread_id,
            text="answer",
            thread_ts=message_id,
            metadata=metadata,
        ),
        message_id,
        card_id,
    )


# ---------------------------------------------------------------------------
# The gate opens for this bot only
# ---------------------------------------------------------------------------


def test_group_mention_of_the_bot_is_delivered(tmp_path):
    channel, _ = _channel(tmp_path)
    inbound = _deliver(channel, _at_bot(message_id="om_a1"))

    assert inbound is not None
    assert inbound.user_id == ALICE


def test_group_at_all_does_not_open_the_gate(tmp_path):
    channel, _ = _channel(tmp_path)
    inbound = _deliver(
        channel,
        _event(
            "@all standup in 5",
            mentions=[_mention("", key="@_all")],
            message_id="om_all",
        ),
    )

    assert inbound is None


def test_group_mention_without_an_open_id_is_dropped(tmp_path):
    """A mention that only carries union_id can never be proven to be ours."""
    channel, _ = _channel(tmp_path)
    inbound = _deliver(
        channel,
        _event(
            "@DeerFlow hi",
            mentions=[_mention(None, union_id="on_some_union")],
            message_id="om_union",
        ),
    )

    assert inbound is None


def test_group_rich_text_mention_of_the_bot_is_delivered(tmp_path):
    """Post bodies carry the @ inline instead of in ``message.mentions``."""
    channel, _ = _channel(tmp_path)
    content = {
        "title": "",
        "content": [
            [
                {"tag": "at", "user_id": BOT, "user_name": "DeerFlow"},
                {"tag": "text", "text": " please summarise"},
            ]
        ],
    }
    inbound = _deliver(channel, _event(content=content, message_id="om_post"))

    assert inbound is not None
    assert inbound.topic_id == f"{ALICE}:om_post"


def test_group_rich_text_mention_of_someone_else_is_dropped(tmp_path):
    channel, _ = _channel(tmp_path)
    content = {
        "title": "",
        "content": [[{"tag": "at", "user_id": "ou_someone_else", "user_name": "Carol"}, {"tag": "text", "text": " hi"}]],
    }
    inbound = _deliver(channel, _event(content=content, message_id="om_post2"))

    assert inbound is None


# ---------------------------------------------------------------------------
# One conversation per person inside a shared Feishu thread
# ---------------------------------------------------------------------------


def test_two_people_in_one_feishu_thread_get_two_conversations(tmp_path):
    channel, store = _channel(tmp_path)

    first = _deliver(channel, _at_bot(message_id="om_a1"))
    assert first.topic_id == f"{ALICE}:om_a1"
    _bot_answered(channel, thread_id="thread-alice", user_id=ALICE, message_id="om_a1", topic_id=first.topic_id, card_id="om_card_a")

    # Bob replies to Alice's message: ``root_id`` points at her message.
    second = _deliver(channel, _at_bot(message_id="om_b1", sender=BOB, root_id="om_a1"))
    assert second.topic_id == f"{BOB}:om_a1"
    assert store.get_thread_id(channel.name, CHAT, topic_id=second.topic_id) is None
    _bot_answered(channel, thread_id="thread-bob", user_id=BOB, message_id="om_b1", topic_id=second.topic_id, card_id="om_card_b")

    # Alice continues in her own conversation.
    third = _deliver(channel, _at_bot(message_id="om_a2", root_id="om_a1"))
    assert third.topic_id == first.topic_id
    assert store.get_thread_id(channel.name, CHAT, topic_id=third.topic_id) == "thread-alice"

    # ...and Bob continues in his. (The resolved key is the message id Bob
    # replied to, but it must point at *his* thread, never Alice's.)
    fourth = _deliver(channel, _at_bot(message_id="om_b2", sender=BOB, root_id="om_b1"))
    assert fourth.topic_id == f"{BOB}:om_b1"
    assert store.get_thread_id(channel.name, CHAT, topic_id=fourth.topic_id) == "thread-bob"


def test_topic_mode_thread_ids_are_scoped_per_person(tmp_path):
    """In a topic group the topic id is shared, the conversation is not."""
    channel, store = _channel(tmp_path)

    alice = _deliver(channel, _at_bot(message_id="om_a1", thread_id="om_topic_root"))
    assert alice.topic_id == f"{ALICE}:om_a1"
    _bot_answered(
        channel,
        thread_id="thread-alice",
        user_id=ALICE,
        message_id="om_a1",
        topic_id=alice.topic_id,
        card_id="om_card_a",
        extra={"thread_id": "om_topic_root"},
    )

    # Once the topic is mapped, Alice keeps talking in the same conversation.
    alice_again = _deliver(channel, _at_bot(message_id="om_a2", thread_id="om_topic_root"))
    assert alice_again.topic_id == f"{ALICE}:om_topic_root"
    assert store.get_thread_id(channel.name, CHAT, topic_id=alice_again.topic_id) == "thread-alice"

    # Bob posts in the very same Feishu topic and gets his own conversation.
    bob = _deliver(channel, _at_bot(message_id="om_b1", sender=BOB, thread_id="om_topic_root"))
    assert bob.topic_id == f"{BOB}:om_b1"
    assert store.get_thread_id(channel.name, CHAT, topic_id=bob.topic_id) is None


def test_bot_card_reply_continues_only_the_requesters_thread(tmp_path):
    """The bot's card is mapped for the person who asked, nobody else."""
    channel, store = _channel(tmp_path)

    channel._remember_thread_mapping(
        OutboundMessage(
            channel_name=channel.name,
            chat_id=CHAT,
            thread_id="thread-alice",
            text="answer",
            thread_ts="om_a1",
            metadata={
                "message_id": "om_a1",
                "topic_id": f"{ALICE}:om_a1",
                "user_id": ALICE,
                "chat_type": "group",
            },
        ),
        "om_a1",
        "om_card_1",
    )

    assert store.get_thread_id(channel.name, CHAT, topic_id=f"{ALICE}:om_card_1") == "thread-alice"

    alice = _deliver(channel, _at_bot(message_id="om_a2", parent_id="om_card_1"))
    assert alice.topic_id == f"{ALICE}:om_card_1"

    bob = _deliver(channel, _at_bot(message_id="om_b1", sender=BOB, parent_id="om_card_1"))
    assert bob.topic_id != alice.topic_id
    assert store.get_thread_id(channel.name, CHAT, topic_id=bob.topic_id) is None


def test_p2p_chat_keeps_one_unscoped_thread(tmp_path):
    channel, _ = _channel(tmp_path)

    first = _deliver(
        channel,
        _event("hi", chat_type="p2p", chat_id="p2p_chat", message_id="om_dm1", mentions=[]),
    )
    second = _deliver(
        channel,
        _event("again", chat_type="p2p", chat_id="p2p_chat", message_id="om_dm2", mentions=[]),
    )

    assert first.topic_id is None
    assert second.topic_id is None


def test_pending_clarification_is_never_shared_between_people(tmp_path):
    channel, _ = _channel(tmp_path)

    with channel._thread_lock:
        channel._pending_clarifications[channel._pending_key(CHAT, ALICE)] = [
            {
                "thread_id": "thread-alice",
                "topic_id": f"{ALICE}:om_a1",
                "source_message_id": "om_a1",
                "card_message_id": "om_card_1",
                "created_at": 9999999999,
            }
        ]

    assert channel._consume_pending_clarification(CHAT, BOB) is None


# ---------------------------------------------------------------------------
# Duplicate delivery
# ---------------------------------------------------------------------------


def test_duplicate_delivery_of_the_same_message_is_ignored(tmp_path):
    channel, _ = _channel(tmp_path)
    inbound = InboundMessage(
        channel_name=channel.name,
        chat_id=CHAT,
        user_id=ALICE,
        text="hello",
        topic_id=f"{ALICE}:om_a1",
        metadata={"message_id": "om_a1", "user_id": ALICE, "chat_type": "group"},
    )

    published: list[InboundMessage] = []

    async def run() -> None:
        with pytest.MonkeyPatch.context() as m:
            m.setattr(channel, "_add_reaction", AsyncMock())
            m.setattr(channel, "_ensure_running_card_started", MagicMock())
            m.setattr(channel, "_attach_connection_identity", AsyncMock(side_effect=lambda msg: msg))

            async def publish(msg):
                published.append(msg)

            m.setattr(channel.bus, "publish_inbound", publish)
            await channel._prepare_inbound("om_a1", inbound)
            await channel._prepare_inbound("om_a1", inbound)

    asyncio.new_event_loop().run_until_complete(run())
    assert len(published) == 1
