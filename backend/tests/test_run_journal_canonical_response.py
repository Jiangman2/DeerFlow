"""Official #5187 response identity, with enterprise attribution/delivery guards."""

import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

pytestmark = pytest.mark.anyio
USAGE = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "input_token_details": {"cache_read": 3}}


def response(*messages):
    return LLMResult(generations=[[ChatGeneration(message=m) for m in messages]])


@pytest.fixture
def journal():
    store = MemoryRunEventStore()
    return RunJournal("r", "t", store, flush_threshold=100), store


@pytest.mark.parametrize("usage", [None, USAGE, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}])
async def test_replayed_response_has_one_durable_row(journal, usage):
    j, store = journal
    rid = uuid4()
    result = response(AIMessage(content="Canonical", usage_metadata=usage))
    for _ in range(3):
        j.on_llm_end(result, run_id=rid)
    await j.flush()
    assert await store.count_messages("t") == 1
    assert len(await store.list_messages("t")) == 1
    assert j.get_completion_data()["message_count"] == 1


@pytest.mark.parametrize("track", [True, False])
async def test_late_usage_preserves_canonical_payload_attribution_and_fallback(journal, track):
    j, store = journal
    j._track_tokens = track
    rid = uuid4()
    canonical = AIMessage(content="Original", tool_calls=[{"id": "one", "name": "read_file", "args": {}}], additional_kwargs={"deerflow_error_fallback": True, "error_detail": "first error"})
    j.on_llm_end(response(canonical), run_id=rid)
    j.on_llm_end(
        response(AIMessage(content="Replay", usage_metadata=USAGE, tool_calls=[{"id": "two", "name": "write_file", "args": {}}], additional_kwargs={"deerflow_error_fallback": True, "error_detail": "replay error"})),
        run_id=rid,
        tags=["subagent:research"],
    )
    await j.flush()
    messages = await store.list_messages("t")
    assert len(messages) == 1
    assert messages[0]["content"]["content"] == "Original"
    assert messages[0]["content"]["tool_calls"] == canonical.tool_calls
    assert messages[0]["metadata"]["usage"] == USAGE
    assert messages[0]["content"]["usage_metadata"] == USAGE
    assert messages[0]["metadata"]["caller"] == "lead_agent"
    assert j._current_run_tool_call_names == {"one": "read_file"}
    assert j.llm_error_fallback_message == "first error"
    assert j.get_last_lead_ai_message().content == "Original"
    assert j.get_completion_data()["last_ai_message"] == "Original"
    assert j.get_completion_data()["lead_agent_tokens"] == (15 if track else 0)
    assert j.get_completion_data()["subagent_tokens"] == 0


@pytest.mark.parametrize("start_kind", ["chat", "llm"])
async def test_enterprise_start_caller_survives_untagged_late_usage(journal, start_kind):
    j, store = journal
    rid = uuid4()
    start = j.on_chat_model_start if start_kind == "chat" else j.on_llm_start
    start({}, [], run_id=rid, metadata={"lc_source": "summarization"})
    j.on_llm_end(response(AIMessage(content="Internal summary")), run_id=rid)
    j.on_llm_end(response(AIMessage(content="Changed replay", usage_metadata=USAGE)), run_id=rid)
    await j.flush()
    messages = await store.list_messages("t")
    assert len(messages) == 1
    assert messages[0]["metadata"]["caller"] == "middleware:summarization"
    assert j.get_completion_data()["middleware_tokens"] == 15
    assert j.get_completion_data()["lead_agent_tokens"] == 0
    assert j.get_completion_data()["last_ai_message"] is None
    assert j.get_last_lead_ai_message() is None


@pytest.mark.parametrize("boundary", ["flush", "middleware", "new_response"])
async def test_late_usage_after_boundary_never_replaces_append_only_history(journal, boundary):
    j, store = journal
    rid = uuid4()
    j.on_llm_end(response(AIMessage(content="First")), run_id=rid)
    if boundary == "flush":
        await j.flush()
    elif boundary == "middleware":
        j.record_middleware("test", name="test", hook="after", action="record", changes={})
    else:
        j.on_llm_end(response(AIMessage(content="Second")), run_id=uuid4())
    j.on_llm_end(response(AIMessage(content="Replay", usage_metadata=USAGE, additional_kwargs={"deerflow_error_fallback": True})), run_id=rid, tags=["subagent:other"])
    await j.flush()
    messages = await store.list_messages("t")
    assert [m["content"]["content"] for m in messages] == (["First", "Second"] if boundary == "new_response" else ["First"])
    assert messages[0]["metadata"]["usage"] == {}
    assert j.get_completion_data()["lead_agent_tokens"] == 15
    assert not j.had_llm_error_fallback
    assert j.get_last_lead_ai_message().content == ("Second" if boundary == "new_response" else "First")


