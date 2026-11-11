"""Presentation-only feedback preserves source templates and financial behavior."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import warnings
from zipfile import ZipFile

from lxml import etree
import pytest

SKILL = Path(__file__).resolve().parents[2] / "skills/public/research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))
from docx_builder import build_docx, inspect_docx
from report_model import derive, validate
from template_builder import template_path
from text_color import black_text_part

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
NS = {"w": W[1:-1]}
LABEL = "AI自动生成"


def case_for(slug):
    raw = json.loads((SKILL / f"examples/cases/{slug}-complete.json").read_text(encoding="utf-8"))
    case, issues = validate(raw, "final")
    assert not [issue for issue in issues if issue["severity"] == "ERROR"]
    return case


def parts(path):
    with ZipFile(path) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def cover_part(package):
    document = etree.fromstring(package["word/document.xml"])
    first = document.xpath("//w:sectPr", namespaces=NS)[0]
    refs = first.xpath("./w:footerReference[@w:type='first']", namespaces=NS)
    assert len(refs) == 1
    rid = refs[0].get(R + "id")
    rels = etree.fromstring(package["word/_rels/document.xml.rels"])
    matches = [node for node in rels if node.get("Id") == rid]
    assert len(matches) == 1
    return "word/" + matches[0].get("Target")


def rewrite(source, target, name, change):
    package = parts(source)
    root = etree.fromstring(package[name])
    change(root)
    package[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    with ZipFile(target, "w") as archive:
        for key, value in package.items():
            archive.writestr(key, value)


@pytest.mark.parametrize("slug", ["group-company", "national-science", "miit"])
@pytest.mark.parametrize("mode", ["draft", "final"])
def test_label_is_small_black_right_aligned_and_only_on_cover(tmp_path, slug, mode):
    case = case_for(slug)
    before = deepcopy(case)
    source = template_path(case["profile"])
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    report = build_docx(case, tmp_path / "report.docx", mode)
    assert case == before
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    package = parts(report)
    reference = parts(source)
    new_part = cover_part(package)
    assert set(package) - set(reference) == {new_part}
    footer = etree.fromstring(package[new_part])
    labels = footer.xpath(".//w:t[text()='AI自动生成']", namespaces=NS)
    assert len(labels) == 1
    run = labels[0].getparent()
    paragraph = run.getparent()
    assert paragraph.find(W + "pPr/" + W + "jc").get(W + "val") == "right"
    assert run.find(W + "rPr/" + W + "sz").get(W + "val") == "18"
    assert run.find(W + "rPr/" + W + "szCs").get(W + "val") == "18"
    assert run.find(W + "rPr/" + W + "color").get(W + "val") == "000000"
    document = etree.fromstring(package["word/document.xml"])
    assert LABEL not in "".join(document.xpath("//w:t/text()", namespaces=NS))
    sections = document.xpath("//w:sectPr", namespaces=NS)
    assert len(sections[0].findall(W + "titlePg")) == 1
    assert all(not s.findall(W + "titlePg") for s in sections[1:])
    assert len(document.xpath("//w:footerReference[@w:type='first']", namespaces=NS)) == 1
    old_sections = etree.fromstring(reference["word/document.xml"]).xpath("//w:sectPr", namespaces=NS)
    for actual, old in zip(sections, old_sections):
        assert [etree.tostring(x) for x in actual.findall(W + "pgNumType")] == [etree.tostring(x) for x in old.findall(W + "pgNumType")]
    for name in reference:
        if name.startswith(("word/footer", "word/header")) and name.endswith(".xml"):
            assert package[name] == black_text_part(name, reference[name])
    expected_fields = []
    if slug == "miit":
        old = etree.fromstring(black_text_part("word/footer1.xml", reference["word/footer1.xml"]))
        expected_fields = old.xpath("//w:instrText/text()", namespaces=NS)
        assert len(expected_fields) == 2 and all("PAGE" in item for item in expected_fields)
        # The entire old page-number paragraph remains the first child of the copy.
        assert etree.tostring(footer[0]) == etree.tostring(old[0])
    assert footer.xpath("//w:instrText/text()", namespaces=NS) == expected_fields
    assert inspect_docx(report, case)["status"] == "PASS"


def test_only_balance_outer_number_format_changes_and_nested_reasons_survive(tmp_path):
    case = case_for("group-company")
    reason = "结余主要原因：（1）测试化验加工费，合同价格下降；（2）试验费，测试次数减少"
    next(row for row in case["expenses"] if row["code"] == "external_testing")["variance_reason"] = reason
    case = derive(case)
    report = build_docx(case, tmp_path / "report.docx")
    package = parts(report)
    source = parts(template_path("group_company"))
    old = etree.fromstring(black_text_part("word/numbering.xml", source["word/numbering.xml"]))
    actual = etree.fromstring(package["word/numbering.xml"])
    old_label = old.xpath("./w:abstractNum[@w:abstractNumId='3']/w:lvl[@w:ilvl='0']/w:lvlText", namespaces=NS)[0]
    new_label = actual.xpath("./w:abstractNum[@w:abstractNumId='3']/w:lvl[@w:ilvl='0']/w:lvlText", namespaces=NS)[0]
    assert old_label.get(W + "val") == "(%1)"
    assert new_label.get(W + "val") == "%1."
    old_label.set(W + "val", "%1.")
    assert etree.tostring(actual) == etree.tostring(old)
    document = etree.fromstring(package["word/document.xml"])
    numbered = document.xpath("//w:p[w:pPr/w:numPr/w:numId/@w:val='9']", namespaces=NS)
    assert len(numbered) == 8
    assert all("结余（负数为超支）" in "".join(p.xpath(".//w:t/text()", namespaces=NS)) for p in numbered)
    assert reason in "".join(document.xpath("//w:t/text()", namespaces=NS))
    assert inspect_docx(report, case)["status"] == "PASS"


def test_zero_budget_categories_still_omit_and_remaining_numbering_starts_at_one(tmp_path):
    case = case_for("group-company")
    for row in case["expenses"][:2]:
        row["budget"] = row["adjusted_budget"] = 0
    case = derive(case)
    report = build_docx(case, tmp_path / "zero.docx")
    package = parts(report)
    document = etree.fromstring(package["word/document.xml"])
    rows = document.xpath("//w:p[w:pPr/w:numPr/w:numId/@w:val='9']", namespaces=NS)
    assert len(rows) == 6
    assert "外协实验/试验费" in "".join(rows[0].xpath(".//w:t/text()", namespaces=NS))
    numbering = etree.fromstring(package["word/numbering.xml"])
    assert numbering.xpath("./w:abstractNum[@w:abstractNumId='3']/w:lvl/w:start/@w:val", namespaces=NS) == ["1"]
    assert inspect_docx(report, case)["status"] == "PASS"


@pytest.mark.parametrize("mutation", ["text", "size", "alignment", "color", "duplicate_label", "page_field"])
def test_cover_fidelity_rejects_extra_or_changed_footer_content(tmp_path, mutation):
    case = case_for("miit")
    source = build_docx(case, tmp_path / "source.docx")
    name = cover_part(parts(source))
    def change(root):
        label = root.xpath(".//w:t[text()='AI自动生成']", namespaces=NS)[0]
        if mutation == "text":
            label.text = "changed"
        elif mutation == "size":
            label.getparent().find(W + "rPr/" + W + "sz").set(W + "val", "28")
        elif mutation == "alignment":
            label.getparent().getparent().find(W + "pPr/" + W + "jc").set(W + "val", "left")
        elif mutation == "color":
            label.getparent().find(W + "rPr/" + W + "color").set(W + "val", "888888")
        elif mutation == "duplicate_label":
            root.append(deepcopy(label.getparent().getparent()))
        else:
            field = root.xpath("//w:instrText", namespaces=NS)[0]
            field.getparent().remove(field)
    target = tmp_path / "changed.docx"
    rewrite(source, target, name, change)
    result = inspect_docx(target, case)
    assert any(row["code"] == "PRESERVED_PART_CHANGED" for row in result["findings"])


@pytest.mark.parametrize("mutation", ["missing_first", "missing_title", "later_title", "later_ref"])
def test_fidelity_rejects_cover_scope_or_inheritance_changes(tmp_path, mutation):
    case = case_for("group-company")
    source = build_docx(case, tmp_path / "source.docx")
    def change(root):
        sections = root.xpath("//w:sectPr", namespaces=NS)
        first = sections[0]
        ref = first.xpath("./w:footerReference[@w:type='first']", namespaces=NS)[0]
        if mutation == "missing_first": first.remove(ref)
        elif mutation == "missing_title": first.remove(first.find(W + "titlePg"))
        elif mutation == "later_title": etree.SubElement(sections[1], W + "titlePg")
        else: sections[1].insert(0, deepcopy(ref))
    target = tmp_path / "changed.docx"
    rewrite(source, target, "word/document.xml", change)
    assert any(row["code"] == "DOCUMENT_STRUCTURE_CHANGED" for row in inspect_docx(target, case)["findings"])


@pytest.mark.parametrize("mutation", ["external_relation", "wrong_target", "wrong_content_type", "other_list", "old_label"])
def test_fidelity_keeps_relationships_types_and_other_numbering_strict(tmp_path, mutation):
    case = case_for("group-company")
    source = build_docx(case, tmp_path / "source.docx")
    if mutation in {"external_relation", "wrong_target"}: name = "word/_rels/document.xml.rels"
    elif mutation == "wrong_content_type": name = "[Content_Types].xml"
    else: name = "word/numbering.xml"
    def change(root):
        if mutation in {"external_relation", "wrong_target"}:
            node = next(x for x in root if x.get("Id") == "rIdResearchFundCover")
            node.set("TargetMode" if mutation == "external_relation" else "Target", "External" if mutation == "external_relation" else "footer1.xml")
        elif mutation == "wrong_content_type": root[-1].set("ContentType", "text/plain")
        else:
            abstract = "4" if mutation == "other_list" else "3"
            node = root.xpath(f"./w:abstractNum[@w:abstractNumId='{abstract}']/w:lvl/w:lvlText", namespaces=NS)[0]
            node.set(W + "val", "%1." if mutation == "other_list" else "(%1)")
    target = tmp_path / "changed.docx"
    rewrite(source, target, name, change)
    assert any(row["code"] == "PRESERVED_PART_CHANGED" for row in inspect_docx(target, case)["findings"])


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra"])
def test_fidelity_requires_exact_package_members(tmp_path, mutation):
    case = case_for("group-company")
    source = build_docx(case, tmp_path / "source.docx")
    package = parts(source)
    name = cover_part(package)
    target = tmp_path / "changed.docx"
    with warnings.catch_warnings(), ZipFile(target, "w") as archive:
        warnings.simplefilter("ignore", UserWarning)
        for key, value in package.items():
            if mutation != "missing" or key != name:
                archive.writestr(key, value)
        if mutation == "duplicate": archive.writestr(name, package[name])
        if mutation == "extra": archive.writestr("word/footer999.xml", package[name])
    assert inspect_docx(target, case)["status"] == "FAIL"
