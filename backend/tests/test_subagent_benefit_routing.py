"""Pinned cost/benefit routing, with the explicit enterprise parallel override."""

from types import SimpleNamespace

import pytest

from deerflow.agents.lead_agent import prompt as prompts
from deerflow.tools.builtins.task_tool import task_tool


@pytest.mark.parametrize("limit", [1, 2, 3, 4])
@pytest.mark.parametrize("receipts", [True, False])
def test_routing_and_verification_are_coherent(monkeypatch, limit, receipts):
    monkeypatch.setattr(prompts, "get_available_subagent_names", lambda **_: ["general-purpose", "bash"])
    config = SimpleNamespace(verification=SimpleNamespace(receipts_enabled=receipts))
    section = prompts._build_subagent_section(limit, 7, app_config=config)
    assert "Default to direct execution" in section
    assert "startup overhead + duplicate context" in section
    assert "coordination and synthesis + state-conflict risk + side-effect risk" in section
    assert "complexity alone" in section.lower()
    assert "Use one specialized subagent" in section
    assert "acceptance_criteria" in section
    assert "`completed` means execution ended" in section
    assert "`UNVERIFIED`: this is missing evidence, not a failed condition" in section
    assert "Do not restart the whole task or repeat an unchanged attempt" in section
    assert "first tool response MUST be one batch of `task` calls" in section
    assert "never authorizes overlapping writes" in section
    assert "two or more independent workstreams" in section
    assert f"MAXIMUM {limit} `task` CALLS PER RESPONSE" in section
    assert "MAXIMUM 7 `task` CALLS PER RUN" in section
    assert ("receipt citations are disabled" in section) is (not receipts)
    if limit == 1:
        assert "Parallel dispatch cannot reduce wall-clock latency" in section
        assert "Expected benefit = specialist capability + context isolation" in section
        assert "Do not chain delegations merely to work around" in section
        assert "**Batch 2**" not in section
    else:
        assert "Inter-agent dependencies" in section
        assert "Unsafe shared state" in section
        assert "Re-evaluate the remaining work after every batch" in section
        assert "smallest useful batch" in section
    for removed in ("DECOMPOSE, DELEGATE, SYNTHESIZE", "Only use `task` when you can launch 2+", "Single task = No value", "For ANY non-trivial task"):
        assert removed not in section
    # Do not advertise not-yet-wired v2.1 APIs merely by copying their prose.
    assert "context_mode=" not in section
    assert "batch_task" not in section


@pytest.mark.parametrize("enabled,names", [(True, []), (False, ["general-purpose"]), (True, ["general-purpose"])])
@pytest.mark.parametrize("limit", [1, 3])
def test_full_prompt_reminders_follow_available_roles(monkeypatch, enabled, names, limit):
    monkeypatch.setattr(prompts, "get_available_subagent_names", lambda **_: names)
    for helper in ("get_skills_prompt_section", "get_deferred_tools_prompt_section", "_build_acp_section", "_build_custom_mounts_section", "_build_memory_tool_section", "get_agent_soul"):
        monkeypatch.setattr(prompts, helper, lambda *_, **__: "")
    config = SimpleNamespace(skill_evolution=SimpleNamespace(enabled=False))
    full = prompts.apply_prompt_template(subagent_enabled=enabled, max_concurrent_subagents=limit, app_config=config)
    active = enabled and bool(names)
    # Confidentiality prose names the reserved tag even when no block exists.
    assert ("\n<subagent_system>\n" in full) is active
    assert ("Benefit-Based Delegation" in full) is active
    assert ("DELEGATION CHECK" in full) is active
    assert "DECOMPOSITION CHECK" not in full
    assert "Orchestrator Mode" not in full
    assert "中国海油通用智能体" in full


def test_tool_schema_does_not_reintroduce_complexity_only_delegation():
    description = task_tool.description
    assert "Default to direct execution" in description
    assert "Complexity or verbose output alone is not sufficient" in description
    assert "one subagent" in description
    assert "Do not overlap files" in description
    assert "Re-evaluate net benefit" in description
    schema = task_tool.tool_call_schema.model_json_schema()
    assert "acceptance_criteria" in schema["properties"]
    assert "acceptance_criteria" not in schema["required"]
    assert "context_mode" not in schema["properties"]
