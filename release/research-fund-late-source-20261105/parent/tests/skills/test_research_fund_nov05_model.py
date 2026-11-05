"""Independent contract and rendered-DOCX regressions; all cases are synthetic."""
import copy
import json
import subprocess
import sys
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import pytest
from lxml import etree

SKILL = Path(__file__).resolve().parents[2] / "skills/public/research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))
from report_model import validate, validation_report
from docx_builder import build_docx, inspect_docx


def complete(profile="group-company"):
    return json.loads((SKILL / "examples/cases" / f"{profile}-complete.json").read_text(encoding="utf-8"))


def with_outsourcing():
    case = complete()
    case["input_contract"]["version"] = "2026-11-05"
    case["input_contract"]["budget_execution_scope"] = "direct_only"
    row = next(row for row in case["expenses"] if row["code"] == "commissioned_research")
    total = row["actual"]
    case["funding"]["outsourcing"] = {
        "total": total, "external_amount": total / 2, "external_share": 0.5,
        "external_organizations": ["合成单位甲", "合成单位乙"],
        "evidence_refs": ["INPUT-APPENDIX-4"],
    }
    case["evidence"].append({"id": "INPUT-APPENDIX-4", "source_file": "synthetic.xlsx", "locator": "Sheet2!A38:G40", "fact": "合成外委比例"})
    return case


def errors(case):
    return [i for i in validate(case, "final")[1] if i["severity"] == "ERROR"]


def document_text(path):
    with zipfile.ZipFile(path) as archive:
        root = etree.fromstring(archive.read("word/document.xml"))
    return "".join(root.itertext())


def test_three_ratios_use_distinct_correct_denominators_and_render(tmp_path):
    case = with_outsourcing()
    normalized, issues = validate(case, "final")
    assert not [i for i in issues if i["severity"] == "ERROR"], issues
    ratios = normalized["_derived"]["outsourcing"]
    assert ratios["basis"] == "recognized_expenditure"
    direct = Decimal(str(ratios["direct_expenditure"]))
    total = Decimal(str(ratios["total"]))
    external = Decimal(str(ratios["external_amount"]))
    assert Decimal(str(ratios["commissioned_to_direct_percent"])) == (100 * total / direct).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    assert Decimal(str(ratios["external_to_direct_percent"])) == (100 * external / direct).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    assert ratios["external_to_commissioned_percent"] == 50
    assert ratios["external_to_direct_percent"] != 50
    doc = build_docx(normalized, tmp_path / "synthetic.docx", "final")
    text = document_text(doc)
    assert f"外委研究费占直接经费总支出的比例为{ratios['commissioned_to_direct_percent']}%" in text
    assert f"外委给系统外单位的外委研究费占直接经费总支出的比例为{ratios['external_to_direct_percent']}%" in text
    assert "合成单位甲、合成单位乙" in text
    assert inspect_docx(doc, normalized)["status"] == "PASS"


def test_legacy_without_appendix_does_not_invent_zero_external_amount(tmp_path):
    case = complete()
    normalized, _ = validate(case, "final")
    assert normalized["_derived"]["outsourcing"]["external_amount"] is None
    assert normalized["_derived"]["outsourcing"]["external_to_direct_percent"] is None
    doc = build_docx(normalized, tmp_path / "legacy.docx", "final")
    text = document_text(doc)
    assert "外委研究费占直接经费总支出的比例为" in text
    assert "外委给系统外单位的外委研究费占直接经费总支出" not in text
    assert inspect_docx(doc, normalized)["status"] == "PASS"


