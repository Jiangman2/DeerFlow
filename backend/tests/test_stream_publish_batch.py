"""Regression tests for ordered, time-bounded model-message batches."""

import asyncio

import pytest

from deerflow.runtime.runs.stream_publish_batch import MessagePublishBatcher


def _ai_delta(content="", *, message_id="ai-1", reasoning="", metadata=None, **fields):
    message = {"type": "ai", "id": message_id, "content": content, "additional_kwargs": {}}
    if reasoning:
        message["additional_kwargs"]["reasoning_content"] = reasoning
    message.update(fields)
    return [message, metadata if metadata is not None else {"langgraph_node": "agent"}]


@pytest.mark.anyio
async def test_adjacent_ai_text_deltas_coalesce_without_losing_order_or_replay_boundary():
    writes = []

    async def one(event, data, mode):
        writes.append((event, data))

    async def many(events):
        writes.extend(events)

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True)
    await batch.publish("messages", _ai_delta("Hel"), "messages")
    await batch.publish("messages", _ai_delta("lo"), "messages")
    await batch.publish("messages", _ai_delta("!"), "messages")
    await batch.publish("updates", {"step": 1}, "updates")
    await batch.finish()

    assert writes == [("messages", _ai_delta("Hello!")), ("updates", {"step": 1})]
    assert (batch.source_message_count, batch.coalesced_message_count, batch.batched_event_count) == (3, 2, 1)


@pytest.mark.anyio
async def test_reasoning_and_text_stay_separate_and_usage_is_not_coalesced():
    writes = []

    async def one(event, data, mode):
        raise AssertionError("unexpected non-message event")

    async def many(events):
        writes.extend(events)

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True)
    await batch.publish("messages", _ai_delta(reasoning="think"), "messages")
    await batch.publish("messages", _ai_delta(reasoning=" more"), "messages")
    await batch.publish("messages", _ai_delta("answer"), "messages")
    await batch.publish("messages", _ai_delta("!", usage_metadata={"output_tokens": 2}), "messages")
    await batch.finish()

    assert writes == [
        ("messages", _ai_delta(reasoning="think more")),
        ("messages", _ai_delta("answer")),
        ("messages", _ai_delta("!", usage_metadata={"output_tokens": 2})),
    ]


@pytest.mark.anyio
async def test_cumulative_usage_and_stable_response_metadata_keep_latest_snapshot():
    writes = []

    async def one(event, data, mode):
        raise AssertionError("unexpected non-message event")

    async def many(events):
        writes.extend(events)

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True)

    def observed_delta(text, output_tokens):
        return _ai_delta(
            reasoning=text,
            message_id="same-completion",
            response_metadata={"model_provider": "openai"},
            usage_metadata={
                "input_tokens": 11646,
                "output_tokens": output_tokens,
                "total_tokens": 11646 + output_tokens,
                "input_token_details": {"cache_read": 3000},
                "output_token_details": {"reasoning": output_tokens},
            },
            type="AIMessageChunk",
        )

    for output_tokens, text in ((6170, "one"), (6171, " two"), (6172, " three")):
        await batch.publish("messages-tuple", observed_delta(text, output_tokens), "messages")
    await batch.finish()

    assert writes == [("messages-tuple", observed_delta("one two three", 6172))]
    assert (batch.source_message_count, batch.coalesced_message_count, batch.batched_event_count) == (3, 2, 1)


@pytest.mark.anyio
async def test_non_cumulative_or_changed_usage_and_response_metadata_stay_separate():
    writes = []

    async def one(event, data, mode):
        raise AssertionError("unexpected non-message event")

    async def many(events):
        writes.extend(events)

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True)

    def delta(text, input_tokens, output_tokens, *, provider="openai", details=None):
        return _ai_delta(
            text,
            response_metadata={"model_provider": provider},
            usage_metadata={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "output_token_details": {"reasoning": output_tokens if details is None else details},
            },
        )

    events = [
        delta("a", 100, 1),
        delta("b", 100, 1),  # Equal per-chunk usage is not a cumulative snapshot.
        delta("c", 100, 2, details=0),  # A nested counter went backwards.
        delta("d", 100, 3, provider="other"),
        delta("e", 101, 4, provider="other"),  # Prompt-token count changed.
    ]
    for event in events:
        await batch.publish("messages", event, "messages")
    await batch.finish()

    assert writes == [("messages", event) for event in events]
    assert batch.coalesced_message_count == 0


@pytest.mark.anyio
async def test_different_message_ids_metadata_and_tool_chunks_do_not_merge():
    writes = []

    async def one(event, data, mode):
        raise AssertionError("unexpected non-message event")

    async def many(events):
        writes.extend(events)

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True)
    first = _ai_delta("a")
    other_id = _ai_delta("b", message_id="ai-2")
    other_node = _ai_delta("c", message_id="ai-2", metadata={"langgraph_node": "subagent"})
    tool_delta = _ai_delta("", message_id="ai-2", tool_call_chunks=[{"name": "bash", "args": "{}"}])
    final_metadata = _ai_delta("d", message_id="ai-2", response_metadata={"finish_reason": "stop"})
    unknown_type = _ai_delta("e", message_id="ai-2", type=["unexpected"])
    await batch.publish("messages", first, "messages")
    await batch.publish("messages", other_id, "messages")
    await batch.publish("messages", other_node, "messages")
    await batch.publish("messages", tool_delta, "messages")
    await batch.publish("messages", final_metadata, "messages")
    await batch.publish("messages", unknown_type, "messages")
    await batch.finish()

    assert writes == [("messages", data) for data in (first, other_id, other_node, tool_delta, final_metadata, unknown_type)]
    assert batch.coalesced_message_count == 0


