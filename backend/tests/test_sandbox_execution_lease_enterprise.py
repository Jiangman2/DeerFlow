"""Enterprise adaptation of upstream execution-scoped sandbox leases."""

from __future__ import annotations

import pytest
from langchain.tools import ToolRuntime

from deerflow.runtime.runs.worker import _build_runtime_context, _install_runtime_context
from deerflow.sandbox.middleware import SandboxMiddleware
from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.sandbox_provider import SandboxProvider, reset_sandbox_provider, set_sandbox_provider
from deerflow.sandbox.tools import ensure_sandbox_initialized_async

pytestmark = pytest.mark.usefixtures("disabled_sandbox_authorization")


class _Client(Sandbox):
    def execute_command(self, command, env=None, timeout=None):
        return command

    def read_file(self, path):
        return ""

    def download_file(self, path):
        return b""

    def list_dir(self, path, max_depth=2):
        return []

    def write_file(self, path, content, append=False):
        return None

    def glob(self, path, pattern, *, include_dirs=False, max_results=200):
        return [], False

    def grep(self, path, pattern, *, glob=None, literal=False, case_sensitive=False, max_results=100):
        return [], False

    def update_file(self, path, content):
        return None


class _Provider(SandboxProvider):
    def __init__(self):
        self.client = _Client("shared")
        self.acquires: list[tuple[str, str]] = []
        self.releases: list[str] = []

    def acquire(self, thread_id=None, *, user_id=None):
        self.acquires.append((thread_id, user_id))
        return "shared"

    def get(self, sandbox_id):
        return self.client if sandbox_id == "shared" else None

    def get_scoped(self, sandbox_id, *, thread_id, user_id):
        if sandbox_id == "shared" and (thread_id, user_id) in self.acquires:
            return self.client
        return None

    def release(self, sandbox_id):
        self.releases.append(sandbox_id)


def _runtime(state, context, call_id):
    return ToolRuntime(
        state=state,
        context=context,
        config={"configurable": {}},
        stream_writer=lambda _: None,
        tools=[],
        tool_call_id=call_id,
        store=None,
    )


@pytest.mark.anyio
async def test_parent_and_subagent_share_client_until_last_execution_releases() -> None:
    provider = _Provider()
    set_sandbox_provider(provider)
    middleware = SandboxMiddleware()
    try:
        parent_state: dict = {}
        parent_context = {"thread_id": "thread-1", "user_id": "user-1"}
        parent = _runtime(parent_state, parent_context, "parent")
        await middleware.abefore_agent(parent_state, parent)
        assert await ensure_sandbox_initialized_async(parent) is provider.client

        child_state = {"sandbox": dict(parent_state["sandbox"])}
        child_context = {"thread_id": "thread-1", "user_id": "user-1"}
        child = _runtime(child_state, child_context, "child")
        await middleware.abefore_agent(child_state, child)
        assert await ensure_sandbox_initialized_async(child) is provider.client

        assert parent_context["sandbox_lease_owner_id"] != child_context["sandbox_lease_owner_id"]
        assert provider.acquires == [("thread-1", "user-1")]

        await middleware.aafter_agent(parent_state, parent)
        assert provider.releases == []
        await middleware.aafter_agent(child_state, child)
        assert provider.releases == ["shared"]
    finally:
        reset_sandbox_provider()


def test_external_run_context_cannot_choose_sandbox_lease_owner() -> None:
    caller_context = {
        "user_id": "user-1",
        "sandbox_lease_owner_id": "attacker-owner",
        "sandbox_command_scope_id": "attacker-scope",
    }
    runtime_context = _build_runtime_context("thread-1", "run-1", caller_context)
    assert runtime_context == {"thread_id": "thread-1", "run_id": "run-1", "user_id": "user-1"}

    config = {"context": dict(caller_context)}
    _install_runtime_context(config, runtime_context)
    assert "sandbox_lease_owner_id" not in config["context"]
    assert "sandbox_command_scope_id" not in config["context"]


@pytest.mark.anyio
async def test_execution_lease_replaces_foreign_checkpoint_reference() -> None:
    provider = _Provider()
    set_sandbox_provider(provider)
    middleware = SandboxMiddleware()
    try:
        state = {"sandbox": {"sandbox_id": "foreign"}}
        context = {"thread_id": "thread-2", "user_id": "user-2"}
        runtime = _runtime(state, context, "replace")
        await middleware.abefore_agent(state, runtime)

        assert await ensure_sandbox_initialized_async(runtime) is provider.client
        assert state["sandbox"] == {"sandbox_id": "shared"}
        assert provider.acquires == [("thread-2", "user-2")]
        assert context["sandbox_id"] == "shared"

        await middleware.aafter_agent(state, runtime)
        assert provider.releases == ["shared"]
    finally:
        reset_sandbox_provider()
