"""Completed task -> deterministic checklist -> durable ledger contract."""

import asyncio
import importlib
from copy import deepcopy
from enum import Enum
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from test_acceptance_runtime import evidence, runtime
from test_acceptance_runtime import provider as provider

from deerflow.agents.middlewares.delegation_ledger import extract_delegations, render_delegation_ledger
from deerflow.subagents.acceptance_checks import check_acceptance_criteria
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.status_contract import SUBAGENT_ACCEPTANCE_VERDICT_KEY, make_subagent_additional_kwargs, read_subagent_result_metadata


def verdict():
    return check_acceptance_criteria(["tests_passed:pytest tests/", "review design"], bash_executions=evidence())


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "timed_out", "polling_timed_out"])
def test_structured_round_trip_is_copied_and_completed_only(status):
    original = verdict()
    payload = make_subagent_additional_kwargs(status, result="report", acceptance_verdict=original)
    decoded = read_subagent_result_metadata(payload)
    if status == "completed":
        assert decoded["acceptance_verdict"] == original
        decoded["acceptance_verdict"]["leaves"][0]["detail"] = "changed"
        assert payload[SUBAGENT_ACCEPTANCE_VERDICT_KEY] == original
        original["unchecked"].clear()
        assert payload[SUBAGENT_ACCEPTANCE_VERDICT_KEY]["unchecked"] == ["review design"]
    else:
        assert SUBAGENT_ACCEPTANCE_VERDICT_KEY not in payload
        assert "acceptance_verdict" not in decoded
        payload[SUBAGENT_ACCEPTANCE_VERDICT_KEY] = original
        assert "acceptance_verdict" not in read_subagent_result_metadata(payload)


