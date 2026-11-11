"""Public-import regressions for computed external-testing parent explanations."""

import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/public/research-fund-settlement/scripts"))

from standard_input import import_group_company_workbook
from test_research_fund_annual_budget import _filled_workbook
from test_research_fund_feedback import move_annual_block, rewrite


def sample(tmp_path, *, parent_reason="", processing=(60, 55, "合成加工合同结余"),
           trials=(40, 35, "合成试验合同结余")):
    values = {"F26": parent_reason}
    for row, (budget, actual, reason) in ((27, processing), (28, trials)):
        values.update({f"B{row}": budget, f"C{row}": budget, f"D{row}": actual,
                       f"E{row}": budget - actual, f"F{row}": reason})
    budget = processing[0] + trials[0]
    actual = processing[1] + trials[1]
    for row, row_budget, row_actual in ((26, budget, actual), (24, 100 - budget, 90 - actual)):
        values.update({f"B{row}": row_budget, f"C{row}": row_budget,
                       f"D{row}": row_actual, f"E{row}": row_budget - row_actual})
    return _filled_workbook(tmp_path / "synthetic-reasons.xlsx", 100, values)


def imported_parent(path):
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    case, report = import_group_company_workbook(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    parent = next(row for row in case["expenses"] if row["code"] == "external_testing")
    return parent, report


@pytest.mark.parametrize("parent_reason", ["", "合成手填父级说明"])
def test_parent_is_computed_from_both_children_and_original_is_preserved(tmp_path, parent_reason):
    parent, report = imported_parent(sample(tmp_path, parent_reason=parent_reason))
    assert report["status"] == "PASS", report
    assert parent["variance_reason"] == "结余主要原因：（1）测试化验加工费，合成加工合同结余；（2）试验费，合成试验合同结余"
    assert parent["input_variance_reason"] == (parent_reason or None)
    assert parent["variance_reason_source_rows"] == [27, 28]
    assert parent["evidence_refs"] == ["INPUT-APPENDIX-3"]
    assert parent["input_row"] == 26


@pytest.mark.parametrize("zero_child", ["processing", "trials"])
def test_blank_zero_child_does_not_add_invented_reason(tmp_path, zero_child):
    parent, report = imported_parent(sample(tmp_path, **{zero_child: (0, 0, "")}))
    assert report["status"] == "PASS", report
    if zero_child == "processing":
        assert parent["variance_reason"] == "结余主要原因：（2）试验费，合成试验合同结余"
        assert parent["variance_reason_source_rows"] == [28]
    else:
        assert parent["variance_reason"] == "结余主要原因：（1）测试化验加工费，合成加工合同结余"
        assert parent["variance_reason_source_rows"] == [27]


def test_both_blank_zero_children_leave_parent_reason_empty(tmp_path):
    parent, report = imported_parent(sample(tmp_path, parent_reason="合成父级旧说明",
                                           processing=(0, 0, ""), trials=(0, 0, "")))
    assert report["status"] == "PASS", report
    assert parent["variance_reason"] is None
    assert parent["input_variance_reason"] == "合成父级旧说明"
    assert parent["variance_reason_source_rows"] == []


@pytest.mark.parametrize("missing_child,cell", [("processing", "F27"), ("trials", "F28")])
@pytest.mark.parametrize("parent_reason", ["", "合成手填父级说明"])
def test_missing_nonzero_child_reason_still_blocks(tmp_path, missing_child, cell, parent_reason):
    _, report = imported_parent(sample(tmp_path, parent_reason=parent_reason,
                                       **{missing_child: (40, 35, "")}))
    assert report["status"] == "FAIL"
    assert any(issue["code"] == "BALANCE_REASON_REQUIRED" and issue["cell"] == cell
               for issue in report["issues"])


@pytest.mark.parametrize("annual_count", [1, 3, 8])
def test_inserted_or_deleted_annual_rows_keep_parent_and_reason_sources_aligned(tmp_path, annual_count):
    path = sample(tmp_path)
    rewrite(path, lambda root: move_annual_block(root, annual_count))
    parent, report = imported_parent(path)
    assert report["status"] == "PASS", report
    delta = annual_count - 4
    assert parent["input_row"] == 26 + delta
    assert parent["variance_reason_source_rows"] == [27 + delta, 28 + delta]
    assert parent["variance_reason"] == "结余主要原因：（1）测试化验加工费，合成加工合同结余；（2）试验费，合成试验合同结余"
