"""Tests for AioSandbox concurrent command serialization (#1433)."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from deerflow.community.aio_sandbox.aio_sandbox import (
    _BASH_EXIT_SENTINEL,
    _capture_bash_exit_code,
    _normalize_bash_exit_output,
)


@pytest.fixture()
def sandbox():
    """Create an AioSandbox with a mocked client."""
    with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient"):
        from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

        sb = AioSandbox(id="test-sandbox", base_url="http://localhost:8080")
        return sb


def test_api_key_is_sent_as_bearer_authorization_header():
    """AIO 1.11.0 accepts SANDBOX_API_KEY via Authorization: Bearer."""
    with patch("deerflow.community.aio_sandbox.aio_sandbox.AioSandboxClient") as client_cls:
        from deerflow.community.aio_sandbox.aio_sandbox import AioSandbox

        AioSandbox(
            id="secured-sandbox",
            base_url="http://localhost:8080",
            api_key="sandbox-secret",
        )

    client_cls.assert_called_once_with(
        base_url="http://localhost:8080",
        headers={"Authorization": "Bearer sandbox-secret"},
        timeout=600,
    )


class TestExecuteCommandSerialization:
    """Verify that concurrent exec_command calls are serialized."""

    def test_lock_prevents_concurrent_execution(self, sandbox):
        """Concurrent threads should not overlap inside execute_command."""
        call_log = []
        barrier = threading.Barrier(3)

        def slow_exec(command, **kwargs):
            call_log.append(("enter", command))
            import time

            time.sleep(0.05)
            call_log.append(("exit", command))
            return SimpleNamespace(data=SimpleNamespace(output=f"ok: {command}"))

        sandbox._client.shell.exec_command = slow_exec

        def worker(cmd):
            barrier.wait()  # ensure all threads contend for the lock simultaneously
            sandbox.execute_command(cmd)

        threads = []
        for i in range(3):
            t = threading.Thread(target=worker, args=(f"cmd-{i}",))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Verify serialization: each "enter" should be followed by its own
        # "exit" before the next "enter" (no interleaving).
        enters = [i for i, (action, _) in enumerate(call_log) if action == "enter"]
        exits = [i for i, (action, _) in enumerate(call_log) if action == "exit"]
        assert len(enters) == 3
        assert len(exits) == 3
        for e_idx, x_idx in zip(enters, exits):
            assert x_idx == e_idx + 1, f"Interleaved execution detected: {call_log}"


class TestBashExitCodeCapture:
    def test_command_appends_private_exit_marker(self):
        captured = _capture_bash_exit_code("python tool.py bad-subcommand")

        assert captured.startswith("python tool.py bad-subcommand\n")
        assert _BASH_EXIT_SENTINEL in captured
        assert '"$__deerflow_bash_exit_code"' in captured

    def test_zero_exit_strips_marker_and_preserves_output(self):
        assert _normalize_bash_exit_output(f"created file\n{_BASH_EXIT_SENTINEL}0\n") == "created file"

    def test_nonzero_exit_is_explicit_actionable_error(self):
        result = _normalize_bash_exit_output(f"usage: update_spec.py project key=value\n{_BASH_EXIT_SENTINEL}2\n")

        assert result.startswith("Error: Bash command exited with code 2.")
        assert "usage: update_spec.py" in result
        assert "--help" in result
        assert "do not guess command variants" in result
        assert _BASH_EXIT_SENTINEL not in result

    def test_execute_command_wraps_shell_api_and_classifies_failure(self, sandbox):
        captured: dict[str, str] = {}

        def mock_exec(command, **kwargs):
            captured["command"] = command
            return SimpleNamespace(data=SimpleNamespace(output=f"bad option\n{_BASH_EXIT_SENTINEL}2\n"))

        sandbox._client.shell.exec_command = mock_exec

        result = sandbox.execute_command("python tool.py invented")

        assert _BASH_EXIT_SENTINEL in captured["command"]
        assert result.startswith("Error: Bash command exited with code 2.")


class TestErrorObservationRetry:
    """Verify ErrorObservation detection and fresh-session retry."""

    def test_retry_on_error_observation(self, sandbox):
        """When output contains ErrorObservation, retry with a fresh session."""
        call_count = 0

        def mock_exec(command, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="success"))

        sandbox._client.shell.exec_command = mock_exec

        result = sandbox.execute_command("echo hello")
        assert result == "success"
        assert call_count == 2

    def test_retry_creates_fresh_session_before_targeting_it(self, sandbox):
        """Recovery must explicitly create a session, then exec against that id.

        The sandbox image only auto-creates a session when exec_command is
        called with *no* id; an exec carrying an unknown id returns HTTP 404
        "Session not found". So the retry must obtain a real, distinct session
        via create_session() first and target that id, rather than fabricating
        an id and handing it straight to exec_command (the regression that
        404'd every recovery and looped runs to the recursion limit).
        """
        exec_calls = []
        created_ids = []
        cleaned_ids = []

        def mock_exec(command, **kwargs):
            exec_calls.append(kwargs)
            if len(exec_calls) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        def mock_create_session(id, **kwargs):
            created_ids.append(id)
            return SimpleNamespace(data=SimpleNamespace(session_id=id))

        def mock_cleanup_session(session_id, **kwargs):
            cleaned_ids.append(session_id)

        sandbox._client.shell.exec_command = mock_exec
        sandbox._client.shell.create_session = mock_create_session
        sandbox._client.shell.cleanup_session = mock_cleanup_session

        result = sandbox.execute_command("test")

        assert result == "ok"
        assert len(exec_calls) == 2
        # First attempt runs on the default session (no id).
        assert "id" not in exec_calls[0]
        # A fresh session was explicitly created...
        assert len(created_ids) == 1
        assert len(created_ids[0]) == 36  # UUID format
        # ...and the retry targets exactly that created session, never an
        # uncreated/fabricated id (which would 404).
        assert exec_calls[1].get("id") == created_ids[0]
        # ...and the healthy replacement is retained for later unscoped calls
        # rather than returning to the already-corrupted implicit session.
        assert cleaned_ids == []

        assert sandbox.execute_command("second") == "ok"
        assert exec_calls[2].get("id") == created_ids[0]

        sandbox.close()
        assert cleaned_ids == [created_ids[0]]

    def test_cleanup_failure_does_not_mask_successful_retry(self, sandbox):
        """A failure releasing the recovery session must not lose the retry output."""

        def mock_exec(command, **kwargs):
            if "id" not in kwargs:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="recovered"))

        def mock_cleanup_session(session_id, **kwargs):
            raise RuntimeError("cleanup boom")

        sandbox._client.shell.exec_command = mock_exec
        sandbox._client.shell.create_session = lambda id, **kwargs: SimpleNamespace(data=SimpleNamespace(session_id=id))
        sandbox._client.shell.cleanup_session = mock_cleanup_session

        # The retry succeeded; a later best-effort close failure must not turn
        # this command into an "Error: ..." result.
        assert sandbox.execute_command("test") == "recovered"
        sandbox.close()

    def test_no_retry_on_clean_output(self, sandbox):
        """Normal output should not trigger a retry."""
        call_count = 0

        def mock_exec(command, **kwargs):
            nonlocal call_count
            call_count += 1
            return SimpleNamespace(data=SimpleNamespace(output="all good"))

        sandbox._client.shell.exec_command = mock_exec

        result = sandbox.execute_command("echo hello")
        assert result == "all good"
        assert call_count == 1

    def test_missing_retained_session_is_recreated_once(self, sandbox):
        """A server-side eviction of the retained replacement is recoverable."""
        from agent_sandbox.core.api_error import ApiError

        exec_ids = []
        created_ids = []

        def mock_exec(command, **kwargs):
            exec_ids.append(kwargs.get("id"))
            if len(exec_ids) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            if len(exec_ids) == 3:
                raise ApiError(status_code=404, body={"message": "Session not found"})
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        def mock_create_session(id, **kwargs):
            created_ids.append(id)
            return SimpleNamespace(data=SimpleNamespace(session_id=id))

        sandbox._client.shell.exec_command = mock_exec
        sandbox._client.shell.create_session = mock_create_session

        assert sandbox.execute_command("first") == "ok"
        first_recovery = created_ids[0]
        assert sandbox.execute_command("second") == "ok"
        assert len(created_ids) == 2
        assert exec_ids == [None, first_recovery, first_recovery, created_ids[1]]

    def test_unrelated_shell_404_is_not_treated_as_missing_session(self, sandbox):
        """Only the structured session-not-found response rotates a session."""
        from agent_sandbox.core.api_error import ApiError

        sandbox._client.shell.exec_command = MagicMock(
            side_effect=ApiError(status_code=404, body={"message": "Not Found"})
        )

        assert sandbox.execute_command("test").startswith("Error:")
        sandbox._client.shell.create_session.assert_not_called()


class TestBashExecUnsupportedFailFast:
    """Regression tests for #3921: sandbox images older than all-in-one-sandbox
    1.9.x have no ``/v1/bash/exec`` route, so every env-bearing command (skills
    declaring ``required-secrets``) hit a bare nginx 404 that the model kept
    retrying. The sandbox must fail fast with an actionable, operator-facing
    error instead."""

    def _api_error_404(self):
        from agent_sandbox.core.api_error import ApiError

        return ApiError(
            headers={"server": "nginx/1.18.0 (Ubuntu)"},
            status_code=404,
            body={"success": False, "message": "Not Found", "data": None},
        )

    def test_bash_exec_404_returns_actionable_error(self, sandbox):
        """A 404 from bash.exec must explain the image capability gap and the
        remediation (upgrade image), not surface the raw nginx error."""
        sandbox._client.bash.exec = MagicMock(side_effect=self._api_error_404())

        out = sandbox.execute_command("echo $TOK", env={"TOK": "secret-v"})

        assert out.startswith("Error:")
        # Actionable: names the missing capability and the minimum image version.
        assert "/v1/bash/exec" in out
        assert "1.9.3" in out
        assert "required-secrets" in out
        # Not the raw upstream 404 body the model can't act on.
        assert "nginx" not in out

    def test_bash_exec_404_is_cached_and_stops_retry_storm(self, sandbox):
        """After one 404 the capability gap is remembered on the instance:
        follow-up env-bearing calls return the same actionable error without
        another HTTP round-trip (the original bug produced 4 consecutive 404s
        as the model retried variants of the command)."""
        sandbox._client.bash.exec = MagicMock(side_effect=self._api_error_404())

        first = sandbox.execute_command("cmd-1", env={"TOK": "v"})
        second = sandbox.execute_command("cmd-2", env={"TOK": "v"})

        assert sandbox._client.bash.exec.call_count == 1
        assert first == second
        assert "1.9.3" in second

    def test_bash_exec_non_404_error_is_not_cached(self, sandbox):
        """Transient failures (e.g. 500) must not permanently disable the env
        path — the next env-bearing call should try bash.exec again."""
        from agent_sandbox.core.api_error import ApiError

        sandbox._client.bash.exec = MagicMock(side_effect=ApiError(status_code=500, body="boom"))

        first = sandbox.execute_command("cmd-1", env={"TOK": "v"})
        second = sandbox.execute_command("cmd-2", env={"TOK": "v"})

        assert sandbox._client.bash.exec.call_count == 2
        assert first.startswith("Error:")
        assert "1.9.3" not in first
        assert second.startswith("Error:")

    def test_env_less_path_unaffected_after_404(self, sandbox):
        """The legacy persistent-shell path must keep working on an image
        without bash.exec — only env injection is unavailable there."""
        sandbox._client.bash.exec = MagicMock(side_effect=self._api_error_404())
        sandbox._client.shell.exec_command = MagicMock(return_value=SimpleNamespace(data=SimpleNamespace(output="plain ok")))

        sandbox.execute_command("cmd", env={"TOK": "v"})
        out = sandbox.execute_command("echo plain")

        assert out == "plain ok"
        sandbox._client.shell.exec_command.assert_called_once()

    def test_bash_exec_success_does_not_mark_unsupported(self, sandbox):
        """A healthy bash.exec keeps the env path fully enabled."""
        sandbox._client.bash.exec = MagicMock(return_value=SimpleNamespace(data=SimpleNamespace(stdout="ok", stderr=None)))

        first = sandbox.execute_command("cmd-1", env={"TOK": "v"})
        second = sandbox.execute_command("cmd-2", env={"TOK": "v"})

        assert first == "ok"
        assert second == "ok"
        assert sandbox._client.bash.exec.call_count == 2

    def test_bash_exec_uses_and_releases_explicit_transient_session(self, sandbox):
        """Env-bearing commands must not consume a server session forever."""
        sandbox._client.bash.exec = MagicMock(
            return_value=SimpleNamespace(data=SimpleNamespace(stdout="ok", stderr=None))
        )

        assert sandbox.execute_command("cmd", env={"TOK": "v"}) == "ok"

        session_id = sandbox._client.bash.create_session.call_args.kwargs["session_id"]
        assert sandbox._client.bash.exec.call_args.kwargs["session_id"] == session_id
        sandbox._client.bash.close_session.assert_called_once_with(session_id)

    def test_bash_exec_cleanup_failure_does_not_mask_success(self, sandbox):
        sandbox._client.bash.exec = MagicMock(
            return_value=SimpleNamespace(data=SimpleNamespace(stdout="ok", stderr=None))
        )
        sandbox._client.bash.close_session = MagicMock(side_effect=RuntimeError("cleanup failed"))

        assert sandbox.execute_command("cmd", env={"TOK": "v"}) == "ok"

    def test_bash_exec_missing_session_retries_once_without_latching_unsupported(self, sandbox):
        from agent_sandbox.core.api_error import ApiError

        missing = ApiError(status_code=404, body={"message": "Session not found: transient"})
        sandbox._client.bash.exec = MagicMock(
            side_effect=[
                missing,
                SimpleNamespace(data=SimpleNamespace(stdout="ok", stderr=None)),
            ]
        )

        assert sandbox.execute_command("cmd", env={"TOK": "v"}) == "ok"
        assert sandbox._bash_exec_unsupported is False
        assert sandbox._client.bash.exec.call_count == 2
        assert sandbox._client.bash.create_session.call_count == 2
        assert sandbox._client.bash.close_session.call_count == 2

    def test_bash_exec_repeated_missing_session_is_bounded(self, sandbox):
        from agent_sandbox.core.api_error import ApiError

        missing = ApiError(status_code=404, body={"message": "Session not found"})
        sandbox._client.bash.exec = MagicMock(side_effect=missing)

        out = sandbox.execute_command("cmd", env={"TOK": "v"})

        assert out == "Error: bash.exec session disappeared after retry"
        assert sandbox._bash_exec_unsupported is False
        assert sandbox._client.bash.exec.call_count == 2


class TestConfiguredEnvironmentInjection:
    def test_default_environment_is_injected_into_commands(self, sandbox):
        sandbox._default_env = {
            "IMAGE_BACKEND": "qwen",
            "QWEN_API_KEY": "configured-key",
        }
        sandbox._client.bash.exec = MagicMock(return_value=SimpleNamespace(data=SimpleNamespace(stdout="ok", stderr=None)))

        assert sandbox.execute_command("python generate.py") == "ok"
        assert sandbox._client.bash.exec.call_args.kwargs["env"] == {
            "IMAGE_BACKEND": "qwen",
            "QWEN_API_KEY": "configured-key",
        }

    def test_request_scoped_environment_overrides_configured_environment(self, sandbox):
        sandbox._default_env = {
            "QWEN_API_KEY": "configured-key",
            "IMAGE_BACKEND": "qwen",
        }
        sandbox._client.bash.exec = MagicMock(return_value=SimpleNamespace(data=SimpleNamespace(stdout="ok", stderr=None)))

        sandbox.execute_command("python generate.py", env={"QWEN_API_KEY": "request-key"})

        assert sandbox._client.bash.exec.call_args.kwargs["env"] == {
            "QWEN_API_KEY": "request-key",
            "IMAGE_BACKEND": "qwen",
        }


class TestListDirSerialization:
    """Verify that list_dir also acquires the lock."""

    def test_list_dir_uses_lock(self, sandbox):
        """list_dir should hold the lock during execution."""
        lock_was_held = []

        original_exec = MagicMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(
                    output="/a\n/b\n\n__DF_FIND_STATUS__:0\n",
                    exit_code=0,
                )
            )
        )

        def tracking_exec(command, **kwargs):
            lock_was_held.append(sandbox._lock.locked())
            return original_exec(command, **kwargs)

        sandbox._client.shell.exec_command = tracking_exec

        result = sandbox.list_dir("/test")
        assert result == ["/a", "/b"]
        assert lock_was_held == [True], "list_dir must hold the lock during exec_command"


class TestNoChangeTimeout:
    """Verify that no_change_timeout is forwarded to every exec_command call."""

    def test_execute_command_passes_no_change_timeout(self, sandbox):
        """execute_command should pass no_change_timeout to exec_command."""
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        sandbox.execute_command("echo hello")

        assert len(calls) == 1
        assert calls[0].get("no_change_timeout") == sandbox._DEFAULT_NO_CHANGE_TIMEOUT

    def test_retry_passes_no_change_timeout(self, sandbox):
        """The ErrorObservation retry path should also pass no_change_timeout."""
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(data=SimpleNamespace(output="'ErrorObservation' object has no attribute 'exit_code'"))
            return SimpleNamespace(data=SimpleNamespace(output="ok"))

        sandbox._client.shell.exec_command = mock_exec

        sandbox.execute_command("echo hello")

        assert len(calls) == 2
        assert calls[0].get("no_change_timeout") == sandbox._DEFAULT_NO_CHANGE_TIMEOUT
        assert calls[1].get("no_change_timeout") == sandbox._DEFAULT_NO_CHANGE_TIMEOUT

    def test_list_dir_passes_no_change_timeout(self, sandbox):
        """list_dir should pass no_change_timeout to exec_command."""
        calls = []

        def mock_exec(command, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=SimpleNamespace(
                    output="/a\n/b\n\n__DF_FIND_STATUS__:0\n",
                    exit_code=0,
                )
            )

        sandbox._client.shell.exec_command = mock_exec

        sandbox.list_dir("/test")

        assert len(calls) == 1
        assert calls[0].get("no_change_timeout") == sandbox._DEFAULT_NO_CHANGE_TIMEOUT

    def test_list_dir_surfaces_missing_root(self, sandbox):
        sandbox._client.shell.exec_command = MagicMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(
                    output="__DF_FIND_STATUS__:missing\n",
                    exit_code=1,
                )
            )
        )

        with pytest.raises(FileNotFoundError):
            sandbox.list_dir("/missing")

    def test_list_dir_rejects_partial_traversal(self, sandbox):
        sandbox._client.shell.exec_command = MagicMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(
                    output="/visible\n\n__DF_FIND_STATUS__:1\n",
                    exit_code=1,
                )
            )
        )

        with pytest.raises(OSError, match="results would be incomplete"):
            sandbox.list_dir("/test")

    def test_list_dir_rejects_empty_client_response(self, sandbox):
        sandbox._client.shell.exec_command = MagicMock(
            return_value=SimpleNamespace(data=None)
        )

        with pytest.raises(OSError, match="empty response"):
            sandbox.list_dir("/test")


class TestConcurrentFileWrites:
    """Verify file write paths do not lose concurrent updates."""

    def test_append_should_preserve_both_parallel_writes(self, sandbox):
        storage = {"content": "seed\n"}
        state_lock = threading.Lock()

        def write_back(*, file, content, append=False, **kwargs):
            with state_lock:
                if append:
                    storage["content"] += content
                else:
                    storage["content"] = content
            return SimpleNamespace(data=SimpleNamespace())

        sandbox.read_file = MagicMock(side_effect=AssertionError("native append must not pre-read"))
        sandbox._client.file.write_file = write_back

        barrier = threading.Barrier(2)

        def writer(payload: str):
            barrier.wait()
            sandbox.write_file("/tmp/shared.log", payload, append=True)

        threads = [
            threading.Thread(target=writer, args=("A\n",)),
            threading.Thread(target=writer, args=("B\n",)),
        ]

        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert storage["content"] in {"seed\nA\nB\n", "seed\nB\nA\n"}


class TestWriteFile:
    def test_append_uses_server_append_without_pre_read(self, sandbox):
        sandbox.read_file = MagicMock(side_effect=RuntimeError("read timed out"))
        sandbox._client.file.write_file = MagicMock()

        sandbox.write_file("/mnt/user-data/workspace/report.txt", "tail", append=True)

        sandbox.read_file.assert_not_called()
        sandbox._client.file.write_file.assert_called_once_with(
            file="/mnt/user-data/workspace/report.txt",
            content="tail",
            append=True,
        )

    def test_overwrite_keeps_existing_request_shape(self, sandbox):
        sandbox._client.file.write_file = MagicMock()

        sandbox.write_file("/mnt/user-data/workspace/report.txt", "replacement")

        sandbox._client.file.write_file.assert_called_once_with(
            file="/mnt/user-data/workspace/report.txt",
            content="replacement",
        )


class TestWriteFileParents:
    def test_write_file_creates_parent_directory(self, sandbox):
        shell_calls = []
        writes = []
        sandbox._client.shell.exec_command = lambda **kwargs: shell_calls.append(kwargs) or SimpleNamespace(data=SimpleNamespace(output=""))
        sandbox._client.file.write_file = lambda **kwargs: writes.append(kwargs)

        sandbox.write_file("/mnt/user-data/workspace/project/svg_output/01.svg", "<svg/>")

        assert shell_calls[0]["command"] == "mkdir -p /mnt/user-data/workspace/project/svg_output"
        assert writes == [{"file": "/mnt/user-data/workspace/project/svg_output/01.svg", "content": "<svg/>"}]


class TestReadFileFallback:
    def test_file_api_parse_error_falls_back_to_shell(self, sandbox):
        """A malformed remote error response must not hide a readable file."""
        sandbox._client.file.read_file = MagicMock(side_effect=ValueError("invalid response"))
        sandbox._client.shell.exec_command = MagicMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(
                    output=f"<svg>content</svg>\n{_BASH_EXIT_SENTINEL}0\n"
                )
            )
        )

        result = sandbox.read_file("/mnt/user-data/workspace/svg_output/01.svg")

        assert result == "<svg>content</svg>"
        sandbox._client.shell.exec_command.assert_called_once()

    def test_file_api_parse_error_returns_normalized_shell_failure(self, sandbox):
        """A missing file should become an actionable Error string."""
        sandbox._client.file.read_file = MagicMock(side_effect=ValueError("invalid response"))
        sandbox._client.shell.exec_command = MagicMock(
            return_value=SimpleNamespace(
                data=SimpleNamespace(
                    output=f"cat: /mnt/user-data/workspace/missing.svg: No such file\n{_BASH_EXIT_SENTINEL}1\n"
                )
            )
        )

        result = sandbox.read_file("/mnt/user-data/workspace/missing.svg")

        assert result.startswith("Error: Bash command exited with code 1.")


class TestDownloadFile:
    """Tests for AioSandbox.download_file."""

    def test_returns_concatenated_bytes(self, sandbox):
        """download_file should join chunks from the client iterator into bytes."""
        sandbox._client.file.download_file = MagicMock(return_value=[b"hel", b"lo"])

        result = sandbox.download_file("/mnt/user-data/outputs/file.bin")

        assert result == b"hello"
        sandbox._client.file.download_file.assert_called_once_with(path="/mnt/user-data/outputs/file.bin")

    def test_returns_empty_bytes_for_empty_file(self, sandbox):
        """download_file should return b'' when the iterator yields nothing."""
        sandbox._client.file.download_file = MagicMock(return_value=iter([]))

        result = sandbox.download_file("/mnt/user-data/outputs/empty.bin")

        assert result == b""

    def test_uses_lock_during_download(self, sandbox):
        """download_file should hold the lock while calling the client."""
        lock_was_held = []

        def tracking_download(path):
            lock_was_held.append(sandbox._lock.locked())
            return iter([b"data"])

        sandbox._client.file.download_file = tracking_download

        sandbox.download_file("/mnt/user-data/outputs/file.bin")

        assert lock_was_held == [True], "download_file must hold the lock during client call"

    def test_raises_oserror_on_client_error(self, sandbox):
        """download_file should wrap client exceptions as OSError."""
        sandbox._client.file.download_file = MagicMock(side_effect=RuntimeError("network error"))

        with pytest.raises(OSError, match="network error"):
            sandbox.download_file("/mnt/user-data/outputs/file.bin")

    def test_preserves_oserror_from_client(self, sandbox):
        """OSError raised by the client should propagate without re-wrapping."""
        sandbox._client.file.download_file = MagicMock(side_effect=OSError("disk error"))

        with pytest.raises(OSError, match="disk error"):
            sandbox.download_file("/mnt/user-data/outputs/file.bin")

    def test_rejects_path_outside_virtual_prefix_and_logs_error(self, sandbox, caplog):
        """download_file must reject downloads outside /mnt/user-data and log the reason."""
        sandbox._client.file.download_file = MagicMock()

        with caplog.at_level("ERROR"):
            with pytest.raises(PermissionError, match="must be under"):
                sandbox.download_file("/etc/passwd")

        assert "outside allowed directory" in caplog.text
        sandbox._client.file.download_file.assert_not_called()

    @pytest.mark.parametrize(
        "path",
        [
            "/mnt/workspace/../../etc/passwd",
            "../secret",
            "/a/b/../../../etc/shadow",
        ],
    )
    def test_rejects_path_traversal(self, sandbox, path):
        """download_file must reject paths containing '..' before calling the client."""
        sandbox._client.file.download_file = MagicMock()

        with pytest.raises(PermissionError, match="path traversal"):
            sandbox.download_file(path)

        sandbox._client.file.download_file.assert_not_called()

    def test_single_chunk(self, sandbox):
        """download_file should work correctly with a single-chunk response."""
        sandbox._client.file.download_file = MagicMock(return_value=[b"single-chunk"])

        result = sandbox.download_file("/mnt/user-data/outputs/single.bin")

        assert result == b"single-chunk"


class TestClose:
    """Verify AioSandbox.close() tears down the host-side HTTP client (#2872)."""

    def test_close_calls_real_nested_httpx_client(self, sandbox):
        """close() must close the real httpx.Client at the bottom of the chain.

        Mirrors the actual Fern structure:
            Sandbox._client_wrapper.httpx_client  -> Fern HttpClient (no close())
                .httpx_client                     -> httpx.Client    (the real owner)

        The intermediate HttpClient deliberately exposes NO close(), so a naive
        one-level lookup (the original bug) would silently close nothing.
        """
        real_httpx = MagicMock(spec=["close"])
        fern_http = SimpleNamespace(httpx_client=real_httpx)  # no close on this layer
        sandbox._client._client_wrapper = SimpleNamespace(httpx_client=fern_http)

        sandbox.close()

        real_httpx.close.assert_called_once_with()

    def test_close_clears_client_reference(self, sandbox):
        """After close(), the client reference must be dropped (use-after-close safety)."""
        real_httpx = MagicMock(spec=["close"])
        fern_http = SimpleNamespace(httpx_client=real_httpx)
        sandbox._client._client_wrapper = SimpleNamespace(httpx_client=fern_http)

        sandbox.close()

        assert sandbox._client is None
        assert sandbox._closed is True

    def test_close_is_idempotent(self, sandbox):
        """Calling close() multiple times must close the underlying client at most once."""
        real_httpx = MagicMock(spec=["close"])
        fern_http = SimpleNamespace(httpx_client=real_httpx)
        sandbox._client._client_wrapper = SimpleNamespace(httpx_client=fern_http)

        sandbox.close()
        sandbox.close()
        sandbox.close()

        assert real_httpx.close.call_count == 1

    def test_close_swallows_exceptions(self, sandbox, caplog):
        """close() must be best-effort: client errors are logged but never raised."""
        real_httpx = MagicMock(spec=["close"])
        real_httpx.close.side_effect = RuntimeError("teardown boom")
        fern_http = SimpleNamespace(httpx_client=real_httpx)
        sandbox._client._client_wrapper = SimpleNamespace(httpx_client=fern_http)

        with caplog.at_level("WARNING"):
            sandbox.close()

        assert "Error closing AioSandbox client" in caplog.text

    def test_close_falls_back_to_client_close(self, sandbox):
        """If no nested httpx.Client is reachable, close() degrades to the client's own close()."""
        # Replace the mocked client with a stub that exposes only top-level close()
        client = MagicMock(spec=["close"])
        sandbox._client = client

        sandbox.close()

        client.close.assert_called_once_with()

    def test_close_when_no_close_attr_does_not_raise(self, sandbox):
        """A client without any close attribute must not crash close()."""
        sandbox._client = SimpleNamespace()  # no close, no _client_wrapper
        sandbox.close()  # must not raise
        assert sandbox._client is None
