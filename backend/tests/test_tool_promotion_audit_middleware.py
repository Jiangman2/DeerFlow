"""Tests for deferred-tool promotion audit events."""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from deerflow.agents.middlewares.tool_promotion_audit_middleware import DeferredToolPromotionAuditMiddleware
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.middleware_recorder import ParentLoopMiddlewareRecorder


class _Recorder:
    def __init__(self):
        self.calls: list[dict] = []
        self.claimed: set[str] = set()

    def claim_tool_promotions(self, tool_names):
        names = sorted(set(tool_names) - self.claimed)
        self.claimed.update(names)
        return names

    def record_middleware(self, **kwargs):
        self.calls.append(kwargs)


class _ToolRequest:
    def __init__(self, *, name="tool_search", state=None, context=None, query="private query"):
        self.tool_call = {"name": name, "id": "tc1", "args": {"query": query}}
        self.state = state or {}
        self.runtime = SimpleNamespace(context=context or {})


def _middleware():
    return DeferredToolPromotionAuditMiddleware(frozenset({"mcp_a", "mcp_b"}), "h1")


def test_records_only_new_names_from_the_final_current_catalog_command():
    recorder = _Recorder()
    request = _ToolRequest(
        state={"promoted": {"catalog_hash": "h1", "names": ["mcp_b"]}},
        context={"__run_journal": recorder},
        query="credential-adjacent query",
    )
    result = Command(
        update={
            "promoted": {"catalog_hash": "h1", "names": ["not_deferred", "mcp_b", "mcp_a", "mcp_a"]},
            "messages": [ToolMessage(content="private schema", tool_call_id="tc1", name="tool_search")],
        }
    )

    assert _middleware().wrap_tool_call(request, lambda _: result) is result

    assert recorder.calls == [
        {
            "tag": "tool_promotion",
            "name": "DeferredToolPromotionAuditMiddleware",
            "hook": "wrap_tool_call",
            "action": "promote",
            "changes": {
                "source": "tool_search",
                "tool_names": ["mcp_a"],
                "count": 1,
                "is_subagent": False,
                "agent_id": None,
            },
        }
    ]
    persisted = repr(recorder.calls)
    assert "credential-adjacent query" not in persisted
    assert "private schema" not in persisted
    assert "h1" not in persisted


def test_narrow_recorder_identifies_subagent_without_caller_flag():
    recorder = _Recorder()
    request = _ToolRequest(
        context={
            "__run_tool_promotion_recorder": recorder,
            "agent_id": "general-purpose",
        }
    )
    result = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})

    assert _middleware().wrap_tool_call(request, lambda _: result) is result

    assert recorder.calls[0]["changes"]["is_subagent"] is True
    assert recorder.calls[0]["changes"]["agent_id"] == "general-purpose"


def test_lead_attribution_ignores_caller_supplied_subagent_fields():
    recorder = _Recorder()
    request = _ToolRequest(
        context={
            "__run_journal": recorder,
            "is_subagent": True,
            "agent_id": "forged-agent",
        }
    )
    result = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})

    assert _middleware().wrap_tool_call(request, lambda _: result) is result

    assert recorder.calls[0]["changes"]["is_subagent"] is False
    assert recorder.calls[0]["changes"]["agent_id"] is None


@pytest.mark.asyncio
async def test_async_records_promotion_but_repeated_stale_and_non_search_results_do_not():
    recorder = _Recorder()
    middleware = _middleware()
    request = _ToolRequest(context={"__run_journal": recorder})
    promoted = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})

    async def handle_promoted(_):
        return promoted

    assert await middleware.awrap_tool_call(request, handle_promoted) is promoted
    assert recorder.calls[0]["changes"]["tool_names"] == ["mcp_a"]

    request.state = {"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}}

    async def handle_stale(_):
        return Command(update={"promoted": {"catalog_hash": "stale", "names": ["mcp_b"]}})

    assert await middleware.awrap_tool_call(request, handle_promoted) is promoted
    assert await middleware.awrap_tool_call(request, handle_stale)
    request.tool_call["name"] = "another_tool"
    assert await middleware.awrap_tool_call(request, handle_promoted) is promoted
    assert len(recorder.calls) == 1


def test_final_handler_result_is_observed_without_rebuilding_it():
    """An outer audit wrapper must see names after inner policy filtering."""
    recorder = _Recorder()
    request = _ToolRequest(context={"__run_journal": recorder})
    policy_filtered = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})

    observed = _middleware().wrap_tool_call(request, lambda _: policy_filtered)

    assert observed is policy_filtered
    assert recorder.calls[0]["changes"]["tool_names"] == ["mcp_a"]


@pytest.mark.parametrize(
    "result",
    [
        ToolMessage(content="no match", tool_call_id="tc1", name="tool_search"),
        Command(update={}),
        Command(update={"promoted": {"catalog_hash": "h1", "names": []}}),
        Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a", 7]}}),
    ],
)
def test_non_promotion_and_malformed_results_emit_nothing(result):
    recorder = _Recorder()
    request = _ToolRequest(context={"__run_journal": recorder})

    assert _middleware().wrap_tool_call(request, lambda _: result) is result
    assert recorder.calls == []


