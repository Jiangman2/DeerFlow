from pathlib import Path


p = Path("/app/projects/linkage_full_pipeline_ppt169_20260504")
repls = {
    "page_01.svg": {
        "{{TITLE}}": "PPT Master 脚本联通验收",
        "{{SUBTITLE}}": "source_to_md -> project_manager -> svg_pipeline",
        "{{PROJECT_ID}}": "PROJECT ID: LINKAGE-2026-05",
        "{{DATE}}": "2026-05-04",
        "{{AUTHOR}}": "自动化联通测试",
    },
    "page_02.svg": {
        "{{TOC_ITEM_1_TITLE}}": "源转换与入库",
        "{{TOC_ITEM_1_DESC}}": "多格式转 Markdown 并 import",
        "{{TOC_ITEM_2_TITLE}}": "SVG 主链路",
        "{{TOC_ITEM_2_DESC}}": "quality/split/finalize/export",
        "{{TOC_ITEM_3_TITLE}}": "联通结论",
        "{{TOC_ITEM_3_DESC}}": "阶段成功与依赖边界",
        "{{TOC_ITEM_4_TITLE}}": "",
        "{{TOC_ITEM_4_DESC}}": "",
        "{{TOC_ITEM_5_TITLE}}": "",
        "{{TOC_ITEM_6_TITLE}}": "",
        "{{DATE}}": "2026-05-04",
        "{{PAGE_NUM}}": "2",
    },
    "page_03.svg": {
        "{{KEY_MESSAGE}}": "结论：源转换链路可联通",
        "{{CONTENT_AREA}}": "已验证 HTML/Excel/PDF/PPT 转 Markdown，并成功入库到 sources/",
        "{{SOURCE}}": "/tmp/ppt-linkage fixtures",
        "{{PAGE_NUM}}": "3",
    },
    "page_04.svg": {
        "{{KEY_MESSAGE}}": "结论：后处理链路可联通",
        "{{CONTENT_AREA}}": "已验证 svg_quality_checker -> total_md_split -> finalize_svg",
        "{{SOURCE}}": "pipeline verification",
        "{{PAGE_NUM}}": "4",
    },
    "page_05.svg": {
        "{{KEY_MESSAGE}}": "结论：导出链路可联通",
        "{{CONTENT_AREA}}": "svg_to_pptx -s final 导出主PPT与backup SVG",
        "{{SOURCE}}": "export verification",
        "{{PAGE_NUM}}": "5",
    },
    "page_06.svg": {
        "{{THANK_YOU}}": "THANK YOU",
        "{{ENDING_SUBTITLE}}": "联通验收完成",
        "{{CONTACT_NAME}}": "PPT Master Automation",
        "{{CONTACT_INFO}}": "ops@example.com",
        "{{COPYRIGHT}}": "2026 Integration Test",
    },
}

for f, m in repls.items():
    fp = p / "svg_output" / f
    s = fp.read_text(encoding="utf-8")
    for k, v in m.items():
        s = s.replace(k, v)
    fp.write_text(s, encoding="utf-8")

(p / "design_spec.md").write_text(
    "# Design Specification\n\n- linkage pipeline test\n",
    encoding="utf-8",
)
(p / "spec_lock.md").write_text(
    '{\n  "canvas": {"viewBox": "0 0 1280 720"}\n}\n',
    encoding="utf-8",
)

notes = []
for i in range(1, 7):
    notes.append(f"# page_{i:02d}.svg\n- stage linkage test page {i}")
(p / "notes" / "total.md").write_text("\n---\n".join(notes), encoding="utf-8")
print("filled")
