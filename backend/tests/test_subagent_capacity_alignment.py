"""Frozen execution capacity must agree with prompts and actual task guards."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from deerflow.agents.lead_agent import agent as lead
from deerflow.agents.lead_agent import prompt
from deerflow.agents.middlewares.subagent_limit_middleware import SubagentLimitMiddleware
from deerflow.config.app_config import AppConfig
from deerflow.config.subagent_runtime_config import SubagentRuntimeConfig
from deerflow.config.subagents_config import clamp_subagent_concurrency, effective_subagent_concurrency
from deerflow.trace_context import request_trace_context


@pytest.mark.parametrize(
    "requested,capacity,expected",
    [(None, 3, 3), (None, 12, 12), (99, 3, 3), (9, 12, 9), (99, 64, 64), (0, 3, 1), (-9, 3, 1), ("2", 3, 2)],
)
def test_effective_capacity_uses_startup_value(requested, capacity, expected):
    reloaded = SimpleNamespace(subagent_runtime=SubagentRuntimeConfig(max_running=60))
    assert effective_subagent_concurrency(requested, reloaded, execution_capacity=capacity) == expected


def test_legacy_config_defaults_and_explicit_safety_ceiling():
    assert effective_subagent_concurrency(None, object()) == 3
    assert effective_subagent_concurrency(None, SimpleNamespace(subagent_runtime=SubagentRuntimeConfig(max_running=8))) == 8
    assert clamp_subagent_concurrency(100) == 64
    assert clamp_subagent_concurrency(100, execution_capacity=0) == 1


@pytest.fixture
def assembly(monkeypatch, tmp_path):
    app_config = AppConfig(
        models=[{"name": "safe-model", "use": "langchain_openai:ChatOpenAI", "model": "safe-model"}],
        sandbox={"use": "deerflow.sandbox.local:LocalSandboxProvider"},
        subagent_runtime={"max_running": 32},
        summarization={"enabled": False},
        memory={"enabled": False},
        skills={"path": str(tmp_path)},
    )
    monkeypatch.setattr(lead, "get_app_config", lambda: app_config)
    monkeypatch.setattr(lead, "configured_subagent_max_running", lambda: 2)
    monkeypatch.setattr(lead, "_load_enabled_available_skills", lambda *a, **kw: [])
    monkeypatch.setattr(lead, "create_chat_model", lambda **kw: object())
    monkeypatch.setattr(lead, "create_agent", lambda **kw: kw)
    monkeypatch.setattr(lead, "build_tracing_callbacks", lambda: [])
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kw: [])
    monkeypatch.setattr(prompt, "get_available_subagent_names", lambda **kw: ["general-purpose"])
    monkeypatch.setattr(prompt, "get_or_new_skill_storage", lambda **kw: SimpleNamespace(load_skills=lambda **kw: []))
    monkeypatch.setattr(prompt, "get_or_new_user_skill_storage", lambda *a, **kw: SimpleNamespace(load_skills=lambda **kw: []))
    monkeypatch.setattr(prompt, "get_agent_soul", lambda *a, **kw: "")
    with request_trace_context():
        yield app_config


@pytest.mark.parametrize("bootstrap", [False, True])
@pytest.mark.parametrize("requested,expected", [(None, 2), (99, 2), (1, 1)])
def test_real_lead_prompt_and_middleware_use_frozen_limit(assembly, bootstrap, requested, expected):
    result = lead.make_lead_agent({"context": {"subagent_enabled": True, "is_bootstrap": bootstrap, "max_concurrent_subagents": requested}})
    limit = next(m for m in result["middleware"] if isinstance(m, SubagentLimitMiddleware))
    assert limit.max_concurrent == expected
    assert f"MAXIMUM {expected} `task` CALLS PER RESPONSE" in result["system_prompt"]
    # A real after_model guard truncates calls; this is not a prompt-only test.
    calls = [{"name": "task", "id": str(i), "args": {}} for i in range(4)]
    update = limit.after_model({"messages": [AIMessage(content="", tool_calls=calls)]}, None)
    assert len(update["messages"][0].tool_calls) == expected


def test_explicit_parallel_middleware_and_total_run_limit_preserved(assembly):
    from deerflow.agents.middlewares.explicit_parallel_delegation_middleware import ExplicitParallelDelegationMiddleware

    stack = lead.build_middlewares(
        {"context": {"subagent_enabled": True, "is_plan_mode": True, "max_total_subagents": 1}},
        model_name="safe-model",
        app_config=assembly,
        subagent_execution_capacity=8,
    )
    assert any(isinstance(m, ExplicitParallelDelegationMiddleware) for m in stack)
    limit = next(m for m in stack if isinstance(m, SubagentLimitMiddleware))
    assert limit.max_concurrent == 8
    assert limit.max_total == 1


@pytest.mark.parametrize("requested,expected", [(None, 8), (100, 8), (1, 1)])
def test_embedded_context_and_reset_cannot_drift_prompt_from_guard(assembly, monkeypatch, requested, expected):
    import deerflow.client as embedded

    # Use a minimal client without filesystem initialization, but its real
    # assembly path and middleware. A normal constructor sets the same snapshot.
    client = embedded.DeerFlowClient.__new__(embedded.DeerFlowClient)
    client._app_config = assembly
    client._subagent_execution_capacity = 8
    client._agent_name = None
    client._available_skills = None
    client._middlewares = []
    client._agent = None
    client._agent_config_key = None
    client._checkpointer = None
    monkeypatch.setattr(embedded, "create_chat_model", lambda **kw: object())
    monkeypatch.setattr(embedded, "create_agent", lambda **kw: kw)
    monkeypatch.setattr(embedded, "get_enabled_skills_for_config", lambda config: [])
    monkeypatch.setattr(client, "_get_tools", lambda **kw: [])
    monkeypatch.setattr("deerflow.runtime.checkpointer.get_checkpointer", lambda: None)
    config = {"configurable": {"subagent_enabled": False}, "context": {"max_concurrent_subagents": 1}}
    context = {"subagent_enabled": True, "max_concurrent_subagents": requested, "user_id": "alice"}
    client._ensure_agent(config, context=context)
    for current in [32, 1]:
        assembly.subagent_runtime = SubagentRuntimeConfig(max_running=current)
        client.reset_agent()
        client._ensure_agent(config, context=context)
        limit = next(m for m in client._agent["middleware"] if isinstance(m, SubagentLimitMiddleware))
        assert limit.max_concurrent == expected
        assert f"MAXIMUM {expected} `task` CALLS PER RESPONSE" in client._agent["system_prompt"]
    assert config["configurable"] == {"subagent_enabled": False}
    assert config["context"] == {"max_concurrent_subagents": 1}


def test_subagents_disabled_does_not_insert_guard_or_delegation_text(assembly):
    result = lead.make_lead_agent({"context": {"subagent_enabled": False}})
    assert not any(isinstance(m, SubagentLimitMiddleware) for m in result["middleware"])
    assert "MAXIMUM 2 `task` CALLS PER RESPONSE" not in result["system_prompt"]