def test_recorder_failure_does_not_replace_the_tool_result(caplog):
    class BrokenRecorder:
        def record_middleware(self, **kwargs):
            raise RuntimeError("event store unavailable")

    request = _ToolRequest(context={"__run_journal": BrokenRecorder()})
    result = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})

    with caplog.at_level("WARNING"):
        assert _middleware().wrap_tool_call(request, lambda _: result) is result

    assert "Failed to record middleware:tool_promotion event" in caplog.text
    assert "event store unavailable" not in caplog.text


@pytest.mark.anyio
async def test_event_round_trips_through_run_journal_and_store():
    store = MemoryRunEventStore()
    journal = RunJournal("run-1", "thread-1", store, flush_threshold=100)
    request = _ToolRequest(context={"__run_journal": journal})
    result = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})

    assert _middleware().wrap_tool_call(request, lambda _: result) is result
    await journal.flush()

    events = await store.list_events("thread-1", "run-1")
    assert len(events) == 1
    assert events[0]["event_type"] == "middleware:tool_promotion"
    assert events[0]["category"] == "middleware"
    assert events[0]["content"]["changes"] == {
        "source": "tool_search",
        "tool_names": ["mcp_a"],
        "count": 1,
        "is_subagent": False,
        "agent_id": None,
    }


@pytest.mark.anyio
async def test_parallel_searches_claim_the_same_new_name_only_once():
    """Parallel tool Sends share pre-step state but must not duplicate events."""
    store = MemoryRunEventStore()
    journal = RunJournal("run-parallel", "thread-1", store, flush_threshold=100)
    middleware = _middleware()
    result = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})
    requests = [
        _ToolRequest(context={"__run_journal": journal}),
        _ToolRequest(context={"__run_journal": journal}),
    ]

    async def handler(_):
        await asyncio.sleep(0)
        return result

    observed = await asyncio.gather(*(middleware.awrap_tool_call(request, handler) for request in requests))
    await journal.flush()

    assert observed == [result, result]
    events = await store.list_events("thread-1", "run-parallel")
    assert [event["event_type"] for event in events] == ["middleware:tool_promotion"]


@pytest.mark.asyncio
async def test_child_claims_are_atomic_independent_and_closed_with_recorder():
    store = MemoryRunEventStore()
    journal = RunJournal("r", "t", store, flush_threshold=100)
    recorders = [ParentLoopMiddlewareRecorder(journal, asyncio.get_running_loop()) for _ in range(2)]
    middleware = _middleware()
    result = Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a"]}})
    for index, recorder in enumerate(recorders):
        request = _ToolRequest(context={"__run_tool_promotion_recorder": recorder, "agent_id": "child" + str(index)})
        await asyncio.gather(*(asyncio.to_thread(middleware.wrap_tool_call, request, lambda _: result) for _ in range(10)))
        await recorder.aclose()
        assert recorder.claim_tool_promotions(["mcp_b"]) == []
    middleware.wrap_tool_call(_ToolRequest(context={"__run_journal": journal}), lambda _: result)
    await journal.flush()
    events = await store.list_events("t", "r")
    assert [e["content"]["changes"]["agent_id"] for e in events] == ["child0", "child1", None]
    await journal.close()
    assert journal.claim_tool_promotions(["mcp_b"]) == []


@pytest.mark.asyncio
async def test_routing_and_search_share_claims_without_recording_query():
    from langchain_core.messages import HumanMessage

    from deerflow.agents.middlewares.mcp_routing_middleware import McpRoutingMiddleware

    store = MemoryRunEventStore()
    journal = RunJournal("r", "t", store, flush_threshold=100)
    context = {"__run_journal": journal}
    routing = McpRoutingMiddleware({"mcp_a": {"priority": 1, "keywords": ["SECRET_HINT"]}}, "h1", 3)
    state = {"messages": [HumanMessage(content="SECRET_HINT")], "promoted": {"catalog_hash": "old", "names": ["mcp_a"]}}
    assert routing.before_model(state, SimpleNamespace(context=context))["promoted"]["names"] == ["mcp_a"]
    await routing.abefore_model(state, SimpleNamespace(context=context))
    _middleware().wrap_tool_call(_ToolRequest(context=context), lambda _: Command(update={"promoted": {"catalog_hash": "h1", "names": ["mcp_a", "mcp_b"]}}))
    await journal.flush()
    events = await store.list_events("t", "r")
    assert [e["content"]["changes"]["tool_names"] for e in events] == [["mcp_a"], ["mcp_b"]]
    assert [e["content"]["changes"]["source"] for e in events] == ["routing_hint", "tool_search"]
    assert "SECRET_HINT" not in repr(events)
    await journal.close()