@pytest.mark.parametrize("field,value,code", [
    ("total", 999, "OUTSOURCING_TOTAL_MISMATCH"),
    ("external_amount", 999, "OUTSOURCING_EXTERNAL_EXCEEDS_TOTAL"),
    ("total", -1, "OUTSOURCING_NEGATIVE_AMOUNT"),
    ("external_amount", -1, "OUTSOURCING_NEGATIVE_AMOUNT"),
    ("external_share", 50, "OUTSOURCING_SHARE_RANGE"),
    ("external_share", .3, "OUTSOURCING_SHARE_MISMATCH"),
    ("total", None, "OUTSOURCING_AMOUNT_REQUIRED"),
    ("external_amount", None, "OUTSOURCING_AMOUNT_REQUIRED"),
    ("external_organizations", [], "OUTSOURCING_ORGANIZATIONS_REQUIRED"),
    ("external_organizations", [" "], "OUTSOURCING_ORGANIZATIONS_INVALID"),
    ("external_organizations", "合成单位", "OUTSOURCING_ORGANIZATIONS_INVALID"),
    ("evidence_refs", [], "MISSING_EVIDENCE"),
    ("evidence_refs", ["missing"], "UNKNOWN_EVIDENCE"),
    ("external_amount", "NaN", "INVALID_DATA"),
    ("external_share", "Infinity", "INVALID_DATA"),
])
def test_true_amount_evidence_and_input_errors_still_block_final(field, value, code):
    case = with_outsourcing()
    case["funding"]["outsourcing"][field] = value
    assert code in {i["code"] for i in errors(case)}


def test_audited_amount_cannot_silently_change_appendix_four_basis():
    case = with_outsourcing()
    row = next(row for row in case["expenses"] if row["code"] == "commissioned_research")
    row["audited"] = row["actual"] - 1
    assert "OUTSOURCING_TOTAL_MISMATCH" in {i["code"] for i in errors(case)}


def test_direct_denominator_excludes_indirect_costs_and_parent_detail_double_counting():
    case = with_outsourcing()
    original = validate(case, "final")[0]["_derived"]["outsourcing"]
    sample = copy.deepcopy(case["expenses"][0])
    sample.update(code="testing_processing", name="合成下级费用", actual=50, audited=None, parent_code="external_testing", include_in_total=False)
    indirect = copy.deepcopy(sample)
    indirect.update(code="research_staff", name="合成间接费用", actual=123, scope="indirect", parent_code=None, include_in_total=True)
    case["expenses"].extend([sample, indirect])
    current = validate(case, "draft")[0]["_derived"]["outsourcing"]
    assert current["direct_expenditure"] == original["direct_expenditure"]
    assert current["commissioned_to_direct_percent"] == original["commissioned_to_direct_percent"]
    assert current["external_to_direct_percent"] == original["external_to_direct_percent"]


def test_external_zero_is_valid_without_names_but_still_has_evidence():
    case = with_outsourcing()
    case["funding"]["outsourcing"].update(external_amount=0, external_share=0, external_organizations=[])
    assert not errors(case)
    normalized, _ = validate(case, "final")
    assert normalized["_derived"]["outsourcing"]["external_to_direct_percent"] == 0


def test_zero_denominator_is_undefined_not_fabricated_zero_percent():
    case = with_outsourcing()
    for row in case["expenses"]:
        row.update(actual=0, reported=0, audited=None)
    case["funding"]["outsourcing"].update(total=0, external_amount=0, external_share=0, external_organizations=[])
    normalized, _ = validate(case, "draft")
    ratios = normalized["_derived"]["outsourcing"]
    assert ratios["commissioned_to_direct_percent"] is None
    assert ratios["external_to_direct_percent"] is None
    assert ratios["external_to_commissioned_percent"] is None


@pytest.mark.parametrize("adjustments", [
    {"none_declared": None, "items": []},
    {"none_declared": False, "items": []},
    {"none_declared": False, "items": [{"content": "合成事项"}]},
    {"none_declared": False, "items": [{"level": "major", "content": "合成事项"}]},
])
def test_incomplete_budget_narrative_is_information_and_final_builds(adjustments, tmp_path):
    case = complete()
    case["funding"]["adjustments"] = adjustments
    normalized, issues = validate(case, "final")
    assert not [i for i in issues if i["severity"] in {"ERROR", "WARNING"}], issues
    assert [i for i in issues if i["severity"] == "INFO"]
    assert validation_report(case, "final")["summary"]["information"] > 0
    doc = build_docx(normalized, tmp_path / "budget.docx", "final")
    assert inspect_docx(doc, normalized)["status"] == "PASS"
    text = document_text(doc)
    assert "未发生概预算变更" not in text
    assert "已获批" not in text
    if adjustments["items"] and not adjustments["items"][0].get("level"):
        assert "分类未提供" in text
        assert "一般变更" not in text


def test_conflicting_budget_declaration_still_blocks():
    case = complete()
    case["funding"]["adjustments"] = {"none_declared": True, "items": [{"content": "合成事项"}]}
    assert "ADJUSTMENT_DECLARATION_CONFLICT" in {i["code"] for i in errors(case)}


