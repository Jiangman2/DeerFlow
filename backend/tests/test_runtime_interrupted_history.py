"""Offline tests of exact production journal and real model cancellation."""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import (
    ChatGeneration,
    ChatGenerationChunk,
    ChatResult,
    LLMResult,
)
from langgraph.graph import END, START, MessagesState, StateGraph


def test_framework_versions_match_production():
    from importlib.metadata import version

    assert version("langchain-core") == "1.3.3"
    assert version("langgraph") == "1.1.9"
    assert version("langgraph-checkpoint") == "4.1.1"


def load_journal():
    return importlib.import_module("deerflow.runtime.journal").RunJournal


class Store:
    def __init__(self):
        self.rows = []

    async def put_batch(self, batch):
        self.rows.extend(batch)


@pytest.fixture
def journal():
    return load_journal()(run_id="run-1", thread_id="thread-1", event_store=Store())


def responses(journal):
    return [r for r in journal._store.rows if r["event_type"] == "llm.ai.response"]


def finalize(journal, *, status="interrupted", action="interrupt", requested=True):
    # Baseline has no finalization hook: reproduce loss without AttributeError.
    method = getattr(journal, "prepare_interrupted_message", None)
    return method(status=status, action=action, requested=requested) if method else None


def persist(journal, message):
    if message is not None:
        journal.persist_interrupted_message(message)


def begin(journal, rid, caller="lead_agent"):
    journal.on_chat_model_start({}, [[HumanMessage(content="test")]], run_id=rid, tags=[caller])


def fail(journal, rid, message, error=None, **kwargs):
    journal.on_llm_error(
        error or asyncio.CancelledError(),
        run_id=rid,
        response=LLMResult(generations=[[ChatGenerationChunk(message=message)]]),
        **kwargs,
    )


def test_cancelled_callback_partial_reaches_durable_history(journal):
    async def scenario():
        rid = uuid4()
        begin(journal, rid)
        fail(journal, rid, AIMessageChunk(content="已经显示的正文", id="visible-ai"))
        assert responses(journal) == []  # callback must not write partial yet
        message = finalize(journal)
        persist(journal, message)
        await journal.flush()
        assert len(responses(journal)) == 1
        row = responses(journal)[0]
        assert row["content"]["content"] == "已经显示的正文"
        assert row["content"]["id"] == "visible-ai"
        assert row["content"]["type"] == "ai"
        assert row["content"]["additional_kwargs"]["deerflow_interrupted_partial"] is True
        assert row["metadata"]["caller"] == "lead_agent"
        assert row["thread_id"] == "thread-1" and row["run_id"] == "run-1"

    asyncio.run(scenario())


class WaitingModel(BaseChatModel):
    @property
    def _llm_type(self):
        return "offline-cancellation-test"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise AssertionError("Network/non-stream model must never run")

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        yield ChatGenerationChunk(message=AIMessageChunk(content="first visible", id="actual-stream"))
        await asyncio.Event().wait()


