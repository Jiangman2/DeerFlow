from __future__ import annotations

import copy
import json
import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "public" / "research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))

from group_template import (  # noqa: E402
    NS,
    fill_group_template,
    zero_budget_narrative_paragraph_indices,
)
from docx_builder import build_docx, inspect_docx  # noqa: E402
from report_model import derive, validate  # noqa: E402
from template_builder import (  # noqa: E402
    _body_paragraphs,
    _body_tables,
    template_path,
)


def _case() -> dict:
    return json.loads(
        (SKILL / "examples" / "cases" / "group-company-complete.json").read_text(
            encoding="utf-8"
        )
    )


def _fill(case: dict):
    with zipfile.ZipFile(template_path("group_company")) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    paragraphs = _body_paragraphs(root)
    tables = _body_tables(root)
    fill_group_template(case, paragraphs, tables, "final")
    return root, paragraphs, tables


def _text(node) -> str:
    return "".join(node.xpath(".//w:t/text()", namespaces=NS))


@pytest.mark.parametrize(
    ("budget", "adjusted", "omitted"),
    [
        (0, None, True),
        (35, 0, True),
        (0, 35, False),
        (0, "0.00", True),
        (None, None, False),
        ("", None, False),
        (0, "", False),
        (0, "0.01", False),
        (0, -1, False),
    ],
)
def test_only_explicit_zero_displayed_budget_omits_narrative(
    budget, adjusted, omitted
):
    case = {
        "expenses": [
            {
                "code": "equipment_software",
                "budget": budget,
                "adjusted_budget": adjusted,
            }
        ]
    }
    assert zero_budget_narrative_paragraph_indices(case) == (
        (101, 114) if omitted else ()
    )


def test_zero_categories_remove_whole_paragraphs_and_commissioned_ratio():
    case = _case()
    for item in case["expenses"]:
        if item["code"] in {"equipment_software", "commissioned_research"}:
            item["budget"] = item["adjusted_budget"] = 0
    normalized = derive(case)
    untouched = copy.deepcopy(normalized)
    root, paragraphs, tables = _fill(normalized)

    omitted = {101, 104, 105, 114, 117}
    assert zero_budget_narrative_paragraph_indices(normalized) == tuple(sorted(omitted))
    # Exactly these five original paragraph nodes disappear, not empty fillers.
    assert len(_body_paragraphs(root)) == len(paragraphs) - len(omitted)
    for index, paragraph in enumerate(paragraphs):
        assert (paragraph.getparent() is None) == (index in omitted)
    assert "材料/燃料动力费：批复概预算140万元" in _text(paragraphs[102])
    assert "外委研究费占直接经费总支出" not in _text(root)

    # Reporting omission must not hide actual spending from totals or tables.
    assert normalized == untouched
    assert normalized["_derived"]["totals"]["recognized"] == 755
    assert "经费实际支出755万元" in _text(paragraphs[97])
    assert len(_body_tables(root)) == 18
    expense_summary_rows = tables[2].xpath("./w:tr", namespaces=NS)
    equipment_cells = expense_summary_rows[2].xpath("./w:tc", namespaces=NS)
    assert [_text(equipment_cells[index]) for index in (1, 2, 3, 4)] == [
        "0", "0", "170", "-170"
    ]
    commissioned_rows = tables[11].xpath("./w:tr", namespaces=NS)
    commissioned_cells = commissioned_rows[4].xpath("./w:tc", namespaces=NS)
    assert [_text(commissioned_cells[index]) for index in (4, 5, 6)] == [
        "0", "96", "-96"
    ]


def test_adjusted_nonzero_original_zero_is_retained_with_displayed_amount():
    case = _case()
    item = next(row for row in case["expenses"] if row["code"] == "equipment_software")
    item["budget"] = 0
    item["adjusted_budget"] = 180
    normalized = derive(case)
    root, paragraphs, _ = _fill(normalized)

    assert zero_budget_narrative_paragraph_indices(normalized) == ()
    assert len(_body_paragraphs(root)) == len(paragraphs)
    assert "设备与软件费：批复概预算180万元" in _text(paragraphs[101])
    assert "设备与软件费：结余" in _text(paragraphs[114])


def test_annual_approval_narrative_and_table_keep_distinct_totals():
    case = _case()
    case["funding"]["annual_appropriations"][2]["amount"] = 100
    normalized = derive(case)
    _, paragraphs, tables = _fill(normalized)

    assert "分年实际下达预算总额700万元" in _text(paragraphs[92])
    assert "2025年下达100万元" in _text(paragraphs[92])
    assert "本项目批复概预算总额800万元" in _text(paragraphs[72])
    rows = tables[1].xpath("./w:tr", namespaces=NS)
    assert _text(rows[2].xpath("./w:tc", namespaces=NS)[4]) == "800"
    assert _text(rows[5].xpath("./w:tc", namespaces=NS)[4]) == "100"


def test_missing_annual_amount_does_not_fabricate_annual_total():
    case = _case()
    case["funding"]["annual_appropriations"][2]["amount"] = None
    _, paragraphs, _ = _fill(derive(case))

    assert "分年实际下达预算总额；" in _text(paragraphs[92])
    assert "总额800万元" not in _text(paragraphs[92])
    assert "总额600万元" not in _text(paragraphs[92])


def test_combined_rules_pass_final_validation_build_and_inspection(tmp_path):
    case = _case()
    materials = next(item for item in case["expenses"] if item["code"] == "materials_energy")
    for item in case["expenses"]:
        if item["code"] in {"equipment_software", "commissioned_research"}:
            for field in ("budget", "adjusted_budget", "reported", "actual"):
                materials[field] += item[field]
                item[field] = 0
            item["variance_reason"] = ""
    annual = case["funding"]["annual_appropriations"][-1]
    annual["amount"] = 100
    annual["sources"]["company"] = 25
    normalized, issues = validate(case, mode="final")
    assert not [item for item in issues if item["severity"] == "ERROR"], issues
    output = build_docx(normalized, tmp_path / "combined.docx", mode="final")
    assert inspect_docx(output, case)["status"] == "PASS"
    with zipfile.ZipFile(output) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    text = _text(root)
    assert "分年实际下达预算总额700万元" in text
    assert "本项目批复概预算总额800万元" in text
    assert "经费实际支出755万元" in text
    assert "设备与软件费：批复概预算" not in text
    assert "外委研究费：结余" not in text
    assert len(_body_paragraphs(root)) == 215
    assert len(_body_tables(root)) == 18
