"""Host adapter for accepting a ppt-master presentation deliverable.

The vendored Skill remains the source of workflow truth.  DeerFlow invokes its
official read-only delivery checker, then compares those package facts with the
persisted user/design contract before ``present_files`` may mark the workflow
complete.
"""

from __future__ import annotations

import json
import re
import shlex
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from deerflow.sandbox.exceptions import SandboxError

_PROJECT_FROM_EXPORT_RE = re.compile(r"^(/mnt/user-data/workspace/projects/[^/]+)/exports/[^/]+$")
_ENABLED_ROW_RE = re.compile(r"\|\s*{label}\s*\|\s*enabled\b", re.IGNORECASE)
_PAGE_COUNT_RE = re.compile(r"\|\s*Page Count\s*\|\s*(\d+)\s*\|", re.IGNORECASE)
_SPEC_SECTION_RE = re.compile(r"^##\s+([A-Za-z0-9_]+)\s*$", re.MULTILINE)
_TEMPLATE_APPLICATION_RE = re.compile(
    r"^\s*-\s*\*\*Template Application\*\*:\s*(.*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_TEMPLATE_PROTOTYPE_RE = re.compile(r"(?<![A-Za-z0-9_-])(\d{2}_[A-Za-z0-9_-]+)(?:\.svg)?(?![A-Za-z0-9_-])")
_PROJECT_ROOT_RE = re.compile(r"^/mnt/user-data/workspace/projects/[^/]+$")
_PPT_MASTER_TEMPLATE_WORKSPACE_RE = re.compile(
    r"^/mnt/skills/(?:public|custom|legacy)/ppt-master/templates/"
    r"(?:brands|styles|layouts|decks)/[^/]+$"
)
_TEMPLATE_MANIFEST_MARKER = "__DEERFLOW_PPT_TEMPLATE_MANIFEST_V1__"


def _trusted_template_manifests(
    sandbox: Any,
    source_root: str,
    project: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], str | None]:
    """Fingerprint a selected Skill workspace and its project-local copy.

    Browser-download APIs intentionally reject ``/mnt/skills``.  Template
    acceptance still needs byte-level comparison, so the host runs one fixed,
    read-only manifest program inside the active sandbox.  The only variable
    inputs are roots validated by the caller; file contents never leave the
    sandbox and the result contains only relative paths, sizes, and SHA-256
    digests.
    """

    script = f"""
import hashlib
import json
import os
import sys

MARKER = {_TEMPLATE_MANIFEST_MARKER!r}
PORTABLE_ROOTS = ("templates", "images", "icons")


def fingerprint(root):
    result = {{}}
    for portable_root in PORTABLE_ROOTS:
        base = os.path.join(root, portable_root)
        if not os.path.isdir(base):
            continue
        for current, dirs, files in os.walk(base):
            dirs.sort()
            files.sort()
            for filename in files:
                path = os.path.join(current, filename)
                digest = hashlib.sha256()
                size = 0
                with open(path, "rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        size += len(chunk)
                        digest.update(chunk)
                relative = os.path.relpath(path, root).replace(os.sep, "/")
                result[relative] = {{"size": size, "sha256": digest.hexdigest()}}
    return result


try:
    roots = {{"source": fingerprint(sys.argv[1]), "target": fingerprint(sys.argv[2])}}
except Exception as exc:
    print(MARKER + json.dumps({{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}"}}))
    raise SystemExit(2)
print(MARKER + json.dumps({{"ok": True, "roots": roots}}, sort_keys=True))
""".strip()
    command = (
        f"python3 -c {shlex.quote(script)} {shlex.quote(source_root)} {shlex.quote(project)} "
        f"# __SOURCE_ROOT__{source_root}____TARGET_ROOT__{project}__"
    )
    try:
        output = sandbox.execute_command(command, timeout=15)
    except Exception as exc:
        return {}, {}, f"trusted template manifest command failed: {type(exc).__name__}"
    if not isinstance(output, str):
        return {}, {}, "trusted template manifest command returned a non-text result"
    marker_index = output.find(_TEMPLATE_MANIFEST_MARKER)
    if marker_index < 0:
        detail = output.strip().splitlines()[-1] if output.strip() else "no output"
        return {}, {}, f"trusted template manifest command returned no receipt: {detail[:240]}"
    payload_text = output[marker_index + len(_TEMPLATE_MANIFEST_MARKER) :].lstrip()
    try:
        payload, _ = json.JSONDecoder().raw_decode(payload_text)
    except json.JSONDecodeError as exc:
        return {}, {}, f"trusted template manifest receipt is invalid JSON: {exc.msg}"
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        detail = payload.get("error") if isinstance(payload, dict) else "non-object receipt"
        return {}, {}, f"trusted template manifest failed: {detail}"
    roots = payload.get("roots")
    source = roots.get("source") if isinstance(roots, dict) else None
    target = roots.get("target") if isinstance(roots, dict) else None
    if not isinstance(source, dict) or not isinstance(target, dict):
        return {}, {}, "trusted template manifest receipt is missing source/target mappings"
    if not all(isinstance(path, str) and isinstance(fingerprint, dict) for path, fingerprint in source.items()):
        return {}, {}, "trusted template source manifest has invalid entries"
    if not all(isinstance(path, str) and isinstance(fingerprint, dict) for path, fingerprint in target.items()):
        return {}, {}, "trusted template target manifest has invalid entries"
    return source, target, None


