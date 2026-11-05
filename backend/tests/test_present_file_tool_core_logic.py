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


def _write_minimal_docx(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")


def _write_minimal_xlsx(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")


def _write_minimal_skill(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "example-skill/SKILL.md",
            "---\nname: example-skill\ndescription: Example\n---\n",
        )


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
    workspace_file = user_data_dir / "workspace" / "generated.txt"
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
        filepaths=["/mnt/user-data/workspace/generated.txt"],
        tool_call_id="tc-workspace-remote",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/generated.txt"]
    assert (outputs_dir / "generated.txt").read_bytes() == b"document bytes"


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


def test_present_files_auto_includes_docx_preview_pdf(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    document_path = outputs_dir / "report.docx"
    preview_path = outputs_dir / "report.preview.pdf"
    _write_minimal_docx(document_path)
    preview_path.write_bytes(b"%PDF-preview")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(document_path)],
        tool_call_id="tc-docx",
    )

    assert result.update["artifacts"] == [
        "/mnt/user-data/outputs/report.docx",
        "/mnt/user-data/outputs/report.preview.pdf",
    ]


def test_present_files_generates_missing_xlsx_preview_in_active_sandbox(
    tmp_path,
    monkeypatch,
):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    workbook_path = outputs_dir / "metrics.xlsx"
    preview_path = outputs_dir / "metrics.preview.pdf"
    _write_minimal_xlsx(workbook_path)

    class PreviewSandbox:
        def execute_command(self, command, timeout=None):
            assert "libreoffice" in command
            assert "/mnt/user-data/outputs/metrics.xlsx" in command
            assert "UserInstallation" in command
            assert timeout == 240
            preview_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            return ""

        def download_file(self, path):
            assert path == "/mnt/user-data/outputs/metrics.preview.pdf"
            return preview_path.read_bytes()

    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: PreviewSandbox()),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime_with_sandbox(str(outputs_dir)),
        filepaths=[str(workbook_path)],
        tool_call_id="tc-xlsx-generate-preview",
    )

    assert result.update["artifacts"] == [
        "/mnt/user-data/outputs/metrics.xlsx",
        "/mnt/user-data/outputs/metrics.preview.pdf",
    ]


def test_present_files_generates_missing_pptx_preview_in_active_sandbox(
    tmp_path,
    monkeypatch,
):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    deck_path = outputs_dir / "deck.pptx"
    preview_path = outputs_dir / "deck.preview.pdf"
    _write_minimal_pptx(deck_path)

    class PreviewSandbox:
        def execute_command(self, command, timeout=None):
            assert "libreoffice" in command
            assert "/mnt/user-data/outputs/deck.pptx" in command
            assert timeout == 240
            preview_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            return ""

        def download_file(self, path):
            return preview_path.read_bytes()

    sandbox = PreviewSandbox()
    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: sandbox),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime_with_sandbox(str(outputs_dir)),
        filepaths=[str(deck_path)],
        tool_call_id="tc-pptx-generate-preview",
    )

    assert result.update["artifacts"] == [
        "/mnt/user-data/outputs/deck.pptx",
        "/mnt/user-data/outputs/deck.preview.pdf",
    ]
    assert preview_path.read_bytes().startswith(b"%PDF-")


def test_present_files_generates_preview_from_original_workspace_deck(
    tmp_path,
    monkeypatch,
):
    user_data_dir = tmp_path / "threads" / "thread-1" / "user-data"
    outputs_dir = user_data_dir / "outputs"
    workspace_deck = user_data_dir / "workspace" / "projects" / "deck" / "exports" / "报告.pptx"
    persisted_deck = outputs_dir / "报告.pptx"
    preview_path = outputs_dir / "报告.preview.pdf"
    workspace_preview_path = workspace_deck.with_suffix(".preview.pdf")
    workspace_deck.parent.mkdir(parents=True)
    outputs_dir.mkdir(parents=True)
    _write_minimal_pptx(workspace_deck)

    def resolve_virtual_path(thread_id, path, *, user_id=None):
        del thread_id, user_id
        if path == "/mnt/user-data/workspace/projects/deck/exports/报告.pptx":
            return workspace_deck
        if path == "/mnt/user-data/workspace/projects/deck/exports/报告.preview.pdf":
            return workspace_preview_path
        if path == "/mnt/user-data/outputs/报告.preview.pdf":
            return preview_path
        if path == "/mnt/user-data/outputs/报告.pptx":
            return persisted_deck
        raise AssertionError(f"Unexpected virtual path: {path}")

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=resolve_virtual_path),
    )

    class PreviewSandbox:
        def execute_command(self, command, timeout=None):
            assert "/mnt/user-data/workspace/projects/deck/exports/报告.pptx" in command
            assert "/mnt/user-data/outputs/报告.pptx" not in command.split("--convert-to pdf", 1)[1]
            assert timeout == 240
            preview_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            return ""

        def download_file(self, path):
            return preview_path.read_bytes()

    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: PreviewSandbox()),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime_with_sandbox(str(outputs_dir)),
        filepaths=["/mnt/user-data/workspace/projects/deck/exports/报告.pptx"],
        tool_call_id="tc-workspace-pptx-preview",
    )

    assert result.update["artifacts"] == [
        "/mnt/user-data/outputs/报告.pptx",
        "/mnt/user-data/outputs/报告.preview.pdf",
    ]
    assert persisted_deck.is_file()
    assert preview_path.read_bytes().startswith(b"%PDF-")


