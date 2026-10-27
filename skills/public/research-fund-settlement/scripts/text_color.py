"""Apply the report's black-text rule without changing template geometry."""

from __future__ import annotations

import re

from lxml import etree


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
W14 = "{http://schemas.microsoft.com/office/word/2010/wordml}"
NS = {"w": W_NS}
TEXT_PART = re.compile(
    r"word/(?:document|styles|stylesWithEffects|numbering|comments|footnotes|endnotes|header\d+|footer\d+)\.xml\Z"
)
# CT_RPr children that follow w:color. Preserve the original order otherwise.
AFTER_COLOR = {
    W + name for name in (
        "spacing", "w", "kern", "position", "sz", "szCs", "highlight", "u",
        "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em",
        "lang", "eastAsianLayout", "specVanish", "oMath", "rPrChange",
    )
}


def black_text_part(name: str, data: bytes) -> bytes:
    """Only font colors and their inheritance are editable outside the body."""
    if not TEXT_PART.fullmatch(name):
        return data
    root = etree.fromstring(data)
    before = etree.tostring(root)
    # Word 2010 glyph fills override w:color (the group template uses theme tx1).
    # Remove only that font-fill override so the explicit black run color wins.
    for fill in list(root.iter(W14 + "textFill")):
        fill.getparent().remove(fill)
    if name in {"word/styles.xml", "word/stylesWithEffects.xml"}:
        defaults = root.find(W + "docDefaults")
        if defaults is None:
            defaults = etree.Element(W + "docDefaults")
            root.insert(0, defaults)
        run_defaults = defaults.find(W + "rPrDefault")
        if run_defaults is None:
            run_defaults = etree.Element(W + "rPrDefault")
            defaults.insert(0, run_defaults)
        if run_defaults.find(W + "rPr") is None:
            etree.SubElement(run_defaults, W + "rPr")
    # Explicit run colors also cover tables, TOC/page fields, text boxes and
    # headers/footers. Styles/defaults keep regenerated fields black in Word.
    for run in root.iter(W + "r"):
        if run.find(W + "rPr") is None:
            run.insert(0, etree.Element(W + "rPr"))
    for properties in root.iter(W + "rPr"):
        if properties.find(W + "color") is None:
            color = etree.Element(W + "color")
            offset = next(
                (i for i, child in enumerate(properties) if child.tag in AFTER_COLOR),
                len(properties),
            )
            properties.insert(offset, color)
    for color in root.iter(W + "color"):
        color.set(W + "val", "000000")
        for attribute in ("themeColor", "themeTint", "themeShade"):
            color.attrib.pop(W + attribute, None)
    if etree.tostring(root) == before:
        return data
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def strip_font_color_for_structure(root: etree._Element) -> None:
    """Ignore only the approved font-color delta when comparing body layout."""
    for color in list(root.iter(W + "color")):
        color.getparent().remove(color)
    for fill in list(root.iter(W14 + "textFill")):
        fill.getparent().remove(fill)
    for properties in list(root.iter(W + "rPr")):
        if not properties.attrib and len(properties) == 0:
            properties.getparent().remove(properties)


def black_text_findings(archive) -> list[dict[str, str]]:
    findings = []
    for name in archive.namelist():
        if TEXT_PART.fullmatch(name):
            data = archive.read(name)
            if black_text_part(name, data) != data:
                findings.append({
                    "code": "NON_BLACK_REPORT_TEXT",
                    "message": f"报告字体未统一为黑色或仍含主题色覆盖：{name}",
                })
    return findings