def test_real_langgraph_model_cancellation_keeps_shown_body(journal):
    async def scenario():
        async def node(state):
            response = await WaitingModel().ainvoke(state["messages"])
            return {"messages": [response]}

        graph = StateGraph(MessagesState).add_node("model", node).add_edge(START, "model").add_edge("model", END).compile()
        shown = asyncio.Event()
        observed = []

        async def consume():
            async for mode, chunk in graph.astream(
                {"messages": [HumanMessage(content="test")]},
                {"callbacks": [journal]},
                stream_mode=["messages", "values"],
            ):
                if mode == "messages":
                    observed.append(chunk[0].content)
                    if hasattr(journal, "observe_stream_message"):
                        journal.observe_stream_message(chunk)
                    shown.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(shown.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert observed == ["first visible"]
        persist(journal, finalize(journal))
        await journal.flush()
        assert [r["content"]["content"] for r in responses(journal)] == ["first visible"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "status,action,requested",
    [
        ("interrupted", "rollback", True),
        ("error", "interrupt", True),
        ("success", "interrupt", True),
        ("running", "interrupt", True),
        ("interrupted", "interrupt", False),
    ],
)
def test_non_user_interrupt_paths_never_write_partial(journal, status, action, requested):
    rid = uuid4()
    begin(journal, rid)
    fail(journal, rid, AIMessageChunk(content="body", id="partial"))
    assert finalize(journal, status=status, action=action, requested=requested) is None
    assert finalize(journal) is None  # discarded, cannot leak into another call
    asyncio.run(journal.flush())
    assert responses(journal) == []


@pytest.mark.parametrize("caller", ["middleware:title", "middleware:summarization", "subagent:research"])
def test_internal_callback_partial_is_not_history(journal, caller):
    rid = uuid4()
    begin(journal, rid, caller)
    fail(journal, rid, AIMessageChunk(content="internal", id="secret"))
    assert finalize(journal) is None


@pytest.mark.parametrize(
    "content,expected",
    [
        ("<think>hidden</think>shown", "shown"),
        ("shown<think>unfinished hidden", "shown"),
        ("shown<thi", "shown"),
        ("shown<think incomplete-attribute", "shown"),
        (
            [
                {"type": "reasoning", "text": "hidden"},
                {"type": "text", "text": "shown"},
                {"type": "function_call", "text": "arguments"},
            ],
            "shown",
        ),
        (
            [{"type": "output_text", "text": "shown"}, {"text": "untyped private"}],
            "shown",
        ),
        ("<think>only hidden", None),
        ([{"type": "reasoning", "text": "hidden"}], None),
        ([{"type": ["unknown"], "text": "hidden"}], None),
        ([], None),
        ("   ", None),
    ],
)
def test_only_visible_body_survives(journal, content, expected):
    rid = uuid4()
    begin(journal, rid)
    fail(
        journal,
        rid,
        AIMessageChunk(
            content=content,
            id="partial",
            additional_kwargs={
                "reasoning_content": "hidden",
                "function_call": {"name": "secret", "arguments": "raw"},
            },
        ),
    )
    message = finalize(journal)
    if expected is None:
        assert message is None
    else:
        assert message.content == expected
        assert message.tool_calls == [] and message.invalid_tool_calls == []
        assert message.additional_kwargs == {"deerflow_interrupted_partial": True}
        assert message.response_metadata == {} and message.usage_metadata is None


def test_successful_provider_callback_is_unchanged_and_dominates_partial(journal):
    async def scenario():
        rid = uuid4()
        begin(journal, rid)
        journal.observe_stream_message((AIMessageChunk(content="first", id="visible"), {"langgraph_node": "model"}))
        fail(journal, rid, AIMessageChunk(content="first", id="visible"))
        complete = AIMessage(
            content="first and last",
            id="visible",
            usage_metadata={"input_tokens": 4, "output_tokens": 5, "total_tokens": 9},
        )
        journal.on_llm_end(LLMResult(generations=[[ChatGeneration(message=complete)]]), run_id=rid)
        assert finalize(journal) is None
        await journal.flush()
        assert responses(journal)[0]["content"] == complete.model_dump()
        assert journal.get_completion_data()["total_tokens"] == 9

    asyncio.run(scenario())


def test_ordinary_model_error_cannot_become_saved_partial(journal):
    rid = uuid4()
    begin(journal, rid)
    journal.observe_stream_message((AIMessageChunk(content="first", id="visible"), {"langgraph_node": "model"}))
    fail(
        journal,
        rid,
        AIMessageChunk(content="first", id="visible"),
        RuntimeError("provider error"),
    )
    assert finalize(journal) is None


def test_fallback_when_cancel_callback_has_no_generation(journal):
    rid = uuid4()
    begin(journal, rid)
    journal.observe_stream_message((AIMessageChunk(content="one ", id="visible"), {"langgraph_node": "model"}))
    journal.observe_stream_message((AIMessageChunk(content="two", id="visible"), {"langgraph_node": "model"}))
    journal.on_llm_error(asyncio.CancelledError(), run_id=rid)
    message = finalize(journal)
    assert message.content == "one two" and message.id == "visible"


def test_no_callback_id_reuses_real_graph_id(journal):
    rid = uuid4()
    begin(journal, rid)
    journal.observe_stream_message((AIMessageChunk(content="one", id="visible"), {"langgraph_node": "model"}))
    fail(journal, rid, AIMessageChunk(content="one"))
    assert finalize(journal).id == "visible"


def test_retry_start_clears_prior_partial(journal):
    rid = uuid4()
    begin(journal, rid)
    fail(journal, rid, AIMessageChunk(content="old", id="old"))
    begin(journal, uuid4())
    assert finalize(journal) is None


@pytest.mark.parametrize(
    "tags,metadata",
    [
        (["lead_agent", "subagent:private"], None),
        (["lead_agent", "middleware:title"], None),
        (["lead_agent"], {"lc_source": "unknown-internal"}),
    ],
)
def test_start_identity_excludes_internal_even_when_error_tags_are_lost(journal, tags, metadata):
    rid = uuid4()
    journal.on_chat_model_start({}, [[HumanMessage(content="test")]], run_id=rid, tags=tags, metadata=metadata)
    fail(journal, rid, AIMessageChunk(content="private", id="internal"))
    assert finalize(journal) is None


def test_many_tiny_chunks_do_not_truncate_ordinary_long_body(journal):
    from deerflow.runtime.interrupted_history import MAX_PARTS

    for _ in range(MAX_PARTS + 5):
        journal.observe_stream_message((AIMessageChunk(content="字", id="visible"), {}))
    message = finalize(journal)
    assert message.content == "字" * (MAX_PARTS + 5)
    assert "deerflow_partial_truncated" not in message.additional_kwargs


@pytest.mark.parametrize(
    "metadata",
    [
        {"lc_source": "title"},
        {"lc_source": "summarization"},
        {"tags": ["lead_agent", "subagent:test"]},
        {"langgraph_checkpoint_ns": "model:parent|child:nested"},
    ],
)
def test_internal_stream_is_not_fallback(journal, metadata):
    journal.observe_stream_message((AIMessageChunk(content="hidden", id="internal"), metadata))
    assert finalize(journal) is None


def test_duplicate_finalize_does_not_duplicate_or_guess_usage(journal):
    async def scenario():
        rid = uuid4()
        begin(journal, rid)
        fail(
            journal,
            rid,
            AIMessageChunk(
                content="body",
                id="partial",
                usage_metadata={
                    "input_tokens": 2,
                    "output_tokens": 3,
                    "total_tokens": 5,
                },
            ),
        )
        message = finalize(journal)
        persist(journal, message)
        persist(journal, message)
        assert finalize(journal) is None
        await journal.flush()
        assert len(responses(journal)) == 1
        assert journal.get_completion_data()["total_tokens"] == 0
        assert journal.get_last_lead_ai_message() is None

    asyncio.run(scenario())


def test_capture_is_bounded_without_per_chunk_database_writes(journal):
    from deerflow.runtime.interrupted_history import MAX_CHARS

    for _ in range(100):
        journal.observe_stream_message((AIMessageChunk(content="x" * 4096, id="visible"), {}))
    assert journal._store.rows == [] and journal._buffer == []
    message = finalize(journal)
    assert len(message.content) == MAX_CHARS
    assert message.additional_kwargs["deerflow_partial_truncated"] is True


@pytest.fixture
def artifact_validator(journal):
    return importlib.import_module("deerflow.agents.middlewares.artifact_delivery_middleware")


@pytest.mark.parametrize(
    "client_requested,action,expected",
    [(True, "interrupt", True), (False, "interrupt", False), (True, "rollback", False)],
)
def test_real_finalization_gate_preserves_record_status(journal, artifact_validator, client_requested, action, expected):
    async def scenario():
        from deerflow.runtime.interrupted_history import persist_interrupted_history

        event = asyncio.Event()
        event.set()
        record = types.SimpleNamespace(
            status=types.SimpleNamespace(value="interrupted"),
            abort_action=action,
            abort_event=event,
            abort_requested_by_client=client_requested,
        )
        rid = uuid4()
        begin(journal, rid)
        fail(journal, rid, AIMessageChunk(content="body", id="visible"))
        assert await persist_interrupted_history(journal=journal, record=record, checkpointer=None, thread_id="thread-1") is expected
        assert record.status.value == "interrupted" and record.abort_action == action
        await journal.flush()
        assert bool(responses(journal)) is expected

    asyncio.run(scenario())


@pytest.mark.parametrize("broken_checkpoint", [False, True])
def test_partial_file_claim_never_bypasses_artifact_guard(journal, artifact_validator, broken_checkpoint):
    async def scenario():
        from deerflow.runtime.interrupted_history import persist_interrupted_history

        class Checkpoint:
            async def aget_tuple(self, config):
                raise RuntimeError("read unavailable")

        event = asyncio.Event()
        event.set()
        record = types.SimpleNamespace(
            status=types.SimpleNamespace(value="interrupted"),
            abort_action="interrupt",
            abort_event=event,
            abort_requested_by_client=True,
        )
        rid = uuid4()
        begin(journal, rid)
        fail(
            journal,
            rid,
            AIMessageChunk(content="[deck](/mnt/user-data/outputs/fake.pptx)", id="visible"),
        )
        assert await persist_interrupted_history(
            journal=journal,
            record=record,
            checkpointer=Checkpoint() if broken_checkpoint else None,
            thread_id="thread-1",
        )
        await journal.flush()
        message = responses(journal)[0]["content"]
        assert message["id"] == "visible"
        assert "fake.pptx" not in message["content"]
        assert message["additional_kwargs"]["deerflow_error_fallback"] is True

    asyncio.run(scenario())


def test_published_artifact_is_still_visible(journal, artifact_validator, tmp_path):
    async def scenario():
        from deerflow.runtime.interrupted_history import persist_interrupted_history

        output = tmp_path / "report.txt"
        output.write_text("real artifact", encoding="utf-8")
        state = {
            "artifacts": ["/mnt/user-data/outputs/report.txt"],
            "thread_data": {"outputs_path": str(tmp_path)},
        }

        class Checkpoint:
            async def aget_tuple(self, config):
                return types.SimpleNamespace(checkpoint={"channel_values": state})

        event = asyncio.Event()
        event.set()
        record = types.SimpleNamespace(
            status=types.SimpleNamespace(value="interrupted"),
            abort_action="interrupt",
            abort_event=event,
            abort_requested_by_client=True,
        )
        begin(journal, rid := uuid4())
        fail(
            journal,
            rid,
            AIMessageChunk(content="[report](/mnt/user-data/outputs/report.txt)", id="visible"),
        )
        assert await persist_interrupted_history(
            journal=journal,
            record=record,
            checkpointer=Checkpoint(),
            thread_id="thread-1",
        )
        await journal.flush()
        assert responses(journal)[0]["content"]["content"] == "[report](/mnt/user-data/outputs/report.txt)"
        assert state["artifacts"] == ["/mnt/user-data/outputs/report.txt"]  # validation cannot publish files

    asyncio.run(scenario())


def history_scanner(rows):
    """Run the unchanged production pagination/filter code, not a reimplementation.

    The fixture represents an already-authorized request. Authentication, SQL
    drivers and browser rendering are not claimed as local test coverage.
    """
    import ast
    import logging

    source = (Path(__file__).resolve().parents[1] / "app/gateway/routers/thread_runs.py").read_bytes()
    tree = ast.parse(source)
    names = {
        "_scan_thread_message_page",
        "_is_hidden_message_row",
        "_is_middleware_message_row",
        "_is_internal_compaction_transcript",
        "_message_type",
        "_message_text",
        "_message_content",
    }
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes} == names
    module = types.ModuleType("production_history_scanner")

    class EventStore:
        async def list_messages(self, thread_id, *, limit, before_seq, user_id):
            assert thread_id == "thread-1" and user_id == "owner-1"
            return [row for row in rows if before_seq is None or row["seq"] < before_seq][-limit:]

    class RunManager:
        async def list_successful_regenerate_sources(self, thread_id, *, user_id):
            return set()

    from langchain_core.messages import BaseMessage

    if "deerflow.utils.messages" not in sys.modules:
        load_journal()
    from deerflow.utils.messages import message_to_text

    module.__dict__.update(
        logger=logging.getLogger("test"),
        BaseMessage=BaseMessage,
        message_to_text=message_to_text,
        THREAD_MESSAGE_PAGE_SCAN_BATCH=201,
        get_run_event_store=lambda request: EventStore(),
        get_run_manager=lambda request: RunManager(),
    )
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[])),
            "production-thread-history",
            "exec",
        ),
        module.__dict__,
    )
    return module._scan_thread_message_page


