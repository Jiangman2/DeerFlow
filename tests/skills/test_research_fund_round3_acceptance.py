"""Group acceptance dates are optional input, never a report-generation gate."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import zipfile
from xml.etree import ElementTree as ET

import pytest

SKILL = Path(__file__).resolve().parents[2] / "skills/public/research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))
from report_model import validate, validation_report


MISSING = object()
ACCEPTANCE_VALUES = [MISSING, None, "", "2026-02-30", "待补充", "2026-02-18", "2030-12-31"]


def complete(profile="group-company"):
    return json.loads((SKILL / "examples/cases" / f"{profile}-complete.json").read_text(encoding="utf-8"))


def set_acceptance(case, value):
    if value is MISSING:
        case["project"].pop("acceptance_date", None)
    else:
        case["project"]["acceptance_date"] = value


@pytest.mark.parametrize("version", ["2026-09", "2026-11", "2026-11-05"])
@pytest.mark.parametrize("acceptance", ACCEPTANCE_VALUES,
                         ids=["absent", "null", "blank", "invalid", "placeholder", "same-day", "later"])
def test_group_contracts_do_not_require_or_validate_acceptance(version, acceptance):
    case = complete()
    case["input_contract"]["version"] = version
    if version == "2026-11-05":
        case["input_contract"]["budget_execution_scope"] = "direct_only"
    set_acceptance(case, acceptance)
    original = copy.deepcopy(case)
    normalized, issues = validate(case, "final")
    assert not [issue for issue in issues if issue["severity"] == "ERROR"], issues
    assert not any("acceptance" in issue["path"] or "验收日期" in issue["message"] for issue in issues)
    assert normalized["project"] == original["project"]
    assert case == original
    assert validation_report(case, "final")["status"] == "PASS"


@pytest.mark.parametrize("acceptance", [None, "invalid", "2030-12-31"])
def test_draft_does_not_ask_for_acceptance(acceptance):
    case = complete()
    set_acceptance(case, acceptance)
    _, issues = validate(case, "draft")
    assert not any("acceptance" in issue["path"] or "验收日期" in issue["message"] for issue in issues)


@pytest.mark.parametrize("prepared,code", [(None, "MISSING_REQUIRED"), ("", "MISSING_REQUIRED"),
                                           ("2026-02-30", "INVALID_DATE"), ("not a date", "INVALID_DATE")])
def test_group_preparation_date_is_checked_independently(prepared, code):
    case = complete()
    case["project"].pop("acceptance_date")
    case["report"]["prepared_date"] = prepared
    _, issues = validate(case, "final")
    assert any(issue["severity"] == "ERROR" and issue["code"] == code
               and issue["path"] == "report.prepared_date" for issue in issues)
    assert not any("acceptance" in issue["path"] for issue in issues)


def test_malformed_acceptance_does_not_mask_invalid_preparation_date():
    case = complete()
    case["project"]["acceptance_date"] = "invalid"
    case["report"]["prepared_date"] = "2026-02-30"
    _, issues = validate(case, "final")
    assert any(issue["code"] == "INVALID_DATE" and issue["path"] == "report.prepared_date" for issue in issues)
    assert not any(issue["path"] == "project.acceptance_date" for issue in issues)


def test_acceptance_placeholder_exemption_does_not_cover_other_fields():
    case = complete()
    case["project"].update(acceptance_date="TBD", name="待补充")
    _, issues = validate(case, "final")
    assert any(issue["code"] == "PLACEHOLDER_FOUND" and issue["path"] == "project.name" for issue in issues)
    assert not any(issue["path"] == "project.acceptance_date" for issue in issues)


@pytest.mark.parametrize("profile", ["national-science", "miit"])
@pytest.mark.parametrize("mutation,code,path", [
    (lambda case: case["report"].update(cutoff_date=None), "MISSING_REQUIRED", "report.cutoff_date"),
    (lambda case: case["report"].update(prepared_date=None), "MISSING_REQUIRED", "report.prepared_date"),
    (lambda case: case["project"].update(start_date="2030-01-01", end_date="2020-01-01"), "INVALID_PLANNED_PERIOD", "project.start_date"),
    (lambda case: case["project"].update(actual_start_date="invalid", actual_end_date="2026-01-01"), "INVALID_DATE", "project.actual_start_date"),
])
def test_other_profile_date_rules_are_preserved(profile, mutation, code, path):
    case = complete(profile)
    mutation(case)
    _, issues = validate(case, "final")
    assert any(issue["severity"] == "ERROR" and issue["code"] == code and issue["path"] == path for issue in issues)


@pytest.mark.parametrize("profile", ["national-science", "miit"])
def test_other_profiles_keep_their_placeholder_rule(profile):
    case = complete(profile)
    case["project"]["acceptance_date"] = "TBD"
    _, issues = validate(case, "final")
    assert any(issue["code"] == "PLACEHOLDER_FOUND" and issue["path"] == "project.acceptance_date" for issue in issues)


@pytest.mark.parametrize("acceptance", [MISSING, "TBD", "2030-12-31"], ids=["absent", "malformed", "later"])
def test_cli_checks_builds_and_inspects_without_acceptance_gate(tmp_path, acceptance):
    case = complete()
    set_acceptance(case, acceptance)
    case_path = tmp_path / "synthetic.json"
    case_path.write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8")
    docx = tmp_path / "report.docx"
    cli = [sys.executable, "-B", str(SKILL / "scripts/report_cli.py")]
    for args in (["check", "--case", str(case_path), "--mode", "final"],
                 ["build", "--case", str(case_path), "--output", str(docx), "--mode", "final"],
                 ["inspect", "--case", str(case_path), "--docx", str(docx)]):
        result = subprocess.run(cli + args, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["status"] == "PASS"
    with zipfile.ZipFile(docx) as archive:
        document = ET.fromstring(archive.read("word/document.xml"))
        text = "".join(document.itertext())
    assert "2026年2月18日" in text
    assert "2030年12月31日" not in text and "TBD" not in text
    assert json.loads(case_path.read_text(encoding="utf-8")) == case
