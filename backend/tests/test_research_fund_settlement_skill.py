"""Executable and hostile-input regressions for research-fund-settlement."""

from __future__ import annotations

import importlib
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = REPO_ROOT / "skills" / "public" / "research-fund-settlement"
SCRIPTS = SKILL_ROOT / "scripts"
STANDARD_WORKBOOK = (
    SKILL_ROOT / "assets" / "reference-examples" / "group-company-standard-input.xlsx"
)


@pytest.fixture()
def standard_input(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    sys.modules.pop("standard_input", None)
    return importlib.import_module("standard_input")


def _rewrite_xlsx(
    source: Path,
    target: Path,
    *,
    rewrite: dict[str, callable] | None = None,
    extra: tuple[str, bytes] | None = None,
) -> None:
    rewrite = rewrite or {}
    with zipfile.ZipFile(source) as input_archive, zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as output_archive:
        for info in input_archive.infolist():
            payload = input_archive.read(info.filename)
            if info.filename in rewrite:
                payload = rewrite[info.filename](payload)
            output_archive.writestr(info, payload)
        if extra is not None:
            output_archive.writestr(extra[0], extra[1])


def _filled_sheet_xml(standard_input, payload: bytes) -> bytes:
    root = ET.fromstring(payload)
    sheet_data = root.find(f"{{{standard_input.SS}}}sheetData")
    assert sheet_data is not None

    def set_value(ref: str, value: str | int | float) -> None:
        row_number = int("".join(character for character in ref if character.isdigit()))
        row = next(
            node
            for node in sheet_data.findall(f"{{{standard_input.SS}}}row")
            if node.attrib.get("r") == str(row_number)
        )
        cell = next(
            (
                node
                for node in row.findall(f"{{{standard_input.SS}}}c")
                if node.attrib.get("r") == ref
            ),
            None,
        )
        if cell is None:
            cell = ET.SubElement(row, f"{{{standard_input.SS}}}c", {"r": ref})
        for child in list(cell):
            cell.remove(child)
        if isinstance(value, str):
            cell.set("t", "inlineStr")
            inline = ET.SubElement(cell, f"{{{standard_input.SS}}}is")
            text = ET.SubElement(inline, f"{{{standard_input.SS}}}t")
            text.text = value
        else:
            cell.attrib.pop("t", None)
            number = ET.SubElement(cell, f"{{{standard_input.SS}}}v")
            number.text = str(value)

    text_values = {
        "B2": "KJ-2023-001",
        "B3": "深水科研项目",
        "B4": "集团公司项目",
        "B5": "中海油研究总院有限责任公司",
        "B6": "2023-01-01 至 2025-12-31",
        "B7": "张三",
        "B8": "李四",
        "B9": "王五",
        "A16": "2023年批准额",
        "F16": "2023年支出额",
    }
    for ref, value in text_values.items():
        set_value(ref, value)

    for ref, value in {
        "B15": 800,
        "C15": 0,
        "D15": 0,
        "E15": 800,
        "G15": 755,
        "B16": 800,
        "C16": 0,
        "D16": 0,
        "E16": 800,
        "G16": 755,
    }.items():
        set_value(ref, value)

    rows = {
        23: (800, 755, 45, "八类直接经费汇总"),
        24: (180, 170, 10, "设备采购结余"),
        25: (140, 135, 5, "材料采购结余"),
        26: (120, 110, 10, "外协结算结余"),
        27: (70, 65, 5, "测试加工结余"),
        28: (50, 45, 5, "试验结算结余"),
        29: (100, 96, 4, "外委研究结余"),
        30: (70, 66, 4, "出版知识产权结余"),
        31: (70, 65, 5, "差旅会议结余"),
        32: (60, 58, 2, "咨询劳务结余"),
        33: (60, 55, 5, "其他费用结余"),
        34: (0, 0, 0, ""),
        35: (0, 0, 0, ""),
        36: (0, 0, 0, ""),
    }
    for row_number, (budget, spent, balance, reason) in rows.items():
        for column in ("B", "C"):
            set_value(f"{column}{row_number}", budget)
        set_value(f"D{row_number}", spent)
        set_value(f"E{row_number}", balance)
        if reason:
            set_value(f"F{row_number}", reason)

    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def test_audited_blank_workbook_structure_is_accepted(standard_input):
    workbook = standard_input.FixedWorkbook(STANDARD_WORKBOOK)

    assert workbook.sheet_name == "Sheet2"
    assert workbook.get("A1") == "附录1"
    assert workbook.get("A36") == "2、依托工程费"


def test_filled_standard_workbook_imports_without_manual_repairs(
    standard_input,
    tmp_path,
):
    filled = tmp_path / "group-company-filled.xlsx"
    _rewrite_xlsx(
        STANDARD_WORKBOOK,
        filled,
        rewrite={
            "xl/worksheets/sheet1.xml": lambda payload: _filled_sheet_xml(
                standard_input,
                payload,
            )
        },
    )

    case, report = standard_input.import_group_company_workbook(filled)

    assert report["status"] == "PASS"
    assert report["summary"] == {"errors": 0, "warnings": 0}
    assert case["project"]["name"] == "深水科研项目"
    assert case["funding"]["approved_total"] == 800
    assert case["audit"]["reported_expenditure"] == 755
    assert case["input_contract"]["status"] == "PASS"


def test_changed_merge_layout_is_rejected(standard_input, tmp_path):
    def remove_merge(payload: bytes) -> bytes:
        root = ET.fromstring(payload)
        merge_cells = root.find(f"{{{standard_input.SS}}}mergeCells")
        assert merge_cells is not None
        victim = next(
            node
            for node in merge_cells.findall(f"{{{standard_input.SS}}}mergeCell")
            if node.attrib.get("ref") == "B2:E2"
        )
        merge_cells.remove(victim)
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    mutated = tmp_path / "changed-merges.xlsx"
    _rewrite_xlsx(
        STANDARD_WORKBOOK,
        mutated,
        rewrite={"xl/worksheets/sheet1.xml": remove_merge},
    )

    with pytest.raises(ValueError, match="合并关系已改变"):
        standard_input.FixedWorkbook(mutated)


def test_worksheet_relationship_cannot_escape_safe_directory(
    standard_input,
    tmp_path,
):
    def escape_target(payload: bytes) -> bytes:
        root = ET.fromstring(payload)
        relationship = next(
            node
            for node in root.findall(f"{{{standard_input.PKG_REL}}}Relationship")
            if node.attrib.get("Type") == standard_input.WORKSHEET_REL
        )
        relationship.set("Target", "../evil.xml")
        return ET.tostring(root, encoding="utf-8", xml_declaration=True)

    mutated = tmp_path / "relationship-traversal.xlsx"
    _rewrite_xlsx(
        STANDARD_WORKBOOK,
        mutated,
        rewrite={"xl/_rels/workbook.xml.rels": escape_target},
    )

    with pytest.raises(ValueError, match="越出 xl/worksheets"):
        standard_input.FixedWorkbook(mutated)


def test_zip_member_path_traversal_is_rejected(standard_input, tmp_path):
    mutated = tmp_path / "zip-traversal.xlsx"
    _rewrite_xlsx(
        STANDARD_WORKBOOK,
        mutated,
        extra=("../outside.xml", b"<outside/>")
    )

    with pytest.raises(ValueError, match="不安全部件路径"):
        standard_input.FixedWorkbook(mutated)


def test_uncompressed_archive_limit_is_enforced(
    standard_input,
    monkeypatch,
):
    monkeypatch.setattr(standard_input, "MAX_ARCHIVE_UNCOMPRESSED_BYTES", 1024)

    with pytest.raises(ValueError, match="解压后过大"):
        standard_input.FixedWorkbook(STANDARD_WORKBOOK)


def test_complete_case_check_build_and_inspect_round_trip(tmp_path):
    case = SKILL_ROOT / "examples" / "cases" / "group-company-complete.json"
    report = tmp_path / "validation-report.json"
    output = tmp_path / "集团公司科研经费决算报告.docx"

    check = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "report_cli.py"),
            "check",
            "--case",
            str(case),
            "--mode",
            "final",
            "--report",
            str(report),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr

    build = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "report_cli.py"),
            "build",
            "--case",
            str(case),
            "--mode",
            "final",
            "--output",
            str(output),
            "--validation-report",
            str(report),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert output.stat().st_size > 50_000

    inspect = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "report_cli.py"),
            "inspect",
            "--docx",
            str(output),
            "--case",
            str(case),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert inspect.returncode == 0, inspect.stdout + inspect.stderr
    assert '"template_fidelity"' in inspect.stdout
    assert '"status": "PASS"' in inspect.stdout
