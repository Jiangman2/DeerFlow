"""End-to-end contract tests for the vendored ppt-master command pipeline.

External image/TTS calls use deterministic provider doubles.  The tests still
exercise the real dispatchers, CLI parsing, project files, and output contracts
without consuming a paid API or depending on public-network availability.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_DIR = REPO_ROOT / "skills" / "public" / "ppt-master"
SCRIPTS_DIR = SKILL_DIR / "scripts"


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=cwd or REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _load_script(name: str):
    path = SCRIPTS_DIR / name
    module_name = f"ppt_master_e2e_{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS_DIR))
    return module


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "deck_ppt169_20260717"
    for name in ("sources", "svg_output", "svg_final", "notes", "images", "exports"):
        (project / name).mkdir(parents=True)
    (project / "sources" / "brief.md").write_text(
        "# Audit\nA deterministic deck.", encoding="utf-8"
    )
    (project / "spec_lock.md").write_text(
        "# Spec Lock\n\n"
        "## canvas\n- viewBox: 0 0 1280 720\n- format: PPT 16:9\n\n"
        "## colors\n"
        "- bg: #F8FAFC\n"
        "- primary: #2563EB\n"
        "- text: #0F172A\n\n"
        "## typography\n"
        "- font_family: Arial, sans-serif\n"
        "- title_family: Arial, sans-serif\n"
        "- body_family: Arial, sans-serif\n"
        "- body: 24\n"
        "- title: 42\n\n"
        "## pptx_structure\n- mode: flat\n\n"
        "## Canvas\n- format: ppt169\n",
        encoding="utf-8",
    )
    (project / "svg_output" / "01_cover.svg").write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
<rect id="background" width="1280" height="720" fill="#f8fafc"/>
<g id="content"><rect x="120" y="160" width="1040" height="400" fill="#ffffff" stroke="#2563eb"/>
<text x="640" y="360" text-anchor="middle" font-family="Arial" font-size="54" fill="#0f172a">PPT Master E2E</text></g>
</svg>""",
        encoding="utf-8",
    )
    (project / "notes" / "01_cover.md").write_text(
        "# 01_cover\nPipeline narration.", encoding="utf-8"
    )
    return project


def test_upstream_project_manager_has_no_deerflow_specific_path_guard():
    content = (SCRIPTS_DIR / "project_manager.py").read_text(encoding="utf-8")

    assert "/mnt/user-data/outputs" not in content
    assert "cannot be created under /mnt/user-data/outputs" not in content


def test_vendored_skill_keeps_deerflow_adapter_outside_upstream_body():
    content = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")

    assert 'version: "4.3.0"' in content
    assert "official_repository: \"https://github.com/hugohe3/ppt-master\"" in content
    assert "Hosted DeerFlow preview boundary" not in content
    assert "/mnt/user-data/outputs" not in content


def test_source_import_dispatcher_converts_text(tmp_path: Path):
    source = tmp_path / "brief.txt"
    output = tmp_path / "brief.md"
    source.write_text("PPT source material", encoding="utf-8")

    result = _run(
        str(SCRIPTS_DIR / "source_to_md.py"), str(source), "-o", str(output), "--json"
    )

    assert output.exists()
    assert "PPT source material" in output.read_text(encoding="utf-8")
    assert "brief.md" in result.stdout


def test_image_generation_dispatches_to_configured_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _load_script("image_gen.py")
    calls: list[dict] = []

    def generate(**kwargs):
        calls.append(kwargs)
        output = Path(kwargs["output_dir"]) / kwargs["filename"]
        output.write_bytes(b"\x89PNG\r\n\x1a\nprovider-double")
        return str(output)

    monkeypatch.setattr(
        module, "_resolve_backend", lambda: (SimpleNamespace(generate=generate), "qwen")
    )
    monkeypatch.setattr(module, "_load_image_env_file", lambda: None)
    monkeypatch.setattr(module, "_validate_runtime_config", lambda: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "image_gen.py",
            "offshore research illustration",
            "--backend",
            "qwen",
            "--output",
            str(tmp_path),
            "--filename",
            "hero.png",
        ],
    )

    module.main()

    assert (tmp_path / "hero.png").exists()
    assert calls[0]["prompt"] == "offshore research illustration"