def test_present_files_rejects_pptx_when_preview_generation_fails(
    tmp_path,
    monkeypatch,
):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    deck_path = outputs_dir / "报告_20260805_111535.pptx"
    _write_minimal_pptx(deck_path)

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, **kwargs: outputs_dir / path.removeprefix("/mnt/user-data/outputs/")),
    )
    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: None),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime_with_sandbox(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/报告_20260805_111535.pptx"],
        tool_call_id="tc-pptx-preview-required",
    )

    assert "artifacts" not in result.update


def test_present_files_rejects_docx_when_preview_generation_fails(
    tmp_path,
    monkeypatch,
):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    document_path = outputs_dir / "报告.docx"
    _write_minimal_docx(document_path)

    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, **kwargs: outputs_dir / path.removeprefix("/mnt/user-data/outputs/")),
    )
    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: None),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime_with_sandbox(str(outputs_dir)),
        filepaths=["/mnt/user-data/outputs/报告.docx"],
        tool_call_id="tc-docx-preview-required",
    )

    assert "artifacts" not in result.update
    assert "Office preview generation failed" in result.update["messages"][0].content


def test_ppt_master_acceptance_failure_keeps_workflow_blocked(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    deck_path = outputs_dir / "demo.pptx"
    preview_path = outputs_dir / "demo.preview.pdf"
    _write_minimal_pptx(deck_path)
    preview_path.write_bytes(b"%PDF-preview")
    runtime = _make_runtime_with_sandbox(str(outputs_dir))
    runtime.state["skill_workflow"] = {
        "activation_id": "a1",
        "skill_name": "ppt-master",
        "completion_contract": "presentation_delivery",
    }
    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, **kwargs: outputs_dir / path.removeprefix("/mnt/user-data/outputs/")),
    )
    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: SimpleNamespace()),
    )
    monkeypatch.setattr(
        present_file_tool_module,
        "validate_ppt_master_delivery",
        lambda **kwargs: (_ for _ in ()).throw(ValueError("缺少对象动画")),
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=runtime,
        filepaths=[str(deck_path)],
        tool_call_id="tc-accept-fail",
    )

    assert "artifacts" not in result.update
    assert "缺少对象动画" in result.update["messages"][0].content
    assert result.update["skill_workflow"]["status"] == "blocked"
    assert result.update["skill_workflow"]["resume_pointer"] == str(deck_path)
    assert result.update["skill_workflow"]["gate_owner_tool"] == "present_files"


def test_ppt_master_acceptance_success_completes_workflow(tmp_path, monkeypatch):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    deck_path = outputs_dir / "demo.pptx"
    preview_path = outputs_dir / "demo.preview.pdf"
    _write_minimal_pptx(deck_path)
    preview_path.write_bytes(b"%PDF-preview")
    runtime = _make_runtime_with_sandbox(str(outputs_dir))
    runtime.state["skill_workflow"] = {
        "activation_id": "a1",
        "skill_name": "ppt-master",
        "completion_contract": "presentation_delivery",
    }
    monkeypatch.setattr(
        present_file_tool_module,
        "get_paths",
        lambda: SimpleNamespace(resolve_virtual_path=lambda thread_id, path, **kwargs: outputs_dir / path.removeprefix("/mnt/user-data/outputs/")),
    )
    monkeypatch.setattr(
        present_file_tool_module,
        "get_sandbox_provider",
        lambda: SimpleNamespace(get=lambda sandbox_id: SimpleNamespace()),
    )
    monkeypatch.setattr(
        present_file_tool_module,
        "validate_ppt_master_delivery",
        lambda **kwargs: {"status": "passed"},
    )

    result = present_file_tool_module.present_file_tool.func(
        runtime=runtime,
        filepaths=[str(deck_path)],
        tool_call_id="tc-accept-pass",
    )

    assert result.update["artifacts"] == [
        "/mnt/user-data/outputs/demo.pptx",
        "/mnt/user-data/outputs/demo.preview.pdf",
    ]
    assert result.update["skill_workflow"]["status"] == "completed"
    assert result.update["skill_workflow"]["current_stage"] == "delivery-accepted"
    message = result.update["messages"][0]
    assert message.status == "success"
    assert message.artifact["presented_files"][0].endswith("demo.pptx")


def test_present_files_accepts_valid_skill_package(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    skill_path = outputs_dir / "example.skill"
    _write_minimal_skill(skill_path)

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(skill_path)],
        tool_call_id="tc-skill",
    )

    assert result.update["artifacts"] == ["/mnt/user-data/outputs/example.skill"]


def test_present_files_rejects_invalid_skill_package(tmp_path):
    outputs_dir = tmp_path / "threads" / "thread-1" / "user-data" / "outputs"
    outputs_dir.mkdir(parents=True)
    skill_path = outputs_dir / "broken.skill"
    with zipfile.ZipFile(skill_path, "w") as archive:
        archive.writestr("README.md", "not a skill")

    result = present_file_tool_module.present_file_tool.func(
        runtime=_make_runtime(str(outputs_dir)),
        filepaths=[str(skill_path)],
        tool_call_id="tc-invalid-skill",
    )

    assert "artifacts" not in result.update
    assert "missing SKILL.md" in result.update["messages"][0].content
