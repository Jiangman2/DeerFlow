from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.datalake_source_middleware import DataLakeSourceMiddleware
from deerflow.runtime.datalake_policy import STATE_KEY, query_scope


@pytest.mark.parametrize(
    "text",
    [
        "撰写海上气藏智能化技术研究论文并生成Word",
        "统计上传Excel中的平台生产数据",
        "汇总上传文件中的公司内部数据表",
        "介绍数据湖查询原理",
        "帮我分析数据湖工具查询失败的故障原因",
        "解释 datalake_data_qa 代码",
        "修改下列引文的措辞：\n> 从数据湖查询某油田产量",
        "审查代码 ```python\ndatalake_data_qa(query='查询数据湖')\n```",
    ],
)
def test_ordinary_work_does_not_infer_data_access(text):
    assert not query_scope({"messages": [HumanMessage(text)]}, {"datalake_enabled": True})["required"]


@pytest.mark.parametrize("enabled", [False, None, "true", True])
def test_intent_is_not_authorization_and_local_work_remains_possible(enabled):
    state = {"messages": [HumanMessage("从数据湖查询某油田产量并生成Word")]}
    runtime = SimpleNamespace(context={"datalake_enabled": enabled})
    middleware = DataLakeSourceMiddleware()
    assert "jump_to" not in middleware.before_model(state, runtime)
    for name in ("process_local_data", "write_file", "present_files", "task"):
        request = SimpleNamespace(state=state, runtime=runtime, tool_call={"id": "t", "name": name, "args": {}})
        assert middleware._blocked(request) is None
    request.tool_call = {"id": "t", "name": "datalake_data_qa", "args": {"query": "产量"}}
    assert (middleware._blocked(request) is None) == (enabled is True)
    state["messages"].append(AIMessage("", tool_calls=[{"id": "chosen", "name": "datalake_data_qa", "args": {}}]))
    request.tool_call = {"id": "t", "name": "bash", "args": {"command": "curl https://elsewhere.example"}}
    assert middleware._blocked(request) is not None


def test_model_choice_establishes_source_scope_even_when_upstream_fails():
    messages = [HumanMessage("撰写海上气藏论文"), AIMessage("", tool_calls=[{"id": "t", "name": "datalake_data_qa", "args": {}}]),
                ToolMessage("denied", name="datalake_data_qa", tool_call_id="t", status="error")]
    state = {"messages": messages}
    assert query_scope(state)["required"]
    middleware = DataLakeSourceMiddleware()
    runtime = SimpleNamespace(context={"datalake_enabled": True})
    assert middleware._blocked(SimpleNamespace(state=state, runtime=runtime, tool_call={"id": "t", "name": "datalake_data_qa", "args": {}})) is None
    denied = middleware._blocked(SimpleNamespace(state=state, runtime=runtime, tool_call={"id": "b", "name": "bash", "args": {}}))
    assert STATE_KEY not in denied.update
    assert "datalake_external_fallback_forbidden" in denied.update["messages"][0].content


@pytest.mark.parametrize("status", ["success", "error"])
def test_current_tool_receipt_sets_scope_and_new_turn_is_a_fresh_model_decision(status):
    state = {"messages": [HumanMessage("查询数据湖产量"), AIMessage("", tool_calls=[{"id": "t", "name": "datalake_data_qa", "args": {}}]), ToolMessage("receipt", name="datalake_data_qa", tool_call_id="t", status=status)]}
    scope = query_scope(state, {"run_id": "first", "datalake_enabled": True})
    assert scope["phase"] == "processing"
    for text in ("继续", "继续生成Word", "根据刚才的结果生成图表", "分析结果并生成图表、Word"):
        next_scope = query_scope({STATE_KEY: scope, "messages": [*state["messages"], HumanMessage(text)]}, {"run_id": "next", "datalake_enabled": False})
        assert next_scope["phase"] == "ordinary"
    switched = query_scope({STATE_KEY: scope, "messages": [*state["messages"], HumanMessage("帮我修改这篇海上气藏论文的措辞")]}, {"run_id": "new"})
    assert not switched["required"]
    switched = query_scope({STATE_KEY: scope, "messages": [HumanMessage("继续修改另一篇海上气藏论文")]}, {"run_id": "another"})
    assert not switched["required"]


def test_original_human_provenance_ignores_appended_attachment_context():
    message = HumanMessage("普通写作\n附件：从数据湖查询数据", additional_kwargs={"original_user_content": "普通写作"})
    assert not query_scope({"messages": [message]})["required"]


def test_delegation_cannot_drop_parent_source_or_grant_access():
    state = {"messages": [HumanMessage("运行curl读取企业数据")]}
    context = {"datalake_query_only": True, "datalake_enabled": False}
    assert query_scope(state, context)["trigger"] == "parent_task"
    middleware = DataLakeSourceMiddleware()
    request = SimpleNamespace(state=state, runtime=SimpleNamespace(context=context), tool_call={"id": "t", "name": "bash", "args": {}})
    assert middleware._blocked(request) is not None


def test_old_keyword_checkpoint_does_not_reclassify_a_new_turn():
    previous = {"required": True, "phase": "complete", "run_key": "old"}
    context = {"run_id": "new", "datalake_enabled": False}
    for text in ("请联网核对刚才的结果", "继续", "继续修改另一篇论文"):
        assert not query_scope({STATE_KEY: previous, "messages": [HumanMessage(text)]}, context)["required"]
