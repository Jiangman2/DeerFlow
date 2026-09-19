import builtins
import os
import shutil
import subprocess
import sys

import pytest

import deerflow.sandbox.local.local_sandbox as local_sandbox
from deerflow.sandbox.local.local_sandbox import (
    LocalSandbox,
    PathMapping,
    _BoundedPipeCapture,
)


def _open(base, file, mode="r", *args, **kwargs):
    if "b" in mode:
        return base(file, mode, *args, **kwargs)
    return base(file, mode, *args, encoding=kwargs.pop("encoding", "gbk"), **kwargs)


def test_bounded_pipe_capture_decodes_configured_encoding():
    capture = _BoundedPipeCapture(encoding="cp1252")
    capture.append("café".encode("cp1252"))

    assert capture.read() == "café"


def test_bounded_pipe_capture_normalizes_windows_newlines_when_enabled():
    capture = _BoundedPipeCapture(normalize_newlines=True)
    capture.append(b"crlf\r\nbare-cr\rlf\n")

    assert capture.read() == "crlf\nbare-cr\nlf\n"


def test_bounded_pipe_capture_preserves_posix_newlines_by_default():
    capture = _BoundedPipeCapture()
    capture.append(b"crlf\r\nbare-cr\rlf\n")

    assert capture.read() == "crlf\r\nbare-cr\rlf\n"


@pytest.mark.parametrize("encoding", [None, "utf-8"])
def test_windows_pipe_capture_uses_explicit_encoding_or_locale(monkeypatch, encoding):
    monkeypatch.setattr(local_sandbox.locale, "getpreferredencoding", lambda _: "cp936")
    if os.name != "nt":
        monkeypatch.setattr(
            subprocess,
            "CREATE_NEW_PROCESS_GROUP",
            0,
            raising=False,
        )
    expected = "你好 日本語"
    payload = (expected + "\r\n").encode(encoding or "cp936")

    stdout, stderr, returncode, timed_out = LocalSandbox._run_windows_command(
        [
            sys.executable,
            "-c",
            f"import os; p=bytes.fromhex('{payload.hex()}'); os.write(1,p); os.write(2,p)",
        ],
        10,
        encoding=encoding,
    )

    assert stdout == stderr == expected + "\n"
    assert returncode == 0
    assert timed_out is False


@pytest.mark.skipif(os.name != "nt", reason="Requires real Windows PowerShell")
@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize(
    "no_console",
    [False, True],
    ids=["inherited-console", "no-console"],
)
def test_windows_powershell_cjk_roundtrip(shell_name, no_console):
    shell = shutil.which(shell_name)
    if shell is None:
        pytest.skip(f"{shell_name} is not installed")
    probe = r"""
import sys
import deerflow.sandbox.local.local_sandbox as local_sandbox
from deerflow.sandbox.local.local_sandbox import LocalSandbox

local_sandbox.locale.getpreferredencoding = lambda _: "cp936"
LocalSandbox._get_shell = staticmethod(lambda: sys.argv[1])
expected = "\u4f60\u597d \u65e5\u672c\u8a9e"
command = f"Write-Output '{expected}'; [Console]::Error.WriteLine('{expected}'); exit 3"
output = LocalSandbox("encoding-probe").execute_command(command, timeout=15)
assert output == expected + "\n\nStd Error:\n" + expected + "\n\nExit Code: 3", ascii(output)
"""
    env = {**os.environ, "PYTHONUTF8": "0"}
    result = subprocess.run(
        [sys.executable, "-c", probe, shell],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if no_console else 0,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_read_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "\u201cutf8\u201d"
    path.write_text(text, encoding="utf-8")
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    assert LocalSandbox("t").read_file(str(path)) == text


def test_write_file_uses_utf8_on_windows_locale(tmp_path, monkeypatch):
    path = tmp_path / "utf8.txt"
    text = "emoji \U0001f600"
    base = builtins.open

    monkeypatch.setattr(local_sandbox, "open", lambda file, mode="r", *args, **kwargs: _open(base, file, mode, *args, **kwargs), raising=False)

    LocalSandbox("t").write_file(str(path), text)

    assert path.read_text(encoding="utf-8") == text


def test_get_shell_prefers_posix_shell_from_path_before_windows_fallback(monkeypatch):
    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", lambda candidates: r"C:\Program Files\Git\bin\sh.exe" if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh") else None)

    assert LocalSandbox._get_shell() == r"C:\Program Files\Git\bin\sh.exe"


def test_get_shell_uses_powershell_fallback_on_windows(monkeypatch):
    calls: list[tuple[str, ...]] = []

    def fake_find(candidates: tuple[str, ...]) -> str | None:
        calls.append(candidates)
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", fake_find)

    assert LocalSandbox._get_shell() == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert calls[1] == (
        "pwsh",
        "pwsh.exe",
        "powershell",
        "powershell.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "cmd.exe",
    )


def test_get_shell_uses_cmd_as_last_windows_fallback(monkeypatch):
    def fake_find(candidates: tuple[str, ...]) -> str | None:
        if candidates == ("/bin/zsh", "/bin/bash", "/bin/sh", "sh"):
            return None
        return r"C:\Windows\System32\cmd.exe"

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"SystemRoot": r"C:\Windows"})
    monkeypatch.setattr(LocalSandbox, "_find_first_available_shell", fake_find)

    assert LocalSandbox._get_shell() == r"C:\Windows\System32\cmd.exe"


