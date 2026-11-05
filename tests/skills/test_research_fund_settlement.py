from __future__ import annotations

import copy
import importlib.util
import json
import sys
import zipfile
from xml.etree import ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "public" / "research-fund-settlement"
SCRIPTS = SKILL / "scripts"
CASES = SKILL / "examples" / "cases"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(SCRIPTS))
report_model = _load_module("research_fund_report_model", SCRIPTS / "report_model.py")
docx_builder = _load_module("research_fund_docx_builder", SCRIPTS / "docx_builder.py")
from text_color import black_text_part  # noqa: E402


def _case(name: str) -> dict:
    return json.loads((CASES / name).read_text(encoding="utf-8"))


def test_rule_catalog_has_all_33_source_items_and_profile_split():
    source = json.loads(
        (SKILL / "references" / "source-field-catalog.json").read_text(encoding="utf-8")
    )
    rules = json.loads(
        (SKILL / "references" / "profile-rules.json").read_text(encoding="utf-8")
    )

    assert source["row_count"] == 33
    assert len(source["rows"]) == 33
    assert [
        len(rules["profiles"][profile]["sections"])
        for profile in ("group_company", "national_science", "miit")
    ] == [9, 9, 15]


def test_money_conversion_is_deterministic():
    assert report_model.convert_money(1250000, "元") == 125
    assert report_model.convert_money(1250, "千元") == 125
    assert report_model.convert_money(1.25, "亿元") == 12500


def test_miit_calculation_and_generic_project_name():
    case = _case("miit-complete.json")
    normalized, issues = report_model.validate(case, mode="final")

    assert not [item for item in issues if item["severity"] == "ERROR"]
    assert normalized["project"]["name"] == "深海智能装备协同控制技术研制项目"
    assert normalized["_derived"]["totals"]["reported"] == 959
    assert normalized["_derived"]["totals"]["audit_reduction"] == 17
    assert normalized["_derived"]["totals"]["audited"] == 942
    assert normalized["_derived"]["totals"]["balance"] == 58


def test_group_limited_company_requires_tax_excluded():
    case = _case("group-company-complete.json")
    _, issues = report_model.validate(case, mode="final")
    assert not [item for item in issues if item["severity"] == "ERROR"]

    broken = copy.deepcopy(case)
    broken["funding"]["tax_basis"] = "tax_included"
    _, broken_issues = report_model.validate(broken, mode="final")
    assert any(item["code"] == "TAX_BASIS_MISMATCH" for item in broken_issues)


def test_final_mode_blocks_unresolved_conflicts_missing_evidence_and_placeholders():
    case = _case("national-science-complete.json")
    case["project"]["code"] = "XX"
    case["expenses"][0]["evidence_refs"] = []
    case["conflicts"] = [
        {
            "field": "audit.recognized_expenditure",
            "values": [480, 482],
            "sources": ["审计报告", "账面汇总"],
            "resolution": None,
        }
    ]

    _, issues = report_model.validate(case, mode="final")
    codes = {item["code"] for item in issues}
    assert "PLACEHOLDER_FOUND" in codes
    assert "MISSING_EVIDENCE" in codes
    assert "UNRESOLVED_CONFLICT" in codes


def test_all_complete_cases_build_valid_docx_with_expected_sections(tmp_path: Path):
    for case_name, expected_sections in [
        ("group-company-complete.json", 9),
        ("national-science-complete.json", 9),
        ("miit-complete.json", 15),
    ]:
        case = _case(case_name)
        normalized, issues = report_model.validate(case, mode="final")
        assert not [item for item in issues if item["severity"] == "ERROR"]
        output = tmp_path / case_name.replace(".json", ".docx")
        docx_builder.build_docx(normalized, output, mode="final")
        inspection = docx_builder.inspect_docx(output, normalized)
        assert inspection["status"] == "PASS", inspection
        assert inspection["selected_section_count"] == expected_sections
        with zipfile.ZipFile(output) as archive:
            text = archive.read("word/document.xml").decode("utf-8")
        assert normalized["project"]["name"] in text
        assert "待补充" not in text


def test_miit_output_never_inherits_example_project_name(tmp_path: Path):
    case = _case("miit-complete.json")
    normalized, _ = report_model.validate(case, mode="final")
    output = tmp_path / "miit.docx"
    docx_builder.build_docx(normalized, output, mode="final")

    with zipfile.ZipFile(output) as archive:
        text = archive.read("word/document.xml").decode("utf-8")
    assert "深海智能装备协同控制技术研制项目" in text
    assert "高技术船舶科研专项" not in text


def test_outputs_preserve_each_reference_template_layout(tmp_path: Path):
    templates = {
        "group-company-complete.json": "group-company-example.docx",
        "national-science-complete.json": "national-science-example.docx",
        "miit-complete.json": "miit-example.docx",
    }
    editable_parts = {"word/document.xml", "word/settings.xml"}
    word_ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

    for case_name, template_name in templates.items():
        normalized, _ = report_model.validate(_case(case_name), mode="final")
        output = tmp_path / case_name.replace(".json", "-template.docx")
        docx_builder.build_docx(normalized, output, mode="final")
        template = SKILL / "assets" / "reference-examples" / template_name

        with zipfile.ZipFile(template) as reference, zipfile.ZipFile(output) as actual:
            assert set(reference.namelist()) == set(actual.namelist())
            for part in set(reference.namelist()) - editable_parts:
                # Only deterministic black font colors may change these parts.
                assert black_text_part(part, reference.read(part)) == actual.read(part), part

            reference_root = ET.fromstring(reference.read("word/document.xml"))
            actual_root = ET.fromstring(actual.read("word/document.xml"))
            reference_body = reference_root.find(f"{word_ns}body")
            actual_body = actual_root.find(f"{word_ns}body")
            assert reference_body is not None and actual_body is not None
            assert [child.tag for child in reference_body] == [
                child.tag for child in actual_body
            ]
            assert [
                ET.tostring(node) for node in reference_root.iter(f"{word_ns}sectPr")
            ] == [ET.tostring(node) for node in actual_root.iter(f"{word_ns}sectPr")]

        inspection = docx_builder.inspect_docx(output, normalized)
        assert inspection["template_fidelity"]["status"] == "PASS", inspection