def test_chart_svg_passes_quality_gate_and_exports(tmp_path: Path):
    project = _make_project(tmp_path)
    chart = SKILL_DIR / "templates" / "charts" / "column_chart.svg"
    if not chart.exists():
        pytest.skip("upstream chart fixture is unavailable")
    (project / "svg_output" / "01_cover.svg").write_bytes(chart.read_bytes())

    _run(
        str(SCRIPTS_DIR / "svg_quality_checker.py"),
        str(project / "svg_output"),
        "--quick-generate",
    )
    output = tmp_path / "chart.pptx"
    _run(
        str(SCRIPTS_DIR / "svg_to_pptx.py"),
        str(project),
        "-o",
        str(output),
        "--no-notes",
    )

    assert output.exists() and output.stat().st_size > 10_000


def test_quality_gate_rejects_css_line_height_before_native_export(tmp_path: Path):
    project = _make_project(tmp_path)
    svg_path = project / "svg_output" / "01_cover.svg"
    svg_path.write_text(
        svg_path.read_text(encoding="utf-8").replace(
            'font-size="54"',
            'font-size="54" style="line-height: 1.25"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _run(str(SCRIPTS_DIR / "svg_quality_checker.py"), str(project / "svg_output"))

    diagnostic = f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
    assert "line-height" in diagnostic


def test_native_export_rejects_missing_master_typography_rows(tmp_path: Path):
    project = _make_project(tmp_path)
    spec_lock = project / "spec_lock.md"
    spec_lock.write_text(
        spec_lock.read_text(encoding="utf-8").replace("- title: 42\n", ""),
        encoding="utf-8",
    )

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        _run(
            str(SCRIPTS_DIR / "svg_to_pptx.py"),
            str(project),
            "-o",
            str(tmp_path / "invalid.pptx"),
        )

    diagnostic = f"{exc_info.value.stdout}\n{exc_info.value.stderr}"
    assert "typography rows: title" in diagnostic


def test_template_fill_analyze_scaffold_and_check_plan(tmp_path: Path):
    pptx = pytest.importorskip("pptx")
    template = tmp_path / "template.pptx"
    presentation = pptx.Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    slide.shapes.title.text = "Template title"
    slide.placeholders[1].text = "Template body"
    presentation.save(template)
    library = tmp_path / "template.slide_library.json"
    plan = tmp_path / "fill_plan.json"

    _run(
        str(SCRIPTS_DIR / "template_fill_pptx.py"),
        "analyze",
        str(template),
        "-o",
        str(library),
    )
    _run(
        str(SCRIPTS_DIR / "template_fill_pptx.py"),
        "scaffold",
        str(library),
        "-o",
        str(plan),
    )
    _run(
        str(SCRIPTS_DIR / "template_fill_pptx.py"),
        "check-plan",
        str(library),
        str(plan),
    )

    assert library.exists() and plan.exists()


def test_audio_pipeline_uses_provider_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    module = _load_script("notes_to_audio.py")
    project = _make_project(tmp_path)

    monkeypatch.setattr(module, "_load_tts_env_file", lambda: None)
    monkeypatch.setattr(
        module.backend_qwen, "read_qwen_api_key", lambda _name: "test-key"
    )

    def generate(_text, output_path, **_kwargs):
        output_path.write_bytes(b"ID3provider-double")

    monkeypatch.setattr(module.backend_qwen, "generate", generate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "notes_to_audio.py",
            str(project),
            "--provider",
            "qwen",
            "--voice-id",
            "Cherry",
        ],
    )

    assert module.main() == 0
    assert (project / "audio" / "01_cover.wav").exists()


def test_export_pipeline_writes_openable_pptx_with_animation_options(tmp_path: Path):
    project = _make_project(tmp_path)
    output = tmp_path / "animated.pptx"

    _run(str(SCRIPTS_DIR / "finalize_svg.py"), str(project))
    _run(
        str(SCRIPTS_DIR / "svg_to_pptx.py"),
        str(project),
        "-s",
        "svg_final",
        "-o",
        str(output),
        "-t",
        "fade",
        "-a",
        "appear",
    )

    assert output.exists()
    presentation = pytest.importorskip("pptx").Presentation(output)
    assert len(presentation.slides) == 1
