"""Transport dedupe must happen before visible placeholder side effects."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.feishu import FeishuChannel
from app.channels.message_bus import InboundMessage, MessageBus


@pytest.mark.asyncio
async def test_redelivery_after_final_reply_never_creates_orphan_thinking_card():
    bus = MessageBus()
    channel = FeishuChannel(bus, {})
    channel._attach_connection_identity = AsyncMock(side_effect=lambda m: m)
    channel._add_reaction = AsyncMock()
    channel._ensure_running_card_started = MagicMock()
    msg = InboundMessage(channel_name="feishu", chat_id="chat", user_id="sender", text="总结", thread_ts="msg")
    await asyncio.gather(channel._prepare_inbound("msg", msg), channel._prepare_inbound("msg", msg))
    # A final reply removes the running card; a late SDK retry must still be ignored.
    channel._running_card_ids.clear()
    await channel._prepare_inbound("msg", msg)
    await asyncio.sleep(0)
    assert channel._ensure_running_card_started.call_count == 1
    assert channel._add_reaction.await_count == 1
    assert bus.inbound_queue.qsize() == 1


@pytest.mark.asyncio
async def test_failure_before_dispatch_does_not_permanently_swallow_redelivery():
    bus = MessageBus()
    channel = FeishuChannel(bus, {})
    channel._attach_connection_identity = AsyncMock(side_effect=[RuntimeError("synthetic DB unavailable"), InboundMessage(channel_name="feishu", chat_id="chat", user_id="sender", text="x")])
    channel._add_reaction = AsyncMock()
    channel._ensure_running_card_started = MagicMock()
    msg = InboundMessage(channel_name="feishu", chat_id="chat", user_id="sender", text="x")
    with pytest.raises(RuntimeError):
        await channel._prepare_inbound("msg", msg)
    await channel._prepare_inbound("msg", msg)
    await asyncio.sleep(0)
    assert channel._ensure_running_card_started.call_count == 1


@pytest.mark.asyncio
async def test_sdk_business_failure_does_not_masquerade_as_successful_card_patch():
    from types import SimpleNamespace

    channel = FeishuChannel(MessageBus(), {})
    channel._PatchMessageRequest = MagicMock()
    channel._PatchMessageRequestBody = MagicMock()
    channel._api_client = MagicMock()
    channel._api_client.im.v1.message.patch.return_value = SimpleNamespace(code=999)
    with pytest.raises(RuntimeError, match="rejected"):
        await channel._update_card("message", "synthetic progress")
