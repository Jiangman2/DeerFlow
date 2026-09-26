"""Runtime composition, not a claim of production RBAC policy approval."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.tools import StructuredTool

from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
from deerflow.authz.adapter import GuardrailAuthorizationAdapter
from deerflow.authz.provider import AuthzDecision
from deerflow.authz.rbac import RbacAuthorizationProvider
from deerflow.config.app_config import AppConfig
from deerflow.guardrails.middleware import GuardrailMiddleware
from deerflow.tools.mcp_metadata import tag_mcp_tool


def tool(name):
    return StructuredTool.from_function(lambda: "executed", name=name, description="test")


def config(enabled=True):
    return AppConfig.model_validate(
        {
            "models": [{"name": "test", "use": "langchain_openai:ChatOpenAI", "model": "test"}],
            "sandbox": {"use": "test"},
            "authorization": {"enabled": enabled, "provider": {"use": "deerflow.authz.rbac:RbacAuthorizationProvider", "config": {"roles": {"user": {"tools": {"allow": ["safe", "mcp_safe"]}}, "guest": {"tools": {"allow": []}}}}}},
            "tool_search": {"enabled": True},
            "skills": {"deferred_discovery": True},
        }
    )


@pytest.fixture
def embedded(monkeypatch):
    from deerflow import client as module

    app = config()
    monkeypatch.setattr(module, "get_app_config", lambda: app)
    monkeypatch.setattr(module, "create_chat_model", lambda **kwargs: object())
    monkeypatch.setattr(module, "create_agent", lambda **kwargs: kwargs)
    monkeypatch.setattr(module, "apply_prompt_template", lambda **kwargs: "test")
    monkeypatch.setattr(module, "get_enabled_skills_for_config", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "build_skill_search_setup", lambda *args, **kwargs: SimpleNamespace(describe_skill_tool=tool("describe_skill"), skill_names=set()))
    builds = []

    def middlewares(*args, **kwargs):
        builds.append(kwargs)
        return build_lead_runtime_middlewares(app_config=app, authorization_provider=kwargs["authorization_provider"], deferred_setup=kwargs["deferred_setup"])

    monkeypatch.setattr(module, "build_middlewares", middlewares)
    client = module.DeerFlowClient(checkpointer=False)
    candidates = [tool("safe"), tag_mcp_tool(tool("mcp_safe")), tag_mcp_tool(tool("mcp_denied"))]
    monkeypatch.setattr(client, "_get_tools", lambda **kwargs: candidates)
    return client, app, builds, candidates


def test_embedded_filters_late_helpers_and_mcp_with_same_provider(embedded):
    client, _, builds, candidates = embedded
    context = {"user_id": "consumer", "user_role": "user", "thread_id": "thread"}
    client._ensure_agent({"configurable": {}}, context=context)
    assert [t.name for t in client._agent["tools"]] == ["safe", "mcp_safe", "tool_search"]
    assert candidates[0] is client._agent["tools"][0]
    assert builds[0]["deferred_setup"].deferred_names == {"mcp_safe"}
    guard = next(m for m in client._agent["middleware"] if isinstance(m, GuardrailMiddleware))
    assert guard.provider._provider is builds[0]["authorization_provider"]
    handler = MagicMock()
    request = SimpleNamespace(tool_call={"name": "mcp_denied", "id": "injected", "args": {}}, runtime=SimpleNamespace(context=context))
    assert guard.wrap_tool_call(request, handler).status == "error"
    handler.assert_not_called()


def test_embedded_policy_rebuild_and_disabled_cache(embedded):
    client, app, builds, _ = embedded
    cfg = {"configurable": {"user_id": "consumer", "user_role": "user"}}
    client._ensure_agent(cfg)
    client._ensure_agent(cfg)
    assert len(builds) == 2
    assert builds[0]["authorization_provider"] is not builds[1]["authorization_provider"]
    client._ensure_agent(cfg, context={"user_role": "guest"})
    assert client._agent["tools"] == []
    app.authorization.enabled = False
    client._ensure_agent(cfg)
    assert len(builds) == 4
    assert {t.name for t in client._agent["tools"]} == {"safe", "mcp_safe", "mcp_denied", "tool_search", "describe_skill"}
    previous = client._agent
    client._ensure_agent(cfg)
    assert client._agent is previous
    assert len(builds) == 4


def test_embedded_stream_identity_reaches_assembly_and_execution(embedded, monkeypatch):
    client, _, _, _ = embedded
    captured = {}

    class Graph:
        def stream(self, state, config, *, context, **kwargs):
            captured["execution"] = context
            return iter(())

    def ensure(config, *, context):
        captured["assembly"] = dict(context)
        client._agent = Graph()

    monkeypatch.setattr(client, "_ensure_agent", ensure)
    monkeypatch.setattr("deerflow.client.build_tracing_callbacks", lambda: [])
    attributes = {"department": "engineering"}
    list(client.stream("test", thread_id="t", user_id="owner", user_role="guest", is_internal=False, channel_user_id="sender", authz_attributes=attributes))
    for key in ("user_id", "user_role", "is_internal", "channel_user_id", "authz_attributes"):
        assert captured["assembly"][key] == captured["execution"][key]
    assert captured["execution"]["authz_attributes"] is not attributes


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("failure", ["deny", "error"])
def test_runtime_denial_audited_before_explicit_guardrail(asynchronous, failure):
    class Provider(RbacAuthorizationProvider):
        def authorize(self, request):
            if failure == "error":
                raise RuntimeError("policy unavailable")
            return AuthzDecision(allow=False)

        async def aauthorize(self, request):
            return self.authorize(request)

    app = config()
    app.guardrails.enabled = True
    from deerflow.config.guardrails_config import GuardrailProviderConfig

    app.guardrails.provider = GuardrailProviderConfig(use="deerflow.guardrails.builtin:AllowlistProvider")
    chain = build_lead_runtime_middlewares(app_config=app, authorization_provider=Provider(roles={"user": {}}))
    guards = [m for m in chain if isinstance(m, GuardrailMiddleware)]
    assert len(guards) == 2
    assert isinstance(guards[0].provider, GuardrailAuthorizationAdapter)
    # Real LangChain composition must support the two independent wrap guards.
    create_agent(model=FakeListChatModel(responses=["ok"]), tools=[tool("safe")], middleware=guards)
    journal = SimpleNamespace(record_middleware=MagicMock())
    request = SimpleNamespace(tool_call={"name": "safe", "id": "call", "args": {}}, runtime=SimpleNamespace(context={"user_id": "owner", "__run_journal": journal}))
    inner = AsyncMock() if asynchronous else MagicMock()
    result = asyncio.run(guards[0].awrap_tool_call(request, inner)) if asynchronous else guards[0].wrap_tool_call(request, inner)
    assert result.status == "error"
    inner.assert_not_called()
    journal.record_middleware.assert_called_once()
    assert journal.record_middleware.call_args.kwargs["changes"]["provider_error"] is (failure == "error")


@pytest.mark.parametrize("allowed", [True, False])
def test_embedded_model_selection_gates_the_actual_factory(embedded, monkeypatch, allowed):
    from deerflow import client as module
    from deerflow.authz.model_authorization import ModelAuthorizationError

    client, app, _, _ = embedded
    app.models.append(app.models[0].model_copy(update={"name": "permitted"}))
    app.authorization.provider.config["roles"]["user"]["models"] = {"allow": ["permitted"] if allowed else []}
    model_factory = MagicMock(return_value=object())
    monkeypatch.setattr(module, "create_chat_model", model_factory)
    cfg = {"configurable": {"model_name": "test", "user_id": "consumer", "user_role": "user"}}
    if allowed:
        client._ensure_agent(cfg)
        assert model_factory.call_args.kwargs["name"] == "permitted"
        assert model_factory.call_args.kwargs["app_config"] is app
    else:
        with pytest.raises(ModelAuthorizationError):
            client._ensure_agent(cfg)
        model_factory.assert_not_called()
