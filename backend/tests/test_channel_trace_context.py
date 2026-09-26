"""Trace ownership for independently dispatched inbound channel messages."""

import asyncio

from app.channels.manager import ChannelManager
from app.channels.message_bus import InboundMessage, MessageBus
from app.channels.store import ChannelStore
from deerflow.trace_context import get_current_trace_id, request_trace_context


def test_concurrent_inbound_messages_receive_distinct_scoped_trace_ids(tmp_path):
    async def run():
        manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
        manager._semaphore = asyncio.Semaphore(2)
        entered = asyncio.Event()
        release = asyncio.Event()
        observations = {}

        async def handle_chat(msg, *, bound_identity_checked):
            assert bound_identity_checked is True
            before = get_current_trace_id()
            observations[msg.text] = [before]
            if len(observations) == 2:
                entered.set()
            await release.wait()
            observations[msg.text].append(get_current_trace_id())

        manager._handle_chat = handle_chat
        first = asyncio.create_task(manager._handle_message(InboundMessage(channel_name="slack", chat_id="C1", user_id="U1", text="one")))
        second = asyncio.create_task(manager._handle_message(InboundMessage(channel_name="slack", chat_id="C2", user_id="U2", text="two")))
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
        finally:
            release.set()
            await asyncio.gather(first, second)

        assert get_current_trace_id() is None
        assert all(values[0] and values[0] == values[1] for values in observations.values())
        assert observations["one"][0] != observations["two"][0]

    asyncio.run(run())


def test_inbound_message_inherits_explicit_parent_trace_and_restores_it(tmp_path):
    async def run():
        manager = ChannelManager(MessageBus(), ChannelStore(path=tmp_path / "channels.json"))
        manager._semaphore = asyncio.Semaphore(2)
        observed = []

        async def handle_chat(msg, *, bound_identity_checked):
            observed.append(get_current_trace_id())
            await asyncio.sleep(0)
            observed.append(get_current_trace_id())

        manager._handle_chat = handle_chat
        with request_trace_context("channel-parent"):
            await manager._handle_message(InboundMessage(channel_name="slack", chat_id="C1", user_id="U1", text="hi"))
            assert get_current_trace_id() == "channel-parent"
        assert get_current_trace_id() is None
        assert observed == ["channel-parent", "channel-parent"]

    asyncio.run(run())