def test_execute_command_uses_powershell_command_mode_on_windows(monkeypatch):
    calls: list[tuple[list[str], float, dict[str, str], str | None]] = []

    def fake_run(args, timeout, env, *, encoding=None):
        calls.append((args, timeout, env, encoding))
        return "ok", "", 0, False

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"PATH": r"C:\Windows", "OPENAI_API_KEY": "should-not-leak"})
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"))
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(fake_run))

    output = LocalSandbox("t").execute_command("Write-Output hello")

    assert output == "ok"
    # Platform secrets are scrubbed from the inherited environment even on the
    # Windows PowerShell path (#3861); benign PATH is preserved and the env is an
    # explicit scrubbed dict, no longer None.
    assert calls == [
        (
            [
                r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                "-NoProfile",
                "-Command",
                "try{[Console]::InputEncoding=[System.Text.Encoding]::UTF8}catch{};try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8}catch{};$OutputEncoding=[System.Text.Encoding]::UTF8;Write-Output hello",
            ],
            600,
            {"PATH": r"C:\Windows"},
            "utf-8",
        )
    ]


def test_execute_command_forces_utf8_console_for_powershell_cjk_output(monkeypatch):
    calls: list[tuple[list[str], float, dict[str, str], str | None]] = []

    def fake_run(args, timeout, env, *, encoding=None):
        calls.append((args, timeout, env, encoding))
        return "你好", "", 0, False

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"PATH": r"C:\Windows"})
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: "pwsh"))
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(fake_run))

    output = LocalSandbox("t").execute_command("Write-Output 你好")

    assert output == "你好"
    command = calls[0][0][3]
    assert command.startswith("try{[Console]::InputEncoding=[System.Text.Encoding]::UTF8}catch{};")
    assert command.endswith("Write-Output 你好")
    assert calls[0][3] == "utf-8"


def test_execute_command_keeps_msys_path_conversion_for_host_commands_on_windows(
    monkeypatch,
):
    calls: list[tuple[list[str], float, dict[str, str]]] = []

    def fake_run(args, timeout, env):
        calls.append((args, timeout, env))
        return "ok", "", 0, False

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"PATH": r"C:\Program Files\Git\bin"})
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Program Files\Git\bin\sh.exe"))
    monkeypatch.setattr(LocalSandbox, "_msys_path_conversion_exclusions", lambda self: "/mnt/user-data")
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(fake_run))

    output = LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    assert calls == [
        (
            [r"C:\Program Files\Git\bin\sh.exe", "-c", "echo hello"],
            600,
            {
                "PATH": r"C:\Program Files\Git\bin",
                "MSYS2_ARG_CONV_EXCL": "/mnt/user-data",
            },
        )
    ]


def test_msys_path_conversion_exclusions_omit_blanket_patterns():
    sandbox = LocalSandbox(
        "t",
        [
            PathMapping(container_path="/", local_path="C:\\"),
            PathMapping(container_path="/mnt/data;*", local_path=r"C:\data"),
            PathMapping(
                container_path="/mnt/user-data/",
                local_path=r"C:\user-data",
            ),
            PathMapping(
                container_path="/mnt/user-data",
                local_path=r"C:\user-data",
            ),
        ],
    )

    assert sandbox._msys_path_conversion_exclusions() == "/mnt/user-data"


def test_execute_command_does_not_set_msys_env_for_non_msys_posix_shell_on_windows(monkeypatch):
    calls: list[tuple[list[str], float, dict[str, str]]] = []

    def fake_run(args, timeout, env):
        calls.append((args, timeout, env))
        return "ok", "", 0, False

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"PATH": r"C:\tools"})
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\tools\busybox\sh.exe"))
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(fake_run))

    output = LocalSandbox("t").execute_command("echo /mnt/skills/demo")

    assert output == "ok"
    # Non-MSYS posix shell adds no MSYS_* vars; the env is the scrubbed inherited
    # environment, not None (#3861).
    assert calls[0][2] == {"PATH": r"C:\tools"}
    assert "MSYS_NO_PATHCONV" not in calls[0][2]


def test_execute_command_uses_cmd_command_mode_on_windows(monkeypatch):
    calls: list[tuple[list[str], float, dict[str, str]]] = []

    def fake_run(args, timeout, env):
        calls.append((args, timeout, env))
        return "ok", "", 0, False

    monkeypatch.setattr(local_sandbox.os, "name", "nt")
    monkeypatch.setattr(local_sandbox.os, "environ", {"PATH": r"C:\Windows", "GITHUB_TOKEN": "should-not-leak"})
    monkeypatch.setattr(LocalSandbox, "_get_shell", staticmethod(lambda: r"C:\Windows\System32\cmd.exe"))
    monkeypatch.setattr(LocalSandbox, "_run_windows_command", staticmethod(fake_run))

    output = LocalSandbox("t").execute_command("echo hello")

    assert output == "ok"
    # Platform secrets are scrubbed even on the Windows cmd path (#3861); the env
    # is an explicit scrubbed dict, no longer None.
    assert calls == [
        (
            [r"C:\Windows\System32\cmd.exe", "/c", "echo hello"],
            600,
            {"PATH": r"C:\Windows"},
        )
    ]
