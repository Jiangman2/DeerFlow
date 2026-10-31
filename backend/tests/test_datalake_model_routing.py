"""Model choice, not wording, controls authorized DataLake tool eligibility."""
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.agents.middlewares.datalake_source_middleware import DataLakeSourceMiddleware
from deerflow.runtime.datalake_policy import STATE_KEY, query_scope

TOOLS = {"datalake_data_qa", "datalake_smart_qa"}


def request(text, enabled=True, messages=None):
    return SimpleNamespace(
        state={"messages": messages or [HumanMessage(text, id="user")]},
        runtime=SimpleNamespace(context={"datalake_enabled": enabled, "run_id": "run"}),
        system_message=SystemMessage("original instructions"),
        tools=[{"name": n} for n in sorted(TOOLS | {"bash", "web_search", "process_local_data"})],
        override=lambda **kw: SimpleNamespace(**kw),
        tool_call={"id": "data", "name": "datalake_data_qa", "args": {"query": text}},
    )


@pytest.mark.parametrize("text", ["KL6-1-2井的套管程序是什么", "KL6-1-2井的套管程序是多少", "那北京呢", "相同口径，去年呢", "任意新术语", "介绍气田概念"])
def test_enabled_tools_are_visible_without_keyword_classification(text):
    req = request(text)
    result = DataLakeSourceMiddleware()._prepare(req)
    assert TOOLS <= {t["name"] for t in result.tools}
    assert "bash" in {t["name"] for t in result.tools}
    assert not query_scope(req.state, req.runtime.context)["required"]
    assert DataLakeSourceMiddleware()._blocked(req) is None


@pytest.mark.parametrize("enabled", [False, None, "true", 1])
@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_model_choice_never_grants_authorization(enabled, tool):
    req = request("模型判断要查询", enabled)
    req.tool_call["name"] = tool
    assert not TOOLS & {t["name"] for t in DataLakeSourceMiddleware()._prepare(req).tools}
    denied = DataLakeSourceMiddleware()._blocked(req)
    assert "datalake_access_required" in denied.update["messages"][0].content


def chosen_messages():
    return [HumanMessage("KL6-1-2井的套管程序是什么", id="u"), AIMessage("", tool_calls=[
        {"id": "data", "name": "datalake_data_qa", "args": {"query": "井身结构"}},
        {"id": "shell", "name": "bash", "args": {"command": "curl elsewhere"}},
    ])]


@pytest.mark.parametrize("status", [None, "success", "error"])
def test_model_selection_blocks_external_fallback_including_parallel_batch(status):
    messages = chosen_messages()
    if status:
        messages.append(ToolMessage("fixture", name="datalake_data_qa", tool_call_id="data", status=status))
    req = request("", messages=messages)
    scope = query_scope(req.state, req.runtime.context)
    assert scope["required"]
    req.tool_call = {"id": "shell", "name": "bash", "args": {}}
    assert "datalake_external_fallback_forbidden" in DataLakeSourceMiddleware()._blocked(req).update["messages"][0].content
    req.tool_call["name"] = "process_local_data"
    assert DataLakeSourceMiddleware()._blocked(req) is None


def test_legacy_keyword_scope_does_not_lock_new_model_selection():
    req = request("介绍气田概念")
    req.state[STATE_KEY] = {"required": True, "run_key": "run", "trigger": "current_request"}
    assert not query_scope(req.state, req.runtime.context)["required"]
    assert "bash" in {t["name"] for t in DataLakeSourceMiddleware()._prepare(req).tools}


def test_compaction_retains_current_choice_but_new_human_releases_scope():
    context = {"run_id": "one"}
    scope = query_scope({"messages": chosen_messages()}, context)
    compacted = {STATE_KEY: scope, "messages": [AIMessage("compacted result")]}
    assert query_scope(compacted, context)["required"]
    compacted["messages"].append(HumanMessage("新的任务", id="new-human"))
    assert not query_scope(compacted, context)["required"]
    assert not query_scope({STATE_KEY: scope, "messages": []}, {"run_id": "another"})["required"]


def test_tool_receipt_alone_cannot_create_model_choice():
    state = {"messages": [HumanMessage("普通问题"), ToolMessage("fixture", name="datalake_data_qa", tool_call_id="unknown")]}
    assert not query_scope(state)["required"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_actual_graph_defers_to_model_with_authorization_enforced(asynchronous, enabled):
    from langchain.agents import create_agent
    from langchain_core.tools import tool
    from test_staged_execution import ScriptedModel, call
    calls = []

    @tool
    def datalake_data_qa(query: str) -> str:
        """Return synthetic fixture data."""
        calls.append(query)
        return "synthetic fixture"

    question = "KL6-1-2井的套管程序是什么"
    graph = create_agent(ScriptedModel(responses=[call("datalake_data_qa", "data", query=question), AIMessage("done")]),
                         tools=[datalake_data_qa], context_schema=dict, middleware=[DataLakeSourceMiddleware()])
    args = {"messages": [HumanMessage(question)]}
    kw = {"context": {"datalake_enabled": enabled, "run_id": "test"}}
    result = await graph.ainvoke(args, **kw) if asynchronous else graph.invoke(args, **kw)
    assert calls == ([question] if enabled else [])
    if not enabled:
        assert any("datalake_access_required" in str(m.content) for m in result["messages"] if isinstance(m, ToolMessage))
