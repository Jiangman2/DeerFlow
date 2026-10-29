"""Combined graph tests: orchestration never widens Data Lake access."""

from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from test_staged_execution import STAGES, ScriptedModel, call, config

from deerflow.agents.middlewares.datalake_source_middleware import DataLakeSourceMiddleware
from deerflow.agents.middlewares.staged_execution_middleware import StagedExecutionMiddleware


@pytest.mark.parametrize("authorized", [False, True])
@pytest.mark.parametrize("name", ["start_execution_stages", "advance_execution_stage"])
def test_stage_bookkeeping_is_not_an_external_data_source(name, authorized):
    state = {"messages": [HumanMessage("从数据湖查询产量并生成报告")]}
    runtime = SimpleNamespace(context={"datalake_enabled": authorized})
    guard = DataLakeSourceMiddleware()
    request = SimpleNamespace(state=state, runtime=runtime, tool_call={"name": name, "id": "t", "args": {}})
    assert guard._blocked(request) is None
    request.tool_call["name"] = "bash"
    assert guard._blocked(request) is not None
    request.tool_call["name"] = "datalake_data_qa"
    assert (guard._blocked(request) is None) == authorized


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.asyncio
async def test_actual_combined_graph_preserves_stage_tools_and_completes(async_mode):
    visible_tools = []

    class ObservedModel(ScriptedModel):
        def bind_tools(self, tools, **kwargs):
            visible_tools.append({t.name for t in tools})
            return self

    @tool
    def read_file() -> str:
        """Read a synthetic already-authorized local result."""
        return "synthetic local evidence"

    responses = [
        call("start_execution_stages", "plan", stages=STAGES),
        call("read_file", "r1"),
        call("advance_execution_stage", "next", stage=1, result="checked", evidence_ids=["r1"]),
        call("read_file", "r2"),
        call("advance_execution_stage", "finish", stage=2, result="checked", evidence_ids=["r2"]),
        AIMessage(content="synthetic result"),
    ]
    graph = create_agent(ObservedModel(responses=responses), tools=[read_file], context_schema=dict, middleware=[DataLakeSourceMiddleware(), StagedExecutionMiddleware(config())])
    args = {"messages": [HumanMessage("从数据湖查询产量并整理结果", id="u")]}
    kwargs = {"context": {"run_id": "r1", "thread_id": "synthetic", "datalake_enabled": True}}
    result = await graph.ainvoke(args, **kwargs) if async_mode else graph.invoke(args, **kwargs)
    assert all({"start_execution_stages", "advance_execution_stage"} <= names for names in visible_tools)
    assert result["staged_execution"]["status"] == "completed"
    assert not any(m.status == "error" for m in result["messages"] if isinstance(m, ToolMessage))
    assert result["datalake_query_scope"]["required"]
