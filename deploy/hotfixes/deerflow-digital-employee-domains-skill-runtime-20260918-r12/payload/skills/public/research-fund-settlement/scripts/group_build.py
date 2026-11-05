"""Build and inspect group-company reports with variable annual rows."""

from __future__ import annotations

import hashlib
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from lxml import etree

from group_template import NS, W, fill_group_template
from template_builder import (
    EDITABLE_PARTS,
    TEMPLATES,
    _body_paragraphs,
    _body_tables,
    _enable_field_updates,
    _scrub_template_placeholders,
    _serialize_document,
    _verify_template,
    template_path,
)


def build_group_docx(
    case: dict[str, Any], output_path: str | Path, mode: str = "final"
) -> Path:
    profile = "group_company"
    source = template_path(profile)
    _verify_template(profile, source)
    output = Path(output_path).resolve()
    if output == source.resolve():
        raise ValueError("输出路径不能覆盖只读版式模板")
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
        paragraphs = _body_paragraphs(document)
        tables = _body_tables(document)
        expected = TEMPLATES[profile]
        if len(paragraphs) != expected["paragraphs"] or len(tables) != expected["tables"]:
            raise ValueError("集团公司模板主体结构与已审核槽位合同不一致")
        if len(document.xpath("//w:sectPr", namespaces=NS)) != expected["sections"]:
            raise ValueError("集团公司模板分节结构与版式合同不一致")

        fill_group_template(case, paragraphs, tables, mode)
        if mode == "final":
            _scrub_template_placeholders(document)
        replacement = {
            "word/document.xml": _serialize_document(document),
            "word/settings.xml": _enable_field_updates(archive.read("word/settings.xml")),
        }
        with tempfile.NamedTemporaryFile(
            delete=False, dir=output.parent, suffix=".docx"
        ) as temp:
            temp_path = Path(temp.name)
        try:
            with zipfile.ZipFile(temp_path, "w") as target:
                for info in archive.infolist():
                    target.writestr(
                        info,
                        replacement.get(info.filename, archive.read(info.filename)),
                    )
            temp_path.replace(output)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    return output


def _normalized_structure_signature(data: bytes) -> str:
    root = etree.fromstring(data)
    tables = _body_tables(root)
    if len(tables) > 1:
        rows = tables[1].xpath("./w:tr", namespaces=NS)
        while len(rows) > 6:
            tables[1].remove(rows[-1])
            rows = tables[1].xpath("./w:tr", namespaces=NS)
    for run in root.xpath("//w:r", namespaces=NS):
        for child in list(run):
            if child.tag in {f"{W}t", f"{W}delText"}:
                run.remove(child)
        if not run.attrib and len(run) == 0 and run.getparent() is not None:
            run.getparent().remove(run)
    canonical = etree.tostring(root, method="c14n")
    return hashlib.sha256(canonical).hexdigest()


def inspect_group_fidelity(docx_path: str | Path) -> dict[str, Any]:
    profile = "group_company"
    output = Path(docx_path)
    source = template_path(profile)
    findings: list[dict[str, str]] = []
    try:
        _verify_template(profile, source)
        with zipfile.ZipFile(source) as reference, zipfile.ZipFile(output) as actual:
            reference_names = set(reference.namelist())
            actual_names = set(actual.namelist())
            if reference_names != actual_names:
                findings.append(
                    {
                        "code": "PACKAGE_PART_SET_CHANGED",
                        "message": "输出文档的 OOXML 部件集合与正式模板不一致",
                    }
                )
            for name in sorted(reference_names & actual_names - EDITABLE_PARTS):
                if reference.read(name) != actual.read(name):
                    findings.append(
                        {
                            "code": "PRESERVED_PART_CHANGED",
                            "message": f"未授权变更模板部件：{name}",
                        }
                    )
            if _normalized_structure_signature(
                reference.read("word/document.xml")
            ) != _normalized_structure_signature(actual.read("word/document.xml")):
                findings.append(
                    {
                        "code": "DOCUMENT_STRUCTURE_CHANGED",
                        "message": "文档结构偏离正式模板允许的文本替换和年度行扩展范围",
                    }
                )
    except (
        OSError,
        KeyError,
        zipfile.BadZipFile,
        etree.XMLSyntaxError,
        ValueError,
    ) as exc:
        findings.append({"code": "FIDELITY_CHECK_FAILED", "message": str(exc)})
    return {
        "status": "PASS" if not findings else "FAIL",
        "template": source.name,
        "template_sha256": TEMPLATES[profile]["sha256"],
        "preserved_parts": (
            "all except word/document.xml text, cloned annual rows, "
            "and word/settings.xml updateFields"
        ),
        "findings": findings,
    }
