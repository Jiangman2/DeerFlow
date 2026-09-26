"""Unknown session-create outcomes must never return to the warm pool (#5711)."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox
from deerflow.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider


@pytest.fixture
def sandbox():
    with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
        return AioSandbox(id="test", base_url="http://sandbox")


def test_ambiguous_shell_create_quarantines_and_close_retries_cleanup(sandbox):
    sandbox._client.shell.create_session.side_effect = TimeoutError("response lost")
    with pytest.raises(RuntimeError, match="quarantined"):
        sandbox._create_shell_session(sandbox._client)
    assert sandbox.requires_container_recycle is True
    sandbox._client.shell.create_session.reset_mock()
    with pytest.raises(RuntimeError, match="quarantined"):
        sandbox._create_shell_session(sandbox._client)
    sandbox._client.shell.create_session.assert_not_called()
    client = sandbox._client
    client.shell.cleanup_session.reset_mock()
    sandbox.close()
    assert sandbox._client is None
    client.shell.cleanup_session.assert_called_once()
    assert sandbox.requires_container_recycle is True


def test_ambiguous_bash_create_never_executes_and_requires_recycle(sandbox):
    sandbox._client.bash.create_session.side_effect = TimeoutError("response lost")
    result = sandbox._run_bash_exec("echo hello", {})
    assert "quarantined" in result
    sandbox._client.bash.exec.assert_not_called()
    assert sandbox.requires_container_recycle is True


def test_cleanup_failure_does_not_clear_ambiguous_tombstone(sandbox):
    sandbox._client.bash.create_session.side_effect = TimeoutError("response lost")
    sandbox._client.bash.close_session.side_effect = OSError("cleanup failed")
    with pytest.raises(RuntimeError, match="quarantined"):
        sandbox._create_bash_session(sandbox._client)
    assert sandbox.requires_container_recycle is True
    sandbox._client.bash.create_session.reset_mock()
    with pytest.raises(RuntimeError, match="quarantined"):
        sandbox._create_bash_session(sandbox._client)
    sandbox._client.bash.create_session.assert_not_called()


def test_inflight_create_requires_recycle_until_resolved(sandbox):
    def create_session(**_kwargs):
        assert sandbox.requires_container_recycle is True

    sandbox._client.shell.create_session.side_effect = create_session
    sandbox._create_shell_session(sandbox._client)
    assert sandbox.requires_container_recycle is False


def test_definite_create_failure_does_not_quarantine(sandbox):
    import httpx

    sandbox._client.shell.create_session.side_effect = httpx.ConnectError("not connected")
    with pytest.raises(httpx.ConnectError):
        sandbox._create_shell_session(sandbox._client)
    assert sandbox.requires_container_recycle is False


def test_bounded_request_options_disable_retries(sandbox):
    sandbox._create_shell_session(sandbox._client)
    assert sandbox._client.shell.create_session.call_args.kwargs["request_options"] == {
        "timeout_in_seconds": 5,
        "max_retries": 0,
    }


def test_provider_release_recycles_quarantined_sandbox_without_parking():
    provider = AioSandboxProvider.__new__(AioSandboxProvider)
    provider._lock = threading.Lock()
    sandbox = SimpleNamespace(requires_container_recycle=True)
    provider._sandboxes = {"sandbox-1": sandbox}
    provider._destroy_tracked = MagicMock(return_value=True)
    provider.release("sandbox-1")
    provider._destroy_tracked.assert_called_once()


def test_provider_release_keeps_quarantined_sandbox_tracked_if_destroy_deferred():
    provider = AioSandboxProvider.__new__(AioSandboxProvider)
    provider._lock = threading.Lock()
    sandbox = SimpleNamespace(requires_container_recycle=True)
    provider._sandboxes = {"sandbox-1": sandbox}
    provider._destroy_tracked = MagicMock(return_value=False)
    provider.release("sandbox-1")
    assert provider._sandboxes["sandbox-1"] is sandbox
