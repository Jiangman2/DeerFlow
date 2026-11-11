"""November 5 input-to-report compatibility with the authored blank template."""
from pathlib import Path
import sys
from decimal import Decimal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'skills/public/research-fund-settlement/scripts'))
from standard_input import FixedWorkbook, import_group_company_workbook
from report_model import validate
from docx_builder import build_docx, inspect_docx
from test_research_fund_annual_budget import _filled_workbook, SKILL
from test_research_fund_feedback import rewrite, set_cell


def test_blank_template_does_not_force_zero_over_zero_formula(tmp_path):
    workbook = FixedWorkbook(SKILL / 'assets/reference-examples/group-company-standard-input.xlsx')
    assert workbook.blank('B40') and workbook.blank('D40')
    assert 'B40' not in workbook.formulas and 'D40' not in workbook.formulas
    path = _filled_workbook(tmp_path / 'zero-commissioned.xlsx', 100)
    case, report = import_group_company_workbook(path)
    assert report['status'] == 'PASS', report
    outsourcing = case['funding']['outsourcing']
    assert outsourcing['total'] == outsourcing['external_amount'] == 0
    assert not [issue for issue in report['issues'] if issue['code'] == 'INVALID_FORMULA']


def test_one_third_share_survives_import_and_formal_report(tmp_path):
    path = _filled_workbook(tmp_path / 'third-share.xlsx', 100)
    def change(root):
        for ref, value in {
            'B10': '2026-02-18',
            'B24': 70, 'C24': 70, 'D24': 60, 'E24': 10,
            'B29': 30, 'C29': 30, 'D29': 30, 'E29': 0,
            'C40': 10, 'E40': '合成外委单位',
        }.items():
            set_cell(root, ref, value)
        # Empty total and ratio are derived, with enough fractional precision.
        set_cell(root, 'B40')
        set_cell(root, 'D40')
    rewrite(path, change)
    case, report = import_group_company_workbook(path)
    assert report['status'] == 'PASS', report
    assert abs(Decimal(str(case['funding']['outsourcing']['external_share'])) - Decimal(1) / 3) < Decimal('.000001')
    case['project']['acceptance_date'] = '2026-01-31'
    normalized, issues = validate(case, 'final')
    assert not [issue for issue in issues if issue['severity'] == 'ERROR'], issues
    assert normalized['_derived']['outsourcing']['external_to_direct_percent'] == 11.11
    assert normalized['_derived']['outsourcing']['external_to_commissioned_percent'] == 33.33
    doc = build_docx(normalized, tmp_path / 'third-share.docx', 'final')
    assert inspect_docx(doc, normalized)['status'] == 'PASS'


def test_present_but_wrong_total_does_not_get_recalculated(tmp_path):
    path = _filled_workbook(tmp_path / 'wrong-commissioned.xlsx', 100)
    rewrite(path, lambda root: set_cell(root, 'B40', 99))
    _, report = import_group_company_workbook(path)
    assert report['status'] == 'FAIL'
    assert any(issue['code'] == 'OUTSOURCING_TOTAL_MISMATCH' for issue in report['issues'])


def test_nonzero_indirect_cost_and_balance_import_into_formal_direct_execution_report(tmp_path):
    path = _filled_workbook(tmp_path / 'direct-and-indirect.xlsx', 100)
    def change(root):
        set_cell(root, 'B10', '2026-02-18')
        for row in (34, 35):
            for column, value in {'B':350, 'C':350, 'D':300, 'E':50, 'F':'合成间接经费结余说明'}.items():
                set_cell(root, f'{column}{row}', value)
    rewrite(path, change)
    case, report = import_group_company_workbook(path)
    assert report['status'] == 'PASS', report
    assert case['input_contract']['budget_execution_scope'] == 'direct_only'
    assert case['funding']['approved_total'] == case['funding']['adjusted_total'] == 100
    assert case['audit']['reported_expenditure'] == 90
    case['project']['acceptance_date'] = '2026-01-31'
    normalized, issues = validate(case, 'final')
    assert not [issue for issue in issues if issue['severity'] == 'ERROR'], issues
    assert normalized['_derived']['totals']['recognized'] == 90
    assert normalized['_derived']['totals']['execution_rate'] == 90
    assert normalized['_derived']['totals']['balance'] == 10
    assert normalized['_derived']['cost_totals']['indirect']['recognized'] == 300
    assert normalized['_derived']['cost_totals']['indirect']['balance'] == 50
    assert normalized['_derived']['cost_totals']['all']['recognized'] == 390
    doc = build_docx(normalized, tmp_path / 'direct-and-indirect.docx', 'final')
    assert inspect_docx(doc, normalized)['status'] == 'PASS'


def test_direct_mismatch_is_still_rejected_despite_excluded_indirect_cost(tmp_path):
    path = _filled_workbook(tmp_path / 'direct-mismatch.xlsx', 100)
    def change(root):
        # Keep a nonzero indirect block, but deliberately alter the Appendix 2 total.
        for row in (34, 35):
            for column, value in {'B':350, 'C':350, 'D':300, 'E':50, 'F':'合成间接经费结余说明'}.items():
                set_cell(root, f'{column}{row}', value)
        set_cell(root, 'G15', 91)
    rewrite(path, change)
    _, report = import_group_company_workbook(path)
    assert report['status'] == 'FAIL'
    assert any(issue['code'] == 'EXPENSE_ACTUAL_TOTAL_MISMATCH' for issue in report['issues'])
