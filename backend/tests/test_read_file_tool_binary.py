"""read_file tool behaviour on binary files.

``read_file`` decodes with UTF-8. Binary uploads (``.xlsx``, images, ...) raise
``UnicodeDecodeError`` deep in the sandbox layer, which previously surfaced to
the model as a vague ``Unexpected error reading file`` message. The model could
not tell that the file was binary, so it retried ``read_file`` instead of
switching to ``bash`` + pandas/openpyxl — burning LLM round-trips. These tests
pin the actionable error contract and guard the normal text path.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.sandbox.local.local_sandbox import LocalSandbox
from deerflow.sandbox.tools import read_file_tool


def _local_runtime(tmp_path: Path) -> SimpleNamespace:
    for sub in ("workspace", "uploads", "outputs"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    thread_data = {
        "workspace_path": str(tmp_path / "workspace"),
        "uploads_path": str(tmp_path / "uploads"),
        "outputs_path": str(tmp_path / "outputs"),
    }
    return SimpleNamespace(
        state={"sandbox": {"sandbox_id": "local:t1"}, "thread_data": thread_data},
        context={"thread_id": "t1"},
    )


def test_read_file_tool_binary_file_returns_actionable_hint(tmp_path, monkeypatch) -> None:
    runtime = _local_runtime(tmp_path)
    # .xlsx is a zip container: header bytes PK\x03\x04 plus a non-UTF-8 byte 0x82
    # that makes strict UTF-8 decoding fail (the exact byte seen in the field logs).
    (tmp_path / "uploads" / "data.xlsx").write_bytes(b"PK\x03\x04\x14\x00\x00\x00\x08\x00\x82\x6a\xb1\x55")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)

    result = read_file_tool.func(
        runtime=runtime,
        description="read uploaded excel",
        path="/mnt/user-data/uploads/data.xlsx",
    )

    assert "Unexpected error" not in result, result
    assert "binary" in result.lower(), result
    # The model must be steered to bash + pandas/openpyxl, not another read_file.
    assert "bash" in result.lower(), result


def test_read_file_tool_text_file_unaffected(tmp_path, monkeypatch) -> None:
    runtime = _local_runtime(tmp_path)
    (tmp_path / "uploads" / "notes.txt").write_text("hello 你好\nsecond line", encoding="utf-8")
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized", lambda runtime: LocalSandbox("t1"))
    monkeypatch.setattr("deerflow.sandbox.tools.ensure_thread_directories_exist", lambda runtime: None)

    result = read_file_tool.func(
        runtime=runtime,
        description="read notes",
        path="/mnt/user-data/uploads/notes.txt",
    )

    assert "hello 你好" in result, result
    assert "binary" not in result.lower(), result


@pytest.mark.parametrize("suffix", [".png", ".JPG", ".jpeg", ".webp", ".gif", ".bmp", ".tiff", ".avif"])
def test_known_image_rejected_without_sandbox_io(tmp_path, monkeypatch, suffix):
    runtime = _local_runtime(tmp_path)
    monkeypatch.setattr("deerflow.sandbox.tools.read_current_file_content", lambda *a: pytest.fail("image must not reach text endpoint"))
    result = read_file_tool.func(runtime=runtime, path="/mnt/user-data/uploads/image" + suffix)
    assert "READ_FILE_IMAGE_UNSUPPORTED" in result
    assert "if" in result.lower() and "view_image" in result
    assert "Do not retry" in result


@pytest.mark.asyncio
async def test_async_image_rejected_before_sandbox_startup(tmp_path, monkeypatch):
    async def no_sandbox(*a):
        pytest.fail("image rejection must not start/wait for sandbox")

    monkeypatch.setattr("deerflow.sandbox.tools.ensure_sandbox_initialized_async", no_sandbox)
    result = await read_file_tool.coroutine(runtime=_local_runtime(tmp_path), path="/mnt/user-data/uploads/image.png")
    assert "READ_FILE_IMAGE_UNSUPPORTED" in result


@pytest.mark.asyncio
async def test_disabled_skill_still_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setattr("deerflow.sandbox.tools._is_disabled_skill_path", lambda *a, **kw: True)
    result = await read_file_tool.coroutine(runtime=_local_runtime(tmp_path), path="/mnt/skills/public/disabled/image.png")
    assert "is disabled" in result


def test_svg_source_is_still_readable_text(tmp_path, monkeypatch):
    monkeypatch.setattr("deerflow.sandbox.tools.read_current_file_content", lambda *a: "<svg>你好</svg>")
    assert read_file_tool.func(runtime=_local_runtime(tmp_path), path="/mnt/user-data/workspace/plot.svg") == "<svg>你好</svg>"


@pytest.mark.parametrize("code", ["READ_FILE_TIMEOUT", "READ_FILE_TRANSPORT", "READ_FILE_IMAGE_UNSUPPORTED"])
def test_remote_failure_is_not_hidden_by_requested_line_range(tmp_path, monkeypatch, code):
    diagnostic = f"Error: {code}. The read failed."
    monkeypatch.setattr("deerflow.sandbox.tools.read_current_file_content", lambda *a: diagnostic)
    result = read_file_tool.func(runtime=_local_runtime(tmp_path), path="/mnt/user-data/workspace/notes.txt", start_line=20, end_line=30)
    assert result == diagnostic
