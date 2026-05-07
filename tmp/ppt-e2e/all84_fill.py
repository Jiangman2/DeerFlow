from pathlib import Path


p = Path("/app/projects/all84_fullchain_showcase_ppt169_20260504")
repls = {
    "page_01.svg": {
        "{{TITLE}}": "PPT-Master 全能力联通验收",
        "{{SUBTITLE}}": "source_to_md + 84脚本覆盖 + 原生导出",
        "{{PROJECT_ID}}": "PROJECT ID: ALL84-2026-05",
        "{{DATE}}": "2026-05-04",
        "{{AUTHOR}}": "自动化任务",
    },
    "page_02.svg": {
        "{{TOC_ITEM_1_TITLE}}": "多源资料入库",
        "{{TOC_ITEM_1_DESC}}": "Web/HTML/Excel/PDF/PPT -> sources",
        "{{TOC_ITEM_2_TITLE}}": "84脚本全执行",
        "{{TOC_ITEM_2_DESC}}": "内部模块 import+exec 全覆盖",
        "{{TOC_ITEM_3_TITLE}}": "原生导出链路",
        "{{TOC_ITEM_3_DESC}}": "quality/split/finalize/export",
        "{{TOC_ITEM_4_TITLE}}": "质量观察",
        "{{TOC_ITEM_4_DESC}}": "模板与内容贴合度",
        "{{TOC_ITEM_5_TITLE}}": "",
        "{{TOC_ITEM_6_TITLE}}": "",
        "{{DATE}}": "2026-05-04",
        "{{PAGE_NUM}}": "2",
    },
    "page_03.svg": {
        "{{KEY_MESSAGE}}": "源转换已联通",
        "{{CONTENT_AREA}}": "已完成 web/html/excel/pdf/ppt 到 markdown 并入库。",
        "{{SOURCE}}": "/tmp/all84-task",
        "{{PAGE_NUM}}": "3",
    },
    "page_04.svg": {
        "{{KEY_MESSAGE}}": "84脚本执行覆盖",
        "{{CONTENT_AREA}}": "内部模块导入与代表函数执行均已纳入同任务流程。",
        "{{SOURCE}}": "internal_module_coverage.py",
        "{{PAGE_NUM}}": "4",
    },
    "page_05.svg": {
        "{{KEY_MESSAGE}}": "图像与后处理能力",
        "{{CONTENT_AREA}}": "包含 image_sources/image_backends/svg_finalize/template_import 等能力验证。",
        "{{SOURCE}}": "module-level coverage",
        "{{PAGE_NUM}}": "5",
    },
    "page_06.svg": {
        "{{KEY_MESSAGE}}": "导出能力验证",
        "{{CONTENT_AREA}}": "svg_quality_checker -> total_md_split -> finalize_svg -> svg_to_pptx。",
        "{{SOURCE}}": "native pipeline",
        "{{PAGE_NUM}}": "6",
    },
    "page_07.svg": {
        "{{THANK_YOU}}": "THANK YOU",
        "{{ENDING_SUBTITLE}}": "ALL84 全联通任务完成",
        "{{CONTACT_NAME}}": "PPT Master QA",
        "{{CONTACT_INFO}}": "ops@example.com",
        "{{COPYRIGHT}}": "2026 Full Coverage Test",
    },
}

for f, m in repls.items():
    fp = p / "svg_output" / f
    s = fp.read_text(encoding="utf-8")
    for k, v in m.items():
        s = s.replace(k, v)
    fp.write_text(s, encoding="utf-8")

(p / "design_spec.md").write_text(
    "# Design Specification\n\n- all84 fullchain task\n",
    encoding="utf-8",
)

(p / "spec_lock.md").write_text(
    '{\n  "canvas": {"viewBox": "0 0 1280 720"}\n}\n',
    encoding="utf-8",
)

notes = []
for i in range(1, 8):
    notes.append(f"# page_{i:02d}.svg\n- all84 task page {i}")
(p / "notes" / "total.md").write_text("\n---\n".join(notes), encoding="utf-8")
print("filled")
