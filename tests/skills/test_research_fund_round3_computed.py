"""Computed input roles, dependencies and reserved annual rows stay bounded."""

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/public/research-fund-settlement/scripts"))

from standard_input import FixedWorkbook, import_group_company_workbook
from test_research_fund_annual_budget import _filled_workbook
from test_research_fund_feedback import move_annual_block, rewrite, set_cell
from test_research_fund_round3_reasons import sample


def clear(root, refs):
    for ref in refs:
        set_cell(root, ref)


def test_blank_parent_and_direct_totals_derive_without_rewriting_source(tmp_path):
    path = sample(tmp_path)
    rewrite(path, lambda root: clear(root, ["E15", "G15", "E16", "E27", "E28"]
                                     + [f"{col}{row}" for row in (23, 26) for col in "BCDE"]))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    case, report = import_group_company_workbook(path)
    assert report["status"] == "PASS", report
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    parent = next(row for row in case["expenses"] if row["code"] == "external_testing")
    assert [parent[key] for key in ("budget", "adjusted_budget", "actual", "input_balance")] == [100, 100, 90, 10]
    assert case["funding"]["approved_total"] == 100
    assert case["audit"]["reported_expenditure"] == 90
    workbook = FixedWorkbook(path)
    assert "B26" not in workbook.formulas and workbook.get("B26") is None
    assert workbook.evaluator.value("B23") == 100
    assert workbook.evaluator.value("E23") == 10


def test_entered_formulas_can_reference_blank_automatic_subtotals(tmp_path):
    path = sample(tmp_path)
    def change(root):
        clear(root, [f"{col}{row}" for row in (23, 26) for col in "BCDE"])
        set_cell(root, "E15", formula="B23")
        set_cell(root, "G15", formula="D23")
    rewrite(path, change)
    case, report = import_group_company_workbook(path)
    assert report["status"] == "PASS", report
    assert case["funding"]["approved_total"] == 100
    assert case["audit"]["reported_expenditure"] == 90


@pytest.mark.parametrize("annual_count", [1, 3, 8])
def test_automatic_parent_uses_actual_rows_after_annual_changes(tmp_path, annual_count):
    path = sample(tmp_path)
    def change(root):
        move_annual_block(root, annual_count)
        delta = annual_count - 4
        clear(root, [f"{col}{row + delta}" for row in (23, 26) for col in "BCDE"])
    rewrite(path, change)
    case, report = import_group_company_workbook(path)
    assert report["status"] == "PASS", report
    parent = next(row for row in case["expenses"] if row["code"] == "external_testing")
    assert parent["input_row"] == 26 + annual_count - 4
    assert parent["budget"] == 100 and parent["actual"] == 90 and parent["input_balance"] == 10


def test_prefilled_zero_formulas_do_not_activate_reserved_annual_rows(tmp_path):
    path = _filled_workbook(tmp_path / "reserved.xlsx", 100)
    def change(root):
        for row in (17, 18, 19):
            set_cell(root, f"E{row}", formula=f"SUM(B{row}:D{row})")
    rewrite(path, change)
    case, report = import_group_company_workbook(path)
    assert report["status"] == "PASS", report
    assert len(case["funding"]["annual_appropriations"]) == 1


@pytest.mark.parametrize("zero_ref", ["B17", "G17", "E17"])
def test_explicit_zero_year_remains_in_report(tmp_path, zero_ref):
    path = _filled_workbook(tmp_path / "zero-year.xlsx", 100)
    def change(root):
        set_cell(root, "B6", "按批复执行")
        set_cell(root, "A17", "2026年批准额")
        set_cell(root, "F17", "2026年支出额")
        set_cell(root, "E17", formula="SUM(B17:D17)")
        set_cell(root, zero_ref, 0)
    rewrite(path, change)
    case, report = import_group_company_workbook(path)
    assert report["status"] == "PASS", report
    assert [(row["year"], row["amount"]) for row in case["funding"]["annual_appropriations"]] == [(2025, 100), (2026, 0)]


@pytest.mark.parametrize("formula", ["WEBSERVICE(1)", "E17", "1/0", "SUM(B17:D99999)"])
def test_invalid_formula_in_otherwise_empty_annual_row_is_not_ignored(tmp_path, formula):
    path = _filled_workbook(tmp_path / "invalid-reserved.xlsx", 100)
    rewrite(path, lambda root: set_cell(root, "E17", formula=formula))
    _, report = import_group_company_workbook(path)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "INVALID_FORMULA" and issue["cell"] == "E17" for issue in report["issues"])


def test_nonzero_formula_in_empty_annual_row_cannot_hide_a_mismatch(tmp_path):
    path = _filled_workbook(tmp_path / "nonzero-reserved.xlsx", 100)
    def change(root):
        set_cell(root, "B6", "按批复执行")
        set_cell(root, "A17", "2026年批准额")
        set_cell(root, "F17", "2026年支出额")
        set_cell(root, "E17", formula="1")
    rewrite(path, change)
    _, report = import_group_company_workbook(path)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "ANNUAL_SOURCE_TOTAL_MISMATCH" and issue["cell"] == "E17" for issue in report["issues"])


@pytest.mark.parametrize("ref,value,code", [
    ("B26", 99, "EXTERNAL_TESTING_BREAKDOWN_MISMATCH"),
    ("E26", 0, "EXTERNAL_TESTING_BREAKDOWN_MISMATCH"),
    ("B23", 99, "DIRECT_SUBTOTAL_MISMATCH"),
    ("E15", 99, "APPROVED_SOURCE_TOTAL_MISMATCH"),
])
def test_explicit_wrong_computed_values_are_not_overwritten(tmp_path, ref, value, code):
    path = sample(tmp_path)
    rewrite(path, lambda root: set_cell(root, ref, value))
    _, report = import_group_company_workbook(path)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == code for issue in report["issues"])


def test_explicit_wrong_parent_formula_is_not_overwritten(tmp_path):
    path = sample(tmp_path)
    rewrite(path, lambda root: set_cell(root, "B26", formula="B27+B28+1"))
    _, report = import_group_company_workbook(path)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "EXTERNAL_TESTING_BREAKDOWN_MISMATCH" for issue in report["issues"])


def test_cycle_through_blank_automatic_parent_stays_an_error(tmp_path):
    path = sample(tmp_path)
    def change(root):
        set_cell(root, "B26")
        set_cell(root, "B27", formula="B26")
    rewrite(path, change)
    _, report = import_group_company_workbook(path)
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "INVALID_FORMULA" and issue["cell"] == "B26" for issue in report["issues"])


def test_automatic_roles_do_not_expand_to_appendix_four_or_ordinary_inputs(tmp_path):
    path = sample(tmp_path)
    rewrite(path, lambda root: clear(root, ["B24", "B40", "D40"]))
    workbook = FixedWorkbook(path)
    assert all(ref not in workbook.automatic_formulas for ref in ("B24", "B40", "D40", "E27", "E20"))
    assert workbook.evaluator.value("B24") == workbook.evaluator.value("B40") == 0