def _safe_read(sandbox: Any, path: str) -> str | None:
    try:
        content = sandbox.read_file(path)
    except (OSError, RuntimeError, ValueError, SandboxError):
        return None
    return content if isinstance(content, str) else None


def _project_path(source_path: str, workflow: dict[str, Any]) -> str | None:
    match = _PROJECT_FROM_EXPORT_RE.match(source_path)
    if match:
        return match.group(1)
    value = workflow.get("project_path")
    if isinstance(value, str) and value.startswith("/mnt/user-data/workspace/projects/"):
        return value.rstrip("/")
    return None


def _parse_json_output(output: str) -> dict[str, Any]:
    start = output.find("{")
    if start < 0:
        raise ValueError("ppt-master delivery checker did not return JSON")
    try:
        value = json.loads(output[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"ppt-master delivery checker returned invalid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise ValueError("ppt-master delivery checker returned a non-object report")
    return value


def _section_rows(text: str, section: str) -> list[str]:
    matches = list(_SPEC_SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        if match.group(1) != section:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        return [
            line
            for line in text[match.end() : end].splitlines()
            if line.lstrip().startswith("-")
        ]
    return []


def _section_values(text: str | None, section: str) -> dict[str, str]:
    """Return the simple ``- key: value`` rows from one spec-lock section."""

    if not text:
        return {}
    values: dict[str, str] = {}
    for row in _section_rows(text, section):
        match = re.match(r"^\s*-\s*([^:]+?)\s*:\s*(.*?)\s*$", row)
        if match:
            values[match.group(1).strip()] = match.group(2).strip()
    return values


def _json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _confirmed_structural_template_selection(
    sandbox: Any,
    project: str,
) -> tuple[bool, list[str]]:
    """Resolve the official Confirm UI Deck/Layout handoff.

    The Skill permits a selected Deck/Layout to be consumed as ``style``,
    ``layout``, or ``mirror``.  The host does not choose among those routes;
    it only requires Strategist to persist that official decision before a
    generated package can be accepted.
    """

    selection = _json_object(
        _safe_read(sandbox, f"{project}/confirm_ui/template_selection.json")
    )
    if not selection or selection.get("status") != "confirmed":
        return False, []
    if selection.get("mode") != "templates":
        return False, []
    selections = selection.get("selections")
    structural = [
        item
        for item in selections
        if isinstance(item, dict) and str(item.get("kind") or "").casefold() in {"deck", "layout"}
    ] if isinstance(selections, list) else []
    if not structural:
        return False, []

    issues: list[str] = []
    handoff = _json_object(
        _safe_read(sandbox, f"{project}/confirm_ui/template_handoff.json")
    )
    if not handoff or handoff.get("status") != "ready":
        issues.append("已确认的 Deck/Layout 模板尚未形成 ready 的 template_handoff.json")
    else:
        selection_hash = selection.get("selection_sha256")
        handoff_hash = handoff.get("selection_sha256")
        if selection_hash and handoff_hash != selection_hash:
            issues.append("template_handoff.json 与已确认模板选择的哈希不一致")
    return True, issues


def ppt_master_template_install_issues(
    sandbox: Any,
    project: str,
) -> list[str]:
    """Compare a single selected workspace with its project-local install.

    The official apply-template stage defines one atomic copy of the portable
    ``templates``/``images``/``icons`` roots.  Confirm UI currently owns the
    handoff receipt but intentionally does not duplicate that file mapping.  A
    host therefore verifies the completed mapping before accepting template
    completion or Strategist Stage 1.  Multi-workspace fusion remains governed
    by the Skill because source collisions can be resolved segment-by-segment;
    this exact comparison is for the overwhelmingly common single selection.
    """

    selection = _json_object(_safe_read(sandbox, f"{project}/confirm_ui/template_selection.json"))
    if not selection or selection.get("status") != "confirmed" or selection.get("mode") != "templates":
        return []
    selections = [item for item in selection.get("selections") or [] if isinstance(item, dict)]
    if len(selections) != 1:
        return []
    source_root = str(selections[0].get("workspace_root") or "").rstrip("/")
    if not _PPT_MASTER_TEMPLATE_WORKSPACE_RE.fullmatch(source_root):
        return ["confirmed template workspace_root is outside the ppt-master template library"]
    if not _PROJECT_ROOT_RE.fullmatch(project):
        return ["ppt-master project root is outside the writable projects directory"]

    issues: list[str] = []
    source_manifest, target_manifest, manifest_error = _trusted_template_manifests(
        sandbox,
        source_root,
        project,
    )
    if manifest_error:
        return [manifest_error]
    for relative, source_fingerprint in source_manifest.items():
        target_fingerprint = target_manifest.get(relative)
        if target_fingerprint is None:
            issues.append(f"installed template is missing {relative}")
        elif target_fingerprint != source_fingerprint:
            issues.append(f"installed template differs from selected workspace at {relative}")
        if len(issues) >= 24:
            issues.append("installed template has additional omitted mapping errors")
            return issues
    if not source_manifest:
        issues.append("selected template workspace contains no readable portable files")
    return issues


def ppt_master_template_contract_issues(
    sandbox: Any,
    project: str,
    workflow: dict[str, Any],
    *,
    design_spec_text: str | None = None,
    spec_lock_text: str | None = None,
    check_design_spec: bool = True,
    check_spec_lock: bool = True,
    require_current_run_authority: bool = False,
    verify_prototype_files: bool = False,
) -> list[str]:
    """Validate the official template handoff before Executor work begins.

    The Skill remains the authority for choosing ``style``, ``layout``, or
    ``mirror``.  This host check only proves that a confirmed Deck/Layout handoff
    reached the Strategist artifacts and that a structured decision is complete
    enough for Executor to consume.  It deliberately runs before SVG authoring
    as well as at final delivery so a flat imitation cannot consume an entire
    presentation run before the mismatch is discovered.
    """

    structural_selected, issues = _confirmed_structural_template_selection(sandbox, project)
    if not structural_selected:
        return issues

    loaded_documents = {
        str(path).replace("\\", "/")
        for path in (workflow.get("loaded_authority_documents") or [])
        if isinstance(path, str)
    }
    if require_current_run_authority:
        skill_path = str(workflow.get("skill_path") or "").replace("\\", "/")
        skill_root = str(PurePosixPath(skill_path).parent)
        required_authorities = [f"{skill_root}/references/strategist-template.md"]
        if check_spec_lock:
            required_authorities.append(f"{skill_root}/templates/spec_lock_reference.md")
        for authority in required_authorities:
            if authority not in loaded_documents:
                issues.append(f"current run has not read required template authority: {authority}")

    design_spec = design_spec_text if design_spec_text is not None else _safe_read(sandbox, f"{project}/design_spec.md")
    application = ""
    prototype_refs: set[str] = set()
    if check_design_spec:
        if not design_spec:
            issues.append("confirmed Deck/Layout selection is missing design_spec.md")
        else:
            application_match = _TEMPLATE_APPLICATION_RE.search(design_spec)
            application = application_match.group(1).strip() if application_match else ""
            if not application:
                issues.append("design_spec.md is missing a non-empty Template Application plan")
            prototype_refs = {match.group(1) for match in _TEMPLATE_PROTOTYPE_RE.finditer(application)}

    if not check_spec_lock:
        return issues

    spec_lock = spec_lock_text if spec_lock_text is not None else _safe_read(sandbox, f"{project}/spec_lock.md")
    if not spec_lock:
        issues.append("confirmed Deck/Layout selection is missing spec_lock.md")
        return issues

    structure = _section_values(spec_lock, "pptx_structure")
    mode = structure.get("mode", "").casefold()
    reuse_scope = structure.get("template_reuse_scope", "").casefold()
    if not reuse_scope:
        issues.append("spec_lock.md pptx_structure is missing template_reuse_scope (style, layout, or mirror)")
        return issues

    if reuse_scope == "style":
        if mode != "flat":
            issues.append("template_reuse_scope: style requires pptx_structure.mode: flat")
        if prototype_refs:
            issues.append(
                "Template Application explicitly names Deck/Layout prototypes, so template_reuse_scope: style contradicts the confirmed structural plan"
            )
        return issues

    if reuse_scope not in {"layout", "mirror"}:
        issues.append("pptx_structure.template_reuse_scope must be style, layout, or mirror")
        return issues
    if mode != "structured":
        issues.append(f"template_reuse_scope: {reuse_scope} requires pptx_structure.mode: structured")
    adherence = structure.get("template_adherence", "").casefold()
    if adherence not in {"strict", "adaptive"}:
        issues.append("structured template reuse requires template_adherence: strict or adaptive")
    if reuse_scope == "mirror" and adherence and adherence != "strict":
        issues.append("template_reuse_scope: mirror requires template_adherence: strict")

    section_rows = {
        section: _section_values(spec_lock, section)
        for section in ("pptx_masters", "pptx_layouts", "page_pptx_layouts", "page_layouts")
    }
    for section, rows in section_rows.items():
        if not rows:
            issues.append(f"structured template reuse is missing non-empty {section} mappings")

    expected_slides = None
    if design_spec:
        page_count_match = _PAGE_COUNT_RE.search(design_spec)
        if page_count_match:
            expected_slides = int(page_count_match.group(1))
    if expected_slides:
        for section in ("page_layouts", "page_pptx_layouts"):
            count = len(section_rows[section])
            if count and count != expected_slides:
                issues.append(f"{section} must cover every page: expected {expected_slides}, found {count}")

    mapped_prototypes = {
        PurePosixPath(value.strip().strip('"\'')).stem
        for value in section_rows["page_layouts"].values()
        if value.strip()
    }
    missing_from_plan = sorted(prototype_refs - mapped_prototypes)
    if missing_from_plan:
        issues.append(
            "page_layouts does not realize Template Application prototype(s): "
            + ", ".join(missing_from_plan)
        )
    if verify_prototype_files:
        for prototype in sorted(mapped_prototypes):
            if _safe_read(sandbox, f"{project}/templates/{prototype}.svg") is None:
                issues.append(f"page_layouts references missing installed prototype: templates/{prototype}.svg")
    return issues


def _local_xml_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _xml_signature(element: ET.Element) -> tuple[Any, ...]:
    """Return a whitespace-insensitive signature for one inherited SVG atom."""

    return (
        _local_xml_name(element.tag),
        tuple(sorted((_local_xml_name(key), value) for key, value in element.attrib.items())),
        (element.text or "").strip(),
        tuple(_xml_signature(child) for child in element),
    )


def _svg_by_id(root: ET.Element) -> dict[str, ET.Element]:
    return {
        element.attrib["id"]: element
        for element in root.iter()
        if element.attrib.get("id")
    }


def ppt_master_template_svg_issues(
    sandbox: Any,
    project: str,
    spec_lock_text: str,
    target_path: str,
    svg_text: str,
) -> list[str]:
    """Prove that a structured page still contains its selected prototype.

    ``layout``/``mirror`` pages are required by the Skill to start from the
    complete ``page_layouts`` SVG.  The host therefore compares structural root
    identity, inherited layout/master atoms, their referenced definitions, and
    slot identity before a generated page mutation is accepted.  Slide-local
    additions and text/tspan content inside an existing slot remain free for the
    Executor to author.
    """

    structure = _section_values(spec_lock_text, "pptx_structure")
    if structure.get("template_reuse_scope", "").casefold() not in {"layout", "mirror"}:
        return []

    basename = PurePosixPath(target_path).name
    page_match = re.match(r"^(\d{1,3})(?:[_-].*)?\.svg$", basename, re.IGNORECASE)
    if not page_match:
        return [f"structured SVG target cannot be mapped to a page key: {basename}"]
    page_key = f"P{int(page_match.group(1)):02d}"
    page_layouts = _section_values(spec_lock_text, "page_layouts")
    prototype_value = page_layouts.get(page_key, "").strip().strip('"\'')
    if not prototype_value:
        return [f"page_layouts is missing the prototype mapping for {page_key}"]
    prototype = PurePosixPath(prototype_value).stem
    prototype_text = _safe_read(sandbox, f"{project}/templates/{prototype}.svg")
    if not prototype_text:
        return [f"installed template prototype is missing: templates/{prototype}.svg"]

    try:
        prototype_root = ET.fromstring(prototype_text)
    except ET.ParseError as exc:
        return [f"installed template prototype is invalid XML: templates/{prototype}.svg ({exc})"]
    try:
        generated_root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        return [f"generated SVG is invalid XML: {basename} ({exc})"]

    issues: list[str] = []
    for attribute in (
        "viewBox",
        "data-pptx-master",
        "data-pptx-master-name",
        "data-pptx-layout",
        "data-pptx-layout-name",
        "data-pptx-show-master-shapes",
        "data-pptx-show-inherited-shapes",
    ):
        expected = prototype_root.attrib.get(attribute)
        if expected is not None and generated_root.attrib.get(attribute) != expected:
            issues.append(f"{page_key} does not preserve prototype root attribute {attribute}: {expected}")

    prototype_ids = _svg_by_id(prototype_root)
    generated_ids = _svg_by_id(generated_root)
    referenced_definition_ids: set[str] = set()
    for element in list(prototype_root):
        scope = element.attrib.get("data-pptx-shape-scope", "").casefold()
        layer = element.attrib.get("data-pptx-layer", "").casefold()
        element_id = element.attrib.get("id")
        inherited = scope in {"master", "layout"} or layer in {"master", "layout"}
        slot = bool(element.attrib.get("data-pptx-slot"))
        if not element_id or not (inherited or slot):
            continue
        generated = generated_ids.get(element_id)
        if generated is None:
            issues.append(f"{page_key} dropped prototype element #{element_id}")
            continue
        if inherited and _xml_signature(generated) != _xml_signature(element):
            issues.append(f"{page_key} modified inherited prototype element #{element_id}")
        if slot:
            for attribute in (
                "data-pptx-slot",
                "data-pptx-placeholder",
                "data-pptx-frame",
                "data-pptx-bounds",
            ):
                expected = element.attrib.get(attribute)
                if expected is not None and generated.attrib.get(attribute) != expected:
                    issues.append(f"{page_key} changed prototype slot #{element_id} attribute {attribute}")
        if inherited:
            serialized = ET.tostring(element, encoding="unicode")
            referenced_definition_ids.update(re.findall(r"url\(#([A-Za-z_][\w.-]*)\)", serialized))

    for definition_id in sorted(referenced_definition_ids):
        expected = prototype_ids.get(definition_id)
        generated = generated_ids.get(definition_id)
        if expected is None:
            continue
        if generated is None or _xml_signature(generated) != _xml_signature(expected):
            issues.append(f"{page_key} does not preserve inherited SVG definition #{definition_id}")
    return issues


def _design_requirements(
    design_spec: str | None,
    spec_lock: str | None,
    persisted: dict[str, bool],
) -> tuple[dict[str, bool], dict[str, int]]:
    required = dict(persisted)
    counts: dict[str, int] = {}
    if design_spec:
        for capability, label in (
            ("speaker_notes", "Speaker Notes"),
            ("object_animations", "Custom Animations"),
            ("narration_audio", "Narration Audio"),
        ):
            if re.search(_ENABLED_ROW_RE.pattern.format(label=re.escape(label)), design_spec, re.IGNORECASE):
                required[capability] = True
        if re.search(r"\|\s*AI Image Acquisition Path\s*\|\s*(?!none\b)[^|\r\n]+", design_spec, re.IGNORECASE):
            required["ai_images"] = True
        if re.search(r"Native-ready\*{0,2}\s*:\s*yes", design_spec, re.IGNORECASE):
            required["native_charts"] = True
        if re.search(
            r"Template Application\*{0,2}\s*:\s*(?![^\r\n]*\bfree design\b)[^\r\n]+",
            design_spec,
            re.IGNORECASE,
        ):
            required["template"] = True
        page_count = _PAGE_COUNT_RE.search(design_spec)
        if page_count:
            counts["slides"] = int(page_count.group(1))

    if spec_lock:
        chart_rows = _section_rows(spec_lock, "page_charts")
        ai_rows = [line for line in _section_rows(spec_lock, "images") if "source=ai" in line.casefold()]
        master_rows = _section_rows(spec_lock, "pptx_masters")
        layout_rows = _section_rows(spec_lock, "pptx_layouts")
        if chart_rows:
            required["native_charts"] = True
            counts["charts"] = len(chart_rows)
        if ai_rows:
            required["ai_images"] = True
            counts["ai_images"] = len(ai_rows)
        if master_rows or layout_rows:
            required["template"] = True
            counts["masters"] = len(master_rows)
            counts["layouts"] = len(layout_rows)
    return required, counts


def _chart_count(path: Path) -> int:
    with zipfile.ZipFile(path, "r") as archive:
        return sum(
            1
            for name in archive.namelist()
            if re.fullmatch(r"ppt/charts/chart[1-9]\d*\.xml", name)
        )


def _final_svg_report_ok(report_text: str | None) -> bool:
    if not report_text:
        return False
    try:
        report = json.loads(report_text)
    except json.JSONDecodeError:
        return False
    summary = report.get("summary") if isinstance(report, dict) else None
    return (
        isinstance(report, dict)
        and report.get("stage") == "final"
        and isinstance(summary, dict)
        and summary.get("errors") == 0
    )


def validate_ppt_master_delivery(
    *,
    sandbox: Any,
    workflow: dict[str, Any],
    source_path: str,
    actual_pptx: Path,
) -> dict[str, Any]:
    """Raise ``ValueError`` until the persisted contract matches the PPTX."""

    if workflow.get("skill_name") != "ppt-master":
        return {}
    if actual_pptx.suffix.lower() != ".pptx":
        return {}

    project = _project_path(source_path, workflow)
    authority = str(workflow.get("selected_authority") or "")
    profile = str(workflow.get("selected_profile") or "")
    quick = profile.endswith("/quick-generate.md")
    issues: list[str] = []
    if not authority:
        issues.append("尚未持久化所选官方顶层路线文档")
    if not project:
        issues.append("无法定位持久化项目目录")
        raise ValueError("PPT 工作流验收失败：" + "；".join(issues))

    design_spec = _safe_read(sandbox, f"{project}/design_spec.md")
    spec_lock = _safe_read(sandbox, f"{project}/spec_lock.md")
    if authority.endswith("/generate-pptx.md") and not quick:
        if not design_spec:
            issues.append("默认 Generate 路线缺少 design_spec.md")
        if not spec_lock:
            issues.append("默认 Generate 路线缺少 spec_lock.md")
        elif not re.search(r"^##\s+pptx_structure\s*$[\s\S]*?^-\s+mode:\s*\S+", spec_lock, re.MULTILINE):
            issues.append("spec_lock.md 缺少非空 pptx_structure.mode")
        if not _final_svg_report_ok(_safe_read(sandbox, f"{project}/validation/svg_quality_report.json")):
            issues.append("最终 SVG 质量报告不存在、不是 final 阶段或仍含错误")

    required, expected = _design_requirements(
        design_spec,
        spec_lock,
        {
            str(name): bool(value)
            for name, value in (workflow.get("required_capabilities") or {}).items()
        },
    )

    structure = _section_values(spec_lock, "pptx_structure")
    pptx_structure_mode = structure.get("mode", "").casefold()
    template_reuse_scope = structure.get("template_reuse_scope", "").casefold()
    structural_template_selected, _ = _confirmed_structural_template_selection(
        sandbox,
        project,
    )
    if structural_template_selected:
        required["template"] = True
    issues.extend(
        ppt_master_template_contract_issues(
            sandbox,
            project,
            workflow,
            design_spec_text=design_spec,
            spec_lock_text=spec_lock,
        )
    )

    skill_path = str(workflow.get("skill_path") or "")
    skill_dir = str(PurePosixPath(skill_path).parent)
    if not skill_dir.startswith("/mnt/skills/"):
        issues.append("持久化的 ppt-master Skill 路径无效")
        raise ValueError("PPT 工作流验收失败：" + "；".join(issues))
    checker = f"{skill_dir}/scripts/pptx_delivery_check.py"
    try:
        output = sandbox.execute_command(
            f"python3 {shlex.quote(checker)} {shlex.quote(source_path)}",
            timeout=180,
        )
    except (OSError, RuntimeError, ValueError, SandboxError) as exc:
        raise ValueError(f"官方 pptx_delivery_check.py 执行失败：{exc}") from None
    report = _parse_json_output(output)
    if report.get("status") == "failed":
        issues.append("官方 pptx_delivery_check.py 报告失败")

    package = report.get("package") if isinstance(report.get("package"), dict) else {}
    parts = package.get("parts") if isinstance(package.get("parts"), dict) else {}
    slides = report.get("slides") if isinstance(report.get("slides"), dict) else {}
    media = report.get("media") if isinstance(report.get("media"), dict) else {}
    motion = report.get("motion") if isinstance(report.get("motion"), dict) else {}
    transitions = motion.get("transitions") if isinstance(motion.get("transitions"), dict) else {}
    animations = motion.get("object_animations") if isinstance(motion.get("object_animations"), dict) else {}
    embedded = media.get("embedded") if isinstance(media.get("embedded"), list) else []
    image_count = sum(1 for item in embedded if isinstance(item, dict) and item.get("kind") == "image")
    audio_count = sum(1 for item in embedded if isinstance(item, dict) and item.get("kind") == "audio")
    chart_count = _chart_count(actual_pptx)

    actual_slide_count = int(slides.get("count") or parts.get("slides") or 0)
    expected_slides = expected.get("slides")
    if expected_slides and actual_slide_count != expected_slides:
        issues.append(f"页数不一致：要求 {expected_slides}，实际 {actual_slide_count}")
    if required.get("speaker_notes") and int(parts.get("notes") or 0) < actual_slide_count:
        issues.append("演讲者备注未覆盖全部页面")
    if required.get("ai_images") and image_count < max(1, expected.get("ai_images", 1)):
        issues.append("AI 图片未按设计契约嵌入 PPTX")
    if required.get("native_charts") and chart_count < max(1, expected.get("charts", 1)):
        issues.append(
            f"原生图表数量不足：要求至少 {max(1, expected.get('charts', 1))}，实际 {chart_count}"
        )
    if required.get("template"):
        if int(parts.get("masters") or 0) < max(1, expected.get("masters", 1)):
            issues.append("PPTX 母版数量不满足模板契约")
        if int(parts.get("layouts") or 0) < max(1, expected.get("layouts", 1)):
            issues.append("PPTX 版式数量不满足模板契约")
    if required.get("transitions") and int(transitions.get("visual_effect_slide_count") or 0) == 0:
        issues.append("PPTX 未包含页面转场")
    if required.get("timed_advance") and int(transitions.get("timed_advance_slide_count") or 0) < actual_slide_count:
        issues.append("自动翻页未覆盖全部页面")
    if required.get("object_animations") and int(animations.get("object_animation_slide_count") or 0) == 0:
        issues.append("PPTX 未包含对象级动画")
    if required.get("narration_audio"):
        if audio_count == 0:
            issues.append("PPTX 未嵌入配音音频")
        if int(animations.get("audio_timing_slide_count") or 0) == 0:
            issues.append("PPTX 未建立音频播放时序")

    if issues:
        raise ValueError("PPT 工作流验收失败：" + "；".join(issues))
    return {
        "status": "passed",
        "project_path": project,
        "required_capabilities": sorted(name for name, enabled in required.items() if enabled),
        "pptx_structure_mode": pptx_structure_mode or None,
        "template_reuse_scope": template_reuse_scope or None,
        "slides": actual_slide_count,
        "notes": int(parts.get("notes") or 0),
        "images": image_count,
        "charts": chart_count,
        "audio": audio_count,
        "transition_slides": int(transitions.get("visual_effect_slide_count") or 0),
        "object_animation_slides": int(animations.get("object_animation_slide_count") or 0),
        "audio_timing_slides": int(animations.get("audio_timing_slide_count") or 0),
    }


__all__ = ["validate_ppt_master_delivery"]
