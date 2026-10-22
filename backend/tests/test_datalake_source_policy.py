from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from deerflow.agents.middlewares.datalake_source_middleware import DataLakeSourceMiddleware
from deerflow.runtime.datalake_policy import ACCESS_HINT, ALLOWED_TOOLS, STATE_KEY, explicit_data_question, query_scope


def runtime(enabled=False, run="run-1", **context):
    return SimpleNamespace(context={"datalake_enabled": enabled, "run_id": run, **context})


def state(text="查询作业井基本信息表中设计井深最大的2口井"):
    return {"messages": [HumanMessage(content=text)]}


@pytest.mark.parametrize(
    "question",
    [
        "查询作业井基本信息表中设计井深最大的2口井",
        "用数据湖查询今年的产量",
        "从数据湖查找某井井史",
        "调用 datalake_smart_qa 查报告",
        "检索企业内部数据表",
        "数据湖里今年的产量是多少？",
        "数据湖里有哪些作业井？",
        "作业井基本信息表中设计井深最大的2口井是哪些？",
    ],
)
def test_explicit_enterprise_data_question(question):
    assert explicit_data_question(question)


@pytest.mark.parametrize("question", ["什么是数据湖？", "数据湖是什么", "查询北京天气", "搜索公开的海油年报", "比较数据湖与数据仓库的架构"])
def test_general_explanations_and_public_information_are_not_permission_gated(question):
    assert not explicit_data_question(question)


def test_unavailable_access_returns_button_hint_before_model():
    middleware = DataLakeSourceMiddleware()
    update = middleware.before_model(state(), runtime())
    assert update["jump_to"] == "end"
    assert update["messages"][0].content == ACCESS_HINT
    assert middleware.before_model(state(), runtime(enabled=True)).get("jump_to") is None


@pytest.mark.parametrize("name", ["web_search", "web_fetch", "browser_navigate", "bash", "python", "external_mcp_search", "tool_search"])
def test_source_restriction_blocks_all_unapproved_tool_paths(name):
    request = SimpleNamespace(state=state(), runtime=runtime(enabled=True), tool_call={"id": "call", "name": name, "args": {"query": "内部井深"}})
    handler = MagicMock(side_effect=AssertionError("external tool must not run"))
    result = DataLakeSourceMiddleware().wrap_tool_call(request, handler)
    assert result.update[STATE_KEY]["required"] is True
    assert "datalake_external_fallback_forbidden" in result.update["messages"][0].content
    handler.assert_not_called()


@pytest.mark.parametrize("result_text", ['{"status":"empty"}', '{"status":"failed"}', '{"code":"http_403"}', '{"code":"datalake_disabled"}', '{"answer":"命中"}'])
def test_attempted_data_tool_locks_scope_even_if_initial_wording_was_ambiguous(result_text):
    s = state("帮我查一下这些井的数据")
    s["messages"] += [AIMessage(content="", tool_calls=[{"name": "datalake_data_qa", "id": "d1", "args": {"query": "问题"}}]), ToolMessage(content=result_text, name="datalake_data_qa", tool_call_id="d1")]
    assert query_scope(s, runtime(True).context)["required"]
    # An external call in the SAME model batch cannot race the Data QA call.
    s["messages"] = s["messages"][:2]
    request = SimpleNamespace(state=s, runtime=runtime(True), tool_call={"id": "s1", "name": "web_search", "args": {"query": "问题"}})
    assert DataLakeSourceMiddleware()._blocked(request) is not None


def test_checkpoint_survives_compaction_and_new_public_question_resets_scope():
    saved = {STATE_KEY: {"run_key": "run-1", "required": True}, "messages": [HumanMessage(content="摘要", name="summary")]}
    assert query_scope(saved, runtime(True).context)["required"]
    saved["messages"].append(HumanMessage(content="继续"))
    assert query_scope(saved, runtime(True, run="run-2").context)["required"]
    saved["messages"].append(HumanMessage(content="查询北京天气"))
    assert not query_scope(saved, runtime(True, run="run-3").context)["required"]


def test_delegate_cannot_clear_inherited_restriction_with_different_wording():
    assert query_scope(state("请用公开搜索查一下"), runtime(True, datalake_query_only=True).context)["required"]


def test_model_schema_filter_and_guidance_apply_without_changing_other_requests():
    middleware = DataLakeSourceMiddleware()
    tools = [SimpleNamespace(name=n) for n in ("datalake_data_qa", "datalake_smart_qa", "web_search", "bash", "task")]
    request = SimpleNamespace(state=state(), runtime=runtime(True), system_message=None, tools=tools, override=lambda **kw: kw)
    output = middleware._prepare(request)
    assert {t.name for t in output["tools"]} == {"datalake_data_qa", "datalake_smart_qa", "task"}
    assert "不能改用外部搜索" in str(output["system_message"].content)
    request.state = state("查询天气")
    assert middleware._prepare(request)["tools"] == tools


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_real_agent_unauthorized_query_never_calls_model_or_search():
    @tool
    def web_search(query: str):
        """Search public websites."""
        raise AssertionError("must not run")

    model = Model(responses=[])
    agent = create_agent(model=model, tools=[web_search], middleware=[DataLakeSourceMiddleware()])
    result = agent.invoke(state(), context={"datalake_enabled": False, "run_id": "run-1"})
    assert result["messages"][-1].content == ACCESS_HINT


@pytest.mark.asyncio
async def test_real_async_agent_blocks_search_after_empty_data_result():
    calls = []

    @tool
    def datalake_data_qa(query: str):
        """Query enterprise data."""
        calls.append("data")
        return '{"status":"empty","answer":""}'

    @tool
    def web_search(query: str):
        """Search public websites."""
        calls.append("external")
        return "must not run"

    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "datalake_data_qa", "id": "d1", "args": {"query": "井数据"}}]),
            AIMessage(content="", tool_calls=[{"name": "web_search", "id": "s1", "args": {"query": "井数据"}}]),
            AIMessage(content="数据湖未返回结果，请补充查询条件。"),
        ]
    )
    agent = create_agent(model=model, tools=[datalake_data_qa, web_search], middleware=[DataLakeSourceMiddleware()])
    result = await agent.ainvoke(state("帮我查一下这些井的数据"), context={"datalake_enabled": True, "run_id": "run-1"})
    assert calls == ["data"]
    assert result[STATE_KEY]["required"]
    assert any(isinstance(m, ToolMessage) and "datalake_external_fallback_forbidden" in m.content for m in result["messages"])


def test_allowed_local_tools_include_no_arbitrary_network_execution():
    assert not {"bash", "python", "browser_navigate", "web_search", "web_fetch", "tool_search"} & ALLOWED_TOOLS


@pytest.mark.asyncio
async def test_parallel_external_calls_are_blocked_without_checkpoint_update_conflict():
    @tool
    def web_search(query: str):
        """Search public websites."""
        raise AssertionError("must not run")

    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "web_search", "id": ident, "args": {"query": "井数据"}} for ident in ("one", "two")]),
            AIMessage(content="请补充数据湖查询条件。"),
        ]
    )
    agent = create_agent(model=model, tools=[web_search], middleware=[DataLakeSourceMiddleware()])
    result = await agent.ainvoke(state(), context={"datalake_enabled": True, "run_id": "run-1"})
    assert result[STATE_KEY]["required"]
    assert sum(isinstance(m, ToolMessage) and m.status == "error" for m in result["messages"]) == 2
