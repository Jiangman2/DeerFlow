"""Core behavior tests for present_files path normalization."""

import importlib
import zipfile
from types import SimpleNamespace

present_file_tool_module = importlib.import_module("deerflow.tools.builtins.present_file_tool")


def _make_runtime(outputs_path: str, workspace_path: str | None = None) -> SimpleNamespace:
    thread_data = {"outputs_path": outputs_path}
    if workspace_path is not None:
        thread_data["workspace_path"] = workspace_path
    return SimpleNamespace(
        state={"thread_data": thread_data},
        context={"thread_id": "thread-1"},
        config={},
    )


def _make_runtime_with_sandbox(outputs_path: str, sandbox_id: str = "sandbox-1") -> SimpleNamespace:
    return SimpleNamespace(
        state={
            "thread_data": {"outputs_path": outputs_path},
            "sandbox": {"sandbox_id": sandbox_id},
        },
        context={"thread_id": "thread-1"},
        config={},
    )


def _write_minimal_pptx(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")


def test_present_files_normalizes_host_outputs_path(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "report.md"
    artifact_path.write_text("ok")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(artifact_path)],
        tool_call_id="tc-1",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/report.md"]
    assert result.update["messages"][0].content == "Successfully presented files"


def test_present_files_keeps_virtual_outputs_path(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "summary.json"
    artifact_path.write_text("{}")

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, *, user_id=None: artifact_path),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/summary.json"],
        tool_call_id="tc-2",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/summary.json"]


def test_present_files_rejects_missing_virtual_outputs_path(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "missing.json"

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, *, user_id=None: artifact_path),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/missing.json"],
        tool_call_id="tc-missing",
    )

    assert "artifacts" not in result.update
    message = result.update["messages"][0]
    assert message.content == "Error: Only files can be presented: /mnt/user-data/outputs/missing.json"
    assert message.name == "present_files"
    assert message.status == "error"


def test_present_files_downloads_virtual_output_from_active_sandbox(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "remote.txt"

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, *, user_id=None: artifact_path),
    )
    sandbox = SimpleNamespace(download_file=lambda path: b"from sandbox")
    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: sandbox),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime_with_sandbox(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/remote.txt"],
        tool_call_id="tc-remote",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/remote.txt"]
    assert artifact_path.read_bytes() == b"from sandbox"


def test_present_files_auto_includes_pptx_preview_pdf(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    deck_path = outputs_dir / "deck.pptx"
    preview_path = outputs_dir / "deck.preview.pdf"
    _write_minimal_pptx(deck_path)
    preview_path.write_bytes(b"pdf")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(deck_path)],
        tool_call_id="tc-pptx",
    )

    assert result.update["artifacts"] == [
        "/mnt/user-data/outputs/deck.pptx",
        "/mnt/user-data/outputs/deck.preview.pdf",
    ]


def test_present_files_uses_config_thread_id_when_context_missing(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-from-config" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    artifact_path = outputs_dir / "summary.json"
    artifact_path.write_text("{}")

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path: artifact_path),
    )

    runtime = SimpleNamespace(
        state={"thread_data": {"outputs_path": str(outputs_dir)}},
        context={},
        config={"configurable": {"thread_id": "thread-from-config"}},
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=runtime,
        filepaths=["/mnt/user-data/outputs/summary.json"],
        tool_call_id="tc-config",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/summary.json"]
    assert result.update["messages"][0].content == "Successfully presented files"


def test_present_files_copies_workspace_file_to_outputs(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    workspace_dir = tmp_path / "threads" / "thread-1" / "user-data" / "workspace"
    outputs_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    deck_path = workspace_dir / "ppt-project" / "exports" / "deck.pptx"
    deck_path.parent.mkdir(parents=True)
    _write_minimal_pptx(deck_path)

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir), str(workspace_dir)),
        filepaths=[str(deck_path)],
        tool_call_id="tc-workspace",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/deck.pptx"]
    assert zipfile.is_zipfile(outputs_dir / "deck.pptx")


def test_present_files_rejects_fake_pptx(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    deck_path = outputs_dir / "fake.pptx"
    deck_path.write_text("<!DOCTYPE html><html></html>")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(deck_path)],
        tool_call_id="tc-fake-pptx",
    )

    assert "artifacts" not in result.update
    message = result.update["messages"][0]
    assert message.name == "present_files"
    assert message.status == "error"
    assert "Invalid PPTX file" in message.content


def test_present_files_rejects_paths_outside_outputs(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    workspace_dir = tmp_path / "threads" / "thread-1" / "user-data" / "workspace"
    outputs_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    leaked_path = workspace_dir / "notes.txt"
    leaked_path.write_text("leak")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(leaked_path)],
        tool_call_id="tc-3",
    )

    assert "artifacts" not in result.update
    assert result.update["messages"][0].content == f"Error: Only files in /mnt/user-data/outputs can be presented: {leaked_path}"
