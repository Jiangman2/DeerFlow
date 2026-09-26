"""Behavioral tests of the exact production-baseline employee runtime delta.

Run with release/employee-sharing/probe_gateway_delta.py. This suite deliberately
targets the deployed static tool-policy protocol, not the checkout's unrelated
replacement. Live LLM, NFS and Kubernetes execution are separate release gates.
"""

import asyncio
import importlib
import sys
import threading
from types import SimpleNamespace

import pytest
import test_agent_skill_publication as skill_cases
from test_agent_skill_publication import (  # noqa: F401
    test_aio_mount_replaces_all_viewer_skill_roots,
    test_cached_sandbox_requires_employee_mount_confirmation,
    test_local_provider_replaces_static_and_personal_skill_mounts,
    test_remote_backend_carries_bounded_projection_and_requires_matching_receipt,
    test_remote_reuse_rechecks_projection_identity,
    test_sandbox_file_checks_use_publication_not_viewer_skill_state,
    test_withdrawal_blocks_even_cached_sandbox_tools,
)

stores = skill_cases.stores
skill = skill_cases.skill


@pytest.fixture
def admitted_skill(stores, tmp_path):
    from deerflow.persistence.agents.publications import TeamPublicationStore
    from deerflow.persistence.agents.runtime import EmployeePublicationBindings
    from deerflow.skills.agent_publication import stage_agent_skills

    paths, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-authority")
    bundle = tmp_path / "prepared"
    stage_agent_skills(bundle, ["analysis"], user_id="alice", app_config=config)
    snapshot = TeamPublicationStore(paths).publish("employee", owner="alice", config={"skills": ["analysis"]}, soul="duty", avatar=None, expected_revision=None, skill_bundle=bundle)
    EmployeePublicationBindings(paths).admit("thread", user_id="bob", name="employee", source="team")
    return snapshot


@pytest.fixture(autouse=True)
def composed_runtime_only(monkeypatch, tmp_path):
    from deerflow.agents.lead_agent import agent

    if not str(agent.__spec__.origin).startswith("employee-candidate:"):
        pytest.skip("requires the audited production delta loader")
    extensions = tmp_path / "extensions.json"
    extensions.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions))
    monkeypatch.setattr("deerflow.config.extensions_config._extensions_config", None)


@pytest.mark.parametrize("same_name_personal_skill", [False, True])
def test_slash_uses_employee_snapshot_without_viewer_install(stores, admitted_skill, same_name_personal_skill):
    from deerflow.agents.middlewares.skill_activation_middleware import SkillActivationMiddleware

    _, config, _, viewer = stores
    # A same-name personal Skill must not shadow the published authority.
    if same_name_personal_skill:
        skill(viewer.get_user_custom_root(), "analysis", "wrong-viewer-authority")
    else:
        assert viewer.load_skills() == []
    instance = SkillActivationMiddleware(app_config=config, user_id="bob", thread_id="thread", available_skills={"analysis"})
    result = instance._resolve_activation("/analysis 生成分析")
    assert result.failure_message is None
    assert result.activation.skill_name == "analysis"
    assert "owner-authority" in result.activation.skill_content
    assert "wrong-viewer-authority" not in result.activation.skill_content
    assert result.activation.editable is False
    assert result.activation.container_file_path == "/mnt/skills/custom/analysis/SKILL.md"
    if same_name_personal_skill:
        assert "wrong-viewer-authority" in viewer.load_skills()[0].skill_file.read_text()
    else:
        assert viewer.load_skills() == []


def test_slash_rejects_viewer_only_skill_and_withdrawal(stores, admitted_skill):
    from deerflow.agents.middlewares.skill_activation_middleware import SkillActivationMiddleware
    from deerflow.persistence.agents.publications import TeamPublicationStore

    paths, config, _, viewer = stores
    skill(viewer.get_user_custom_root(), "viewer-only", "not-authorized")
    instance = SkillActivationMiddleware(app_config=config, user_id="bob", thread_id="thread")
    assert instance._resolve_activation("/viewer-only run").activation is None
    assert instance._resolve_activation("/analysis run").activation is not None
    TeamPublicationStore(paths).unpublish("employee", owner="alice", expected_revision=admitted_skill.revision)
    with pytest.raises(FileNotFoundError, match="withdrawn"):
        instance._resolve_activation("/analysis run")


def test_slash_personal_conversation_retains_personal_catalog(stores, admitted_skill, monkeypatch):
    from deerflow.agents.middlewares import skill_activation_middleware as module

    _, config, _, viewer = stores
    skill(viewer.get_user_custom_root(), "analysis", "wrong-viewer-authority")
    monkeypatch.setattr(module, "get_or_new_user_skill_storage", lambda *args, **kwargs: viewer)
    result = module.SkillActivationMiddleware(app_config=config, user_id="bob", thread_id="private-thread")._resolve_activation("/analysis run")
    assert "wrong-viewer-authority" in result.activation.skill_content
    assert result.activation.editable is True


