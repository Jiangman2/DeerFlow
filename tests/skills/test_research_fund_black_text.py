from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest
from lxml import etree

SKILL = Path(__file__).resolve().parents[2] / "skills/public/research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))

from docx_builder import build_docx, inspect_docx  # noqa: E402
from report_model import derive, validate  # noqa: E402
from template_builder import _body_paragraphs  # noqa: E402

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"


def _case(profile):
    return json.loads((SKILL / f"examples/cases/{profile}-complete.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("profile", ["group-company", "national-science", "miit"])
@pytest.mark.parametrize("mode", ["draft", "final"])
def test_entire_report_has_explicit_black_fonts_including_fields_and_styles(tmp_path, profile, mode):
    case, issues = validate(_case(profile), mode=mode)
    assert not [item for item in issues if item["severity"] == "ERROR"]
    output = build_docx(case, tmp_path / "report.docx", mode)
    with zipfile.ZipFile(output) as archive:
        for name in archive.namelist():
            if not name.startswith("word/") or not name.endswith(".xml"):
                continue
            root = etree.fromstring(archive.read(name))
            assert not list(root.iter(W14 + "textFill")), name
            for color in root.iter(W + "color"):
                assert color.get(W + "val") == "000000", name
                assert not any(color.get(W + key) for key in ("themeColor", "themeTint", "themeShade")), name
            for run in root.iter(W + "r"):
                color = run.find(f"{W}rPr/{W}color")
                assert color is not None and color.get(W + "val") == "000000", name
            if name == "word/styles.xml":
                color = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}color")
                assert color is not None and color.get(W + "val") == "000000"
    assert inspect_docx(output, case)["status"] == "PASS"


def _rewrite_part(source, target, name, transform):
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(target, "w") as after:
        for info in before.infolist():
            data = before.read(info.filename)
            if info.filename == name:
                root = etree.fromstring(data)
                transform(root)
                data = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            after.writestr(info, data)


@pytest.mark.parametrize("part", ["word/document.xml", "word/footer1.xml", "word/styles.xml", "word/comments.xml"])
@pytest.mark.parametrize("override", ["color", "theme"])
def test_inspect_rejects_colored_text_even_with_valid_structure(tmp_path, part, override):
    case, _ = validate(_case("group-company"), mode="final")
    source = build_docx(case, tmp_path / "report.docx")

    def recolor(root):
        color = next(root.iter(W + "color"))
        color.set(W + ("val" if override == "color" else "themeColor"), "FF0000" if override == "color" else "accent1")

    target = tmp_path / "bad.docx"
    _rewrite_part(source, target, part, recolor)
    result = inspect_docx(target, case)
    assert result["status"] == "FAIL"
    assert any(item["code"] == "NON_BLACK_REPORT_TEXT" for item in result["findings"])


def test_black_font_permission_does_not_allow_style_or_geometry_changes(tmp_path):
    case, _ = validate(_case("group-company"), mode="final")
    source = build_docx(case, tmp_path / "report.docx")

    def change_style(root):
        next(root.iter(W + "sz")).set(W + "val", "99")

    def change_margin(root):
        next(root.iter(W + "pgMar")).set(W + "left", "999")

    for part, edit, expected in [
        ("word/styles.xml", change_style, "PRESERVED_PART_CHANGED"),
        ("word/document.xml", change_margin, "DOCUMENT_STRUCTURE_CHANGED"),
    ]:
        target = tmp_path / "bad.docx"
        _rewrite_part(source, target, part, edit)
        result = inspect_docx(target, case)
        assert any(item["code"] == expected for item in result["findings"]), result


def test_inspect_rejects_word_2010_text_fill_override(tmp_path):
    case, _ = validate(_case("group-company"), mode="final")
    source = build_docx(case, tmp_path / "report.docx")

    def red_fill(root):
        properties = next(root.iter(W + "rPr"))
        fill = etree.SubElement(properties, W14 + "textFill")
        solid = etree.SubElement(fill, W14 + "solidFill")
        etree.SubElement(solid, W14 + "srgbClr").set(W14 + "val", "FF0000")

    target = tmp_path / "bad.docx"
    _rewrite_part(source, target, "word/document.xml", red_fill)
    result = inspect_docx(target, case)
    assert any(item["code"] == "NON_BLACK_REPORT_TEXT" for item in result["findings"])


def test_zero_budget_fidelity_requires_exact_case_selected_omissions(tmp_path):
    raw = _case("group-company")
    raw["expenses"][0]["budget"] = raw["expenses"][0]["adjusted_budget"] = 0
    case = derive(raw)
    source = build_docx(case, tmp_path / "zero.docx")
    assert inspect_docx(source, case)["status"] == "PASS"
    assert inspect_docx(source, _case("group-company"))["status"] == "FAIL"

    def delete_extra(root):
        # A nonzero category is never an authorized optional paragraph.
        paragraph = next(p for p in _body_paragraphs(root) if "材料/燃料动力费：批复概预算" in "".join(p.itertext()))
        paragraph.getparent().remove(paragraph)

    target = tmp_path / "bad.docx"
    _rewrite_part(source, target, "word/document.xml", delete_extra)
    assert inspect_docx(target, case)["status"] == "FAIL"