@pytest.mark.parametrize("replay", [True, False])
async def test_mutable_provider_object_cannot_change_persisted_or_delivery_snapshot(journal, replay):
    j, store = journal
    rid = uuid4()
    message = AIMessage(content=[{"type": "text", "text": "Original"}])
    result = response(message)
    j.on_llm_end(result, run_id=rid)
    message.content[0]["text"] = "Mutated"
    if replay:
        message.usage_metadata = {**USAGE, "input_token_details": {"cache_read": 3}}
        j.on_llm_end(result, run_id=rid)
        message.usage_metadata["input_token_details"]["cache_read"] = 999
    await j.flush()
    messages = await store.list_messages("t")
    assert len(messages) == 1
    assert messages[0]["content"]["content"][0]["text"] == "Original"
    assert j.get_completion_data()["last_ai_message"] == "Original"
    assert j.get_last_lead_ai_message().content[0]["text"] == "Original"
    if replay:
        assert messages[0]["metadata"]["usage"]["input_token_details"]["cache_read"] == 3
        assert messages[0]["content"]["usage_metadata"]["input_token_details"]["cache_read"] == 3


@pytest.mark.parametrize("original_count,replay_count", [(1, 2), (2, 1)])
async def test_replay_cannot_change_generation_count(journal, original_count, replay_count):
    j, store = journal
    rid = uuid4()
    j.on_llm_end(response(*(AIMessage(content=str(i)) for i in range(original_count))), run_id=rid)
    j.on_llm_end(response(*(AIMessage(content="Replay", usage_metadata=USAGE) for _ in range(replay_count))), run_id=rid)
    await j.flush()
    messages = await store.list_messages("t")
    assert [m["content"]["content"] for m in messages] == [str(i) for i in range(original_count)]
    assert j.get_completion_data()["message_count"] == original_count
    assert j.get_completion_data()["total_tokens"] == 15


async def test_pending_counts_toward_flush_threshold(journal):
    j, store = journal
    j._flush_threshold = 2
    j.record_middleware("before", name="test", hook="after", action="record", changes={})
    j.on_llm_end(response(AIMessage(content="Pending")), run_id=uuid4())
    await asyncio.gather(*j._pending_flush_tasks)
    assert j._pending_llm_response is None
    assert [e["event_type"] for e in await store.list_events("t", "r")] == ["middleware:before", "llm.ai.response"]


async def test_progress_snapshot_follows_committed_summary():
    snapshots = []

    async def reporter(snapshot):
        snapshots.append(snapshot)

    j = RunJournal("r", "t", MemoryRunEventStore(), progress_reporter=reporter)
    j.on_llm_end(response(AIMessage(content="Complete", usage_metadata=USAGE)), run_id=uuid4())
    await j.flush()
    assert snapshots[0]["message_count"] == 1
    assert snapshots[0]["last_ai_message"] == "Complete"


async def test_enterprise_reconciliation_is_not_a_provider_replay(journal):
    j, store = journal
    rid = uuid4()
    original = AIMessage(id="answer", content="Unaccepted artifact")
    j.on_llm_end(response(original), run_id=rid)
    replacement = original.model_copy(update={"content": "Delivery failed"})
    j.reconcile_ai_message(replacement, reason="interrupted_artifact_delivery")
    j.on_llm_end(response(AIMessage(content="Replayed unsafe artifact", usage_metadata=USAGE)), run_id=rid)
    await j.flush()
    messages = await store.list_messages("t")
    assert len(messages) == 2  # explicit host replacement retains the same message ID
    assert messages[-1]["metadata"]["reconciled"]
    assert messages[-1]["content"]["content"] == "Delivery failed"
    assert j.get_last_lead_ai_message().content == "Delivery failed"
    assert j.get_completion_data()["last_ai_message"] == "Delivery failed"


@pytest.mark.parametrize("flush", [True, False])
async def test_closed_callback_cannot_retain_response_or_restart_writes(journal, flush):
    j, store = journal
    j.on_llm_end(response(AIMessage(content="Pending")), run_id=uuid4())
    await j.close(flush=flush)
    before = j.get_completion_data()
    j.on_llm_end(object(), run_id=uuid4())
    assert j.get_completion_data() == before
    assert j._pending_llm_response is None
    assert j._llm_response_callers == {}
    assert j._buffer == []
    assert await store.count_messages("t") == (1 if flush else 0)


async def test_failed_close_retries_single_canonical_row():
    class FailOnce(MemoryRunEventStore):
        failed = False

        async def put_batch(self, events):
            if not self.failed:
                self.failed = True
                raise RuntimeError("store unavailable")
            return await super().put_batch(events)

    store = FailOnce()
    j = RunJournal("r", "t", store)
    j.on_llm_end(response(AIMessage(content="Pending")), run_id=uuid4())
    with pytest.raises(RuntimeError, match="store unavailable"):
        await j.close()
    assert not j._closed
    assert j.get_completion_data()["message_count"] == 1
    await j.close()
    assert await store.count_messages("t") == 1
    assert j._pending_llm_response is None