def test_false_string_is_not_an_authoritative_no_change_declaration():
    case = complete()
    case["funding"]["adjustments"]["none_declared"] = "false"
    assert "ADJUSTMENT_DECLARATION_INVALID" in {i["code"] for i in errors(case)}


def test_unknown_budget_classification_preserved_without_becoming_general(tmp_path):
    case = complete()
    case["funding"]["adjustments"] = {"none_declared": False, "items": [{"level": "特殊类", "content": "合成事项"}]}
    normalized, issues = validate(case, "final")
    assert not [i for i in issues if i["severity"] == "ERROR"]
    doc = build_docx(normalized, tmp_path / "unknown-class.docx", "final")
    text = document_text(doc)
    assert "原填报分类：特殊类" in text
    assert "一般变更" not in text
    assert inspect_docx(doc, normalized)["status"] == "PASS"


@pytest.mark.parametrize("mutation,code", [
    (lambda case: case["funding"]["sources"][0].update(approved=999), "SOURCE_TOTAL_MISMATCH"),
    (lambda case: case["funding"].update(adjusted_total=999), "EXPENSE_BUDGET_MISMATCH"),
    (lambda case: case["audit"].update(reported_expenditure=999), "AUDIT_REPORTED_MISMATCH"),
    (lambda case: case.update(conflicts=[{"resolution": None}]), "UNRESOLVED_CONFLICT"),
    (lambda case: case["report"].update(prepared_date="2020-01-01"), "REPORT_DATE_NOT_AFTER_ACCEPTANCE"),
])
def test_budget_information_change_does_not_relax_financial_or_date_gates(mutation, code):
    case = complete()
    case["funding"]["adjustments"] = {"none_declared": None, "items": []}
    mutation(case)
    assert code in {i["code"] for i in errors(case)}


def test_group_uses_preparation_date_without_requiring_unused_cutoff(tmp_path):
    case = complete()
    case["report"]["cutoff_date"] = None
    normalized, issues = validate(case, "final")
    assert not [i for i in issues if i["severity"] == "ERROR"], issues
    doc = build_docx(normalized, tmp_path / "date.docx", "final")
    text = document_text(doc)
    assert "截至决算报告编制日，本项目任务合同书批复概预算总额" in text
    assert "2026年2月18日" in text
    assert inspect_docx(doc, normalized)["status"] == "PASS"


def test_supplied_group_cutoff_date_must_still_be_valid():
    case = complete()
    case["report"]["cutoff_date"] = "2026-02-30"
    assert "INVALID_DATE" in {i["code"] for i in errors(case)}


@pytest.mark.parametrize("profile", ["national-science", "miit"])
def test_other_profiles_still_require_cutoff_date(profile):
    case = complete(profile)
    case["report"]["cutoff_date"] = None
    assert any(i["path"] == "report.cutoff_date" for i in errors(case))


