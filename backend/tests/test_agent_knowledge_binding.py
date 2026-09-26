"""Creator knowledge bindings never become publisher-identity credentials."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from deerflow.config.agent_knowledge_config import AgentKnowledgeScope


def selected(**changes):
    return {"mode": "selected", "items": [{"type": "file", "id": "doc-1", "kb_id": "kb-1", "name": "untrusted title", "tenant_id": "tenant-1", **changes}]}


@pytest.mark.parametrize(
    "document",
    [
        {"mode": "selected", "items": []},
        {"mode": "disabled", "items": [{"type": "kb", "id": "kb-1"}]},
        selected(id="../../secrets"),
        selected(type="tag"),
        selected(owner_user_id="publisher"),
        {"mode": "selected", "items": [{"type": "file", "id": "doc-1"}]},
        {"mode": "all"},
        {"version": 2, "mode": "inherit"},
        {"mode": "selected", "items": [{"type": "kb", "id": "kb-" + str(i)} for i in range(21)]},
    ],
)
def test_scope_rejects_ambiguous_or_unsafe_documents(document):
    with pytest.raises(ValidationError):
        AgentKnowledgeScope.model_validate(document)


def test_scope_builds_exact_tool_context_without_expanding_a_file_to_its_parent():
    scope = AgentKnowledgeScope.model_validate(selected())
    context = scope.to_run_context()
    assert context["weknora_knowledge_base_ids"] == []
    assert context["weknora_knowledge_ids"] == ["doc-1"]
    assert context["weknora_tag_scopes"] == []
    assert context["weknora_scope_items"][0]["kb_id"] == "kb-1"
    assert "weknora_access_token" not in context


@pytest.fixture
def upstream(monkeypatch):
    from app.gateway import agent_knowledge as binding
    from app.gateway.routers import weknora

    actor = SimpleNamespace(id="creator")
    session = SimpleNamespace(token="creator-session", user={"id": "wk-creator"}, tenant={"id": "tenant-1"}, memberships=[{"tenant_id": "tenant-stale"}])
    login = AsyncMock(return_value=session)
    replies = {
        "/auth/me": {"data": {"user": {"id": "wk-creator"}, "memberships": [{"tenant_id": "tenant-1"}]}},
        "/knowledge-bases/kb-1": {"data": {"id": "kb-1", "name": "获准的库"}},
        "/knowledge/doc-1": {"data": {"id": "doc-1", "knowledge_base_id": "kb-1", "title": "已验证文件"}},
    }
    calls = []

    async def request(method, path, token, **kwargs):
        calls.append((method, path, token, kwargs))
        value = replies[path]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(binding, "ensure_weknora_session_for_deerflow_user", login)
    monkeypatch.setattr(weknora, "_request", request)
    return binding, actor, replies, calls, login


@pytest.mark.asyncio
async def test_binding_checks_live_access_and_replaces_client_titles(upstream):
    binding, actor, replies, calls, login = upstream
    result = await binding.validate_creator_knowledge_scope(AgentKnowledgeScope.model_validate(selected()), actor)
    assert result.items[0].name == "已验证文件"
    assert result.items[0].kb_name == "获准的库"
    assert result.items[0].tenant_id == "tenant-1"
    login.assert_awaited_once_with(actor)
    assert all(call[2] == "creator-session" for call in calls)
    assert all(call[0] == "GET" for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, {"mode": "inherit"}, {"mode": "disabled"}])
async def test_no_selection_does_not_enumerate_or_request_credentials(upstream, scope):
    binding, actor, replies, calls, login = upstream
    value = AgentKnowledgeScope.model_validate(scope) if scope else None
    assert await binding.validate_creator_knowledge_scope(value, actor) == value
    assert not calls
    login.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["tenant", "membership", "parent", "revoked", "identity"])
async def test_binding_fails_closed_for_stale_membership_or_wrong_resources(upstream, failure):
    binding, actor, replies, calls, login = upstream
    document = selected()
    if failure == "tenant":
        document = selected(tenant_id="tenant-stale")
    elif failure == "membership":
        replies["/auth/me"] = {"data": {"user": {"id": "wk-creator"}}}
    elif failure == "identity":
        replies["/auth/me"]["data"]["user"]["id"] = "different-user"
    elif failure == "parent":
        replies["/knowledge/doc-1"]["data"]["knowledge_base_id"] = "other-kb"
    else:
        replies["/knowledge/doc-1"] = HTTPException(403, "private upstream detail")
    with pytest.raises(HTTPException) as caught:
        await binding.validate_creator_knowledge_scope(AgentKnowledgeScope.model_validate(document), actor)
    assert caught.value.status_code in (403, 502)
    assert "private upstream detail" not in str(caught.value.detail)


@pytest.mark.asyncio
async def test_binding_rejects_missing_authenticated_actor(upstream):
    binding, _, _, calls, login = upstream
    with pytest.raises(HTTPException) as caught:
        await binding.validate_creator_knowledge_scope(AgentKnowledgeScope.model_validate(selected()), None)
    assert caught.value.status_code == 401
    login.assert_not_awaited()


@pytest.mark.asyncio
async def test_bound_scope_replaces_all_client_knowledge_fields(upstream, monkeypatch):
    from deerflow.config.agents_config import AgentConfig

    binding, actor, _, calls, _ = upstream
    agent = AgentConfig(name="reporter", knowledge_scope=AgentKnowledgeScope.model_validate(selected()))
    monkeypatch.setattr(binding, "load_agent_config", lambda *a, **kw: agent)
    monkeypatch.setattr(binding, "is_personal_agent", lambda *a, **kw: True)
    config = {
        section: {"agent_name": "reporter", "agent_source": "personal", "weknora_knowledge_base_ids": ["outside"], "weknora_tag_scopes": [{"knowledge_base_id": "outside", "tag_ids": ["all"]}]} for section in ("context", "configurable")
    }
    await binding.apply_agent_knowledge_scope(config, actor)
    for section in ("context", "configurable"):
        assert config[section]["weknora_knowledge_base_ids"] == []
        assert config[section]["weknora_knowledge_ids"] == ["doc-1"]
        assert config[section]["weknora_tag_scopes"] == []
    assert "creator-session" not in str(config)


@pytest.mark.asyncio
async def test_shared_binding_cannot_impersonate_publisher_before_grants_exist(upstream, monkeypatch):
    from deerflow.config.agents_config import AgentConfig

    binding, actor, _, calls, login = upstream
    agent = AgentConfig(name="reporter", knowledge_scope=AgentKnowledgeScope.model_validate(selected()))
    monkeypatch.setattr(binding, "load_agent_config", lambda *a, **kw: agent)
    config = {"context": {"agent_name": "reporter", "agent_source": "team"}}
    with pytest.raises(HTTPException) as caught:
        await binding.apply_agent_knowledge_scope(config, actor)
    assert caught.value.status_code == 409
    login.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["unknown-source", {"owner": "other-user"}, ["personal"]])
async def test_invalid_source_is_a_client_error_without_resource_access(upstream, monkeypatch, source):
    from unittest.mock import Mock

    binding, actor, _, calls, login = upstream
    loader = Mock()
    monkeypatch.setattr(binding, "load_agent_config", loader)
    with pytest.raises(HTTPException) as caught:
        await binding.apply_agent_knowledge_scope({"context": {"agent_name": "reporter", "agent_source": source}}, actor)
    assert caught.value.status_code == 422
    assert "other-user" not in str(caught.value.detail)
    loader.assert_not_called()
    login.assert_not_awaited()
    assert not calls


@pytest.fixture
def agent_api(tmp_path, monkeypatch):
    from app.gateway.routers import agents
    from deerflow.config import agents_config
    from deerflow.config.paths import Paths
    from deerflow.persistence.agents.file import FileAgentStore

    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(agents_config, "get_paths", lambda: paths)
    monkeypatch.setattr(agents, "get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.persistence.agents.get_agent_store", lambda: FileAgentStore())
    monkeypatch.setattr(agents, "get_agent_store", lambda: FileAgentStore())
    monkeypatch.setattr(agents, "get_effective_user_id", lambda: "creator")
    monkeypatch.setattr(agents, "get_current_user", lambda: SimpleNamespace(id="creator", is_disabled=False))
    monkeypatch.setattr(agents, "_require_agents_api_enabled", lambda: None)
    monkeypatch.setattr(agents, "_ensure_category_exists", lambda *a: None)
    monkeypatch.setattr(agents, "_validate_model_exists", lambda *a: None)
    return agents, paths


@pytest.mark.asyncio
async def test_api_validates_saves_preserves_and_explicitly_clears_scope(agent_api, upstream):
    agents, paths = agent_api
    request = agents.AgentCreateRequest(name="reports", knowledge_scope=selected())
    created = await agents.create_agent_endpoint(request)
    assert created.knowledge_scope.items[0].name == "已验证文件"
    updated = await agents.update_agent("reports", agents.AgentUpdateRequest(description="new description", expected_draft_revision=created.draft_revision))
    assert updated.knowledge_scope == created.knowledge_scope
    cleared = await agents.update_agent("reports", agents.AgentUpdateRequest(knowledge_scope=None, expected_draft_revision=updated.draft_revision))
    assert cleared.knowledge_scope is None


@pytest.mark.asyncio
async def test_api_permission_rejection_does_not_persist(agent_api, upstream):
    agents, paths = agent_api
    _, _, replies, _, _ = upstream
    replies["/knowledge/doc-1"] = HTTPException(403, "revoked")
    with pytest.raises(HTTPException):
        await agents.create_agent_endpoint(agents.AgentCreateRequest(name="reports", knowledge_scope=selected()))
    assert not (paths.user_agent_dir("creator", "reports") / "config.yaml").exists()


@pytest.mark.asyncio
async def test_publish_binding_fails_before_mutating_existing_publication(agent_api, upstream):
    import yaml

    agents, paths = agent_api
    await agents.create_agent_endpoint(agents.AgentCreateRequest(name="reports", knowledge_scope=selected()))
    team = paths.team_agent_dir("reports")
    team.mkdir(parents=True)
    original = yaml.safe_dump({"name": "reports", "description": "old publication", "published_by_user_id": "creator"})
    (team / "config.yaml").write_text(original, encoding="utf-8")
    with pytest.raises(HTTPException) as caught:
        await agents.publish_agent("reports", agents.AgentPublishRequest(overwrite=True))
    assert caught.value.status_code == 422  # No reviewed draft revision supplied.
    assert (team / "config.yaml").read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_denied_binding_never_creates_pending_run(monkeypatch):
    from app.gateway import services

    manager = SimpleNamespace(create_or_reject=AsyncMock())
    thread_store = SimpleNamespace(check_access=AsyncMock(return_value=True))
    monkeypatch.setattr(services, "get_stream_bridge", lambda r: None)
    monkeypatch.setattr(services, "get_run_manager", lambda r: manager)
    monkeypatch.setattr(services, "get_run_context", lambda r: SimpleNamespace(thread_store=thread_store))
    monkeypatch.setattr(services, "resolve_agent_factory", lambda a: None)
    monkeypatch.setattr(services, "apply_checkpoint_to_run_config", AsyncMock())
    monkeypatch.setattr(services, "apply_agent_knowledge_scope", AsyncMock(side_effect=HTTPException(403, "revoked")))
    body = SimpleNamespace(on_disconnect="continue", context={}, input={"messages": []}, config={}, metadata={}, assistant_id="lead_agent", command=None)
    request = SimpleNamespace(headers={}, state=SimpleNamespace(user=SimpleNamespace(id="creator"), auth_source="session"))
    with pytest.raises(HTTPException) as caught:
        await services.start_run(body, "thread", request)
    assert caught.value.status_code == 403
    manager.create_or_reject.assert_not_awaited()


@pytest.mark.parametrize("source", ["team", "legacy"])
def test_direct_runtime_cannot_bypass_shared_grant_gate(source):
    from deerflow.config.agent_knowledge_config import enforce_agent_knowledge_context

    scope = AgentKnowledgeScope.model_validate(selected())
    with pytest.raises(ValueError, match="delegation"):
        enforce_agent_knowledge_context(scope, {"context": scope.to_run_context()}, source=source, personal=False)


def test_runtime_rejects_changed_scope_and_disabled_clears_both_sections():
    from deerflow.config.agent_knowledge_config import enforce_agent_knowledge_context

    scope = AgentKnowledgeScope.model_validate(selected())
    with pytest.raises(ValueError, match="revalidated"):
        enforce_agent_knowledge_context(scope, {"context": {}}, source="personal", personal=True)
    config = {section: scope.to_run_context() for section in ("context", "configurable")}
    config["configurable"]["weknora_knowledge_base_ids"] = ["outside"]
    with pytest.raises(ValueError, match="revalidated"):
        enforce_agent_knowledge_context(scope, config, source="personal", personal=True)
    enforce_agent_knowledge_context(AgentKnowledgeScope(mode="disabled"), config, source="personal", personal=True)
    assert config["context"]["weknora_knowledge_ids"] == []
    assert config["configurable"]["weknora_knowledge_base_ids"] == []


def test_runtime_rejects_smuggled_scope_items_even_with_matching_id_lists():
    from deerflow.config.agent_knowledge_config import enforce_agent_knowledge_context

    scope = AgentKnowledgeScope.model_validate(selected())
    config = {section: scope.to_run_context() for section in ("context", "configurable")}
    config["context"]["weknora_scope_items"].append({"type": "kb", "id": "outside"})
    with pytest.raises(ValueError, match="revalidated"):
        enforce_agent_knowledge_context(scope, config, source="personal", personal=True)


@pytest.mark.asyncio
async def test_fresh_binding_tenant_route_survives_cached_login_memberships(monkeypatch):
    from app.gateway import services

    actor = SimpleNamespace(id="creator")
    login = AsyncMock(return_value=SimpleNamespace(token="caller-token", user={"id": "wk-creator"}, tenant={"id": "old-tenant"}, memberships=[]))
    monkeypatch.setattr(services, "ensure_weknora_session_for_deerflow_user", login)
    scope = AgentKnowledgeScope.model_validate(selected())
    config = {"context": scope.to_run_context()}
    await services.inject_weknora_user_context(config, SimpleNamespace(state=SimpleNamespace(user=actor)), verified_knowledge_tenants={"kb-1": "tenant-1"})
    assert config["context"]["weknora_access_tenant_ids"] == {"kb-1": "tenant-1"}
    assert config["context"]["weknora_access_token"] == "caller-token"
