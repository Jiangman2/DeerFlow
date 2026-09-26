"""Model visibility cannot bypass effective run model:use authorization."""

import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langgraph.errors import GraphBubbleUp

from deerflow.authz.model_authorization import ModelAuthorizationError, authorize_model_name, authorize_model_name_async
from deerflow.authz.provider import AuthzDecision
from deerflow.config.app_config import AppConfig
from deerflow.config.authorization_config import AuthorizationConfig


def config():
    return AppConfig.model_validate(
        {
            "models": [{"name": name, "use": "langchain_openai:ChatOpenAI", "model": name} for name in ("private", "visible", "basic")],
            "sandbox": {"use": "test"},
            "authorization": {
                "enabled": True,
                "provider": {"use": "deerflow.authz.rbac:RbacAuthorizationProvider", "config": {"roles": {"user": {"models": {"allow": ["basic"]}}}}},
            },
        }
    )


def provider(*, allowed=("basic",), selected=("basic", "invented", "visible", "private")):
    calls = []

    def decision(request):
        calls.append(request)
        return AuthzDecision(allow=request.target in allowed)

    return SimpleNamespace(authorize=MagicMock(side_effect=decision), aauthorize=AsyncMock(side_effect=decision), filter_resources=MagicMock(return_value=list(selected)), calls=calls)


@pytest.fixture(params=[False, True], ids=["sync", "async"])
def select(request):
    def run(name, *, app=None, policy=None, context=None):
        kwargs = {"app_config": app or config(), "context": context or {"user_id": "viewer", "user_role": "user"}, "authorization_provider": policy}
        return asyncio.run(authorize_model_name_async(name, **kwargs)) if request.param else authorize_model_name(name, **kwargs)

    return run


@pytest.mark.parametrize("name", [None, "private", "missing", "basic"])
def test_real_policy_resolves_default_and_known_denied_model(select, name):
    assert select(name) == "basic"


def test_fallback_intersects_catalog_and_rechecks_use(select):
    policy = provider()
    assert select("private", policy=policy) == "basic"
    assert [(r.resource, r.action, r.target) for r in policy.calls] == [("model", "use", n) for n in ("private", "visible", "basic")]
    assert all(r.principal.user_id == "viewer" for r in policy.calls)


@pytest.mark.parametrize("fail_closed", [True, False])
def test_explicit_denial_never_fail_opens(select, fail_closed):
    app = config()
    app.authorization.fail_closed = fail_closed
    with pytest.raises(ModelAuthorizationError):
        select("private", app=app, policy=provider(allowed=()))


@pytest.mark.parametrize("fail_closed", [True, False])
@pytest.mark.parametrize("fault", ["decision", "malformed", "missing_provider", "bad_class"])
def test_provider_fault_obeys_valid_fail_flag(select, fail_closed, fault):
    app, policy = config(), provider()
    app.authorization.fail_closed = fail_closed
    if fault == "missing_provider":
        app.authorization.provider = None
        policy = None
    elif fault == "bad_class":
        app.authorization.provider.use = "nonexistent_model_policy:Provider"
        policy = None
    else:
        for method in (policy.authorize, policy.aauthorize):
            method.side_effect = RuntimeError("SECRET_DETAIL") if fault == "decision" else None
            method.return_value = AuthzDecision(allow="yes")
    if fail_closed:
        with pytest.raises(ModelAuthorizationError, match="provider unavailable"):
            select("private", app=app, policy=policy)
    else:
        assert select("private", app=app, policy=policy) == "private"


@pytest.mark.parametrize("fault", ["filter_error", "filter_shape", "fallback_error"])
def test_fault_after_explicit_deny_cannot_resurrect_denied_model(select, fault):
    app, policy = config(), provider()
    app.authorization.fail_closed = False
    if fault == "filter_error":
        policy.filter_resources.side_effect = RuntimeError("SECRET_DETAIL")
    elif fault == "filter_shape":
        policy.filter_resources.return_value = "basic"
    else:
        for method in (policy.authorize, policy.aauthorize):
            method.side_effect = [AuthzDecision(allow=False), RuntimeError("SECRET_DETAIL")]
    with pytest.raises(ModelAuthorizationError):
        select("private", app=app, policy=policy)


@pytest.mark.parametrize("auth", [None, MagicMock()])
def test_unavailable_config_is_not_disabled(select, auth):
    with pytest.raises(ModelAuthorizationError, match="configuration"):
        select("private", app=SimpleNamespace(authorization=auth))


