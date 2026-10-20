import hashlib
import json
import zipfile

import pytest

from deerflow.tools.builtins.presentation_acceptance import (
    _design_requirements,
    ppt_master_template_contract_issues,
    ppt_master_template_install_issues,
    ppt_master_template_svg_issues,
    validate_ppt_master_delivery,
)


class _Sandbox:
    def __init__(self, files, report):
        self.files = files
        self.report = report
        self.commands = []

    def read_file(self, path):
        if path not in self.files:
            raise OSError(path)
        return self.files[path]

    def execute_command(self, command, timeout=None):
        self.commands.append((command, timeout))
        return json.dumps(self.report, ensure_ascii=False)


class _InstallSandbox(_Sandbox):
    def __init__(self, files, report, *, reject_skill_download=False):
        super().__init__(files, report)
        self.reject_skill_download = reject_skill_download
        self.downloaded = []

    def list_dir(self, path, max_depth=2):
        prefix = path.rstrip("/") + "/"
        return sorted(key for key in self.files if key.startswith(prefix))

    def download_file(self, path):
        self.downloaded.append(path)
        if self.reject_skill_download and path.startswith("/mnt/skills/"):
            raise PermissionError("downloads outside /mnt/user-data are forbidden")
        if path not in self.files:
            raise OSError(path)
        value = self.files[path]
        return value if isinstance(value, bytes) else value.encode("utf-8")

    def execute_command(self, command, timeout=None):
        self.commands.append((command, timeout))
        if "__DEERFLOW_PPT_TEMPLATE_MANIFEST_V1__" not in command:
            return json.dumps(self.report, ensure_ascii=False)
        roots = {
            "source": command.split("__SOURCE_ROOT__", 1)[1].split("__", 1)[0],
            "target": command.split("__TARGET_ROOT__", 1)[1].split("__", 1)[0],
        }
        manifests = {}
        for label, root in roots.items():
            manifest = {}
            for path, value in self.files.items():
                prefix = root.rstrip("/") + "/"
                if not path.startswith(prefix):
                    continue
                relative = path[len(prefix) :]
                if relative.split("/", 1)[0] not in {"templates", "images", "icons"}:
                    continue
                payload = value if isinstance(value, bytes) else value.encode("utf-8")
                manifest[relative] = {
                    "size": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            manifests[label] = manifest
        return "__DEERFLOW_PPT_TEMPLATE_MANIFEST_V1__" + json.dumps(
            {"ok": True, "roots": manifests},
            ensure_ascii=False,
        )


def _write_pptx(path, charts=2):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
        for index in range(1, charts + 1):
            archive.writestr(f"ppt/charts/chart{index}.xml", "<c:chart/>")


def _report(*, charts=True, audio=True, animations=True):
    media = [
        {"kind": "image"},
        {"kind": "image"},
    ]
    if audio:
        media.append({"kind": "audio"})
    return {
        "status": "passed",
        "package": {
            "parts": {
                "slides": 5,
                "notes": 5,
                "masters": 2,
                "layouts": 5,
            }
        },
        "slides": {"count": 5},
        "media": {"embedded": media},
        "motion": {
            "transitions": {
                "visual_effect_slide_count": 5,
                "timed_advance_slide_count": 5,
            },
            "object_animations": {
                "object_animation_slide_count": 3 if animations else 0,
                "audio_timing_slide_count": 5 if audio else 0,
            },
        },
    }


def _workflow():
    return {
        "skill_name": "ppt-master",
        "skill_path": "/mnt/skills/public/ppt-master/SKILL.md",
        "selected_authority": "/mnt/skills/public/ppt-master/workflows/generate-pptx.md",
        "project_path": "/mnt/user-data/workspace/projects/demo",
        "required_capabilities": {
            "presentation": True,
            "ai_images": True,
            "speaker_notes": True,
            "transitions": True,
            "timed_advance": True,
            "object_animations": True,
            "narration_audio": True,
            "native_charts": True,
            "template": True,
        },
    }


def _files():
    project = "/mnt/user-data/workspace/projects/demo"
    return {
        f"{project}/design_spec.md": (
            "| Page Count | 5 |\n| Speaker Notes | enabled |\n| Custom Animations | enabled |\n| Narration Audio | enabled |\n| AI Image Acquisition Path | api |\n- **Template Application**: 中国海油 Deck 模板\n- **Native-ready**: yes\n"
        ),
        f"{project}/spec_lock.md": (
            "## pptx_structure\n- mode: structured\n\n"
            "## pptx_masters\n- m1: Master 1\n- m2: Master 2\n\n"
            "## pptx_layouts\n"
            "- l1: Layout 1\n- l2: Layout 2\n- l3: Layout 3\n- l4: Layout 4\n- l5: Layout 5\n\n"
            "## images\n- ai1.png: images/ai1.png | source=ai\n- ai2.png: images/ai2.png | source=ai\n\n"
            "## page_charts\n- P03: column_chart\n- P04: donut_chart\n"
        ),
        f"{project}/validation/svg_quality_report.json": json.dumps({"stage": "final", "summary": {"errors": 0}}),
    }


def test_markdown_bold_contract_rows_enable_native_chart_and_template_requirements():
    required, _ = _design_requirements(
        "- **Template Application**: 中国海油 Deck 模板\n- **Native-ready**: yes\n",
        None,
        {"presentation": True},
    )

    assert required["native_charts"] is True
    assert required["template"] is True


def test_complete_contract_passes_with_official_delivery_report(tmp_path):
    pptx = tmp_path / "demo.pptx"
    _write_pptx(pptx, charts=2)
    sandbox = _Sandbox(_files(), _report())

    receipt = validate_ppt_master_delivery(
        sandbox=sandbox,
        workflow=_workflow(),
        source_path="/mnt/user-data/workspace/projects/demo/exports/demo.pptx",
        actual_pptx=pptx,
    )

    assert receipt["status"] == "passed"
    assert receipt["charts"] == 2
    assert receipt["audio"] == 1
    assert receipt["object_animation_slides"] == 3
    assert "pptx_delivery_check.py" in sandbox.commands[0][0]


def test_missing_requested_capabilities_blocks_delivery(tmp_path):
    pptx = tmp_path / "demo.pptx"
    _write_pptx(pptx, charts=0)
    sandbox = _Sandbox(_files(), _report(audio=False, animations=False))

    with pytest.raises(ValueError) as exc_info:
        validate_ppt_master_delivery(
            sandbox=sandbox,
            workflow=_workflow(),
            source_path="/mnt/user-data/workspace/projects/demo/exports/demo.pptx",
            actual_pptx=pptx,
        )

    message = str(exc_info.value)
    assert "原生图表数量不足" in message
    assert "对象级动画" in message
    assert "配音音频" in message
    assert "音频播放时序" in message


def test_missing_route_and_default_artifacts_are_not_accepted(tmp_path):
    pptx = tmp_path / "demo.pptx"
    _write_pptx(pptx, charts=2)
    workflow = _workflow()
    workflow["selected_authority"] = ""
    sandbox = _Sandbox({}, _report())

    with pytest.raises(ValueError, match="顶层路线"):
        validate_ppt_master_delivery(
            sandbox=sandbox,
            workflow=workflow,
            source_path="/mnt/user-data/workspace/projects/demo/exports/demo.pptx",
            actual_pptx=pptx,
        )


def _with_confirmed_deck_selection(files):
    project = "/mnt/user-data/workspace/projects/demo"
    selected = dict(files)
    selected[f"{project}/confirm_ui/template_selection.json"] = json.dumps(
        {
            "schema_version": 1,
            "phase": "template",
            "status": "confirmed",
            "mode": "templates",
            "selection_sha256": "selection-hash",
            "selections": [
                {
                    "source": "library",
                    "kind": "deck",
                    "id": "example-deck",
                    "workspace_root": "/mnt/skills/public/ppt-master/templates/decks/example-deck",
                }
            ],
        }
    )
    selected[f"{project}/confirm_ui/template_handoff.json"] = json.dumps(
        {
            "schema_version": 1,
            "phase": "template",
            "status": "ready",
            "mode": "templates",
            "selection_sha256": "selection-hash",
        }
    )
    return selected


def test_confirmed_deck_requires_explicit_template_reuse_scope(tmp_path):
    pptx = tmp_path / "demo.pptx"
    _write_pptx(pptx, charts=2)
    sandbox = _Sandbox(_with_confirmed_deck_selection(_files()), _report())

    with pytest.raises(ValueError, match="template_reuse_scope"):
        validate_ppt_master_delivery(
            sandbox=sandbox,
            workflow=_workflow(),
            source_path="/mnt/user-data/workspace/projects/demo/exports/demo.pptx",
            actual_pptx=pptx,
        )


def test_confirmed_deck_layout_scope_requires_complete_structured_mapping(tmp_path):
    pptx = tmp_path / "demo.pptx"
    _write_pptx(pptx, charts=2)
    files = _with_confirmed_deck_selection(_files())
    project = "/mnt/user-data/workspace/projects/demo"
    files[f"{project}/spec_lock.md"] = files[f"{project}/spec_lock.md"].replace(
        "- mode: structured",
        "- mode: structured\n- template_reuse_scope: layout",
    )
    sandbox = _Sandbox(files, _report())

    with pytest.raises(ValueError, match="page_layouts"):
        validate_ppt_master_delivery(
            sandbox=sandbox,
            workflow=_workflow(),
            source_path="/mnt/user-data/workspace/projects/demo/exports/demo.pptx",
            actual_pptx=pptx,
        )


def test_confirmed_deck_layout_scope_accepts_complete_structured_mapping(tmp_path):
    pptx = tmp_path / "demo.pptx"
    _write_pptx(pptx, charts=2)
    files = _with_confirmed_deck_selection(_files())
    project = "/mnt/user-data/workspace/projects/demo"
    files[f"{project}/spec_lock.md"] = files[f"{project}/spec_lock.md"].replace(
        "- mode: structured",
        "- mode: structured\n- template_reuse_scope: layout\n- template_adherence: adaptive",
    ) + (
        "\n## page_layouts\n"
        "- P01: templates/cover.svg\n- P02: templates/content.svg\n"
        "- P03: templates/content.svg\n- P04: templates/content.svg\n"
        "- P05: templates/ending.svg\n"
        "\n## page_pptx_layouts\n"
        "- P01: l1\n- P02: l2\n- P03: l3\n- P04: l4\n- P05: l5\n"
    )
    sandbox = _Sandbox(files, _report())

    receipt = validate_ppt_master_delivery(
        sandbox=sandbox,
        workflow=_workflow(),
        source_path="/mnt/user-data/workspace/projects/demo/exports/demo.pptx",
        actual_pptx=pptx,
    )

    assert receipt["template_reuse_scope"] == "layout"
    assert receipt["pptx_structure_mode"] == "structured"


def test_confirmed_deck_style_scope_remains_a_valid_flat_visual_reference(tmp_path):
    pptx = tmp_path / "demo.pptx"
    _write_pptx(pptx, charts=2)
    files = _with_confirmed_deck_selection(_files())
    project = "/mnt/user-data/workspace/projects/demo"
    files[f"{project}/spec_lock.md"] = (
        "## pptx_structure\n- mode: flat\n- template_reuse_scope: style\n\n## images\n- ai1.png: images/ai1.png | source=ai\n- ai2.png: images/ai2.png | source=ai\n\n## page_charts\n- P03: column_chart\n- P04: donut_chart\n"
    )
    sandbox = _Sandbox(files, _report())

    receipt = validate_ppt_master_delivery(
        sandbox=sandbox,
        workflow=_workflow(),
        source_path="/mnt/user-data/workspace/projects/demo/exports/demo.pptx",
        actual_pptx=pptx,
    )

    assert receipt["template_reuse_scope"] == "style"
    assert receipt["pptx_structure_mode"] == "flat"


def _structured_template_files():
    project = "/mnt/user-data/workspace/projects/demo"
    files = _with_confirmed_deck_selection(_files())
    files[f"{project}/design_spec.md"] = files[f"{project}/design_spec.md"].replace(
        "中国海油 Deck 模板",
        "P01 使用 01_cover.svg；P02-P04 使用 05_content.svg；P05 使用 08_ending.svg",
    )
    files[f"{project}/spec_lock.md"] = (
        "## pptx_structure\n"
        "- mode: structured\n"
        "- template_reuse_scope: layout\n"
        "- template_adherence: adaptive\n\n"
        "## pptx_masters\n- master_01: CNOOC Master\n\n"
        "## pptx_layouts\n- cover: Cover\n- content: Content\n- ending: Ending\n\n"
        "## page_pptx_layouts\n"
        "- P01: cover\n- P02: content\n- P03: content\n- P04: content\n- P05: ending\n\n"
        "## page_layouts\n"
        "- P01: 01_cover\n- P02: 05_content\n- P03: 05_content\n"
        "- P04: 05_content\n- P05: 08_ending\n"
    )
    prototype = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720" '
        'data-pptx-master="master_01" data-pptx-layout="cover">'
        '<rect id="layout-bg" x="0" y="0" width="1280" height="720" fill="#fff" data-pptx-layer="layout"/>'
        '<g id="slot-title" data-pptx-slot="title" data-pptx-frame="10 10 100 40" '
        'data-pptx-bounds="10 10 100 40" data-pptx-placeholder="title"><text>标题</text></g>'
        "</svg>"
    )
    for name in ("01_cover", "05_content", "08_ending"):
        files[f"{project}/templates/{name}.svg"] = prototype.replace('data-pptx-layout="cover"', f'data-pptx-layout="{name}"')
    return files


