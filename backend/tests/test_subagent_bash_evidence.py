"""Real executor lifetime integration; shared cycle-isolation fixtures only."""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from test_subagent_executor import _default_app_config
from test_subagent_executor import _setup_executor_classes as _setup_executor_classes
from test_subagent_executor import base_config as base_config
from test_subagent_executor import classes as classes
from test_subagent_executor import mock_agent as mock_agent

from deerflow.subagents import bash_evidence


@pytest.mark.anyio
@pytest.mark.parametrize("ending", ["complete", "capped", "cancelled", "failed", "timeout", "no_criteria", "receipts_disabled"])
async def test_bash_evidence_survives_compaction_and_terminal_races(classes, base_config, mock_agent, monkeypatch, ending):
    from langgraph.errors import GraphRecursionError

    cfg = _default_app_config()
    cfg.verification = SimpleNamespace(receipts_enabled=ending != "receipts_disabled")
    holder = classes["SubagentResult"](task_id="task", trace_id="trace", status=classes["SubagentStatus"].RUNNING)
    executor = classes["SubagentExecutor"](
        config=base_config,
        tools=[],
        app_config=cfg,
        thread_id="thread",
        user_id="alice",
        acceptance_criteria=None if ending == "no_criteria" else ["tests_passed:pytest tests/"],
    )
    monkeypatch.setattr(bash_evidence, "_harvest_shell_persistence", lambda *args, **kwargs: False)
    call = AIMessage(content="", tool_calls=[{"id": "call-1", "name": "bash", "args": {"command": "pytest tests/"}}])
    tool = ToolMessage(content="3 passed", name="bash", tool_call_id="call-1")
    closed = []

    async def stream(*args, **kwargs):
        try:
            yield {"messages": [call, tool], "sandbox": {"sandbox_id": "child"}}
            if ending == "cancelled":
                holder.cancel_event.set()
            if ending == "failed":
                raise RuntimeError("synthetic failure")
            if ending == "timeout":
                holder.try_set_terminal(classes["SubagentStatus"].TIMED_OUT)
            # The terminal state no longer carries the original command pair.
            yield {"messages": [AIMessage(content="finished")]}
            if ending == "capped":
                raise GraphRecursionError("synthetic cap")
        finally:
            closed.append(True)

    mock_agent.astream = stream
    with patch.object(executor, "_create_agent", return_value=mock_agent):
        result = await executor._aexecute("work", result_holder=holder)
    assert closed == [True]
    if ending == "no_criteria":
        assert result.bash_executions is None
    else:
        assert len(result.bash_executions) == 1
        assert result.bash_executions[0]["command"] == "pytest tests/"
        assert result.bash_executions[0]["shell_persistent"] is False
    expected = {"cancelled": "CANCELLED", "failed": "FAILED", "timeout": "TIMED_OUT"}.get(ending, "COMPLETED")
    assert result.status == getattr(classes["SubagentStatus"], expected)


def test_bounded_merge_is_copied_deduplicated_and_terminal_fenced(classes):
    holder = classes["SubagentResult"](task_id="task", trace_id="trace", status=classes["SubagentStatus"].RUNNING)
    assert holder.snapshot_bash_executions() is None
    holder.update_bash_executions([])
    assert holder.snapshot_bash_executions() == []
    for index in range(25):
        evidence = {"tool_call_id": str(index), "output_tail": "real"}
        holder.update_bash_executions([evidence])
        evidence["output_tail"] = "altered"
    holder.update_bash_executions(None)
    before = holder.snapshot_bash_executions()
    assert [entry["tool_call_id"] for entry in before] == [str(i) for i in range(5, 25)]
    assert all(entry["output_tail"] == "real" for entry in before)
    holder.update_bash_executions([{"tool_call_id": "24", "output_tail": "updated"}])
    assert len(holder.snapshot_bash_executions()) == 20
    assert holder.try_set_terminal(classes["SubagentStatus"].TIMED_OUT)
    holder.update_bash_executions([{"tool_call_id": "late", "output_tail": "fake"}])
    snapshot = holder.snapshot_bash_executions()
    snapshot[0]["output_tail"] = "mutable copy"
    assert holder.snapshot_bash_executions()[0]["output_tail"] == "real"
    assert holder.snapshot_bash_executions()[-1]["tool_call_id"] == "24"
