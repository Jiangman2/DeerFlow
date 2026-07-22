"""Core behavior tests for present_files path normalization."""

import importlib
import zipfile
from pathlib import Path
from types import SimpleNamespace

present_file_tool_module = importlib.import_module("deerflow.tools.builtins.present_file_tool")


def _make_runtime(outputs_path: str) -> SimpleNamespace:
    workspace_path = str((Path(outputs_path).parent / "workspace").resolve())
    return SimpleNamespace(
        state={"thread_data": {"outputs_path": outputs_path, "workspace_path": workspace_path}},
        context={"thread_id": "thread-1"},
        config={},
    )


def _make_runtime_with_sandbox(outputs_path: str) -> SimpleNamespace:
    runtime = _make_runtime(outputs_path)
    runtime.state["sandbox"] = {"sandbox_id": "sandbox-1"}
    return runtime


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


def test_present_files_copies_current_workspace_file_into_outputs(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    workspace_dir = tmp_path / "threads" / "thread-1" / "user-data" / "workspace"
    outputs_dir.mkdir(parents=True)
    workspace_dir.mkdir(parents=True)
    workspace_file = workspace_dir / "notes.txt"
    workspace_file.write_text("deliverable")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(workspace_file)],
        tool_call_id="tc-3",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/notes.txt"]
    assert (outputs_dir / "notes.txt").read_text() == "deliverable"


def test_present_files_rejects_paths_outside_current_thread(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    leaked_path = tmp_path / "other-thread" / "secret.txt"
    leaked_path.parent.mkdir(parents=True)
    leaked_path.write_text("secret")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(leaked_path)],
        tool_call_id="tc-outside",
    )

    assert "artifacts" not in result.update
    assert result.update["messages"][0].content == f"Error: Only files in /mnt/user-data/outputs or the current workspace can be presented: {leaked_path}"


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


def test_present_files_persists_virtual_workspace_file_from_active_sandbox(tmp_path, monkeypatch):
    user_data_dir = tmp_path / "threads" / "thread-1" / "user-data"
    outputs_dir = user_data_dir / "outputs"
    workspace_file = user_data_dir / "workspace" / "generated.docx"
    outputs_dir.mkdir(parents=True)

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, *, user_id=None: workspace_file),
    )
    sandbox = SimpleNamespace(download_file=lambda path: b"document bytes")
    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: sandbox),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime_with_sandbox(str(outputs_dir)),
        filepaths=["/mnt/user-data/workspace/generated.docx"],
        tool_call_id="tc-workspace-remote",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/generated.docx"]
    assert (outputs_dir / "generated.docx").read_bytes() == b"document bytes"


def test_present_files_auto_includes_pptx_preview_pdf(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    deck_path = outputs_dir / "deck.pptx"
    preview_path = outputs_dir / "deck.preview.pdf"
    _write_minimal_pptx(deck_path)
    preview_path.write_bytes(b"%PDF-preview")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(deck_path)],
        tool_call_id="tc-pptx",
    )

    assert result.update["artifacts"] == [
        "/mnt/user-data/outputs/deck.pptx",
        "/mnt/user-data/outputs/deck.preview.pdf",
    ]