def test_template_contract_rejects_flat_lock_that_contradicts_named_deck_prototypes():
    project = "/mnt/user-data/workspace/projects/demo"
    files = _structured_template_files()
    files[f"{project}/spec_lock.md"] = "## pptx_structure\n- mode: flat\n- template_reuse_scope: style\n"
    sandbox = _Sandbox(files, _report())

    issues = ppt_master_template_contract_issues(
        sandbox,
        project,
        _workflow(),
        verify_prototype_files=True,
    )

    assert any("contradicts" in issue for issue in issues)


def test_template_contract_requires_current_run_template_authorities():
    project = "/mnt/user-data/workspace/projects/demo"
    sandbox = _Sandbox(_structured_template_files(), _report())
    workflow = _workflow()
    workflow["loaded_authority_documents"] = [workflow["selected_authority"]]

    issues = ppt_master_template_contract_issues(
        sandbox,
        project,
        workflow,
        require_current_run_authority=True,
        verify_prototype_files=True,
    )

    assert any("strategist-template.md" in issue for issue in issues)
    assert any("spec_lock_reference.md" in issue for issue in issues)


def test_structured_svg_must_preserve_inherited_template_atoms_and_slots():
    project = "/mnt/user-data/workspace/projects/demo"
    files = _structured_template_files()
    sandbox = _Sandbox(files, _report())
    spec_lock = files[f"{project}/spec_lock.md"]
    prototype = files[f"{project}/templates/01_cover.svg"]
    valid_page = prototype.replace(">标题</text>", ">中国海油智能化</text>")
    imitation = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#1F497D"/></svg>'

    assert (
        ppt_master_template_svg_issues(
            sandbox,
            project,
            spec_lock,
            f"{project}/svg_output/01_cover.svg",
            valid_page,
        )
        == []
    )
    issues = ppt_master_template_svg_issues(
        sandbox,
        project,
        spec_lock,
        f"{project}/svg_output/01_cover.svg",
        imitation,
    )

    assert any("data-pptx-master" in issue for issue in issues)
    assert any("#layout-bg" in issue for issue in issues)


