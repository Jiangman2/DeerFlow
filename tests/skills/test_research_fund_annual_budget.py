from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "public" / "research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))

from report_model import validate  # noqa: E402
from standard_input import SS, import_group_company_workbook  # noqa: E402


def _filled_workbook(
    output: Path, annual_approved: int, overrides: dict[str, int | str] | None = None
) -> Path:
    """Populate the actual fixed workbook so tests cover its public import path."""
    values: dict[str, int | str] = {
        "B2": "TEST-2025-01",
        "B3": "年度批准额独立口径验证项目",
        "B4": "有限公司项目",
        "B5": "测试研究中心",
        "B6": "2025-01-01 至 2025-12-31",
        "B7": "测试编制人",
        "B8": "测试负责人",
        "B9": "测试财务负责人",
        "B15": 100,
        "C15": 0,
        "D15": 0,
        "E15": 100,
        "G15": 90,
        "A16": "2025年批准额",
        "B16": annual_approved,
        "C16": 0,
        "D16": 0,
        "E16": annual_approved,
        "F16": "2025年支出额",
        "G16": 90,
    }
    for row in range(23, 37):
        for column in "BCDE":
            values[f"{column}{row}"] = 0
    for row in (23, 24):
        values.update(
            {
                f"B{row}": 100,
                f"C{row}": 100,
                f"D{row}": 90,
                f"E{row}": 10,
                f"F{row}": "采购价格低于预算，形成结余。",
            }
        )
    values.update(overrides or {})

    template = SKILL / "assets" / "reference-examples" / "group-company-standard-input.xlsx"
    with zipfile.ZipFile(template) as source, zipfile.ZipFile(output, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.startswith("xl/worksheets/") and info.filename.endswith(".xml"):
                root = ET.fromstring(data)
                sheet_data = root.find(f"{{{SS}}}sheetData")
                assert sheet_data is not None
                for ref, value in values.items():
                    row_number = ref.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
                    row = sheet_data.find(f"{{{SS}}}row[@r='{row_number}']")
                    assert row is not None, ref
                    cell = row.find(f"{{{SS}}}c[@r='{ref}']")
                    if cell is None:
                        cell = ET.SubElement(row, f"{{{SS}}}c", {"r": ref})
                    for child in list(cell):
                        cell.remove(child)
                    if isinstance(value, str):
                        cell.set("t", "inlineStr")
                        inline = ET.SubElement(cell, f"{{{SS}}}is")
                        ET.SubElement(inline, f"{{{SS}}}t").text = value
                    else:
                        cell.attrib.pop("t", None)
                        ET.SubElement(cell, f"{{{SS}}}v").text = str(value)
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(info, data)
    return output


@pytest.mark.parametrize("annual_approved", [80, 100, 120])
def test_annual_approvals_are_independent_of_contract_estimate(tmp_path: Path, annual_approved: int):
    path = _filled_workbook(tmp_path / "independent-approvals.xlsx", annual_approved)
    case, report = import_group_company_workbook(path)

    assert report["status"] == "PASS", report["issues"]
    assert case["funding"]["approved_total"] == 100
    assert case["funding"]["annual_appropriations"][0]["amount"] == annual_approved
    assert case["funding"]["annual_appropriations"][0]["actual_spent"] == 90
    normalized, issues = validate(case, mode="draft")
    assert not [item for item in issues if item["severity"] == "ERROR"], issues
    assert normalized["funding"]["approved_total"] == 100
    assert normalized["funding"]["annual_appropriations"][0]["amount"] == annual_approved


def test_multiple_annual_rows_keep_their_amounts_when_sum_differs(tmp_path: Path):
    path = _filled_workbook(
        tmp_path / "multiple-annual-approvals.xlsx",
        30,
        {
            "B6": "2024-01-01 至 2025-12-31",
            "A16": "2024年批准额",
            "F16": "2024年支出额",
            "G16": 40,
            "A17": "2025年批准额",
            "B17": 50,
            "C17": 0,
            "D17": 0,
            "E17": 50,
            "F17": "2025年支出额",
            "G17": 50,
        },
    )
    case, report = import_group_company_workbook(path)

    assert report["status"] == "PASS", report["issues"]
    assert case["funding"]["approved_total"] == 100
    annual = case["funding"]["annual_appropriations"]
    assert [(item["year"], item["amount"]) for item in annual] == [(2024, 30), (2025, 50)]
    assert sum(item["actual_spent"] for item in annual) == 90


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"B15": 99}, "APPROVED_SOURCE_TOTAL_MISMATCH"),
        ({"B16": 79}, "ANNUAL_SOURCE_TOTAL_MISMATCH"),
        ({"G16": 89}, "ANNUAL_SPENT_TOTAL_MISMATCH"),
        ({"B24": 99}, "EXPENSE_BUDGET_TOTAL_MISMATCH"),
        ({"D24": 89}, "EXPENSE_ACTUAL_TOTAL_MISMATCH"),
    ],
)
def test_other_budget_and_spending_reconciliations_still_block_bad_input(
    tmp_path: Path, overrides: dict[str, int], expected_code: str
):
    path = _filled_workbook(tmp_path / "invalid-reconciliation.xlsx", 80, overrides)
    _, report = import_group_company_workbook(path)

    assert report["status"] == "FAIL"
    codes = {item["code"] for item in report["issues"]}
    assert expected_code in codes
    assert "ANNUAL_APPROVED_TOTAL_MISMATCH" not in codes
