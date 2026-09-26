"""Parent task -> durable delegation ledger, with no model or network calls."""

import importlib
from enum import Enum
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from deerflow.agents.middlewares.delegation_ledger import extract_delegations, render_delegation_ledger
from deerflow.agents.middlewares.receipt_verification import verify_receipt_citations
from deerflow.agents.middlewares.tool_receipt import TOOL_RECEIPT_KEY, extract_tool_receipts, make_tool_receipt
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.status_contract import (
    SUBAGENT_RECEIPT_VERDICT_KEY,
    SUBAGENT_TOOL_RECEIPTS_KEY,
    make_subagent_additional_kwargs,
    read_subagent_result_metadata,
)


def receipts():
    message = ToolMessage(content="written", name="write_file", tool_call_id="child-write")
    message.additional_kwargs[TOOL_RECEIPT_KEY] = make_tool_receipt({"name": "write_file", "id": "child-write", "args": {}}, message)
    return extract_tool_receipts([message])


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "timed_out"])
def test_wire_round_trip_citations_are_not_terminal_success(status):
    evidence = receipts()
    verdict = verify_receipt_citations("Wrote file [r1 write_file]", evidence)
    payload = make_subagent_additional_kwargs(status, result="report", tool_receipts=evidence, receipt_verdict=verdict)
    restored = read_subagent_result_metadata(payload)
    assert restored["tool_receipts"] == evidence
    assert ("receipt_verdict" in restored) is (status == "completed")
    payload[SUBAGENT_RECEIPT_VERDICT_KEY] = {"citation_resolved": True}
    payload[SUBAGENT_TOOL_RECEIPTS_KEY] = [None, "fake", {}]
    restored = read_subagent_result_metadata(payload)
    assert "receipt_verdict" not in restored
    assert "tool_receipts" not in restored


@pytest.mark.parametrize("ending", ["completed", "failed", "cancelled", "timed_out"])
def test_real_task_transport_preserves_child_evidence(monkeypatch, ending):
    module = importlib.import_module("deerflow.tools.builtins.task_tool")
    status = Enum("TerminalStatus", {name.upper(): name for name in ("completed", "failed", "cancelled", "timed_out")})
    monkeypatch.setattr(module, "SubagentStatus", status)
    evidence = receipts()
    captured = {}
    result = SimpleNamespace(
        status=getattr(status, ending.upper()),
        ai_messages=[],
        result="Created file [r1 write_file]",
        error="failure" if ending != "completed" else None,
        stop_reason=None,
        token_usage_records=[],
        usage_reported=True,
        tool_receipts=evidence,
    )
    monkeypatch.setattr(module, "SubagentExecutor", type("Executor", (), {"__init__": lambda self, **kwargs: captured.update(kwargs), "execute_async": lambda self, prompt, task_id=None: task_id}))
    monkeypatch.setattr(module, "get_subagent_config", lambda _: SubagentConfig(name="general-purpose", description="test", system_prompt="test", timeout_seconds=10))
    monkeypatch.setattr(module, "get_background_task_result", lambda _: result)
    monkeypatch.setattr(module, "get_stream_writer", lambda: lambda _: None)
    monkeypatch.setattr(module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    runtime = SimpleNamespace(state={}, context={"thread_id": "receipt-thread"}, config={"metadata": {"model_name": "test-model"}})
    import asyncio

    command = asyncio.run(module.task_tool.coroutine(runtime=runtime, description="receipt task", prompt="work", subagent_type="general-purpose", tool_call_id="parent-task", acceptance_criteria=[" file:report.md non-empty "]))
    assert captured["acceptance_criteria"] == ["file:report.md non-empty"]
    message = command.update["messages"][0]
    assert message.additional_kwargs[SUBAGENT_TOOL_RECEIPTS_KEY] == evidence
    if ending == "completed":
        assert message.additional_kwargs[SUBAGENT_RECEIPT_VERDICT_KEY]["citation_resolved"] is True
    else:
        assert SUBAGENT_RECEIPT_VERDICT_KEY not in message.additional_kwargs


@pytest.mark.parametrize(
    ("report", "available", "expected"),
    [
        ("Created file [r1 write_file]", True, "1 resolved"),
        ("Created file [r1 bash]", True, "1 failed"),
        ("Created file [r99]", True, "1 unknown"),
        ("文件已生成", True, "UNVERIFIED"),
        ("文件已生成", False, None),
    ],
)
def test_full_report_verification_reaches_bounded_delegation_ledger(report, available, expected):
    module = importlib.import_module("deerflow.tools.builtins.task_tool")
    # Put the claim in the middle, outside the summary's retained head/tail.
    full_report = "context " * 400 + report + " context" * 400
    command = module._task_result_command(tool_call_id="task-1", status="completed", result=full_report, tool_receipts=receipts() if available else None)
    message = command.update["messages"][0]
    assert len(message.additional_kwargs["subagent_result_brief"]) <= 2000
    call = AIMessage(content="delegate", tool_calls=[{"name": "task", "id": "task-1", "args": {"description": "work", "subagent_type": "general-purpose"}}])
    entries = extract_delegations([call, message])
    rendered = render_delegation_ledger(entries)
    assert entries[0]["status"] == "completed"
    if expected:
        assert expected in rendered
        assert "receipt_verdict" in entries[0]
        if expected != "UNVERIFIED":
            assert "does not validate claim correctness" in rendered
    else:
        assert "receipt_verdict" not in entries[0]
        assert "citations:" not in rendered
