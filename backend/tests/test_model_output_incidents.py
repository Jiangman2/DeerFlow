"""Regression cases for the October 14 malformed/repeating model incidents."""

import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from deerflow.agents.middlewares.artifact_delivery_middleware import ArtifactDeliveryMiddleware
from deerflow.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware
from deerflow.agents.middlewares.todo_middleware import TodoMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.models.output_safety import ModelOutputSafetyError


def runtime():
    return SimpleNamespace(context={"thread_id": "incident-thread", "run_id": "incident-run"})


def post_tool(message):
    return {"messages": [HumanMessage(content="请修改文档"), ToolMessage(content="OK", tool_call_id="edited", name="str_replace"), message]}


@pytest.mark.parametrize(
    "content",
    [
        "已为您更新发言稿。\n\n各位同志：作为岗位的一员，我将以严实作风做好工作。\n我的发言完毕，谢谢大家。",
        "以下是完整发言稿：\n\n我将抓好设备管理。\n\n我的发言完毕，谢谢大家！",
        'The document says: "I will improve safety." This is the revised paragraph.',
        '```python\n# Let me explain the example\nprint("I will")\n```',
        [{"type": "text", "text": "修改内容如下：正文引用“我将抓好管理”。"}, {"type": "reasoning", "text": "Let me continue"}],
    ],
    ids=["speech", "full-draft", "quotation", "code", "text-blocks"],
)
def test_delivered_prose_is_not_retried_for_embedded_future_tense(content):
    assert TerminalResponseMiddleware().after_model(post_tool(AIMessage(content=content)), runtime()) is None


def test_genuine_short_plan_still_gets_one_continuation():
    mw = TerminalResponseMiddleware()
    state = post_tool(AIMessage(content="让我继续修改文件。"))
    assert mw.after_model(state, runtime())["jump_to"] == "model"
    assert mw.after_model(state, runtime())["messages"][0].additional_kwargs["deerflow_error_fallback"]


@pytest.mark.parametrize("text,reason", [("", "reasoning_output_limit"), ("让我搜索更多资料。" * 1000, "repetitive_output"), ("有用但未完成的回答", "output_length_limit")], ids=["hidden", "repeating", "partial"])
def test_length_exhaustion_never_replays_another_full_budget(text, reason):
    message = AIMessage(
        id="limited",
        content=text,
        response_metadata={"finish_reason": "length"},
        additional_kwargs={"reasoning_content": "private reasoning" if not text else ""},
    )
    mw = TerminalResponseMiddleware()
    result = mw.after_model(post_tool(message), runtime())
    assert result and result.get("jump_to") != "model"
    final = result["messages"][0]
    assert final.id == message.id
    assert final.additional_kwargs["error_reason"] == reason
    assert final.additional_kwargs["deerflow_terminal_stop"] is True
    assert not final.additional_kwargs.get("reasoning_content")
    assert not mw._pending_prompts


def test_existing_terminal_stop_is_not_overwritten_by_confirmation_or_plan():
    message = AIMessage(content="让我继续。", additional_kwargs={"deerflow_terminal_stop": True})
    assert TerminalResponseMiddleware().after_model(post_tool(message), runtime()) is None


def test_safety_stop_is_not_reengaged_by_unfinished_todos_or_artifact_links():
    message = AIMessage(content="未完成：[文件](/mnt/user-data/outputs/report.docx)", additional_kwargs={"deerflow_terminal_stop": True})
    state = {**post_tool(message), "todos": [{"content": "deliver file", "status": "in_progress"}]}
    assert TodoMiddleware().after_model(state, runtime()) is None
    assert ArtifactDeliveryMiddleware().after_model(state, runtime()) is None


def test_length_stop_strips_partial_calls_instead_of_executing_them():
    message = AIMessage(content="", response_metadata={"finish_reason": "length"}, tool_calls=[{"id": "partial", "name": "write_file", "args": {"path": "x"}}])
    result = TerminalResponseMiddleware().after_model(post_tool(message), runtime())
    assert result["jump_to"] == "end"
    assert not result["messages"][0].tool_calls


def test_missing_parser_error_gets_safe_extra_data_diagnostic_without_repair():
    args = json.dumps({"old_str": "confidential OLD", "new_str": "confidential NEW"}) + ', "path": "/private/file.md"}'
    message = AIMessage(content="", invalid_tool_calls=[{"id": "bad", "name": "str_replace", "args": args, "error": None}])
    patched = DanglingToolCallMiddleware()._build_patched_messages([HumanMessage(content="edit"), message])
    error = next(item for item in patched if isinstance(item, ToolMessage))
    assert "Extra data" in error.content
    assert "column" in error.content
    assert "one JSON object" in error.content
    assert "confidential" not in error.content and "/private" not in error.content
    assert message.invalid_tool_calls[0]["args"] == args
    assert not patched[1].tool_calls


def test_raw_and_invalid_representations_make_one_diagnostic_receipt():
    args = '{"old_str":"a","new_str":"b"}, "path":"x"}'
    message = AIMessage(content="", additional_kwargs={"tool_calls": [{"id": "bad", "type": "function", "function": {"name": "str_replace", "arguments": args}}]})
    patched = DanglingToolCallMiddleware()._build_patched_messages([HumanMessage(content="edit"), message])
    receipts = [item for item in patched if isinstance(item, ToolMessage)]
    assert len(receipts) == 1
    assert "Extra data" in receipts[0].content


class IncidentModel(BaseChatModel):
    incident: str
    calls: int = 0
    seen_receipts: list[str] = []

    @property
    def _llm_type(self):
        return "local-incident-fixture"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        self.calls += 1
        self.seen_receipts.extend(message.content for message in messages if isinstance(message, ToolMessage))
        if self.incident == "stream_guard":
            raise ModelOutputSafetyError("repetitive_output")
        if self.incident == "invalid":
            message = AIMessage(content="", invalid_tool_calls=[{"id": f"bad-{self.calls}", "name": "str_replace", "args": '{"old_str":"a","new_str":"b"}, "path":"x"}', "error": None}])
        else:
            message = AIMessage(content="让我搜索更多资料。" * 1000, response_metadata={"finish_reason": "length"})
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.mark.parametrize("incident,expected_calls", [("stream_guard", 1), ("length", 1), ("invalid", 2)])
def test_real_graph_stops_with_pending_todos_and_never_executes_bad_calls(incident, expected_calls):
    model = IncidentModel(incident=incident)
    agent = create_agent(
        model,
        middleware=[
            LLMErrorHandlingMiddleware(app_config=AppConfig(sandbox=SandboxConfig(use="test"))),
            DanglingToolCallMiddleware(),
            TodoMiddleware(),
            ArtifactDeliveryMiddleware(),
            TerminalResponseMiddleware(),
        ],
    )
    result = agent.invoke(
        {"messages": [HumanMessage(content="edit")], "todos": [{"content": "deliver", "status": "in_progress"}]},
        context={"thread_id": "incident", "run_id": "one"},
    )
    assert model.calls == expected_calls
    final = result["messages"][-1]
    assert final.additional_kwargs["deerflow_terminal_stop"]
    assert final.additional_kwargs["deerflow_error_fallback"]
    assert not final.tool_calls
    if incident == "invalid":
        assert any("Extra data" in receipt for receipt in model.seen_receipts)
