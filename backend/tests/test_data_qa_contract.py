"""Offline contract checks; synthetic SSE is not live new-App acceptance."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from pydantic import ValidationError

from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from deerflow.community.appforge import tools
from deerflow.community.appforge.data_qa_stream import DataQAStreamError, collect_data_qa_stream
from deerflow.config.datalake_tools import DATA_QA_USE, RETIRED_DATALAKE_TOOL_NAMES
from deerflow.config.tool_config import ToolConfig


def tool_graph(node):
    builder = StateGraph(MessagesState)
    builder.add_node("tools", node)
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


def stream(*events):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())


def collect(response):
    return collect_data_qa_stream(response, parse_knowledge=tools._parse_knowledge)


def test_data_qa_never_inherits_other_credentials_or_identity(monkeypatch):
    for name in ("APPFORGE_API_KEY", "APPFORGE_MD_SEARCH_API_KEY", "APPFORGE_HYTOKEN", "APPFORGE_HYACCOUNT", "APPFORGE_DETAIL_QA_HYTOKEN", "APPFORGE_METRICS_QA_HYACCOUNT"):
        monkeypatch.setenv(name, "must-not-inherit")
    monkeypatch.delenv("APPFORGE_DATA_QA_API_KEY", raising=False)
    monkeypatch.setenv("APPFORGE_API_URL", "http://unrelated.invalid")
    with patch.object(tools, "_tool_extra", return_value={}):
        assert tools._api_key("datalake_data_qa") is None
        assert tools._api_url("datalake_data_qa") == "http://10.77.124.142:9080"
        assert tools._get_inputs("datalake_data_qa") == {"hytoken": "", "hyaccount": ""}
        monkeypatch.setenv("APPFORGE_DATA_QA_API_KEY", "dedicated")
        assert tools._api_key("datalake_data_qa") == "dedicated"


def test_new_request_contract_has_exact_body_and_required_query():
    runtime = SimpleNamespace(context={"user_id": "u1", "thread_id": "t1", "datalake_enabled": True})
    with patch.object(tools, "_tool_extra", return_value={"api_key": "Bearer dedicated"}):
        with patch.object(tools, "_stream_invoke", return_value={"answer": "ok"}) as invoke:
            tools._invoke_tool("datalake_data_qa", "  实际问题  ", runtime)
    name, body = invoke.call_args.args
    assert name == "datalake_data_qa"
    assert set(body) == {"id", "inputs", "query", "conversationId"}
    assert body["id"] == "app-v16p2syh"
    assert body["inputs"] == {"hytoken": "", "hyaccount": ""}
    assert body["query"] == "实际问题"
    assert body["conversationId"].startswith("SquareWorkflow_")
    assert tools._headers("Bearer dedicated") == {"Authorization": "Bearer dedicated", "Content-Type": "application/json", "Accept": "text/event-stream"}
    schema = tools.datalake_data_qa_tool.tool_call_schema.model_json_schema()
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query"}


def test_user_thread_tool_isolation_and_no_thread_randomness():
    def cid(user, thread, name="datalake_data_qa"):
        return tools._conversation_id(name, SimpleNamespace(context={"user_id": user, "thread_id": thread}))

    with patch.object(tools, "_tool_extra", return_value={}):
        first = cid("u1", "t1")
        assert first == cid("u1", "t1")
        assert len({first, cid("u2", "t1"), cid("u1", "t2"), cid("u1", "t1", "datalake_md_search")}) == 4
        assert cid("u1", "") != cid("u1", "")


@pytest.mark.parametrize("name", sorted(RETIRED_DATALAKE_TOOL_NAMES))
def test_retired_config_and_pending_tool_calls_never_invoke_upstream(name):
    with pytest.raises(ValidationError, match="Retired DataLake"):
        ToolConfig(name=name, group="knowledge", use=f"deerflow.community.appforge.tools:{name}_tool")
    with pytest.raises(ValidationError, match="Retired DataLake"):
        ToolConfig(name="renamed", group="knowledge", use=f"deerflow.community.appforge:{name}_tool")
    with patch.object(tools, "get_app_config", side_effect=AssertionError("must not read config")):
        assert json.loads(tools._invoke_tool(name, "old", None))["code"] == "datalake_tool_migrated"
    # Exercise a real ToolNode with an unknown, retired pending call, not merely
    # a direct mock wrapper invocation. No retired tool is registered.
    middleware = ToolErrorHandlingMiddleware()
    node = ToolNode([], wrap_tool_call=middleware.wrap_tool_call, awrap_tool_call=middleware.awrap_tool_call)
    result = tool_graph(node).invoke({"messages": [AIMessage(content="", tool_calls=[{"name": name, "id": "saved-call", "args": {"query": "old"}}])]})
    message = result["messages"][-1]
    assert message.tool_call_id == "saved-call"
    assert message.status == "error"
    assert json.loads(message.content)["code"] == "datalake_tool_migrated"


@pytest.mark.asyncio
async def test_pending_tool_call_async_migration():
    middleware = ToolErrorHandlingMiddleware()
    node = ToolNode([], awrap_tool_call=middleware.awrap_tool_call)
    result = await tool_graph(node).ainvoke({"messages": [AIMessage(content="", tool_calls=[{"name": "datalake_detail_qa", "id": "saved", "args": {"query": "old"}}])]})
    assert json.loads(result["messages"][-1].content)["code"] == "datalake_tool_migrated"


def test_new_registration_requires_matching_name_and_export():
    ToolConfig(name="datalake_data_qa", group="knowledge", use=DATA_QA_USE)
    for name, use in [("datalake_data_qa", "wrong:tool"), ("wrong", DATA_QA_USE)]:
        with pytest.raises(ValidationError, match="matching"):
            ToolConfig(name=name, group="knowledge", use=use)


def test_retired_tools_filtered_even_when_enabled():
    from deerflow.tools.tools import filter_tools_by_datalake_scope

    values = [SimpleNamespace(name=n) for n in [*RETIRED_DATALAKE_TOOL_NAMES, "datalake_data_qa", "datalake_smart_qa"]]
    assert {v.name for v in filter_tools_by_datalake_scope(values, enabled=True)} == {"datalake_data_qa", "datalake_smart_qa"}
    assert filter_tools_by_datalake_scope(values, enabled=False) == []


def test_final_full_answer_does_not_duplicate_deltas():
    result = collect(stream({"type": "answer", "content": "结果"}, {"event": "workflow_finished", "data": {"status": "success", "outputs": {"output": "结果"}}}))
    assert result["answer"] == "结果"
    assert result["status"] == "done"


def test_field_observed_workflow_shape_uses_final_answer_and_usage():
    """2026-10-21 canary shape; all business text below is synthetic.

    The receipt redacted the first event and non-end node types. Keep those
    unknown instead of inventing protocol names or treating node success as
    workflow completion. Five intermediate and seven end-node answer events
    preceded workflow_finished in the observed response.
    """

    def event(kind, data, usage=None):
        return {"event": kind, "data": data, "xx_ceai_extra_data": usage or {"inputToken": 0, "outputToken": 0, "totalToken": 0, "status": "<unrecognized>"}}

    events = [event("<unrecognized>", {"id": "synthetic-id", "error": "", "status": "succeeded"})]
    events += [event("answer", {"id": "synthetic-id", "node_type": "<unrecognized>", "text": "合成进度"}) for _ in range(5)]
    events += [event("answer", {"id": "synthetic-id", "node_type": "end", "text": "合成片段"}) for _ in range(7)]
    with pytest.raises(DataQAStreamError, match="incomplete_workflow"):
        collect(stream(*events))
    events.append(event("workflow_finished", {"id": "synthetic-id", "error": "", "status": "succeeded", "outputs": {"output": "合成最终答案"}}, {"inputToken": 46014, "outputToken": 1135, "totalToken": 47149, "status": "success"}))
    result = collect(stream(*events))
    assert result["answer"] == "合成最终答案"
    assert result["status"] == "done"
    assert result["usage"] == {"inputToken": 46014, "outputToken": 1135, "totalToken": 47149}
    assert result["thinking"] == []
    assert not result["truncated"]


@pytest.mark.parametrize(
    "events,code",
    [
        ([{"type": "answer", "content": "partial"}], "incomplete_workflow"),
        ([{"event": "workflow_finished", "data": {"status": "failed", "error": "private"}}], "upstream_failed"),
        ([{"type": "done", "xx_ceai_extra_data": {"status": "running"}}], "unrecognized_terminal_status"),
        ([{"type": "done"}, {"type": "error", "content": "private"}], "upstream_failed"),
    ],
)
def test_failed_or_partial_http_200_is_not_success(events, code):
    with pytest.raises(DataQAStreamError, match=code):
        collect(stream(*events))


@pytest.mark.parametrize(
    "body,code",
    [
        (b"data: [DONE]\n\n", "incomplete_workflow"),
        (b"data: not-json\n\n", "invalid_sse_event"),
        (b'data: {"type":"done"}', "incomplete_sse_frame"),
        (b"data: \xff\n\n", "invalid_sse_event"),
        (b"data: " + b"x" * 70000, "frame_limit"),
    ],
)
def test_malformed_sse_is_bounded_and_diagnostic(body, code):
    with pytest.raises(DataQAStreamError, match=code):
        collect(httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=body))


def test_sse_multiline_heartbeat_and_event_field():
    body = b': keepalive\r\n\r\nevent: answer\r\ndata: {"data":\r\ndata: {"node_type":"end","text":"ok"}}\r\n\r\ndata: {"type":"done"}\r\n\r\n'
    result = collect(httpx.Response(200, headers={"Content-Type": "text/event-stream; charset=utf-8"}, content=body))
    assert result["answer"] == "ok"


def test_interrupt_and_empty_are_distinct():
    assert collect(stream({"type": "done"}))["status"] == "empty"
    result = collect(stream({"event": "workflow_interrupt", "data": {"outputs": {"choices": ["A", "B"]}}}, {"type": "answer", "content": "must not select"}))
    assert result["status"] == "interrupted"
    assert not result["answer"]
    assert result["interrupt"]["choices"] == ["A", "B"]


def test_escaped_answer_budget_does_not_pop_empty_knowledge():
    result = collect(stream({"type": "answer", "content": "\x01" * 6000}, {"type": "done"}))
    raw = tools._safe_json(result)
    assert len(raw) <= 10000
    assert json.loads(raw)["truncated"]


def test_knowledge_truncation_is_reported():
    result = collect(stream({"type": "knowledge", "content": [{"content": "x"}] * 8}, {"type": "done"}))
    assert result["truncated"]
    assert len(result["knowledge"]) == 5