def test_cli_really_builds_final_with_information_and_rejects_financial_mismatch(tmp_path):
    case = with_outsourcing()
    case["funding"]["adjustments"] = {"none_declared": None, "items": []}
    data = tmp_path / "case.json"
    data.write_text(json.dumps(case), encoding="utf-8")
    doc = tmp_path / "cli.docx"
    cli = [sys.executable, "-B", str(SKILL / "scripts/report_cli.py"), "build", "--case", str(data), "--output", str(doc), "--mode", "final"]
    result = subprocess.run(cli, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert doc.exists()
    doc.unlink()
    case["funding"]["outsourcing"]["external_amount"] = 999
    data.write_text(json.dumps(case), encoding="utf-8")
    result = subprocess.run(cli, capture_output=True)
    assert result.returncode == 2
    assert b"BUILD_BLOCKED" in result.stderr
    assert not doc.exists()


def with_indirect_costs():
    case = with_outsourcing()
    indirect = copy.deepcopy(case['expenses'][0])
    indirect.update(code='research_staff', name='研发人员费', scope='indirect',
                    budget=350, adjusted_budget=350, actual=300, reported=300,
                    audited=None, parent_code=None, include_in_total=True,
                    variance_reason='合成间接经费结余说明')
    case['expenses'].append(indirect)
    return case


def test_direct_execution_retains_indirect_costs_but_excludes_them_from_totals(tmp_path):
    case = with_indirect_costs()
    normalized, issues = validate(case, 'final')
    assert not [i for i in issues if i['severity'] == 'ERROR'], issues
    totals = normalized['_derived']['totals']
    assert normalized['_derived']['budget_execution_scope'] == 'direct_only'
    assert totals['target_budget'] == totals['expense_budget'] == 800
    assert totals['reported'] == totals['actual'] == totals['recognized'] == 755
    assert totals['balance'] == 45
    assert totals['execution_rate'] == 94.38
    groups = normalized['_derived']['cost_totals']
    assert groups['direct']['recognized'] == 755
    assert groups['indirect']['recognized'] == 300
    assert groups['indirect']['balance'] == 50
    assert groups['all']['recognized'] == 1055
    assert groups['all']['adjusted_budget'] == 1150
    assert next(row for row in normalized['_derived']['expenses'] if row['code'] == 'research_staff')['include_in_total'] is True
    doc = build_docx(normalized, tmp_path / 'direct-only.docx', 'final')
    assert inspect_docx(doc, normalized)['status'] == 'PASS'
    text = document_text(doc)
    assert '预算执行支出（直接经费）755万元' in text
    assert '间接经费支出300万元单独列示，不纳入预算执行支出及执行率计算' in text
    assert '直接经费预算结余共计45万元' in text
    with zipfile.ZipFile(doc) as archive:
        root = etree.fromstring(archive.read('word/document.xml'))
    ns = {'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
    tables = root.xpath('./w:body/w:tbl', namespaces=ns)
    def cell(table, row, column):
        td = tables[table].xpath('./w:tr', namespaces=ns)[row].xpath('./w:tc', namespaces=ns)[column]
        return ''.join(td.xpath('.//w:t/text()', namespaces=ns))
    assert cell(1, 2, 6) == '755'  # Appendix 2/direct execution actual total.
    assert cell(2, 12, 1) == '350'  # Indirect rows remain complete.
    assert cell(2, 12, 3) == '300'
    assert cell(2, 12, 4) == '50'


def test_indirect_budget_cannot_enter_direct_denominator_after_manual_edit():
    case = with_indirect_costs()
    case['funding']['adjusted_total'] = 1150
    assert 'EXPENSE_BUDGET_MISMATCH' in {i['code'] for i in errors(case)}


def test_indirect_spend_cannot_enter_appendix_two_reported_total_after_manual_edit():
    case = with_indirect_costs()
    case['audit']['reported_expenditure'] = 1055
    assert 'AUDIT_REPORTED_MISMATCH' in {i['code'] for i in errors(case)}


@pytest.mark.parametrize('scope', [None, 'all_funds', 'indirect', '', True])
def test_new_contract_missing_or_wrong_execution_scope_fails_closed(scope):
    case = with_indirect_costs()
    if scope is None:
        case['input_contract'].pop('budget_execution_scope')
    else:
        case['input_contract']['budget_execution_scope'] = scope
    assert 'INVALID_DATA' in {i['code'] for i in errors(case)}


@pytest.mark.parametrize('version', ['2026-09','2026-11'])
def test_legacy_contract_without_scope_retains_historical_all_costs(version):
    case = with_indirect_costs()
    case['input_contract']['version'] = version
    case['input_contract'].pop('budget_execution_scope')
    # An archived all-cost report already carried these totals. Do not reinterpret it.
    case['funding']['adjusted_total'] = 1150
    case['audit']['reported_expenditure'] = 1055
    normalized, issues = validate(case, 'final')
    assert not [i for i in issues if i['severity'] == 'ERROR'], issues
    assert normalized['_derived']['budget_execution_scope'] == 'all_funds'
    assert normalized['_derived']['totals']['recognized'] == 1055
    assert normalized['_derived']['totals']['target_budget'] == 1150
    assert normalized['_derived']['totals']['balance'] == 95


@pytest.mark.parametrize('profile', ['national-science', 'miit'])
def test_group_execution_contract_does_not_change_other_profiles(profile):
    case = complete(profile)
    original, original_issues = validate(case, 'final')
    case['input_contract'] = {'version':'2026-11-05', 'budget_execution_scope':'direct_only'}
    normalized, issues = validate(case, 'final')
    assert normalized['_derived'] == original['_derived']
    assert issues == original_issues
