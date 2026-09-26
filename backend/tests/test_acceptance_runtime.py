"""Real parent checklist + real lease manager; no model or network."""

import asyncio
import threading
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from deerflow.config.authorization_config import AuthorizationConfig, AuthorizationProviderConfig
from deerflow.sandbox.lease import discard_sandbox_lease_manager, get_sandbox_lease_manager
from deerflow.subagents import acceptance_runtime as boundary


def runtime(tmp_path):
    return SimpleNamespace(
        context={"thread_id": "business-thread", "user_id": "alice", "sandbox_lease_owner_id": "parent-holder", "sandbox_command_scope_id": "parent-shell", "sandbox_id": "stale"},
        config={"configurable": {"thread_id": "subgraph-not-the-business-thread"}},
        state={"sandbox": {"sandbox_id": "stale"}, "thread_data": {"workspace_path": str(tmp_path / "workspace"), "outputs_path": str(tmp_path / "outputs")}},
    )


def evidence():
    return [{"command": "pytest tests/", "status": "success", "output_tail": "3 passed", "shell_persistent": False}]


def run_check(parent, criteria, bash=None):
    return asyncio.run(boundary.check_task_acceptance(criteria, runtime=parent, thread_data=parent.state["thread_data"], bash_executions=bash))


@pytest.fixture
def provider(monkeypatch):
    sandbox = SimpleNamespace(id="shared", execute_command=MagicMock(return_value="4"), read_file=MagicMock(return_value="data"))
    provider = SimpleNamespace(acquire_async=AsyncMock(return_value="shared"), get=MagicMock(return_value=sandbox), release=MagicMock())
    monkeypatch.setattr("deerflow.sandbox.sandbox_provider.get_sandbox_provider", lambda: provider)
    monkeypatch.setattr("deerflow.sandbox.tools.get_sandbox_provider", lambda: provider)
    monkeypatch.setattr("deerflow.sandbox.tools._check_employee_execution", lambda _: None)
    monkeypatch.setattr("deerflow.sandbox.tools._validate_employee_sandbox", lambda *_: None)
    yield provider
    discard_sandbox_lease_manager(provider)


@pytest.mark.parametrize("criteria", [None, [], ["explain choices"], ["tests_passed:pytest tests/"], ["file:/etc/passwd exists"]])
def test_no_file_access_never_discovers_policy_or_provider(monkeypatch, tmp_path, criteria):
    forbidden = MagicMock(side_effect=AssertionError("unexpected IO"))
    monkeypatch.setattr("deerflow.sandbox.sandbox_provider.get_sandbox_provider", forbidden)
    monkeypatch.setattr("deerflow.config.get_app_config", forbidden)
    verdict = run_check(runtime(tmp_path), criteria, evidence())
    forbidden.assert_not_called()
    if criteria and criteria[0].startswith("tests_passed"):
        assert verdict["all_hold"] is True
    elif criteria:
        assert verdict["leaves"][0]["checked"] is False
    else:
        assert verdict is None


@pytest.mark.parametrize("mode", ["deny", "missing"])
def test_policy_gate_precedes_provider_and_preserves_independent_evidence(monkeypatch, tmp_path, caplog, mode):
    config = SimpleNamespace(authorization=AuthorizationConfig(enabled=True, provider=AuthorizationProviderConfig(use="deerflow.authz.rbac:RbacAuthorizationProvider", config={"roles": {"user": {"sandbox": {"allow": False}}}})))
    monkeypatch.setattr("deerflow.config.get_app_config", MagicMock(side_effect=FileNotFoundError("PRIVATE_VALUE")) if mode == "missing" else lambda: config)
    forbidden = MagicMock(side_effect=AssertionError("no provider before authorization"))
    monkeypatch.setattr("deerflow.sandbox.sandbox_provider.get_sandbox_provider", forbidden)
    parent = runtime(tmp_path)
    parent.context["user_role"] = "user"
    result = run_check(parent, ["file:a.txt exists", "tests_passed:pytest tests/"], evidence())
    assert [(leaf["checked"], leaf["holds"]) for leaf in result["leaves"]] == [(False, False), (True, True)]
    forbidden.assert_not_called()
    assert "PRIVATE_VALUE" not in caplog.text


