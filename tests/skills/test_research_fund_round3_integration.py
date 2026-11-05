"""Synthetic workbook-to-final-report checks across the Round3 changes."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills/public/research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))

from report_model import validate
from standard_input import FixedWorkbook, import_group_company_workbook
from test_research_fund_feedback import rewrite, set_cell
from test_research_fund_round3_reasons import sample


def public_workbook(tmp_path, parent_reason="合成父级旧说明"):
    path = sample(tmp_path, parent_reason=parent_reason,
                  processing=(50, 45, "合成加工合同结余"), trials=(30, 27, "合成试验合同结余"))
    template = FixedWorkbook(SKILL / "assets/reference-examples/group-company-standard-input.xlsx")
    computed = ["E15", "G15", "E16", "E17", "E18", "E19"]
    computed += [f"{col}{row}" for row in (23, 26) for col in "BCDE"]
    assert all(ref in template.formulas for ref in computed), "The public template must contain its automatic formulas"
    def change(root):
        set_cell(root, "B10", "2026-11-05")
        for ref in computed:
            set_cell(root, ref, formula=template.formulas[ref])
        # Move the non-parent remainder from equipment to commissioned research.
        for col in "BCDE":
            set_cell(root, f"{col}24", 0)
        for col, value in {"B": 20, "C": 20, "D": 18, "E": 2, "F": "合成外委合同结余"}.items():
            set_cell(root, f"{col}29", value)
        # Indirect costs remain visible without entering direct execution totals.
        for row in (34, 35):
            for col, value in {"B": 35, "C": 35, "D": 30, "E": 5, "F": "合成间接经费结余"}.items():
                set_cell(root, f"{col}{row}", value)
        set_cell(root, "C40", 9)
        set_cell(root, "E40", "合成系统外受托单位")
    rewrite(path, change)
    return path


def cli(*args):
    return subprocess.run([sys.executable, "-B", str(SKILL / "scripts/report_cli.py"), *map(str, args)],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)


def test_public_template_import_check_build_and_inspect_without_acceptance_date(tmp_path):
    workbook = public_workbook(tmp_path)
    original_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
    case_path, import_report = tmp_path / "case.json", tmp_path / "import.json"
    result = cli("import-workbook", "--workbook", workbook, "--output", case_path, "--report", import_report)
    assert result.returncode == 0, result.stdout + result.stderr
    case = json.loads(case_path.read_text(encoding="utf-8"))
    assert case["project"]["acceptance_date"] is None
    assert len(case["funding"]["annual_appropriations"]) == 1
    parent = next(row for row in case["expenses"] if row["code"] == "external_testing")
    assert parent["input_variance_reason"] == "合成父级旧说明"
    assert parent["variance_reason_source_rows"] == [27, 28]
    normalized, issues = validate(case, "final")
    assert not [issue for issue in issues if issue["severity"] == "ERROR"], issues
    assert normalized["_derived"]["totals"]["recognized"] == 90
    assert normalized["_derived"]["totals"]["balance"] == 10
    assert normalized["_derived"]["cost_totals"]["indirect"]["recognized"] == 30
    ratios = normalized["_derived"]["outsourcing"]
    assert (ratios["commissioned_to_direct_percent"], ratios["external_to_direct_percent"],
            ratios["external_to_commissioned_percent"]) == (20, 10, 50)
    docx = tmp_path / "final.docx"
    for args in (("check", "--case", case_path, "--mode", "final"),
                 ("build", "--case", case_path, "--output", docx, "--mode", "final"),
                 ("inspect", "--case", case_path, "--docx", docx)):
        result = cli(*args)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "PASS"
    with zipfile.ZipFile(docx) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
    text = "".join(document.itertext())
    assert parent["variance_reason"] in text
    assert "结余原因：结余主要原因：" not in text
    assert "结余主要原因：结余主要原因：" not in text
    assert "合成父级旧说明" not in text
    assert "2026年11月5日" in text
    assert "预算执行支出（直接经费）90万元" in text
    assert "单独列示，不纳入预算执行支出及执行率计算" not in text
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    tables = document.findall("./w:body/w:tbl", ns)
    indirect_cells = tables[2].findall("./w:tr", ns)[12].findall("./w:tc", ns)
    def cell_text(index):
        return "".join(indirect_cells[index].itertext())
    assert (cell_text(1), cell_text(3), cell_text(4)) == ("35", "30", "5")
    assert hashlib.sha256(workbook.read_bytes()).hexdigest() == original_hash


def test_replaced_parent_placeholder_remains_evidence_without_blocking_final(tmp_path):
    case, report = import_group_company_workbook(public_workbook(tmp_path, parent_reason="待补充"))
    assert report["status"] == "PASS", report
    normalized, issues = validate(case, "final")
    assert not [issue for issue in issues if issue["severity"] == "ERROR"], issues
    parent = next(row for row in normalized["expenses"] if row["code"] == "external_testing")
    assert parent["input_variance_reason"] == "待补充"
    assert "待补充" not in parent["variance_reason"]


@pytest.mark.parametrize("code,field", [
    ("external_testing", "variance_reason"),
    ("testing_processing", "variance_reason"),
    ("equipment_software", "input_variance_reason"),
])
def test_source_placeholder_exception_does_not_waive_other_report_facts(tmp_path, code, field):
    case, report = import_group_company_workbook(public_workbook(tmp_path))
    assert report["status"] == "PASS", report
    index = next(index for index, row in enumerate(case["expenses"]) if row["code"] == code)
    case["expenses"][index][field] = "待补充"
    _, issues = validate(case, "final")
    assert any(issue["code"] == "PLACEHOLDER_FOUND" and issue["path"] == f"expenses[{index}].{field}"
               for issue in issues)


@pytest.mark.parametrize("ref,value,expected", [
    ("B26", 81, "EXTERNAL_TESTING_BREAKDOWN_MISMATCH"),
    ("F27", "", "BALANCE_REASON_REQUIRED"),
])
def test_import_failure_still_prevents_formal_cli_build(tmp_path, ref, value, expected):
    workbook = public_workbook(tmp_path)
    rewrite(workbook, lambda root: set_cell(root, ref, value))
    case_path = tmp_path / "invalid.json"
    result = cli("import-workbook", "--workbook", workbook, "--output", case_path)
    assert result.returncode == 2, result.stdout + result.stderr
    assert expected in {issue["code"] for issue in json.loads(result.stdout)["issues"]}
    docx = tmp_path / "must-not-exist.docx"
    result = cli("build", "--case", case_path, "--output", docx, "--mode", "final")
    assert result.returncode == 2
    assert "BUILD_BLOCKED" in result.stderr
    assert not docx.exists()
