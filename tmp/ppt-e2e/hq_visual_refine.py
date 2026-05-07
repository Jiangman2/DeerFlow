from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from svg_editor.annotations import assign_temp_ids, remove_annotation, set_annotation


SVG_NS = {"svg": "http://www.w3.org/2000/svg"}
PROJECT = Path("/app/projects/hq_refined_compare_ppt169_20260504")


def mark_annotations(svg_path: Path) -> None:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    assign_temp_ids(root)

    for elem in root.findall(".//svg:text", SVG_NS):
        t = (elem.text or "").strip()
        eid = elem.get("id", "")
        if not eid:
            continue
        if t == "建设事项优先级矩阵":
            set_annotation(root, eid, "标题精修：改为“建设优先级矩阵（精修）”，字号提高到38，强调结论导向")
        if t.startswith("结论：优先做“高价值快赢"):
            set_annotation(root, eid, "文案精修：压缩为一句话，避免超过两行，字号调整到31")

    tree.write(svg_path, encoding="utf-8", xml_declaration=True)


def apply_annotations(svg_path: Path) -> None:
    tree = ET.parse(svg_path)
    root = tree.getroot()
    for elem in root.iter():
        if elem.get("data-edit-target") != "true":
            continue
        note = elem.get("data-edit-annotation", "")
        text = (elem.text or "").strip()
        if "标题精修" in note and text == "建设事项优先级矩阵":
            elem.text = "建设优先级矩阵（精修）"
            elem.set("font-size", "38")
            elem.set("font-weight", "900")
        if "文案精修" in note and text.startswith("结论：优先做“高价值快赢"):
            elem.text = "结论：先做快赢+底座，12周形成可量化收益。"
            elem.set("font-size", "31")
            elem.set("font-weight", "800")
        eid = elem.get("id", "")
        if eid:
            remove_annotation(root, eid)
    tree.write(svg_path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    page4 = PROJECT / "svg_output" / "page_04.svg"
    page9 = PROJECT / "svg_output" / "page_09.svg"
    mark_annotations(page4)
    mark_annotations(page9)
    apply_annotations(page4)
    apply_annotations(page9)
    print("visual_refine_done")


if __name__ == "__main__":
    main()