@pytest.mark.parametrize("mode", ["normal", "empty_client", "revoked", "bad_mount", "probe_error", "read_error"])
def test_actual_parent_file_check_releases_only_its_holder(provider, disabled_sandbox_authorization, monkeypatch, tmp_path, caplog, mode):
    parent = runtime(tmp_path)
    before = deepcopy(parent)
    manager = get_sandbox_lease_manager(provider)
    manager.retain("parent-holder", "shared", thread_id="business-thread", user_id="alice")
    if mode == "empty_client":
        provider.get.return_value = None
    if mode == "revoked":
        monkeypatch.setattr("deerflow.sandbox.tools._check_employee_execution", MagicMock(side_effect=PermissionError("PRIVATE_VALUE")))
    if mode == "bad_mount":
        monkeypatch.setattr("deerflow.sandbox.tools._validate_employee_sandbox", MagicMock(side_effect=PermissionError("PRIVATE_VALUE")))
    if mode == "probe_error":
        provider.get.return_value.execute_command.side_effect = RuntimeError("PRIVATE_VALUE")
    if mode == "read_error":
        provider.get.return_value.read_file.side_effect = OSError("PRIVATE_VALUE")
    observed = []
    original = boundary.check_acceptance_criteria

    def inspect(*args, **kwargs):
        private = kwargs.get("runtime")
        if private is not None:
            observed.append(private)
            assert private.context["sandbox_lease_owner_id"].startswith("acceptance:")
            assert "sandbox_command_scope_id" not in private.context
            assert private.state["sandbox"] == {"sandbox_id": "shared"}
            assert manager.binding_for(private.context["sandbox_lease_owner_id"]) == "shared"
        return original(*args, **kwargs)

    monkeypatch.setattr(boundary, "check_acceptance_criteria", inspect)
    result = run_check(parent, ["file_written:report.md"])
    assert result["all_hold"] is (mode == "normal")
    assert parent == before
    assert manager.binding_for("parent-holder") == "shared"
    for private in observed:
        assert manager.binding_for(private.context["sandbox_lease_owner_id"]) is None
    provider.release.assert_not_called()
    if mode == "revoked":
        provider.acquire_async.assert_not_awaited()
    else:
        provider.acquire_async.assert_awaited_once_with("business-thread", user_id="alice")
    manager.release("parent-holder")
    provider.release.assert_called_once_with("shared")
    assert "PRIVATE_VALUE" not in str(result) + caplog.text


@pytest.mark.parametrize("phase", ["acquire", "validate", "probe", "read"])
def test_repeated_cancellation_drains_before_holder_release(provider, disabled_sandbox_authorization, monkeypatch, tmp_path, phase):
    entered = threading.Event()
    finish = threading.Event()
    parent = runtime(tmp_path)

    def blocked(*args, **kwargs):
        entered.set()
        assert finish.wait(5)
        return "4" if phase == "probe" else "data"

    if phase == "acquire":

        async def acquire(*args, **kwargs):
            await asyncio.to_thread(blocked)
            return "shared"

        provider.acquire_async.side_effect = acquire
    elif phase == "validate":
        monkeypatch.setattr("deerflow.sandbox.tools._validate_employee_sandbox", blocked)
    else:
        getattr(provider.get.return_value, "execute_command" if phase == "probe" else "read_file").side_effect = blocked

    async def exercise():
        task = asyncio.create_task(boundary.check_task_acceptance(["file_written:report.md"], runtime=parent, thread_data=parent.state["thread_data"], bash_executions=None))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(0.01)
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            provider.release.assert_not_called()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        provider.release.assert_called_once_with("shared")

    asyncio.run(exercise())


def test_missing_thread_never_acquires(provider, disabled_sandbox_authorization, tmp_path):
    parent = runtime(tmp_path)
    parent.context.pop("thread_id")
    parent.config.clear()
    assert run_check(parent, ["file:report.md exists"])["leaves"][0]["checked"] is False
    provider.acquire_async.assert_not_awaited()


@pytest.mark.parametrize("criterion", ["FILE:a.txt ExIsTs", "fıle:a.txt exists", "file_written:a.txt"])
def test_admission_and_checker_share_unicode_patterns(provider, disabled_sandbox_authorization, tmp_path, criterion):
    assert run_check(runtime(tmp_path), [criterion])["all_hold"] is True
    provider.acquire_async.assert_awaited_once()
