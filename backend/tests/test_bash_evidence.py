"""Recorded shell evidence must retain its actual execution/provenance boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Overwrite

from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY
from deerflow.subagents.bash_evidence import _bash_evidence_status, _harvest_shell_persistence, harvest_bash_executions


def _state(command="pytest tests/", output="3 passed", **tool_kwargs):
    return {
        "sandbox": {"sandbox_id": "child-sandbox"},
        "messages": [
            AIMessage(content="", tool_calls=[{"id": "call-1", "name": "bash", "args": {"command": command}}]),
            ToolMessage(content=output, name="bash", tool_call_id="call-1", **tool_kwargs),
        ],
    }


@pytest.mark.parametrize(
    "content,meta,expected",
    [
        ("3 passed", "success", "success"),
        ("3 passed", "error", "error"),
        ("3 passed\nExit Code: 1", "success", "error"),
        ("3 passed\nExit Code: -9", "success", "error"),
        ("3 passed\nExit Code: 0", "success", "success"),
        ("3 passed\nExit Code: 0", "error", "error"),
        ("3 passed\nExit Code: 0", "partial_success", "error"),
        ("Command exited with code 5", "success", "error"),
        ("test phrase Command exited with code 5", "success", "success"),
        ("Exit Code: " + "9" * 5000, "success", "error"),
    ],
)
def test_status_markers_are_bounded_and_cannot_launder_error(content, meta, expected):
    status, marker = _bash_evidence_status(content, meta)
    assert status == expected
    assert marker is None or len(marker) <= 160


@pytest.mark.parametrize("declared", [False, True, None, "false", 0, MagicMock()])
@pytest.mark.parametrize("wrapped", [False, True])
def test_producing_sandbox_uses_scoped_child_identity(monkeypatch, declared, wrapped):
    provider = MagicMock()
    provider.get_scoped.return_value = SimpleNamespace(persistent_shell_sessions=declared)
    monkeypatch.setattr("deerflow.sandbox.sandbox_provider.get_sandbox_provider", lambda: provider)
    state = _state()
    if wrapped:
        state["sandbox"] = Overwrite(state["sandbox"])
    result = harvest_bash_executions(state, thread_id="thread", user_id="alice")
    assert result[0]["shell_persistent"] is (declared if isinstance(declared, bool) else None)
    provider.get_scoped.assert_called_once_with("child-sandbox", thread_id="thread", user_id="alice")
    provider.get.assert_not_called()
    provider.acquire.assert_not_called()


@pytest.mark.parametrize("thread,user", [(None, "alice"), ("thread", None), ("", "alice")])
def test_missing_identity_cannot_lookup_an_unscoped_client(monkeypatch, thread, user):
    provider = MagicMock()
    monkeypatch.setattr("deerflow.sandbox.sandbox_provider.get_sandbox_provider", provider)
    assert _harvest_shell_persistence(_state(), thread_id=thread, user_id=user) is None
    provider.assert_not_called()


def test_foreign_or_absent_client_is_unknown(monkeypatch):
    provider = MagicMock()
    provider.get_scoped.return_value = None
    provider.get.return_value = SimpleNamespace(persistent_shell_sessions=False)
    monkeypatch.setattr("deerflow.sandbox.sandbox_provider.get_sandbox_provider", lambda: provider)
    assert harvest_bash_executions(_state(), thread_id="foreign", user_id="mallory")[0]["shell_persistent"] is None
    provider.get.assert_not_called()


def test_harvest_bounds_and_marks_truncated_command():
    result = harvest_bash_executions(_state("x" * 501, "prefix" + "y" * 2000))
    assert len(result[0]["command"]) == 500
    assert result[0]["command_truncated"] is True
    assert result[0]["output_tail"] == "y" * 1000
    assert result[0]["shell_persistent"] is None


def test_latest_twenty_pairs_only():
    messages = []
    for index in range(30):
        state = _state()
        state["messages"][0].tool_calls[0]["id"] = str(index)
        state["messages"][1].tool_call_id = str(index)
        messages.extend(state["messages"])
    result = harvest_bash_executions({"messages": messages})
    assert [entry["tool_call_id"] for entry in result] == [str(i) for i in range(10, 30)]


@pytest.mark.parametrize("invalid", ["missing_call", "different_id", "different_tool", "missing_command", "empty_id"])
def test_only_matching_real_command_tool_pairs(invalid):
    state = _state()
    if invalid == "missing_call":
        state["messages"].pop(0)
    elif invalid == "different_id":
        state["messages"][1].tool_call_id = "foreign"
    elif invalid == "different_tool":
        state["messages"][1].name = "read_file"
    elif invalid == "missing_command":
        state["messages"][0].tool_calls[0]["args"] = {}
    else:
        state["messages"][0].tool_calls[0]["id"] = ""
    assert harvest_bash_executions(state) == []


def test_malformed_evidence_is_unavailable_without_logging_its_contents(caplog):
    state = _state(additional_kwargs={TOOL_META_KEY: "PRIVATE_PROVIDER_TOKEN"})
    assert harvest_bash_executions(state) is None
    assert "Failed to harvest" in caplog.text
    assert "PRIVATE" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


@pytest.mark.parametrize("state", [None, {}, {"messages": []}])
def test_absent_stream_distinct_from_no_commands(state):
    assert harvest_bash_executions(state) is None
    assert harvest_bash_executions({"messages": [AIMessage(content="analysis")]}) == []
