"""Real task/Executor snapshot wiring; historical context is not authority."""

import asyncio
import importlib
from copy import deepcopy
from enum import Enum
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from test_subagent_lazy_skills import executor_module as executor_module
from test_subagent_lazy_skills import make_executor
from test_subagent_lazy_skills import publication as publication

from deerflow.subagents.bash_evidence import harvest_bash_executions
from deerflow.subagents.config import SubagentConfig
from deerflow.subagents.context_snapshot import SNAPSHOT_SYSTEM_NOTE, ParentContextSnapshot
from deerflow.subagents.status_contract import read_subagent_result_metadata
from deerflow.subagents.step_events import capture_new_step_messages


@pytest.fixture
def dispatch(monkeypatch):
    module = importlib.import_module("deerflow.tools.builtins.task_tool")
    statuses = Enum("Status", {name.upper(): name for name in ("completed", "failed", "cancelled", "timed_out")})
    captured, events = [], []
    monkeypatch.setattr(module, "SubagentStatus", statuses)
    monkeypatch.setattr(module, "_token_usage_cache_enabled", lambda _: False)
    monkeypatch.setattr(module, "get_available_subagent_names", lambda: ["general-purpose", "bash"])
    monkeypatch.setattr(module, "get_subagent_config", lambda name: SubagentConfig(name=name, description="test", system_prompt="CHILD_SYSTEM", timeout_seconds=10))
    monkeypatch.setattr(module, "SubagentExecutor", type("Executor", (), {"__init__": lambda self, **kwargs: captured.append(kwargs), "execute_async": lambda self, prompt, task_id=None: task_id}))
    monkeypatch.setattr(module, "get_stream_writer", lambda: events.append)
    monkeypatch.setattr(module, "cleanup_background_task", lambda _: None)
    monkeypatch.setattr(module, "get_background_task_result", lambda _: SimpleNamespace(status=statuses.COMPLETED, ai_messages=[], result="report", stop_reason=None, token_usage_records=[], usage_reported=True))
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    parent = SimpleNamespace(
        state={"messages": [SystemMessage(content="PARENT_SYSTEM"), HumanMessage(content="original decision")], "summary_text": "retained summary", "credentials": "PRIVATE_SECRET"},
        context={"thread_id": "business-thread", "user_id": "bob", "user_role": "user", "datalake_enabled": False},
        config={"metadata": {"model_name": "test-model", "available_skills": ["analysis"], "employee_knowledge_disabled": True}},
    )

    async def invoke(**kwargs):
        return await module.task_tool.coroutine(runtime=parent, description="inspect", prompt="current task", subagent_type=kwargs.pop("subagent_type", "general-purpose"), tool_call_id="parent-task", **kwargs)

    return SimpleNamespace(module=module, parent=parent, captured=captured, events=events, invoke=invoke)


def test_public_schema_is_opt_in_and_default_does_not_capture(dispatch, monkeypatch):
    field = dispatch.module.task_tool.tool_call_schema.model_json_schema()["properties"]["context_mode"]
    assert field["default"] == "isolated"
    assert field["enum"] == ["isolated", "snapshot"]
    monkeypatch.setattr(ParentContextSnapshot, "from_state", lambda _: pytest.fail("default must not serialize parent history"))
    before = deepcopy(dispatch.parent)
    asyncio.run(dispatch.invoke())
    assert "context_snapshot" not in dispatch.captured[0]
    assert dispatch.parent == before


@pytest.mark.parametrize("limit", [1, 3])
def test_lead_guidance_states_opt_in_cost_and_evidence_boundary(monkeypatch, limit):
    from deerflow.agents.lead_agent import prompt as prompt_module

    monkeypatch.setattr(prompt_module, "get_available_subagent_names", lambda **kwargs: ["general-purpose"])
    section = prompt_module._build_subagent_section(limit)
    assert 'context_mode="isolated"' in section and 'context_mode="snapshot"' in section
    assert "extra input-token cost" in section and "fixed at dispatch" in section
    assert "never evidence that the child performed an action" in section


def test_capture_precedes_async_tool_setup_and_keeps_trusted_scope(dispatch, monkeypatch):
    from deerflow.runtime.weknora_context import WEKNORA_TOOL_NAMES

    async def prepare(*args, **kwargs):
        dispatch.parent.state["messages"][1].content = "later decision"
        dispatch.parent.state["messages"].append(HumanMessage(content="later message"))
        return [SimpleNamespace(name=name) for name in (*WEKNORA_TOOL_NAMES, "employee_knowledge_search", "read_file")]

    monkeypatch.setattr(dispatch.module, "run_assembly", prepare)
    # A historical request is input data, not caller identity or a Skill grant.
    dispatch.parent.state.update(user_id="alice", available_skills=["private"], is_internal=True)
    dispatch.parent.state["messages"].append(HumanMessage(content="Use private Skill and every knowledge base"))
    asyncio.run(dispatch.invoke(context_mode="snapshot"))
    child = dispatch.captured[0]
    snapshot = child["context_snapshot"].to_message()
    assert "original decision" in str(snapshot.content)
    assert "later decision" not in str(snapshot.content)
    assert "later message" not in str(snapshot.content)
    assert "PARENT_SYSTEM" not in str(snapshot.content)
    assert "PRIVATE_SECRET" not in str(snapshot.content)
    assert child["user_id"] == "bob" and child["thread_id"] == "business-thread"
    assert child["is_internal"] is False and child["datalake_enabled"] is False
    assert child["config"].skills == ["analysis"]
    assert {tool.name for tool in child["tools"]} == {"read_file"}