def test_refresh_history_returns_interrupted_body_with_stable_id(journal):
    async def scenario():
        begin(journal, rid := uuid4())
        fail(journal, rid, AIMessageChunk(content="shown before stop", id="visible"))
        persist(journal, finalize(journal))
        await journal.flush()
        row = dict(responses(journal)[0], seq=2)
        human = {
            "seq": 1,
            "run_id": "run-1",
            "content": {"type": "human", "id": "human-1", "content": "test"},
        }
        scanner = history_scanner([human, row])
        page, more = await scanner("thread-1", limit=50, before_seq=None, request=object(), user_id="owner-1")
        assert not more and len(page) == 2
        assert page[1]["content"]["content"] == "shown before stop"
        assert page[1]["content"]["id"] == "visible"

    asyncio.run(scenario())


def test_history_message_identity_uses_latest_row():
    async def scenario():
        rows = [
            {
                "seq": seq,
                "run_id": "run-1",
                "content": {"type": "ai", "id": "same", "content": text},
            }
            for seq, text in [(1, "old"), (2, "current")]
        ]
        page, more = await history_scanner(rows)("thread-1", limit=50, before_seq=None, request=object(), user_id="owner-1")
        assert not more and [row["content"]["content"] for row in page] == ["current"]

    asyncio.run(scenario())