def test_real_composed_subagent_loads_parent_employee_snapshot(stores, admitted_skill, monkeypatch):
    # conftest pre-mocks this canonical module to break an old import cycle.
    # Remove only that mock after its dependencies loaded; the candidate finder
    # must load the actual composed implementation, not a test-only source copy.
    name = "deerflow.subagents.executor"
    parent = importlib.import_module("deerflow.subagents")
    monkeypatch.setattr(parent, "executor", getattr(parent, "executor", None), raising=False)
    monkeypatch.delitem(sys.modules, name, raising=False)
    module = importlib.import_module(name)
    assert str(module.__spec__.origin).startswith("employee-candidate:")
    _, config, _, _ = stores
    executor = object.__new__(module.SubagentExecutor)
    executor.config = SimpleNamespace(skills=None, name="general")
    executor.app_config = config
    executor.user_id = "bob"
    executor.thread_id = "thread"
    executor.trace_id = "baseline-test"
    selected = asyncio.run(executor._load_skills())
    assert "owner-authority" in selected[0].skill_file.read_text()


def test_lead_uses_pinned_definition_and_disables_shared_self_mutation(stores, admitted_skill, monkeypatch):
    from deerflow.agents.lead_agent import agent
    from deerflow.config.app_config import AppConfig
    from deerflow.skills.agent_publication import get_thread_published_skill_storage

    _, skill_config, _, _ = stores
    config = AppConfig(sandbox={"use": "deerflow.sandbox.local:LocalSandboxProvider"}, models=[{"name": "model", "model": "test-model", "use": "langchain_openai.ChatOpenAI"}])
    config.skills.deferred_discovery = False
    config.skills.container_path = "/mnt/skills"
    config.tool_search.enabled = False
    monkeypatch.setattr(agent, "get_app_config", lambda: config)
    monkeypatch.setattr(agent, "_resolve_model_name", lambda *a, **kw: "model")
    monkeypatch.setattr(agent, "create_chat_model", lambda **kw: "model")
    monkeypatch.setattr(agent, "create_agent", lambda **kw: kw)
    monkeypatch.setattr(agent, "build_middlewares", lambda *a, **kw: [])
    monkeypatch.setattr(agent, "apply_prompt_template", lambda **kw: kw)
    monkeypatch.setattr(agent, "load_agent_config", lambda *a, **kw: pytest.fail("must use pinned publication"))
    monkeypatch.setattr(agent, "is_personal_agent", lambda *a, **kw: True)
    catalog = get_thread_published_skill_storage("thread", user_id="bob", app_config=skill_config).load_skills()
    monkeypatch.setattr(agent, "_load_enabled_skills_for_tool_policy", lambda *a, **kw: catalog)
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kw: [SimpleNamespace(name="read_file"), SimpleNamespace(name="memory_search")])
    run_config = {"configurable": {"thread_id": "thread", "user_id": "bob", "agent_source": "team", "agent_name": "employee"}}
    result = agent.make_lead_agent(run_config)
    assert {tool.name for tool in result["tools"]} == {"read_file"}
    assert result["system_prompt"]["agent_soul"] == "duty"
    assert result["system_prompt"]["self_update_enabled"] is False
    assert run_config["configurable"]["agent_publication_revision"] == admitted_skill.revision


@pytest.mark.parametrize("asynchronous", [False, True])
def test_acquire_rechecks_mounts_inside_original_lock(stores, admitted_skill, monkeypatch, asynchronous):
    from deerflow.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider

    provider = object.__new__(AioSandboxProvider)
    lock = threading.Lock()
    monkeypatch.setattr(provider, "_get_thread_lock", lambda *args: lock)
    sandbox_id = provider._sandbox_id_for_thread("thread", "bob")
    monkeypatch.setattr(provider, "_acquire_internal", lambda *args, **kwargs: sandbox_id)

    async def acquire(*args, **kwargs):
        return sandbox_id

    monkeypatch.setattr(provider, "_acquire_internal_async", acquire)
    seen = []

    def verify(*args, **kwargs):
        assert lock.locked()
        seen.append(kwargs["user_id"])
        raise RuntimeError("Existing employee mount mismatch")

    provider._backend = SimpleNamespace(validate_employee_skills=verify)
    with pytest.raises(RuntimeError, match="mount mismatch"):
        if asynchronous:
            asyncio.run(provider.acquire_async("thread", user_id="bob"))
        else:
            provider.acquire("thread", user_id="bob")
    assert seen == ["bob"]
    assert not lock.locked()