def test_disabled_policy_preserves_legacy_none_without_loading_catalog(select):
    assert select(None, app=SimpleNamespace(authorization=AuthorizationConfig())) is None


def test_invalid_identity_does_not_fail_open(select):
    app = config()
    app.authorization.fail_closed = False
    with pytest.raises(ModelAuthorizationError, match="identity"):
        select("private", app=app, context={"authz_attributes": "forged"})


@pytest.mark.parametrize("error", [GraphBubbleUp, asyncio.CancelledError])
def test_control_flow_propagates(select, error):
    app, policy = config(), provider()
    app.authorization.fail_closed = False
    for method in (policy.authorize, policy.aauthorize):
        method.side_effect = error()
    with pytest.raises(error):
        select("private", app=app, policy=policy)


def test_async_provider_loop_affinity_and_principal_snapshot(monkeypatch):
    app, made = config(), []
    context = {"user_id": "viewer", "authz_attributes": {"team": ["before"]}}

    class Provider:
        name = "loop"

        def __init__(self):
            self.loop = asyncio.get_running_loop()
            self.thread = threading.get_ident()
            made.append(self)

        def authorize(self, request):
            raise AssertionError("async path must use aauthorize")

        async def aauthorize(self, request):
            assert asyncio.get_running_loop() is self.loop
            assert request.principal.attributes == {"team": ["before"]}
            context["authz_attributes"]["team"][:] = ["after"]
            return AuthzDecision(allow=request.target == "basic")

        def filter_resources(self, principal, resource_type, candidates):
            assert threading.get_ident() != self.thread
            return ["basic"]

    module = ModuleType("model_authorization_loop_test")
    module.Provider = Provider
    monkeypatch.setitem(sys.modules, module.__name__, module)
    app.authorization.provider.use = module.__name__ + ":Provider"
    app.authorization.provider.config = {}
    for _ in range(2):
        context["authz_attributes"]["team"][:] = ["before"]
        assert asyncio.run(authorize_model_name_async("private", context=context, app_config=app)) == "basic"
    assert len(made) == 2
    assert made[0].loop is not made[1].loop


@pytest.mark.parametrize("bootstrap", [False, True])
@pytest.mark.parametrize("allowed", [True, False])
def test_real_lead_factory_checks_before_llm_construction(monkeypatch, bootstrap, allowed):
    from deerflow.agents.lead_agent import agent as module

    app = config()
    if not allowed:
        app.authorization.provider.config["roles"]["user"]["models"]["allow"] = []
    monkeypatch.setattr(module, "load_agent_config", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "_load_enabled_available_skills", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "build_middlewares", lambda *args, **kwargs: [])
    monkeypatch.setattr(module, "apply_prompt_template", lambda **kwargs: "test")
    monkeypatch.setattr("deerflow.tools.get_available_tools", lambda **kwargs: [])
    model = MagicMock(return_value="llm")
    monkeypatch.setattr(module, "create_chat_model", model)
    monkeypatch.setattr(module, "create_agent", lambda **kwargs: kwargs)
    cfg = {"configurable": {"model_name": "private", "user_role": "user", "user_id": "viewer", "is_bootstrap": bootstrap}}
    if allowed:
        result = module._make_lead_agent(cfg, app_config=app)
        assert result["model"] == "llm"
        assert model.call_args.kwargs["name"] == "basic"
        assert cfg["metadata"]["model_name"] == "basic"
    else:
        with pytest.raises(ModelAuthorizationError):
            module._make_lead_agent(cfg, app_config=app)
        model.assert_not_called()


def test_real_subagent_initialization_gates_configured_override(monkeypatch):
    from deerflow.subagents.config import SubagentConfig
    from deerflow.trace_context import request_trace_context

    name = "_v21_model_policy_executor"
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "packages/harness/deerflow/subagents/executor.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    try:
        app = config()
        with request_trace_context():
            executor = module.SubagentExecutor(SubagentConfig(name="test", description="test", model="private"), tools=[], app_config=app, user_id="viewer", user_role="user")
        monkeypatch.setattr(executor, "_load_skills", AsyncMock(return_value=[]))
        asyncio.run(executor._build_initial_state("task"))
        assert executor.model_name == "basic"
        app.authorization.provider.config["roles"]["user"]["models"]["allow"] = []
        with pytest.raises(ModelAuthorizationError):
            asyncio.run(executor._build_initial_state("retry"))
    finally:
        module._shutdown_isolated_subagent_loop()
        module.atexit.unregister(module._shutdown_isolated_subagent_loop)
