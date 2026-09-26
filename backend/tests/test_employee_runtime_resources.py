"""Delegated retrieval is a narrow tool, never an owner credential context."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from deerflow.config.agent_knowledge_config import AgentKnowledgeScope


def test_delegated_tool_schema_has_only_query_and_removes_browsing():
    from deerflow.runtime.employee_resources import employee_knowledge_search, scope_employee_tools

    publication = SimpleNamespace(config={"resource_policy_version": 1, "knowledge_scope": {"mode": "selected", "items": [{"type": "kb", "id": "kb", "tenant_id": "tenant"}]}})
    tools = [SimpleNamespace(name=n) for n in ("bash", "weknora_search", "weknora_read_document", "weknora_list_documents")]
    selected = scope_employee_tools(tools, publication)
    assert [t.name for t in selected] == ["bash", "employee_knowledge_search"]
    assert set(employee_knowledge_search.tool_call_schema.model_fields) == {"query"}


def test_inherit_and_ordinary_tools_remain_unchanged():
    from deerflow.runtime.employee_resources import scope_employee_tools

    tools = [SimpleNamespace(name="weknora_search")]
    assert scope_employee_tools(tools, None) == tools
    assert scope_employee_tools(tools, SimpleNamespace(config={"knowledge_scope": {"mode": "inherit"}})) == tools


@pytest.mark.parametrize("shared", [False, True])
def test_disabled_knowledge_removes_all_knowledge_tools_and_credentials(shared):
    from deerflow.config.agent_knowledge_config import enforce_agent_knowledge_context
    from deerflow.runtime.employee_resources import scope_employee_tools

    scope = AgentKnowledgeScope(mode="disabled")
    publication = SimpleNamespace(config={"knowledge_scope": scope.model_dump(), "resource_policy_version": 1}) if shared else None
    tools = [SimpleNamespace(name=n) for n in ("bash", "weknora_search", "weknora_read_document", "employee_knowledge_search")]
    assert [t.name for t in scope_employee_tools(tools, publication, knowledge_scope=scope)] == ["bash"]
    cfg = {s: {"weknora_access_token": "caller-secret", "weknora_tenant_id": "tenant", "user_id": "viewer"} for s in ("context", "configurable")}
    enforce_agent_knowledge_context(scope, cfg, source="team" if shared else "personal", personal=not shared, publication=publication)
    for values in cfg.values():
        assert "weknora_access_token" not in values and "weknora_tenant_id" not in values
        assert values["user_id"] == "viewer"


@pytest.mark.asyncio
async def test_delegated_child_uses_business_thread_context_not_graph_checkpoint(monkeypatch):
    from deerflow.runtime import employee_resources

    search = AsyncMock(return_value={"results": []})
    monkeypatch.setattr(employee_resources, "_search", search)
    runtime = SimpleNamespace(context={"thread_id": "parent-business-thread", "user_id": "bob"}, config={"configurable": {"thread_id": "subgraph-coordinate"}})
    await employee_resources.employee_knowledge_search.coroutine("question", runtime)
    search.assert_awaited_once_with(thread_id="parent-business-thread", viewer_id="bob", query="question")


@pytest.mark.parametrize("policy", [None, 1])
@pytest.mark.parametrize("memory_mode", ["middleware", "tool"])
def test_shared_employee_context_cannot_leak_into_personal_persistent_memory(policy, memory_mode):
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.runtime.employee_resources import scope_employee_config, scope_employee_tools

    original = AppConfig(models=[], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), memory={"enabled": True, "injection_enabled": True, "mode": memory_mode})
    publication = SimpleNamespace(config={"knowledge_scope": {"mode": "inherit"}, **({"resource_policy_version": policy} if policy else {})})
    scoped = scope_employee_config(original, publication)
    assert not scoped.memory.enabled and not scoped.memory.injection_enabled
    assert original.memory.enabled and original.memory.injection_enabled
    assert scope_employee_config(original, None) is original
    tools = [SimpleNamespace(name=n) for n in ("bash", "memory_add", "memory_search", "memory_update", "memory_delete")]
    assert [t.name for t in scope_employee_tools(tools, publication)] == ["bash"]


@pytest.mark.parametrize("policy", [None, 1])
def test_shared_employee_disables_injection_capture_and_automatic_compaction_flush(monkeypatch, policy):
    from unittest.mock import MagicMock

    from deerflow.agents.middlewares.dynamic_context_middleware import DynamicContextMiddleware
    from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
    from deerflow.agents.middlewares.summarization_middleware import create_summarization_middleware
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.runtime.employee_resources import scope_employee_config

    original = AppConfig(models=[], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), summarization={"enabled": True})
    scoped = scope_employee_config(original, SimpleNamespace(config={} if policy is None else {"resource_policy_version": policy}))
    memory_read = MagicMock(return_value="PRIVATE-MEMORY")
    memory_manager = MagicMock()
    monkeypatch.setattr("deerflow.agents.lead_agent.prompt._get_memory_context", memory_read)
    monkeypatch.setattr("deerflow.agents.middlewares.memory_middleware.get_memory_manager", memory_manager)
    model = MagicMock()
    model.with_config.return_value = model
    monkeypatch.setattr("deerflow.agents.middlewares.summarization_middleware.create_chat_model", lambda **kw: model)

    _, memory = DynamicContextMiddleware(agent_name="reports", app_config=scoped)._build_full_reminder()
    assert memory is None
    memory_read.assert_not_called()
    MemoryMiddleware(agent_name="reports", memory_config=scoped.memory).after_agent({}, SimpleNamespace(context={"thread_id": "bob-thread"}))
    memory_manager.assert_not_called()
    summarizer = create_summarization_middleware(app_config=scoped)
    assert summarizer is not None
    assert summarizer._before_summarization_hooks == []


def test_selected_shared_scope_requires_trusted_snapshot_and_clears_credentials():
    from deerflow.config.agent_knowledge_config import enforce_agent_knowledge_context

    scope = AgentKnowledgeScope(mode="selected", items=[{"type": "kb", "id": "kb", "tenant_id": "tenant"}])
    cfg = {"context": {"weknora_access_token": "forged", "weknora_knowledge_base_ids": ["other"]}, "configurable": {"weknora_access_token": "forged"}}
    with pytest.raises(ValueError):
        enforce_agent_knowledge_context(scope, cfg, source="team", personal=False)
    publication = SimpleNamespace(config={"resource_policy_version": 1, "knowledge_scope": scope.model_dump()})
    enforce_agent_knowledge_context(scope, cfg, source="team", personal=False, publication=publication)
    for section in ("context", "configurable"):
        assert cfg[section]["weknora_knowledge_base_ids"] == []
        assert "weknora_access_token" not in cfg[section]


@pytest.mark.asyncio
async def test_guard_rechecks_each_tool_and_does_not_run_revoked_work(monkeypatch):
    from deerflow.agents.middlewares.employee_publication_middleware import EmployeePublicationMiddleware
    from deerflow.runtime import employee_resources

    snapshot = SimpleNamespace(config={"resource_policy_version": 1})

    def read(*args, **kwargs):
        return snapshot

    monkeypatch.setattr(employee_resources, "read_employee_publication", read)
    authorize = AsyncMock()
    monkeypatch.setattr(employee_resources, "_authorize", authorize)
    middleware = EmployeePublicationMiddleware(thread_id="thread", user_id="bob")
    handler = AsyncMock(return_value="done")
    request = SimpleNamespace()
    assert await middleware.awrap_tool_call(request, handler) == "done"
    authorize.side_effect = PermissionError("revoked")
    with pytest.raises(PermissionError):
        await middleware.awrap_tool_call(request, handler)
    assert handler.await_count == 1


@pytest.mark.asyncio
async def test_broker_not_registered_fails_closed(monkeypatch):
    from deerflow.runtime import employee_resources

    monkeypatch.setattr(employee_resources, "_search", None)
    runtime = SimpleNamespace(context={"user_id": "bob", "thread_id": "thread"}, config={"configurable": {"thread_id": "thread"}})
    with pytest.raises(RuntimeError):
        await employee_resources.employee_knowledge_search.coroutine("question", runtime)


@pytest.mark.asyncio
async def test_authorization_wait_is_bounded_and_never_invokes_tool(monkeypatch):
    import asyncio

    from deerflow.agents.middlewares.employee_publication_middleware import EmployeePublicationMiddleware
    from deerflow.runtime import employee_resources

    monkeypatch.setattr(employee_resources, "read_employee_publication", lambda *a: SimpleNamespace(config={"resource_policy_version": 1}))
    monkeypatch.setattr(employee_resources, "AUTHORIZATION_TIMEOUT_SECONDS", 0.01)

    async def blocked(*a):
        await asyncio.Event().wait()

    monkeypatch.setattr(employee_resources, "_authorize", blocked)
    handler = AsyncMock()
    with pytest.raises(RuntimeError, match="timed out"):
        await EmployeePublicationMiddleware(thread_id="thread", user_id="bob").awrap_tool_call(object(), handler)
    handler.assert_not_called()
