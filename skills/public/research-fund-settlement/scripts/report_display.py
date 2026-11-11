"""Exact, template-bound presentation changes for generated financial reports."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from lxml import etree

SCHEME = "http"
W_NS = f"{SCHEME}://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = f"{SCHEME}://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = f"{SCHEME}://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = f"{SCHEME}://schemas.openxmlformats.org/package/2006/content-types"
W = "{" + W_NS + "}"
R = "{" + R_NS + "}"
NS = {"w": W_NS}
COVER_LABEL = "AI自动生成"
COVER_REL_ID = "rIdResearchFundCover"
FOOTER_TYPE = R_NS + "/footer"
FOOTER_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"


def _xml(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _balance_numbering(reference: Any, document: etree._Element) -> bytes:
    """Change only the list exclusively used by the eight original balance slots."""
    source = etree.fromstring(reference.read("word/document.xml"))
    paragraphs = source.find(f"{W}body").findall(f"{W}p")
    numbered = source.xpath("//w:numPr[w:numId/@w:val='9']", namespaces=NS)
    expected = [paragraphs[i].find(f"{W}pPr/{W}numPr") for i in range(114, 122)]
    if numbered != expected or any(
        node.find(f"{W}ilvl").get(f"{W}val") != "0" for node in numbered
    ):
        raise ValueError("结余说明编号不再局限于已审核的八个段落")
    numbering = etree.fromstring(reference.read("word/numbering.xml"))
    bound = numbering.xpath("./w:num[w:abstractNumId/@w:val='3']", namespaces=NS)
    if len(bound) != 1 or bound[0].get(f"{W}numId") != "9":
        raise ValueError("结余编号定义被其他列表共用")
    levels = numbering.xpath("./w:abstractNum[@w:abstractNumId='3']/w:lvl", namespaces=NS)
    if len(levels) != 1 or levels[0].get(f"{W}ilvl") != "0":
        raise ValueError("结余编号级别与模板合同不一致")
    label = levels[0].find(f"{W}lvlText")
    if label is None or label.get(f"{W}val") != "(%1)":
        raise ValueError("结余编号文字与模板合同不一致")
    label.set(f"{W}val", "%1.")
    return _xml(numbering)


def apply_report_display(
    reference: Any, document: etree._Element, profile: str
) -> dict[str, bytes]:
    """Apply the same exact permitted delta in generation and fidelity comparison.

    Original assets are never modified. Existing footer parts and page fields
    remain intact; only the first section opts into one new first-page footer.
    Later sections do not enable titlePg, so the label cannot repeat there.
    """
    sections = document.xpath("//w:sectPr", namespaces=NS)
    expected_count = {"group_company": 3, "national_science": 1, "miit": 3}.get(profile)
    if len(sections) != expected_count:
        raise ValueError("封面分节与已审核模板不一致")
    if any(section.findall(f"{W}titlePg") or section.findall(f"{W}headerReference")
           or section.xpath("./w:footerReference[@w:type='first']", namespaces=NS)
           for section in sections):
        raise ValueError("模板存在未经审核的首页页眉或页脚")
    first = sections[0]
    relations = etree.fromstring(reference.read("word/_rels/document.xml.rels"))
    if any(node.get("Id") == COVER_REL_ID for node in relations):
        raise ValueError("封面标识关系名称冲突")
    names = set(reference.namelist())
    footer_numbers = [int(match.group(1)) for name in names
                      if (match := re.fullmatch(r"word/footer(\d+)\.xml", name))]
    footer_name = f"footer{max(footer_numbers, default=0) + 1}.xml"
    footer_part = "word/" + footer_name
    original = first.xpath("./w:footerReference[@w:type='default']", namespaces=NS)
    if len(original) > 1:
        raise ValueError("封面存在多个默认页脚")
    if original:
        matches = [node for node in relations if node.get("Id") == original[0].get(R + "id")]
        if len(matches) != 1 or matches[0].get("Type") != FOOTER_TYPE:
            raise ValueError("封面原页脚关系无效")
        target = matches[0].get("Target", "")
        if not re.fullmatch(r"footer\d+\.xml", target) or matches[0].get("TargetMode"):
            raise ValueError("封面原页脚目标不是模板内部部件")
        if f"word/_rels/{target}.rels" in names:
            raise ValueError("封面原页脚含未审核的附属关系")
        footer = deepcopy(etree.fromstring(reference.read("word/" + target)))
    else:
        footer = etree.Element(W + "ftr", nsmap={"w": W_NS})
    # A separate small paragraph leaves all existing footer/page-field nodes intact.
    paragraph = etree.SubElement(footer, W + "p")
    props = etree.SubElement(paragraph, W + "pPr")
    etree.SubElement(props, W + "spacing", {W + "before": "0", W + "after": "0",
                                           W + "line": "220", W + "lineRule": "exact"})
    etree.SubElement(props, W + "jc", {W + "val": "right"})
    run = etree.SubElement(paragraph, W + "r")
    run_props = etree.SubElement(run, W + "rPr")
    etree.SubElement(run_props, W + "rFonts", {W + "ascii": "宋体", W + "hAnsi": "宋体",
                                               W + "eastAsia": "宋体", W + "cs": "宋体"})
    etree.SubElement(run_props, W + "color", {W + "val": "000000"})
    etree.SubElement(run_props, W + "sz", {W + "val": "18"})
    etree.SubElement(run_props, W + "szCs", {W + "val": "18"})
    etree.SubElement(run, W + "t").text = COVER_LABEL
    reference_node = etree.Element(W + "footerReference", {W + "type": "first", R + "id": COVER_REL_ID})
    reference_position = 0
    for i, child in enumerate(first):
        if child.tag in {W + "headerReference", W + "footerReference"}:
            reference_position = i + 1
    first.insert(reference_position, reference_node)
    title = etree.Element(W + "titlePg")
    following = {W + name for name in ("textDirection", "bidi", "rtlGutter", "docGrid", "printerSettings", "sectPrChange")}
    title_position = next((i for i, node in enumerate(first) if node.tag in following), len(first))
    first.insert(title_position, title)
    etree.SubElement(relations, "{" + REL_NS + "}Relationship", {
        "Id": COVER_REL_ID, "Type": FOOTER_TYPE, "Target": footer_name,
    })
    types = etree.fromstring(reference.read("[Content_Types].xml"))
    if any(node.get("PartName") == "/" + footer_part for node in types):
        raise ValueError("封面页脚内容类型名称冲突")
    etree.SubElement(types, "{" + CT_NS + "}Override", {
        "PartName": "/" + footer_part, "ContentType": FOOTER_CONTENT_TYPE,
    })
    result = {
        "word/_rels/document.xml.rels": _xml(relations),
        "[Content_Types].xml": _xml(types),
        footer_part: _xml(footer),
    }
    if profile == "group_company":
        result["word/numbering.xml"] = _balance_numbering(reference, document)
    return result