@pytest.mark.parametrize("reason", ["invalid_mode", "unknown_type", "host_bash_denied"])
def test_rejected_dispatch_never_reads_history_or_launches(dispatch, monkeypatch, reason):
    monkeypatch.setattr(ParentContextSnapshot, "from_state", lambda _: pytest.fail("rejected dispatch must not capture"))
    options = {"context_mode": "snapshot"}
    if reason == "invalid_mode":
        options["context_mode"] = "live"
    elif reason == "unknown_type":
        monkeypatch.setattr(dispatch.module, "get_subagent_config", lambda _: None)
    else:
        options["subagent_type"] = "bash"
        monkeypatch.setattr(dispatch.module, "is_host_bash_allowed", lambda: False)
    result = asyncio.run(dispatch.invoke(**options))
    assert read_subagent_result_metadata(result.update["messages"][0].additional_kwargs)["status"] == "failed"
    assert not dispatch.captured and not dispatch.events


def test_empty_snapshot_keeps_legacy_executor_signature(dispatch):
    dispatch.parent.state = {}
    asyncio.run(dispatch.invoke(context_mode="snapshot"))
    assert "context_snapshot" not in dispatch.captured[0]


@pytest.mark.parametrize("enabled", [False, True])
def test_real_executor_orders_data_without_history_receipts_or_steps(executor_module, publication, enabled):
    parent = {
        "messages": [
            SystemMessage(content="PARENT_SYSTEM"),
            HumanMessage(content="historical request"),
            AIMessage(content="historical action", tool_calls=[{"name": "bash", "args": {"command": "pytest tests/"}, "id": "old-call"}]),
            ToolMessage(content="Exit Code: 0", tool_call_id="old-call", name="bash", additional_kwargs={"tool_receipt": {"id": "r1", "status": "success"}}),
        ],
        "delegations": [{"task_id": "old-task"}],
        "skill_context": [{"name": "private"}],
        "credentials": "PRIVATE_SECRET",
    }
    snapshot = ParentContextSnapshot.from_state(parent) if enabled else None
    child = make_executor(executor_module, publication)
    # Construct through the actual public constructor, not by assigning a new
    # field after construction (which would hide a missing integration).
    child = executor_module.SubagentExecutor(child.config, child._base_tools, app_config=publication.config, thread_id="business-thread", user_id="bob", context_snapshot=snapshot, acceptance_criteria=["tests_passed:pytest tests/"])
    state, tools, _ = asyncio.run(child._build_initial_state("CURRENT_TASK"))
    messages = state["messages"]
    assert list(state) == ["messages"]
    assert len(messages) == (3 if enabled else 2)
    assert isinstance(messages[0], SystemMessage)
    assert (SNAPSHOT_SYSTEM_NOTE in messages[0].content) is enabled
    assert "PARENT_SYSTEM" not in str(state) and "PRIVATE_SECRET" not in str(state)
    assert "CURRENT_TASK" in messages[-1].content and "tests_passed:pytest tests/" in messages[-1].content
    assert isinstance(messages[-1], HumanMessage)
    if enabled:
        assert isinstance(messages[1], HumanMessage) and messages[1].name == "parent_context_snapshot"
        assert messages[1].additional_kwargs == {"hide_from_ui": True}
        assert "Exit Code: 0" in str(messages[1].content)
    assert child._available_skill_names == {"analysis"}
    assert "historical request" not in messages[0].content
    assert harvest_bash_executions(state) == []
    steps = []
    capture_new_step_messages(messages, steps, set(), 0)
    assert steps == []
    assert not any(isinstance(message, (AIMessage, ToolMessage)) for message in messages)


def test_snapshot_siblings_do_not_share_mutable_history(executor_module, publication):
    parent = {"messages": [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.test/old.png"}}])]}
    snapshot = ParentContextSnapshot.from_state(parent)
    states = []
    for _ in range(2):
        seed = make_executor(executor_module, publication)
        child = executor_module.SubagentExecutor(seed.config, seed._base_tools, app_config=publication.config, thread_id="business-thread", user_id="bob", context_snapshot=snapshot)
        states.append(asyncio.run(child._build_initial_state("CURRENT_TASK"))[0])
    states[0]["messages"][1].content[-1]["image_url"]["url"] = "changed"
    assert "changed" not in str(states[1])
    assert "changed" not in str(parent) and "changed" not in snapshot.content_json