@pytest.mark.parametrize("ending", ["completed", "failed", "cancelled", "timed_out", "capped"])
@pytest.mark.parametrize("kind", ["file", "test", "mixed", "unknown", "empty"])
def test_actual_task_checks_only_completed_results(monkeypatch, provider, disabled_sandbox_authorization, tmp_path, ending, kind):
    module = importlib.import_module("deerflow.tools.builtins.task_tool")
    statuses = Enum("Status", {name.upper(): name for name in ("completed", "failed", "cancelled", "timed_out")})
    monkeypatch.setattr(module, "SubagentStatus", statuses)
    terminal = "completed" if ending == "capped" else ending
    captured = {}
    result = SimpleNamespace(
        status=getattr(statuses, terminal.upper()),
        ai_messages=[],
        result="Child claims everything succeeded",
        error="child failed",
        stop_reason="turn_capped" if ending == "capped" else None,
        token_usage_records=[],
        usage_reported=True,
        tool_receipts=None,
        bash_executions=evidence(),
    )
    monkeypatch.setattr(module, "SubagentExecutor", type("Executor", (), {"__init__": lambda self, **kwargs: captured.update(kwargs), "execute_async": lambda self, prompt, task_id=None: task_id}))
    monkeypatch.setattr(module, "get_subagent_config", lambda _: SubagentConfig(name="general-purpose", description="test", system_prompt="test", timeout_seconds=10))
    monkeypatch.setattr(module, "get_background_task_result", lambda _: result)
    events, cleaned = [], []
    monkeypatch.setattr(module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(module, "cleanup_background_task", cleaned.append)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    parent = runtime(tmp_path)
    parent.config["metadata"] = {"model_name": "test-model"}
    criteria = {"file": ["file:report.md non-empty"], "test": ["tests_passed:pytest tests/"], "mixed": ["file:missing.md exists", "tests_passed:pytest tests/", "review design"], "unknown": ["review design"], "empty": []}[kind]
    if kind == "mixed":
        provider.get.return_value.execute_command.return_value = "NOFILE"
    before = deepcopy(parent)
    response = asyncio.run(module.task_tool.coroutine(runtime=parent, description="check task", prompt="work", subagent_type="general-purpose", tool_call_id="parent-task", acceptance_criteria=criteria))
    assert parent == before
    assert captured["acceptance_criteria"] == criteria
    message = response.update["messages"][0]
    metadata = read_subagent_result_metadata(message.additional_kwargs)
    assert metadata["status"] == terminal
    if ending == "capped":
        assert metadata["stop_reason"] == "turn_capped"
    has_checks = terminal == "completed" and kind != "empty"
    assert ("Acceptance checklist" in message.content) is has_checks
    assert ("acceptance_verdict" in metadata) is has_checks
    assert cleaned == ["parent-task"]
    if terminal == "completed" and kind in ("file", "mixed"):
        provider.acquire_async.assert_awaited_once()
        provider.release.assert_called_once_with("shared")
    else:
        provider.acquire_async.assert_not_awaited()
    if has_checks:
        checklist = metadata["acceptance_verdict"]
        assert checklist["all_hold"] is (kind in ("file", "test"))
        if kind == "mixed":
            assert [(leaf["checked"], leaf["holds"]) for leaf in checklist["leaves"]] == [(True, False), (True, True), (False, False)]
            assert "does not hold" in message.content and "UNVERIFIED" in message.content
    assert events[-1]["type"] == "task_" + terminal
    assert events[-1]["thread_id"] == "business-thread"


def test_task_cancellation_during_check_does_not_publish_completed(monkeypatch):
    module = importlib.import_module("deerflow.tools.builtins.task_tool")
    statuses = Enum("Status", {name.upper(): name for name in ("completed", "failed", "cancelled", "timed_out")})
    monkeypatch.setattr(module, "SubagentStatus", statuses)
    result = SimpleNamespace(status=statuses.COMPLETED, ai_messages=[], result="report", stop_reason=None, token_usage_records=[], usage_reported=True)
    monkeypatch.setattr(module, "SubagentExecutor", type("Executor", (), {"__init__": lambda self, **kwargs: None, "execute_async": lambda self, prompt, task_id=None: task_id}))
    monkeypatch.setattr(module, "get_subagent_config", lambda _: SubagentConfig(name="general-purpose", description="test", system_prompt="test", timeout_seconds=10))
    monkeypatch.setattr(module, "get_background_task_result", lambda _: result)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    events, cleaned, cancelled = [], [], []
    monkeypatch.setattr(module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(module, "cleanup_background_task", cleaned.append)
    monkeypatch.setattr(module, "request_cancel_background_task", cancelled.append)

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(module, "check_task_acceptance", cancel)
    parent = SimpleNamespace(context={"thread_id": "own"}, state={}, config={"metadata": {"model_name": "test"}})
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(module.task_tool.coroutine(runtime=parent, description="work", prompt="work", subagent_type="general-purpose", tool_call_id="task", acceptance_criteria=["review design"]))
    assert [event["type"] for event in events] == ["task_started"]
    assert cleaned == cancelled == ["task"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_checklist_survives_bounded_summary_but_never_promotes_bad_metadata(corrupt):
    module = importlib.import_module("deerflow.tools.builtins.task_tool")
    value = verdict()
    if corrupt:
        value["all_hold"] = True
    message = module._task_result_command(tool_call_id="task", status="completed", result="claimed completion " * 500, acceptance_verdict=value).update["messages"][0]
    call = AIMessage(content="", tool_calls=[{"name": "task", "id": "task", "args": {"description": "work", "subagent_type": "general-purpose"}}])
    entries = extract_delegations([call, message])
    assert len(message.additional_kwargs["subagent_result_brief"]) <= 2000
    rendered = render_delegation_ledger(entries)
    assert ("acceptance: 1 hold, 1 UNVERIFIED" in rendered) is (not corrupt)
    assert ("Acceptance checklist" in message.content) is (not corrupt)
    if not corrupt:
        assert "does not validate claim correctness" in rendered
        entries[0]["acceptance_verdict"]["all_hold"] = True
        assert "acceptance:" not in render_delegation_ledger(entries)
