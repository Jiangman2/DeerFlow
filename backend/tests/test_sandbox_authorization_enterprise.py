"""Sandbox policy boundary: pinned 2.1 semantics plus enterprise fail-closed input."""

import asyncio
import contextvars
import sys
import threading
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.prebuilt.tool_node import ToolCallRequest

from deerflow.agents.middlewares.read_before_write_middleware import ReadBeforeWriteMiddleware
from deerflow.authz import sandbox_authz as gate
from deerflow.authz.provider import AuthzDecision
from deerflow.config.authorization_config import AuthorizationConfig, AuthorizationProviderConfig
from deerflow.sandbox import middleware as middleware_module
from deerflow.sandbox import tools
from deerflow.sandbox.exceptions import SandboxAuthorizationError, SandboxNotFoundError


def _config(*, enabled=True, allow=True, fail_closed=True):
    return SimpleNamespace(
        authorization=AuthorizationConfig(
            enabled=enabled,
            fail_closed=fail_closed,
            provider=AuthorizationProviderConfig(
                use="deerflow.authz.rbac:RbacAuthorizationProvider",
                config={"roles": {"user": {"sandbox": {"allow": allow}}}},
            ),
        )
    )


def _runtime(*, existing=True, user="alice"):
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "own"} if existing else None},
        context={"thread_id": "thread-1", "user_id": user, "user_role": "user"},
        config={},
    )


