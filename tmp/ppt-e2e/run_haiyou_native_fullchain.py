from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path("/app/skills/public/ppt-master")
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
LAYOUT_ROOT = SKILL_ROOT / "templates" / "layouts" / "haiyou_pure_svg"
PROJECTS_ROOT = SKILL_ROOT / "projects"
PYTHON = sys.executable


def run(cmd: list[str]) -> str:
    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
        env.pop(key, None)
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
    out = proc.stdout.strip()
    if out:
        print(out)
    if proc.stderr.strip():
        print(proc.stderr.strip())
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return out


def fill_placeholders(svg_text: str, mapping: dict[str, str]) -> str:
    for k, v in mapping.items():
        svg_text = svg_text.replace(k, v)
    return svg_text


def append_content_lines(svg_text: str, lines: list[str]) -> str:
    payload = []
    y = 190
    for line in lines:
        payload.append(
            f'<text x="64" y="{y}" fill="#123B5E" font-size="28" '
            f'font-family="Microsoft YaHei">{line}</text>'
        )
        y += 54
    return svg_text.replace("</svg>", "\n  " + "\n  ".join(payload) + "\n</svg>")


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"haiyou_native_hq_{stamp}"
    source_md = Path(f"/tmp/{project_name}_source.md")
    web_md = Path(f"/tmp/{project_name}_web.md")

    source_md.write_text(
        "# 海上平台智能运维与安全生产一体化方案\n\n"
        "## 目标\n"
        "- 在 12 个月内将关键设备非计划停机率降低 30%\n"
        "- 建立“采集-诊断-处置-复盘”闭环\n"
        "- 形成可复制到 3 个作业区的标准化能力\n\n"
        "## 基线与约束\n"
        "- 现有 OT 系统异构、数据口径不统一\n"
        "- 安全生产要求高，需优先保障稳定性\n"
        "- 培训周期短，现场班组需要低门槛工具\n\n"
        "## 路线图\n"
        "- Q1: 数据治理与主数据台账\n"
        "- Q2: 故障模式库与诊断引擎上线\n"
        "- Q3: 预警联动与作业票联动\n"
        "- Q4: 全域推广与指标固化\n",
        encoding="utf-8",
    )

    run(
        [
            PYTHON,
            str(SCRIPTS_ROOT / "source_to_md" / "web_to_md.py"),
            "https://example.com",
            "-o",
            str(web_md),
        ]
    )

    init_out = run(
        [
            PYTHON,
            str(SCRIPTS_ROOT / "project_manager.py"),
            "init",
            project_name,
            "--format",
            "ppt169",
        ]
    )

    m = re.search(r"Project created:\s*(projects/\S+)", init_out)
    if not m:
        raise RuntimeError("Cannot parse project path from init output")
    project_path = Path(m.group(1))
    if not project_path.is_absolute():
        project_path = Path.cwd() / project_path
    import_files = [str(source_md)]
    if web_md.exists():
        import_files.append(str(web_md))

    run(
        [
            PYTHON,
            str(SCRIPTS_ROOT / "project_manager.py"),
            "import-sources",
            str(project_path),
            *import_files,
            "--move",
        ]
    )

    for asset in LAYOUT_ROOT.iterdir():
        if asset.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            shutil.copy2(asset, project_path / "images" / asset.name)
        if asset.suffix.lower() == ".svg":
            shutil.copy2(asset, project_path / "templates" / asset.name)

    run([PYTHON, str(SCRIPTS_ROOT / "analyze_images.py"), str(project_path / "images")])

    (project_path / "design_spec.md").write_text(
        "# Design Spec\n\n"
        "- Theme: 海上平台智能运维与安全生产\n"
        "- Template: haiyou_pure_svg\n"
        "- Audience: 管理层 + 运维团队\n"
        "- Visual Goal: 稳重、专业、高可读\n",
        encoding="utf-8",
    )

    (project_path / "spec_lock.md").write_text(
        "## canvas\n"
        "- viewBox: 0 0 1280 720\n"
        "- format: PPT 16:9\n\n"
        "## colors\n"
        "- bg: #FFFFFF\n"
        "- primary: #004C8B\n"
        "- accent: #DE002B\n"
        "- accent_blue: #0074CC\n"
        "- text_soft_1: #EAF2F9\n"
        "- text_soft_2: #DDEAF6\n"
        "- text_soft_3: #E3E8ED\n"
        "- text: #123B5E\n"
        "- text_secondary: #5B7185\n\n"
        "## typography\n"
        '- font_family: "Microsoft YaHei"\n'
        '- body_family: "Microsoft YaHei"\n'
        '- title_family: "Microsoft YaHei"\n'
        "- numeral_family: Arial\n"
        '- code_family: Consolas, "Courier New", monospace\n'
        "- body: 22\n"
        "- title: 42\n"
        "- subtitle: 30\n"
        "- annotation: 16\n\n"
        "- hero_number: 148\n"
        "- ending_title: 112\n\n"
        "## icons\n"
        "- library: chunk-filled\n"
        "- inventory: chart-bar, shield, users, target, bolt\n\n"
        "## images\n"
        "- cover_bg: images/cover_bg.png\n\n"
        "## page_rhythm\n"
        "- P01: anchor\n"
        "- P02: dense\n"
        "- P03: anchor\n"
        "- P04: dense\n"
        "- P05: breathing\n"
        "- P06: anchor\n"
        "- P07: dense\n"
        "- P08: breathing\n"
        "- P09: dense\n"
        "- P10: anchor\n\n"
        "## forbidden\n"
        "- rgba()\n"
        "- <style>, class, <foreignObject>, <script>\n",
        encoding="utf-8",
    )

    svg_output = project_path / "svg_output"
    if svg_output.exists():
        shutil.rmtree(svg_output)
    svg_output.mkdir(parents=True, exist_ok=True)
    for asset in (project_path / "images").iterdir():
        if asset.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            shutil.copy2(asset, svg_output / asset.name)

    page_defs = [
        ("01_cover.svg", "01_cover.svg", {
            "{{TITLE}}": "海上平台智能运维与安全生产",
            "{{SUBTITLE}}": "主链路全调用演示版",
            "{{AUTHOR}}": "PPT Master Native Pipeline",
            "{{DATE}}": datetime.now().strftime("%Y-%m-%d"),
        }, []),
        ("02_toc.svg", "02_toc.svg", {
            "{{TOC_ITEM_1_TITLE}}": "现状与目标",
            "{{TOC_ITEM_2_TITLE}}": "方案与路径",
            "{{TOC_ITEM_3_TITLE}}": "收益与保障",
        }, []),
        ("03_chapter_1.svg", "02_chapter.svg", {
            "{{CHAPTER_NUM}}": "01",
            "{{CHAPTER_TITLE}}": "现状与目标",
        }, []),
        ("04_status.svg", "03_content.svg", {
            "{{PAGE_TITLE}}": "当前痛点与改进机会",
            "{{CONTENT_AREA_LINE_1}}": "关键问题清单",
        }, ["设备告警分散在多个系统，响应链路长", "计划检修与状态检修衔接不足", "经验依赖强，跨班组复用效率低"]),
        ("05_target.svg", "03_content.svg", {
            "{{PAGE_TITLE}}": "年度目标与核心指标",
            "{{CONTENT_AREA_LINE_1}}": "2026 目标卡",
        }, ["非计划停机率下降 30%", "关键风险处置时长缩短 40%", "设备健康度可视化覆盖率达到 95%"]),
        ("06_chapter_2.svg", "02_chapter.svg", {
            "{{CHAPTER_NUM}}": "02",
            "{{CHAPTER_TITLE}}": "方案与路径",
        }, []),
        ("07_arch.svg", "03_content.svg", {
            "{{PAGE_TITLE}}": "一体化能力架构",
            "{{CONTENT_AREA_LINE_1}}": "四层架构",
        }, ["采集层：边缘采集 + 协议适配", "平台层：主数据、规则引擎、事件总线", "应用层：诊断、预警、工单联动"]),
        ("08_roadmap.svg", "03_content.svg", {
            "{{PAGE_TITLE}}": "实施路线图",
            "{{CONTENT_AREA_LINE_1}}": "季度推进",
        }, ["Q1 数据治理与资产台账", "Q2 故障模式库 + 诊断引擎", "Q3 预警联动 + 现场闭环"]),
        ("09_value.svg", "03_content.svg", {
            "{{PAGE_TITLE}}": "价值与风险控制",
            "{{CONTENT_AREA_LINE_1}}": "双维度评估",
        }, ["经营价值：减少停机损失与维修成本", "管理价值：统一口径与决策透明", "风险控制：红线指标日清日结"]),
        ("10_ending.svg", "04_ending.svg", {
            "{{THANK_YOU}}": "谢谢",
        }, []),
    ]

    for file_name, base_svg, mapping, extra_lines in page_defs:
        text = (LAYOUT_ROOT / base_svg).read_text(encoding="utf-8")
        text = fill_placeholders(text, mapping)
        if extra_lines:
            text = append_content_lines(text, extra_lines)
        (svg_output / file_name).write_text(text, encoding="utf-8")

    notes = project_path / "notes" / "total.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    note_lines = []
    for i in range(1, 11):
        note_lines.append(f"## Page {i}")
        note_lines.append(f"第{i}页讲解：围绕“安全生产优先、效率持续提升”展开，强调可执行路径。")
        note_lines.append("")
    notes.write_text("\n".join(note_lines), encoding="utf-8")

    run([PYTHON, str(SCRIPTS_ROOT / "svg_quality_checker.py"), str(project_path)])
    run([PYTHON, str(SCRIPTS_ROOT / "total_md_split.py"), str(project_path)])
    run([PYTHON, str(SCRIPTS_ROOT / "finalize_svg.py"), str(project_path)])
    export_out = run([PYTHON, str(SCRIPTS_ROOT / "svg_to_pptx.py"), str(project_path), "-s", "final"])

    export_match = re.findall(r"(/app/skills/public/ppt-master/projects/.+?\\.pptx)", export_out)
    result = {
        "project_name": project_name,
        "project_path": str(project_path),
        "exports": export_match,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
