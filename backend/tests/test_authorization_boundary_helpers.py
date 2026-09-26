"""Enterprise-safe foundations; not a claim that every runtime is wired yet."""

import asyncio
import threading
from types import SimpleNamespace

import pytest
from langchain_core.tools import StructuredTool

from deerflow.authz.adapter import GuardrailAuthorizationAdapter
from deerflow.authz.enforcement import filter_tools_by_authorization
from deerflow.authz.provider import AuthzDecision, Principal
from deerflow.authz.runtime import construct_authorization_provider, resolve_authorization_provider_spec
from deerflow.authz.tool_filter import apply_tool_authorization
from deerflow.config.authorization_config import AuthorizationConfig, AuthorizationProviderConfig
from deerflow.guardrails.provider import GuardrailRequest


class RecordingProvider:
    name = "recording"

    def __init__(self, *, selected=None):
        self.selected = selected
        self.principals = []

    def authorize(self, request):
        self.principals.append(request.principal)
        return AuthzDecision(allow=request.target == "read_file")

    async def aauthorize(self, request):
        return self.authorize(request)

    def filter_resources(self, principal, resource_type, candidates):
        self.principals.append(principal)
        return self.selected


def tool(name):
    return StructuredTool.from_function(lambda: "not executed", name=name, description="test")


@pytest.mark.parametrize("result", [None, "read_file", ("read_file",), ["read_file", 1], {"read_file": True}])
@pytest.mark.parametrize("fail_closed", [True, False])
def test_malformed_filter_is_never_partially_trusted(result, fail_closed):
    tools = [tool("read_file"), tool("bash")]
    filtered = filter_tools_by_authorization(tools, provider=RecordingProvider(selected=result), principal=Principal(role="employee_user"), fail_closed=fail_closed)
    assert filtered == ([] if fail_closed else tools)
    assert [t.name for t in tools] == ["read_file", "bash"]


def test_filter_never_injects_tool_or_reorders_same_objects():
    tools = [tool("read_file"), tool("bash"), tool("read_file")]
    filtered = filter_tools_by_authorization(tools, provider=RecordingProvider(selected=["admin_tool", "read_file"]), principal=Principal(role="employee_user"), fail_closed=True)
    assert filtered == [tools[0], tools[2]]
    assert all(item is tools[index] for item, index in zip(filtered, (0, 2), strict=True))


def test_layer_identity_matches_adapter_without_role_translation():
    context = {"user_id": "consumer-b", "user_role": None, "oauth_provider": "enterprise", "oauth_id": "subject-b", "channel_user_id": "channel-b", "is_internal": "true", "authz_attributes": {"department": "engineering"}}
    provider = RecordingProvider(selected=["read_file"])
    config = SimpleNamespace(authorization=AuthorizationConfig(enabled=True, default_role="employee_user"))
    tools = [tool("read_file"), tool("bash")]
    filtered, resolved = apply_tool_authorization(tools, context=context, app_config=config, authorization_provider=provider)
    assert resolved is provider
    assert filtered == [tools[0]]
    principal = provider.principals[0]
    assert principal.user_id == "consumer-b"
    assert principal.role == "employee_user"
    assert principal.is_internal is False
    adapter = GuardrailAuthorizationAdapter(provider, default_role="employee_user")
    request = GuardrailRequest(tool_name="read_file", tool_input={}, **context)
    assert adapter.evaluate(request).allow
    assert provider.principals[-1] == principal
    context["authz_attributes"]["department"] = "changed"
    assert principal.attributes == {"department": "engineering"}


def test_disabled_wrapper_keeps_identity_and_never_loads_provider(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("disabled authorization imported provider")

    monkeypatch.setattr("deerflow.authz.tool_filter.resolve_authorization_provider", unexpected)
    config = SimpleNamespace(authorization=AuthorizationConfig(enabled=False, provider=AuthorizationProviderConfig(use="unavailable:Provider")))
    tools = [tool("read_file")]
    filtered, provider = apply_tool_authorization(tools, context={}, app_config=config)
    assert filtered is tools
    assert provider is None


def test_split_discovery_constructs_loop_bound_provider_on_caller_loop(monkeypatch):
    class LoopProvider(RecordingProvider):
        def __init__(self, *, selected=None):
            super().__init__(selected=selected)
            self.loop = asyncio.get_running_loop()
            self.thread = threading.get_ident()

    discovery_threads = []

    def resolve(path, *, expected_type):
        assert path == "test:LoopProvider"
        assert expected_type is type
        discovery_threads.append(threading.get_ident())
        return LoopProvider

    monkeypatch.setattr("deerflow.authz.runtime.resolve_variable", resolve)
    config = AuthorizationConfig(enabled=True, provider=AuthorizationProviderConfig(use="test:LoopProvider", config={"selected": ["read_file"]}))

    async def scenario():
        spec = await asyncio.to_thread(resolve_authorization_provider_spec, config)
        provider = construct_authorization_provider(spec, config)
        assert provider.loop is asyncio.get_running_loop()
        assert provider.thread == threading.get_ident()
        assert discovery_threads == [discovery_threads[0]]
        assert discovery_threads[0] != provider.thread
        assert provider.selected == ["read_file"]

    asyncio.run(scenario())