@pytest.mark.anyio
async def test_coalescing_respects_size_and_source_count_limits():
    writes = []

    async def one(event, data, mode):
        raise AssertionError("unexpected non-message event")

    async def many(events):
        writes.append(tuple(events))

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True, max_events=3, max_coalesced_chars=4, max_delay_seconds=60)
    await batch.publish("messages", _ai_delta("ab"), "messages")
    await batch.publish("messages", _ai_delta("cd"), "messages")
    await batch.publish("messages", _ai_delta("e"), "messages")
    assert writes == [(("messages", _ai_delta("abcd")), ("messages", _ai_delta("e")))]
    await batch.finish()


@pytest.mark.anyio
async def test_messages_flush_at_size_and_before_other_modes():
    writes = []

    async def one(event, data, mode):
        writes.append(("one", event, data, mode))

    async def many(events):
        writes.append(("many", tuple(events)))

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True, max_events=3, max_delay_seconds=60)
    await batch.publish("messages-tuple", 1, "messages")
    await batch.publish("messages-tuple", 2, "messages")
    assert writes == []
    await batch.publish("messages-tuple", 3, "messages")
    await batch.publish("messages-tuple", 4, "messages")
    await batch.publish("updates", {"step": 1}, "updates")
    await batch.finish()

    assert (batch.batch_count, batch.batched_event_count, batch.max_batch_size_observed) == (2, 4, 3)
    assert writes == [
        ("many", (("messages-tuple", 1), ("messages-tuple", 2), ("messages-tuple", 3))),
        ("many", (("messages-tuple", 4),)),
        ("one", "updates", {"step": 1}, "updates"),
    ]


@pytest.mark.anyio
async def test_quiet_stream_flushes_on_deadline_and_finish_flushes_tail():
    writes = []
    written = asyncio.Event()

    async def one(event, data, mode):
        writes.append((event, data))

    async def many(events):
        writes.extend(events)
        written.set()

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True, max_delay_seconds=0.01)
    await batch.publish("messages-tuple", "first", "messages")

    async def next_chunk():
        await asyncio.sleep(0.05)
        return "next"

    pending = asyncio.create_task(batch.next_item(next_chunk))
    await asyncio.wait_for(written.wait(), timeout=1)
    assert await pending == "next"
    await batch.publish("messages-tuple", "last", "messages")
    await batch.finish()
    assert writes == [("messages-tuple", "first"), ("messages-tuple", "last")]


@pytest.mark.anyio
async def test_timer_write_failure_is_returned_to_run():
    started = asyncio.Event()

    async def one(event, data, mode):
        raise AssertionError("must not publish a later event")

    async def many(events):
        started.set()
        raise ConnectionError("batch failed")

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True, max_delay_seconds=0.01)
    await batch.publish("messages-tuple", "first", "messages")
    with pytest.raises(ConnectionError, match="batch failed"):
        await batch.next_item(lambda: asyncio.sleep(1))
    assert started.is_set()
    await batch.finish()


@pytest.mark.anyio
async def test_next_item_does_not_cancel_live_graph_on_flush_deadline():
    received = asyncio.Event()
    writes = []

    async def one(event, data, mode):
        writes.append((event, data))

    async def many(events):
        writes.extend(events)

    async def next_chunk():
        await received.wait()
        return "still alive"

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True, max_delay_seconds=0.01)
    await batch.publish("messages-tuple", "first", "messages")
    pending = asyncio.create_task(batch.next_item(next_chunk))
    await asyncio.sleep(0.03)
    assert writes == [("messages-tuple", "first")]
    assert not pending.done()
    received.set()
    assert await pending == "still alive"
    await batch.finish()


@pytest.mark.anyio
async def test_disabled_batch_preserves_immediate_publication():
    writes = []

    async def one(event, data, mode):
        writes.append((event, data, mode))

    async def many(events):
        raise AssertionError("memory bridge should not batch")

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=False)
    await batch.publish("messages-tuple", "now", "messages")
    assert writes == [("messages-tuple", "now", "messages")]
    await batch.finish()


@pytest.mark.anyio
async def test_finish_does_not_mask_existing_graph_error(caplog):
    async def one(event, data, mode):
        raise AssertionError("no later publish")

    async def many(events):
        raise ConnectionError("Redis write failed")

    batch = MessagePublishBatcher(publish_one=one, publish_many=many, enabled=True)
    await batch.publish("messages-tuple", "first", "messages")
    graph_error = ValueError("model stream failed")
    await batch.finish(prior_error=graph_error)
    assert "Redis write failed" in caplog.text