@pytest.fixture
def setup_gate(monkeypatch):
    config = _config()
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    provider = MagicMock()
    sandbox = MagicMock(id="own")
    sandbox.list_dir.return_value = []
    provider.get.return_value = sandbox
    provider.get_scoped.return_value = sandbox
    provider.acquire.return_value = "own"
    provider.acquire_async = AsyncMock(return_value="own")
    monkeypatch.setattr(tools, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(middleware_module, "get_sandbox_provider", lambda: provider)
    employee = MagicMock()
    mounts = MagicMock()
    monkeypatch.setattr(tools, "_check_employee_execution", employee)
    monkeypatch.setattr(tools, "_validate_employee_sandbox", mounts)
    monkeypatch.setattr(tools, "is_local_sandbox", lambda runtime: False)
    return config, provider, sandbox, employee, mounts


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("allow", [False, True])
def test_live_gate_before_cold_or_reused_client(setup_gate, async_mode, existing, allow):
    config, provider, sandbox, employee, mounts = setup_gate
    config.authorization = _config(allow=allow).authorization
    runtime = _runtime(existing=existing)

    def invoke():
        return asyncio.run(tools.ensure_sandbox_initialized_async(runtime)) if async_mode else tools.ensure_sandbox_initialized(runtime)

    if allow:
        assert invoke() is sandbox
        employee.assert_called_once_with(runtime)
        mounts.assert_called_once_with(provider, "own", runtime)
    else:
        with pytest.raises(SandboxAuthorizationError, match="not permitted"):
            invoke()
        assert not provider.mock_calls
        assert runtime.state["sandbox"] == ({"sandbox_id": "own"} if existing else None)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("existing", [False, True])
def test_eager_denial_does_not_allocate_or_reuse(setup_gate, async_mode, existing):
    config, provider, *_ = setup_gate
    config.authorization = _config(allow=False).authorization
    runtime = _runtime(existing=existing)
    mw = middleware_module.SandboxMiddleware(lazy_init=False)
    result = asyncio.run(mw.abefore_agent(runtime.state, runtime)) if async_mode else mw.before_agent(runtime.state, runtime)
    assert result is None
    assert not provider.mock_calls


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("config", [None, SimpleNamespace(), SimpleNamespace(authorization=MagicMock())])
def test_absent_or_mock_policy_never_disables_authorization(async_mode, config):
    with pytest.raises(SandboxAuthorizationError, match="configuration is unavailable"):
        if async_mode:
            asyncio.run(gate.authorize_sandbox_execution_async(context={}, app_config=config))
        else:
            gate.authorize_sandbox_execution(context={}, app_config=config)


@pytest.mark.parametrize("error", [FileNotFoundError, PermissionError, ValueError])
@pytest.mark.parametrize("async_mode", [False, True])
def test_config_read_failure_is_closed_and_sanitized(monkeypatch, error, async_mode):
    monkeypatch.setattr("deerflow.config.get_app_config", MagicMock(side_effect=error("SECRET_CONFIG_VALUE")))
    with pytest.raises(SandboxAuthorizationError) as exc:
        if async_mode:
            asyncio.run(gate.safe_app_config_async())
        else:
            gate.safe_app_config()
    assert "SECRET" not in str(exc.value)


@pytest.mark.parametrize("async_mode", [False, True])
def test_explicit_disabled_preserves_no_provider_compatibility(async_mode):
    config = _config(enabled=False)
    config.authorization.provider = None
    if async_mode:
        asyncio.run(gate.authorize_sandbox_execution_async(context={}, app_config=config))
    else:
        gate.authorize_sandbox_execution(context={}, app_config=config)


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("fail_closed", [False, True])
@pytest.mark.parametrize("failure", ["resolution", "exception", "malformed", "deny"])
def test_error_policy_and_explicit_denial(monkeypatch, async_mode, fail_closed, failure):
    config = _config(fail_closed=fail_closed)
    # Missing-provider branch lets sync/async use the same test resolver.
    config.authorization.provider = None
    provider = MagicMock()
    provider.authorize.return_value = AuthzDecision(allow=False) if failure == "deny" else None
    provider.aauthorize = AsyncMock(return_value=provider.authorize.return_value)
    if failure == "exception":
        provider.authorize.side_effect = RuntimeError("policy down")
        provider.aauthorize.side_effect = RuntimeError("policy down")
    resolver = MagicMock(return_value=provider, side_effect=ValueError("bad provider") if failure == "resolution" else None)
    monkeypatch.setattr(gate, "resolve_authorization_provider", resolver)

    def invoke():
        if async_mode:
            return asyncio.run(gate.authorize_sandbox_execution_async(context={}, app_config=config))
        return gate.authorize_sandbox_execution(context={}, app_config=config)

    if fail_closed or failure == "deny":
        with pytest.raises(SandboxAuthorizationError):
            invoke()
    else:
        invoke()


@pytest.mark.parametrize("async_mode", [False, True])
def test_graph_control_flow_is_not_a_policy_error(monkeypatch, async_mode):
    config = _config(fail_closed=False)
    config.authorization.provider = None
    provider = MagicMock()
    provider.authorize.side_effect = GraphBubbleUp()
    provider.aauthorize = AsyncMock(side_effect=GraphBubbleUp())
    monkeypatch.setattr(gate, "resolve_authorization_provider", lambda _: provider)
    with pytest.raises(GraphBubbleUp):
        if async_mode:
            asyncio.run(gate.authorize_sandbox_execution_async(context={}, app_config=config))
        else:
            gate.authorize_sandbox_execution(context={}, app_config=config)


def _counting_policy(monkeypatch, config, *, allow=True):
    config.authorization.provider = None
    policy = MagicMock()
    policy.authorize.return_value = AuthzDecision(allow=allow)
    policy.aauthorize = AsyncMock(return_value=AuthzDecision(allow=allow))
    monkeypatch.setattr(gate, "resolve_authorization_provider", lambda _: policy)
    return policy


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("name", ["read_file", "write_file", "str_replace"])
def test_composed_tool_authorizes_once_and_retains_employee_checks(setup_gate, monkeypatch, async_mode, name):
    config, _, sandbox, employee, mounts = setup_gate
    policy = _counting_policy(monkeypatch, config)
    runtime = _runtime()
    path = "/mnt/user-data/outputs/test.md"
    sandbox.read_file.side_effect = FileNotFoundError(path) if name != "read_file" else None
    sandbox.read_file.return_value = "hello"
    request = ToolCallRequest(tool_call={"name": name, "args": {"path": path}, "id": "call"}, tool=None, state={}, runtime=runtime)
    mw = ReadBeforeWriteMiddleware()

    def body(runtime):
        assert tools.ensure_sandbox_initialized(runtime) is sandbox
        return "hello"

    def sync_handler(_):
        return ToolMessage(content=body(runtime), tool_call_id="call", name=name)

    async def async_handler(_):
        return ToolMessage(content=await tools._run_sync_tool_after_async_sandbox_init(body, runtime), tool_call_id="call", name=name)

    result = asyncio.run(mw.awrap_tool_call(request, async_handler)) if async_mode else mw.wrap_tool_call(request, sync_handler)
    assert result.status != "error"
    assert employee.call_count >= 2
    assert mounts.call_count >= 2
    if async_mode:
        policy.aauthorize.assert_awaited_once()
        policy.authorize.assert_not_called()
    else:
        policy.authorize.assert_called_once()
        policy.aauthorize.assert_not_called()


@pytest.mark.parametrize("async_mode", [False, True])
def test_read_gate_does_not_swallow_denial(setup_gate, monkeypatch, async_mode):
    config, provider, *_ = setup_gate
    _counting_policy(monkeypatch, config, allow=False)
    runtime = _runtime()
    request = ToolCallRequest(tool_call={"name": "write_file", "args": {"path": "/test"}, "id": "call"}, tool=None, state={}, runtime=runtime)
    handler = AsyncMock() if async_mode else MagicMock()
    mw = ReadBeforeWriteMiddleware()
    result = asyncio.run(mw.awrap_tool_call(request, handler)) if async_mode else mw.wrap_tool_call(request, handler)
    assert result.status == "error"
    assert "not permitted" in result.content
    handler.assert_not_called()
    assert not provider.mock_calls


def test_scope_cannot_grant_other_runtime_user_or_outlive_invocation(setup_gate, monkeypatch):
    config, *_ = setup_gate
    policy = _counting_policy(monkeypatch, config)
    runtime = _runtime()
    with tools.sandbox_authorization_scope(runtime):
        assert tools._sandbox_authorization_checked(runtime)
        assert not tools._sandbox_authorization_checked(_runtime())
        inherited = contextvars.copy_context()
        runtime.context["user_id"] = "bob"
        assert not tools._sandbox_authorization_checked(runtime)
        runtime.context["user_id"] = "alice"
    assert not inherited.run(tools._sandbox_authorization_checked, runtime)
    with tools.sandbox_authorization_scope(runtime):
        pass
    assert policy.authorize.call_count == 2


def test_each_new_call_rechecks_revocation_and_employee_withdrawal(setup_gate, monkeypatch):
    config, _, _, employee, _ = setup_gate
    policy = _counting_policy(monkeypatch, config)
    runtime = _runtime()
    with tools.sandbox_authorization_scope(runtime):
        tools.ensure_sandbox_initialized(runtime)
        employee.side_effect = PermissionError("publication withdrawn")
        with pytest.raises(PermissionError, match="withdrawn"):
            tools.ensure_sandbox_initialized(runtime)
    policy.authorize.return_value = AuthzDecision(allow=False)
    with pytest.raises(SandboxAuthorizationError):
        tools.ensure_sandbox_initialized(runtime)


def test_legacy_lookup_checks_scope_and_published_mounts(setup_gate):
    _, provider, sandbox, employee, mounts = setup_gate
    runtime = _runtime()
    assert tools.sandbox_from_runtime(runtime) is sandbox
    employee.assert_called_once()
    mounts.assert_called_once()
    provider.get_scoped.return_value = None
    with pytest.raises(SandboxNotFoundError):
        tools.sandbox_from_runtime(runtime)
    provider.get.assert_not_called()


def test_async_provider_import_off_loop_construction_on_loop(setup_gate, monkeypatch):
    config, *_ = setup_gate
    module = ModuleType("sandbox_loop_affine_test")
    records = []

    class LoopProvider:
        name = "loop-affine"

        def __init__(self):
            self.loop = asyncio.get_running_loop()

        def authorize(self, request):
            raise AssertionError("sync policy must not run")

        async def aauthorize(self, request):
            assert self.loop is asyncio.get_running_loop()
            records.append(request)
            return AuthzDecision(allow=True)

        def filter_resources(self, principal, resource_type, candidates):
            return candidates

    module.Provider = LoopProvider
    monkeypatch.setitem(sys.modules, module.__name__, module)
    config.authorization.provider = AuthorizationProviderConfig(use=module.__name__ + ":Provider")
    owner_thread = threading.get_ident()
    original = gate.resolve_authorization_provider_spec

    def discover(conf):
        assert threading.get_ident() != owner_thread
        return original(conf)

    def load():
        assert threading.get_ident() != owner_thread
        return config

    monkeypatch.setattr(gate, "resolve_authorization_provider_spec", discover)
    monkeypatch.setattr("deerflow.config.get_app_config", load)
    runtime = _runtime()
    result = asyncio.run(tools.ls_tool.coroutine(runtime=runtime, description="list", path="/mnt/user-data/workspace"))
    assert result == "(empty)"
    assert len(records) == 1
    assert (records[0].resource, records[0].action, records[0].target) == ("sandbox", "execute", "*")
    assert records[0].principal.user_id == "alice"


def test_concurrent_async_scopes_do_not_share_grants(setup_gate, monkeypatch):
    config, *_ = setup_gate
    policy = _counting_policy(monkeypatch, config)

    async def decide(request):
        await asyncio.sleep(0)
        return AuthzDecision(allow=request.principal.user_id == "alice")

    policy.aauthorize.side_effect = decide

    async def call(user):
        runtime = _runtime(user=user)
        runtime.context["__sandbox_authorization_checked"] = True  # untrusted flag has no effect
        try:
            async with tools.sandbox_authorization_scope_async(runtime):
                await tools.ensure_sandbox_initialized_async(runtime)
                await asyncio.to_thread(tools.ensure_sandbox_initialized, runtime)
            return "allowed"
        except SandboxAuthorizationError:
            return "denied"

    async def run():
        return await asyncio.gather(call("alice"), call("bob"))

    assert asyncio.run(run()) == ["allowed", "denied"]
    assert policy.aauthorize.await_count == 2
    policy.authorize.assert_not_called()


def test_scoped_config_is_authoritative_in_async_worker():
    from deerflow.config.app_config import AppConfig, pop_current_app_config, push_current_app_config

    config = AppConfig.model_construct(authorization=_config(allow=False).authorization)

    async def run():
        push_current_app_config(config)
        try:
            assert await gate.safe_app_config_async() is config
            with pytest.raises(SandboxAuthorizationError):
                await gate.authorize_sandbox_execution_async(context={}, app_config=await gate.safe_app_config_async())
        finally:
            pop_current_app_config()

    asyncio.run(run())


def test_identity_attribute_change_invalidates_scoped_grant(setup_gate):
    runtime = _runtime()
    runtime.context["authz_attributes"] = {"tenant": {"id": "first"}}
    with tools.sandbox_authorization_scope(runtime):
        runtime.context["authz_attributes"]["tenant"]["id"] = "second"
        assert not tools._sandbox_authorization_checked(runtime)


@pytest.mark.parametrize("async_mode", [False, True])
def test_identity_changed_while_policy_runs_does_not_grant_new_principal(setup_gate, monkeypatch, async_mode):
    config, provider, *_ = setup_gate
    policy = _counting_policy(monkeypatch, config)
    runtime = _runtime()

    def decide(request):
        assert request.principal.user_id == "alice"
        runtime.context["user_id"] = "bob"
        return AuthzDecision(allow=True)

    policy.authorize.side_effect = decide
    policy.aauthorize.side_effect = decide
    with pytest.raises(SandboxAuthorizationError, match="context changed"):
        if async_mode:
            asyncio.run(tools.ensure_sandbox_initialized_async(runtime))
        else:
            tools.ensure_sandbox_initialized(runtime)
    assert not provider.mock_calls