def test_single_template_install_requires_every_portable_workspace_file_to_match():
    project = "/mnt/user-data/workspace/projects/demo"
    source = "/mnt/skills/public/ppt-master/templates/decks/中国海油"
    selection = {
        "status": "confirmed",
        "mode": "templates",
        "selections": [{"kind": "deck", "id": "中国海油", "workspace_root": source}],
    }
    files = {
        f"{project}/confirm_ui/template_selection.json": json.dumps(selection),
        f"{source}/templates/design_spec.md": "deck spec",
        f"{source}/templates/01_cover.svg": "<svg/>",
        f"{source}/images/image9.png": b"png-bytes",
        f"{project}/templates/design_spec.md": "deck spec",
    }
    sandbox = _InstallSandbox(files, _report())

    issues = ppt_master_template_install_issues(sandbox, project)

    assert "installed template is missing templates/01_cover.svg" in issues
    assert "installed template is missing images/image9.png" in issues

    files[f"{project}/templates/01_cover.svg"] = "<svg/>"
    files[f"{project}/images/image9.png"] = b"png-bytes"
    assert ppt_master_template_install_issues(sandbox, project) == []


def test_template_install_uses_trusted_manifest_when_skill_download_is_forbidden():
    project = "/mnt/user-data/workspace/projects/demo"
    source = "/mnt/skills/public/ppt-master/templates/decks/china-offshore-oil"
    selection = {
        "status": "confirmed",
        "mode": "templates",
        "selections": [{"kind": "deck", "id": "china-offshore-oil", "workspace_root": source}],
    }
    files = {
        f"{project}/confirm_ui/template_selection.json": json.dumps(selection),
        f"{source}/templates/01_cover.svg": "<svg/>",
        f"{source}/images/cover.png": b"png-bytes",
        f"{project}/templates/01_cover.svg": "<svg/>",
        f"{project}/images/cover.png": b"png-bytes",
    }
    sandbox = _InstallSandbox(files, _report(), reject_skill_download=True)

    assert ppt_master_template_install_issues(sandbox, project) == []
    assert sandbox.downloaded == []
    assert len(sandbox.commands) == 1
    assert sandbox.commands[0][1] == 15


def test_template_install_rejects_workspace_outside_ppt_master_template_library():
    project = "/mnt/user-data/workspace/projects/demo"
    selection = {
        "status": "confirmed",
        "mode": "templates",
        "selections": [{"kind": "deck", "id": "bad", "workspace_root": "/mnt/skills/public/other-skill"}],
    }
    sandbox = _InstallSandbox(
        {f"{project}/confirm_ui/template_selection.json": json.dumps(selection)},
        _report(),
    )

    assert ppt_master_template_install_issues(sandbox, project) == ["confirmed template workspace_root is outside the ppt-master template library"]
    assert sandbox.commands == []
