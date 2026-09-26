"""Restored sandbox ids must stay within the authenticated user/thread scope."""

import threading
from collections import OrderedDict
from types import SimpleNamespace

import pytest
from langgraph.types import Overwrite

from deerflow.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider
from deerflow.sandbox import middleware as middleware_module
from deerflow.sandbox import tools
from deerflow.sandbox.local.local_sandbox_provider import LocalSandboxProvider
from deerflow.sandbox.middleware import SandboxMiddleware
from deerflow.sandbox.sandbox_provider import SandboxProvider

pytestmark = pytest.mark.usefixtures("disabled_sandbox_authorization")


class _ScopedProvider:
    def __init__(self):
        self.sandbox = SimpleNamespace(id="own")
        self.foreign_sandbox = SimpleNamespace(id="foreign")
        self.acquires = []

    def get_scoped(self, sandbox_id, *, thread_id, user_id):
        assert (thread_id, user_id) == ("thread-2", "user-2")
        return self.sandbox if sandbox_id == "own" else None

    def acquire(self, thread_id, *, user_id=None):
        self.acquires.append((thread_id, user_id))
        return "own"

    async def acquire_async(self, thread_id, *, user_id=None):
        return self.acquire(thread_id, user_id=user_id)

    def get(self, sandbox_id):
        return self.sandbox if sandbox_id == "own" else self.foreign_sandbox if sandbox_id == "foreign" else None


def _runtime():
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "foreign"}},
        context={"thread_id": "thread-2", "user_id": "user-2"},
        config={},
    )


def _mock_execution_guards(monkeypatch, provider):
    monkeypatch.setattr(tools, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(tools, "_check_employee_execution", lambda _runtime: None)
    monkeypatch.setattr(tools, "_validate_employee_sandbox", lambda *_args: None)


def test_unscoped_provider_hook_fails_closed():
    class _CustomProvider(SandboxProvider):
        def acquire(self, thread_id=None, *, user_id=None):
            return "custom"

        def get(self, sandbox_id):
            return None

        def release(self, sandbox_id):
            pass

    provider = _CustomProvider()
    assert provider.get_scoped("foreign", thread_id="thread-2", user_id="user-2") is None


def test_aio_provider_scoped_lookup_rejects_foreign_reference():
    provider = object.__new__(AioSandboxProvider)
    provider._lock = threading.RLock()
    provider._thread_sandboxes = {("user-1", "thread-1"): "foreign"}
    provider._sandboxes = {"foreign": SimpleNamespace(id="foreign")}
    provider._last_activity = {}

    assert provider.get_scoped("foreign", thread_id="thread-2", user_id="user-2") is None
    assert provider.get_scoped("foreign", thread_id="thread-1", user_id="user-1").id == "foreign"


def test_local_provider_scoped_lookup_rejects_foreign_reference():
    provider = object.__new__(LocalSandboxProvider)
    provider._lock = threading.RLock()
    provider._thread_sandboxes = OrderedDict({("user-1", "thread-1"): SimpleNamespace(id="local:user-1:thread-1")})

    assert provider.get_scoped("local:user-1:thread-1", thread_id="thread-2", user_id="user-2") is None
    assert provider.get_scoped("local:user-1:thread-1", thread_id="thread-1", user_id="user-1").id == "local:user-1:thread-1"


def test_sync_tool_replaces_foreign_restored_reference(monkeypatch):
    provider = _ScopedProvider()
    _mock_execution_guards(monkeypatch, provider)
    runtime = _runtime()

    assert tools.ensure_sandbox_initialized(runtime) is provider.sandbox
    assert provider.acquires == [("thread-2", "user-2")]
    assert runtime.state["sandbox"] == {"sandbox_id": "own"}


@pytest.mark.asyncio
async def test_async_tool_replaces_foreign_restored_reference(monkeypatch):
    provider = _ScopedProvider()
    _mock_execution_guards(monkeypatch, provider)
    runtime = _runtime()

    assert await tools.ensure_sandbox_initialized_async(runtime) is provider.sandbox
    assert provider.acquires == [("thread-2", "user-2")]
    assert runtime.state["sandbox"] == {"sandbox_id": "own"}


def test_replaced_reference_uses_langgraph_overwrite():
    from langchain_core.messages import ToolMessage

    result = SandboxMiddleware._attach_sandbox_update(
        ToolMessage(content="ok", tool_call_id="test"),
        "own",
        overwrite=True,
    )
    assert isinstance(result.update["sandbox"], Overwrite)
    assert result.update["sandbox"].value == {"sandbox_id": "own"}


def test_eager_agent_replaces_foreign_restored_reference(monkeypatch):
    provider = _ScopedProvider()
    monkeypatch.setattr(middleware_module, "get_sandbox_provider", lambda: provider)
    runtime = _runtime()
    runtime.context["sandbox_id"] = "own"

    result = SandboxMiddleware(lazy_init=False).before_agent(runtime.state, runtime)

    assert isinstance(result["sandbox"], Overwrite)
    assert result["sandbox"].value == {"sandbox_id": "own"}
    assert provider.acquires == [("thread-2", "user-2")]


@pytest.mark.asyncio
async def test_async_eager_agent_replaces_foreign_restored_reference(monkeypatch):
    provider = _ScopedProvider()
    monkeypatch.setattr(middleware_module, "get_sandbox_provider", lambda: provider)
    runtime = _runtime()
    runtime.context["sandbox_id"] = "own"

    result = await SandboxMiddleware(lazy_init=False).abefore_agent(runtime.state, runtime)

    assert isinstance(result["sandbox"], Overwrite)
    assert result["sandbox"].value == {"sandbox_id": "own"}
    assert provider.acquires == [("thread-2", "user-2")]


def test_agent_cleanup_does_not_release_foreign_restored_reference(monkeypatch):
    released = []
    provider = _ScopedProvider()
    provider.release = released.append
    monkeypatch.setattr(middleware_module, "get_sandbox_provider", lambda: provider)

    SandboxMiddleware().after_agent({"sandbox": {"sandbox_id": "foreign"}}, _runtime())

    assert released == []


@pytest.mark.asyncio
async def test_async_agent_cleanup_does_not_release_foreign_restored_reference(monkeypatch):
    released = []
    provider = _ScopedProvider()
    provider.release = released.append
    monkeypatch.setattr(middleware_module, "get_sandbox_provider", lambda: provider)

    await SandboxMiddleware().aafter_agent({"sandbox": {"sandbox_id": "foreign"}}, _runtime())

    assert released == []
