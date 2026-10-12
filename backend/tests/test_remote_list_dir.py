"""Remote list_dir command/parser contract."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

import pytest

from deerflow.sandbox.remote_list_dir import (
    parse_remote_list_dir_output,
    remote_list_dir_command,
)

_POSIX_SH = pytest.mark.skipif(
    os.name == "nt" or shutil.which("sh") is None or shutil.which("head") is None,
    reason="POSIX sh pipeline required",
)


def test_parse_command_failure_is_not_missing_path() -> None:
    with pytest.raises(OSError, match="exited with code 127"):
        parse_remote_list_dir_output(
            "\n__DF_FIND_STATUS__:127\n",
            "/dir",
            pipeline_exit_code=0,
        )


def test_parse_missing_root_marker_is_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        parse_remote_list_dir_output(
            "__DF_FIND_STATUS__:missing\n",
            "/dir",
            pipeline_exit_code=1,
        )


@pytest.mark.parametrize(
    "stdout",
    [
        "\n__DF_FIND_STATUS__:1\n",
        "/dir/visible.txt\n\n__DF_FIND_STATUS__:1\n",
    ],
)
def test_parse_partial_traversal_is_an_incomplete_failure(stdout: str) -> None:
    with pytest.raises(OSError, match="results would be incomplete"):
        parse_remote_list_dir_output(stdout, "/dir", pipeline_exit_code=1)


def test_parse_success_preserves_trailing_filename_whitespace() -> None:
    stdout = "/dir/notes.txt \n/dir/sub\n\n__DF_FIND_STATUS__:0\n"
    assert parse_remote_list_dir_output(stdout, "/dir", pipeline_exit_code=0) == [
        "/dir/notes.txt ",
        "/dir/sub",
    ]


def test_parse_sigpipe_bounded_listing_is_valid() -> None:
    lines = "\n".join(f"/dir/f{i}" for i in range(500))
    entries = parse_remote_list_dir_output(
        f"{lines}\n\n__DF_FIND_STATUS__:141\n",
        "/dir",
        pipeline_exit_code=141,
    )
    assert len(entries) == 500
    assert entries[0] == "/dir/f0"
    assert entries[-1] == "/dir/f499"


@pytest.mark.parametrize(
    ("stdout", "exit_code", "message"),
    [
        ("", 127, "exited with code 127"),
        ("", 0, "marker missing"),
        ("/dir\n", 0, "marker missing"),
    ],
)
def test_parse_requires_a_status_marker(
    stdout: str,
    exit_code: int,
    message: str,
) -> None:
    with pytest.raises(OSError, match=message):
        parse_remote_list_dir_output(
            stdout,
            "/dir",
            pipeline_exit_code=exit_code,
        )


def test_command_checks_root_and_records_find_status_after_cap() -> None:
    command = remote_list_dir_command("/mnt/data dir", 2)
    assert command.startswith("set +e; ")
    assert "[ ! -e '/mnt/data dir' ]" in command
    assert "find -H '/mnt/data dir'" in command
    assert "\\( -type f -o -type d \\)" in command
    assert "head -n 500" in command
    assert command.index("find -H ") < command.index("head -n")
    assert command.index("head -n") < command.rindex("__DF_FIND_STATUS__:")
    assert 'exit "${st:-126}"' in command


def _run(command: str, *, env: dict[str, str] | None = None):
    return subprocess.run(
        ["sh", "-c", command],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _fake_find_environment(tmp_path, script: str) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_find = fake_bin / "find"
    fake_find.write_text(script, encoding="utf-8")
    fake_find.chmod(fake_find.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    return env


@_POSIX_SH
def test_real_pipeline_surfaces_find_failure_after_partial_output(tmp_path) -> None:
    env = _fake_find_environment(
        tmp_path,
        '#!/bin/sh\nprintf "/dir/visible.txt\\n"\nexit 1\n',
    )
    process = _run(remote_list_dir_command(str(tmp_path), 2), env=env)

    assert process.returncode == 1
    with pytest.raises(OSError, match="results would be incomplete"):
        parse_remote_list_dir_output(
            process.stdout,
            str(tmp_path),
            pipeline_exit_code=process.returncode,
        )
