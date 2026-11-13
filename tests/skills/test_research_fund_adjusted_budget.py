"""Different B/C amounts must survive XLSX import, arithmetic and real DOCX fill."""
from pathlib import Path
import sys
from zipfile import ZipFile

from lxml import etree
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/public/research-fund-settlement/scripts"))
from docx_builder import build_docx, inspect_docx
from report_model import validate
from standard_input import import_group_company_workbook
from test_research_fund_annual_budget import _filled_workbook
from test_research_fund_feedback import rewrite, set_cell
from test_research_fund_round3_integration import public_workbook

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def report(workbook, output):
    case, imported = import_group_company_workbook(workbook)
    assert imported["status"] == "PASS", imported
    normalized, issues = validate(case, "final")
    assert not [i for i in issues if i["severity"] == "ERROR"], issues
    build_docx(normalized, output, "final")
    assert inspect_docx(output, normalized)["status"] == "PASS"
    with ZipFile(output) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    paragraphs = ["".join(p.xpath(".//w:t/text()", namespaces=NS))
                  for p in root.xpath("./w:body/w:p", namespaces=NS)]
    return normalized, root, paragraphs


@pytest.mark.parametrize("adjusted,actual,rate", [(125, 90, "72%"), (80, 90, "112.5%"), (0, 0, None)])
def test_adjusted_column_drives_execution_text_without_overwriting_original(tmp_path, adjusted, actual, rate):
    values = {"B10": "2026-11-10", "G15": actual, "G16": actual}
    for row in (23, 24):
        values.update({f"C{row}": adjusted, f"D{row}": actual, f"E{row}": adjusted - actual})
    workbook = _filled_workbook(tmp_path / "different-columns.xlsx", 100, values)
    case, root, paragraphs = report(workbook, tmp_path / "report.docx")
    assert case["funding"]["approved_total"] == 100
    assert case["funding"]["adjusted_total"] == adjusted
    assert case["_derived"]["totals"]["balance"] == adjusted - actual
    summary = next(p for p in paragraphs if p.startswith("截至决算报告编制日"))
    assert f"批复概预算总额{adjusted}万元（调整后）" in summary
    assert f"预算执行支出（直接经费）{actual}万元" in summary
    if rate:
        assert f"预算执行率{rate}" in summary
    else:
        assert case["_derived"]["totals"]["execution_rate"] is None
    expense = [p for p in paragraphs if p.startswith("设备与软件费：批复概预算")]
    if adjusted:
        assert len(expense) == 1
        assert f"批复概预算（调整后）{adjusted}万元，实际支出{actual}万元，结余{adjusted-actual}万元。" in expense[0]
    else:
        assert not expense  # An explicit C=0 must not fall back to B=100.
    assert any("本项目批复概预算总额100万元，其中" in p for p in paragraphs)
    tables = root.xpath("./w:body/w:tbl", namespaces=NS)
    def cell(table, row, col):
        node = tables[table].xpath("./w:tr", namespaces=NS)[row].xpath("./w:tc", namespaces=NS)[col]
        return "".join(node.xpath(".//w:t/text()", namespaces=NS))
    assert cell(1, 2, 4) == "100"  # Appendix 2 original approved amount.
    assert [cell(2, 1, c) for c in (1, 2, 3, 4)] == ["100", str(adjusted), str(actual), str(adjusted-actual)]


def test_formula_subtotals_do_not_double_count_testing_children_or_indirect_cost(tmp_path):
    workbook = public_workbook(tmp_path)
    def change(root):
        for ref, value in {"C27": 60, "E27": 15, "C28": 40, "E28": 13,
                           "C29": 30, "E29": 12}.items():
            set_cell(root, ref, value)
    rewrite(workbook, change)
    case, _, paragraphs = report(workbook, tmp_path / "subtotals.docx")
    assert case["funding"]["approved_total"] == 100
    assert case["funding"]["adjusted_total"] == 130
    assert case["_derived"]["cost_totals"]["indirect"]["adjusted_budget"] == 35
    summary = next(p for p in paragraphs if p.startswith("截至决算报告编制日"))
    assert "批复概预算总额130万元（调整后）" in summary
    assert "预算执行率69.23%" in summary
    assert any("外协实验/试验费：批复概预算（调整后）100万元" in p for p in paragraphs)
    assert any("外委研究费：批复概预算（调整后）30万元" in p for p in paragraphs)


def test_blank_adjusted_amount_keeps_confirmed_zero_semantics(tmp_path):
    workbook = _filled_workbook(tmp_path / "blank-adjusted.xlsx", 100,
                               {"B10": "2026-11-10", "G15": 0, "G16": 0})
    def change(root):
        for row in (23, 24):
            for col in "CDE":
                set_cell(root, f"{col}{row}")
    rewrite(workbook, change)
    case, _, paragraphs = report(workbook, tmp_path / "blank.docx")
    assert case["funding"]["adjusted_total"] == 0
    summary = next(p for p in paragraphs if p.startswith("截至决算报告编制日"))
    assert "批复概预算总额0万元（调整后）" in summary
    assert not any(p.startswith("设备与软件费：批复概预算") for p in paragraphs)
