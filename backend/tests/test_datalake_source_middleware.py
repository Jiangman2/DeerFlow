"""DataLake intent, task ownership and pre-dispatch stopping regressions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from deerflow.agents.middlewares.datalake_source_middleware import SOURCE_STOP_HINT, DataLakeSourceMiddleware
from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware
from deerflow.runtime.datalake_policy import ACCESS_HINT, STATE_KEY, explicit_data_question, merge_query_scope, query_scope


def _runtime(enabled=False, run_id="r1", **context):
    return SimpleNamespace(context={"datalake_enabled": enabled, "run_id": run_id, **context})


def _state(text, previous=None, history=()):
    return {"messages": [*history, HumanMessage(content=text)], STATE_KEY: previous or {}}


@pytest.mark.parametrize(
    "text",
    [
        "什么是数据湖",
        "请解释数据湖的使用方法",
        "如何通过数据湖按钮开启权限？",
        "分析为什么 bash 被误判为数据湖查询",
        "排查数据湖查询失败的原因",
        "查询数据湖接口为什么报错",
        "请分析这句报错：" + ACCESS_HINT,
        "分析这条提示：“请查询数据湖中的生产数据”，为什么会出现？",
        "解释提示 `query data lake records` 的含义",
        "介绍数据湖原理，并查询公开天气",
        "请查询这个报错的原因：" + ACCESS_HINT,
    ],
)
def test_diagnostics_and_product_questions_are_not_data_requests(text):
    assert not explicit_data_question(text)
    assert not query_scope(_state(text), _runtime().context)["required"]


@pytest.mark.parametrize(
    "text",
    [
        "查询作业井基本信息表中设计井深最大的2口井",
        "查询数据湖中的生产数据",
        "数据湖里有哪些井？",
        "请从数据湖统计本月产量",
        "使用 datalake_data_qa 查询井深",
        "query data lake records",
        "分析报错，然后查询数据湖中的生产数据",
        "解释数据湖权限，并查询作业井基本信息表",
        "分析提示：“查询数据湖中的产量”，再查询数据湖中的井深",
        "请查询数据湖中的‘生产数据’",
    ],
)
def test_real_queries_and_mixed_requests_still_require_the_data_source(text):
    assert explicit_data_question(text)


def test_scope_retains_query_owner_and_authorization_is_separate():
    history = [HumanMessage(content="查询数据湖中的产量", id="business-task")]
    scope = query_scope({"messages": history}, _runtime().context)
    assert scope["query_id"] == "business-task"
    assert scope["origin"] == "user_query"
    scope["status"] = "waiting_access"
    for reply in ("继续", "重试", "已开启"):
        resumed = query_scope(_state(reply, scope, history), _runtime(True, "r2").context)
        assert resumed["required"]
        assert resumed["query_id"] == scope["query_id"]
        assert resumed["status"] == "active"
    independent = query_scope(_state("分析上传的文件", scope, history), _runtime(True, "r2").context)
    assert not independent["required"]
    assert "query_id" not in independent


@pytest.mark.parametrize("reply", ["继续", "重试", "已开启"])
def test_legacy_false_positive_does_not_survive_a_continuation(reply):
    legacy = {"run_key": "old", "required": True}
    history = [HumanMessage(content="请分析这句报错：" + ACCESS_HINT)]
    assert not query_scope(_state(reply, legacy, history), _runtime().context)["required"]
    assert not query_scope(_state(reply, legacy), _runtime().context)["required"]
    history = [HumanMessage(content="查询数据湖中的产量", id="original")]
    scope = query_scope(_state(reply, legacy, history), _runtime().context)
    assert scope["required"] and scope["query_id"] == "original"


def test_compacted_task_owner_survives_but_does_not_attach_to_an_unrelated_task():
    previous = {"run_key": "old", "required": True, "query_id": "original", "origin": "user_query", "status": "waiting_access"}
    assert query_scope(_state("继续", previous), _runtime().context)["required"]
    assert not query_scope(_state("继续", previous, [HumanMessage(content="分析上传文件")]), _runtime().context)["required"]


def test_parallel_scope_merge_keeps_refusal_and_owner():
    owner = {"run_key": "r1", "required": True, "query_id": "q1", "origin": "data_tool", "status": "waiting_access"}
    ordinary = {"run_key": "r1", "required": False, "status": "inactive"}
    assert merge_query_scope(owner, ordinary) == merge_query_scope(ordinary, owner) == owner
    assert merge_query_scope(owner, {**ordinary, "run_key": "r2"})["required"] is False


def test_assistant_hint_does_not_lock_an_independent_question():
    state = {"messages": [HumanMessage(content="分析上传文件"), AIMessage(content=ACCESS_HINT)]}
    assert not query_scope(state, _runtime().context)["required"]


@pytest.mark.parametrize("outcome", ["empty", "failed", "403", "success"])
def test_actual_data_tool_attempts_keep_source_after_any_result(outcome):
    state = _state("看看这些井")
    state["messages"] += [AIMessage(content="", tool_calls=[{"id": "q", "name": "datalake_data_qa", "args": {}}]), ToolMessage(content=outcome, name="datalake_data_qa", tool_call_id="q")]
    assert query_scope(state, _runtime(True).context)["required"]
    assert query_scope(state, _runtime(True).context)["origin"] == "data_tool"


def test_legacy_same_run_lock_gains_an_owner_from_a_real_tool_receipt():
    state = _state("看看这些井", {"run_key": "r1", "required": True})
    state["messages"].append(ToolMessage(content="failed", name="datalake_data_qa", tool_call_id="q"))
    scope = query_scope(state, _runtime().context)
    assert scope["query_id"] and scope["origin"] == "data_tool"


def test_delegate_cannot_escape_source_by_rewording_its_prompt():
    assert query_scope(_state("使用公开搜索"), _runtime(True, datalake_query_only=True).context)["required"]


def test_access_hint_is_terminal_wait_and_is_idempotent():
    m = DataLakeSourceMiddleware()
    state = _state("查询数据湖中的产量")
    first = m.before_model(state, _runtime())
    assert first["jump_to"] == "end"
    assert first[STATE_KEY]["status"] == "waiting_access"
    message = first["messages"][0]
    assert message.content == ACCESS_HINT
    assert message.additional_kwargs["deerflow_terminal_stop"]
    assert message.additional_kwargs["deerflow_waiting_for_user"]
    assert message.id == m.before_model(state, _runtime())["messages"][0].id


def test_quoted_search_argument_never_starts_a_lock():
    request = SimpleNamespace(state=_state("分析这个报错"), runtime=_runtime(), tool_call={"id": "q", "name": "web_search", "args": {"query": ACCESS_HINT}})
    handler = Mock(return_value="normal result")
    assert DataLakeSourceMiddleware().wrap_tool_call(request, handler) == "normal result"
    handler.assert_called_once()


@pytest.mark.parametrize("enabled,expected", [(False, "datalake_access_required"), (True, "datalake_external_fallback_forbidden")])
def test_execution_boundary_refusal_stops_before_another_model_call(enabled, expected):
    m = DataLakeSourceMiddleware()
    request = SimpleNamespace(state=_state("查询数据湖中的产量"), runtime=_runtime(enabled), tool_call={"id": "b", "name": "bash", "args": {"command": "ls"}})
    handler = Mock(side_effect=AssertionError("must not execute"))
    blocked = m.wrap_tool_call(request, handler)
    assert json.loads(blocked.update["messages"][0].content)["code"] == expected
    request.state.update(blocked.update)
    stop = m.before_model(request.state, request.runtime)
    assert stop["jump_to"] == "end"
    assert stop["messages"][0].additional_kwargs["deerflow_terminal_stop"]
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_async_diagnostic_tool_executes_and_real_query_is_denied():
    m = DataLakeSourceMiddleware()
    request = SimpleNamespace(state=_state("分析提示：" + ACCESS_HINT), runtime=_runtime(), tool_call={"id": "b", "name": "bash", "args": {"command": "ls"}})
    handler = AsyncMock(return_value="local files")
    assert await m.awrap_tool_call(request, handler) == "local files"
    request.state = _state("查询数据湖中的产量")
    result = await m.awrap_tool_call(request, handler)
    assert result.update[STATE_KEY]["status"] == "waiting_access"
    assert handler.await_count == 1


class _Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.asyncio
async def test_graph_stops_entire_mixed_batch_before_any_side_effect(enabled):
    executions = []

    @tool
    def datalake_data_qa(query: str) -> str:
        """Query enterprise records."""
        executions.append("data")
        return "no results"

    @tool
    def bash(command: str) -> str:
        """Run a command."""
        executions.append("bash")
        return "files"

    model = _Model(
        responses=[
            AIMessage(
                content="I will query and search",
                id="batch",
                tool_calls=[
                    {"id": "q", "name": "datalake_data_qa", "args": {"query": "这些井"}},
                    {"id": "b", "name": "bash", "args": {"command": "curl https://example.com"}},
                ],
            ),
            AIMessage(content="unexpected retry"),
        ]
    )
    # Include the generic continuation middleware: waiting must survive it.
    agent = create_agent(model=model, tools=[datalake_data_qa, bash], middleware=[DataLakeSourceMiddleware(), TerminalResponseMiddleware()])
    result = await agent.ainvoke(_state("看看这些井"), context=_runtime(enabled).context)
    assert executions == []
    assert model.i == 1  # exactly one invocation, no post-error retry
    last = result["messages"][-1]
    assert last.content == (SOURCE_STOP_HINT if enabled else ACCESS_HINT)
    assert last.id == "batch" and not last.tool_calls
    assert last.additional_kwargs["deerflow_waiting_for_user"]


def test_model_preparation_keeps_diagnostic_tools_and_filters_real_queries():
    m = DataLakeSourceMiddleware()
    request = SimpleNamespace(state=_state("分析提示：" + ACCESS_HINT), runtime=_runtime(True), tools=[{"name": "bash"}, {"name": "datalake_data_qa"}], system_message=None)
    request.override = lambda **kwargs: kwargs
    assert m._prepare(request)["tools"] == request.tools
    request.state = _state("查询数据湖中的产量")
    assert m._prepare(request)["tools"] == [{"name": "datalake_data_qa"}]


@pytest.mark.asyncio
async def test_checkpoint_resumes_blocked_query_then_releases_an_independent_local_task():
    executions = []

    @tool
    def datalake_data_qa(query: str) -> str:
        """Query enterprise records."""
        executions.append("data")
        return "verified records"

    @tool
    def bash(command: str) -> str:
        """Process local files."""
        executions.append("local")
        return "local files processed"

    model = _Model(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "first", "name": "datalake_data_qa", "args": {"query": "这些井"}}]),
            AIMessage(content="", tool_calls=[{"id": "resumed", "name": "datalake_data_qa", "args": {"query": "这些井"}}]),
            AIMessage(content="查询完成，结果已返回。"),
            AIMessage(content="", tool_calls=[{"id": "local", "name": "bash", "args": {"command": "ls"}}]),
            AIMessage(content="本地处理完成。"),
        ]
    )
    agent = create_agent(model=model, tools=[datalake_data_qa, bash], middleware=[DataLakeSourceMiddleware(), TerminalResponseMiddleware()], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "query-resume"}}
    first = await agent.ainvoke({"messages": [HumanMessage(content="看看这些井")]}, config, context=_runtime().context)
    assert executions == [] and first[STATE_KEY]["status"] == "waiting_access"
    owner = first[STATE_KEY]["query_id"]
    resumed = await agent.ainvoke({"messages": [HumanMessage(content="已开启")]}, config, context=_runtime(True, "r2").context)
    assert executions == ["data"]
    assert resumed[STATE_KEY]["query_id"] == owner
    independent = await agent.ainvoke({"messages": [HumanMessage(content="处理上传的本地文件")]}, config, context=_runtime(False, "r3").context)
    assert executions == ["data", "local"]
    assert not independent[STATE_KEY]["required"]


@pytest.mark.asyncio
async def test_graph_does_not_execute_an_external_fallback_after_a_failed_query():
    executions = []

    @tool
    def datalake_data_qa(query: str) -> str:
        """Query enterprise records."""
        executions.append("data")
        return "查询失败，无法取得记录"

    @tool
    def web_search(query: str) -> str:
        """Search the web."""
        executions.append("external")
        return "external records"

    model = _Model(
        responses=[
            AIMessage(content="", tool_calls=[{"id": "q", "name": "datalake_data_qa", "args": {"query": "井深"}}]),
            AIMessage(content="", tool_calls=[{"id": "fallback", "name": "web_search", "args": {"query": "井深"}}]),
            AIMessage(content="unexpected retry"),
        ]
    )
    agent = create_agent(model=model, tools=[datalake_data_qa, web_search], middleware=[DataLakeSourceMiddleware(), TerminalResponseMiddleware()])
    result = await agent.ainvoke(_state("查询数据湖中的井深"), context=_runtime(True).context)
    assert executions == ["data"]
    assert result["messages"][-1].content == SOURCE_STOP_HINT
    assert result[STATE_KEY]["status"] == "source_blocked"
