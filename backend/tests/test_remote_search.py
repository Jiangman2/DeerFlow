"""Remote grep/glob status and truncation contract."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess

import pytest

from deerflow.sandbox.remote_search import (
    parse_remote_search_output,
    remote_search_command,
)

_POSIX_SH = pytest.mark.skipif(
    os.name == "nt" or shutil.which("sh") is None or shutil.which("head") is None,
    reason="POSIX sh pipeline required",
)


def test_parse_missing_root_is_file_not_found() -> None:
    with pytest.raises(FileNotFoundError):
        parse_remote_search_output(
            "__DF_SEARCH_STATUS__:missing\n",
            "/dir",
            tool="grep",
            limit=10,
        )


@pytest.mark.parametrize("stdout", ["", "/dir/a.py:1:needle\n"])
def test_parse_requires_status_marker(stdout: str) -> None:
    with pytest.raises(OSError, match="status marker missing"):
        parse_remote_search_output(stdout, "/dir", tool="grep", limit=10)


@pytest.mark.parametrize(
    ("tool", "status"),
    [("grep", 0), ("grep", 141), ("find", 0), ("find", 141)],
)
def test_parse_success_preserves_trailing_whitespace(tool: str, status: int) -> None:
    output = parse_remote_search_output(
        f"/dir/notes.txt \n\n__DF_SEARCH_STATUS__:{status}\n",
        "/dir",
        tool=tool,
        limit=10,
    )
    assert output == ("/dir/notes.txt ", False)


def test_parse_distinguishes_complete_and_truncated_at_boundary() -> None:
    complete = parse_remote_search_output(
        "/dir/a\n/dir/b\n\n__DF_SEARCH_STATUS__:0\n",
        "/dir",
        tool="find",
        limit=2,
    )
    truncated = parse_remote_search_output(
        "/dir/a\n/dir/b\n/dir/c\n\n__DF_SEARCH_STATUS__:141\n",
        "/dir",
        tool="find",
        limit=2,
    )

    assert complete == ("/dir/a\n/dir/b", False)
    assert truncated == ("/dir/a\n/dir/b", True)


@pytest.mark.parametrize(
    ("tool", "status"),
    [("grep", 2), ("find", 1)],
)
def test_parse_rejects_partial_read_failures(tool: str, status: int) -> None:
    with pytest.raises(OSError, match="results would be incomplete"):
        parse_remote_search_output(
            f"/dir/visible\n\n__DF_SEARCH_STATUS__:{status}\n",
            "/dir",
            tool=tool,
            limit=10,
        )


@pytest.mark.parametrize(
    ("tool", "status"),
    [("grep", 126), ("grep", 127), ("find", 127)],
)
def test_parse_rejects_command_failures(tool: str, status: int) -> None:
    with pytest.raises(OSError, match=f"exited with code {status}"):
        parse_remote_search_output(
            f"\n__DF_SEARCH_STATUS__:{status}\n",
            "/dir",
            tool=tool,
            limit=10,
        )


def test_command_checks_root_and_reads_one_line_past_limit() -> None:
    command = remote_search_command(
        "find -H '/mnt/data dir' -type f -print 2>/dev/null",
        "/mnt/data dir",
        limit=20,
    )
    assert command.startswith("set +e; ")
    assert "[ ! -e '/mnt/data dir' ]" in command
    assert "head -n 21" in command
    assert command.index("find -H ") < command.index("head -n 21")
    assert command.index("head -n 21") < command.rindex("__DF_SEARCH_STATUS__:")
    assert command.endswith("exit 0")


@_POSIX_SH
def test_real_pipeline_reports_raw_output_truncation(tmp_path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_find = fake_bin / "find"
    fake_find.write_text(
        '#!/bin/sh\nprintf "/dir/a\\n/dir/b\\n/dir/c\\n"\nexit 0\n',
        encoding="utf-8",
    )
    fake_find.chmod(fake_find.stat().st_mode | stat.S_IEXEC)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin) + os.pathsep + env.get("PATH", "")
    process = subprocess.run(
        [
            "sh",
            "-c",
            remote_search_command(
                f"find -H {shlex.quote(str(tmp_path))} -type f -print 2>/dev/null",
                str(tmp_path),
                limit=2,
            ),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert process.returncode == 0
    assert parse_remote_search_output(
        process.stdout,
        str(tmp_path),
        tool="find",
        limit=2,
    ) == ("/dir/a\n/dir/b", True)
