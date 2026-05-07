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
PYTHON = sys.executable


def run(cmd: list[str]) -> str:
    env = os.environ.copy()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        env.pop(key, None)
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
    if proc.stdout.strip():
        print(proc.stdout.strip())
    if proc.stderr.strip():
        print(proc.stderr.strip())
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")
    return proc.stdout


def fill(text: str, mapping: dict[str, str]) -> str:
    for k, v in mapping.items():
        text = text.replace(k, v)
    return text


def add_nodes(svg: str, nodes: list[str]) -> str:
    return svg.replace("</svg>", "\n  " + "\n  ".join(nodes) + "\n</svg>")


def kpi_cards() -> list[str]:
    return [
        '<rect x="72" y="176" width="354" height="172" rx="18" fill="#EAF4FD" stroke="#A9CCE9"/>',
        '<rect x="462" y="176" width="354" height="172" rx="18" fill="#EAF4FD" stroke="#A9CCE9"/>',
        '<rect x="852" y="176" width="354" height="172" rx="18" fill="#EAF4FD" stroke="#A9CCE9"/>',
        '<text x="96" y="220" fill="#0F3E68" font-size="26" font-family="Microsoft YaHei" font-weight="700">设备可利用率</text>',
        '<text x="96" y="304" fill="#004C8B" font-size="64" font-family="Arial" font-weight="700">98.6%</text>',
        '<text x="486" y="220" fill="#0F3E68" font-size="26" font-family="Microsoft YaHei" font-weight="700">故障闭环时长</text>',
        '<text x="486" y="304" fill="#004C8B" font-size="64" font-family="Arial" font-weight="700">5.8h</text>',
        '<text x="876" y="220" fill="#0F3E68" font-size="26" font-family="Microsoft YaHei" font-weight="700">隐患整改达成率</text>',
        '<text x="876" y="304" fill="#004C8B" font-size="64" font-family="Arial" font-weight="700">96%</text>',
        '<rect x="72" y="382" width="1134" height="222" rx="16" fill="#FFFFFF" stroke="#C9DCEC"/>',
        '<text x="96" y="432" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">KPI解释：以“安全红线零突破 + 关键资产稳态运行”为核心，按周跟踪趋势、按月固化改进。</text>',
        '<text x="96" y="478" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">结果：停机损失同比下降 27%，高频缺陷复发率下降 31%，跨班组知识复用效率显著提升。</text>',
    ]


def trend_chart() -> list[str]:
    return [
        '<rect x="90" y="180" width="820" height="420" rx="14" fill="#FFFFFF" stroke="#C9DCEC"/>',
        '<line x1="150" y1="560" x2="860" y2="560" stroke="#7CA4C5" stroke-width="2"/>',
        '<line x1="150" y1="240" x2="150" y2="560" stroke="#7CA4C5" stroke-width="2"/>',
        '<polyline points="190,520 300,486 410,458 520,430 630,392 740,350 850,318" fill="none" stroke="#0074CC" stroke-width="6"/>',
        '<polyline points="190,540 300,530 410,512 520,495 630,470 740,452 850,438" fill="none" stroke="#00A67A" stroke-width="6"/>',
        '<text x="175" y="592" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">Q1</text>',
        '<text x="285" y="592" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">Q2</text>',
        '<text x="395" y="592" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">Q3</text>',
        '<text x="505" y="592" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">Q4</text>',
        '<text x="615" y="592" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">Q5</text>',
        '<text x="725" y="592" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">Q6</text>',
        '<text x="835" y="592" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">Q7</text>',
        '<rect x="944" y="218" width="260" height="382" rx="14" fill="#F4F9FE" stroke="#C9DCEC"/>',
        '<text x="968" y="262" fill="#0F3E68" font-size="24" font-family="Microsoft YaHei" font-weight="700">趋势解读</text>',
        '<text x="968" y="314" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">• 可靠性指数持续爬坡</text>',
        '<text x="968" y="356" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">• 闭环时长稳步收敛</text>',
        '<text x="968" y="398" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">• 关键风险按月清零</text>',
        '<rect x="968" y="438" width="20" height="20" fill="#0074CC"/>',
        '<text x="998" y="455" fill="#123B5E" font-size="20" font-family="Microsoft YaHei">可靠性指数</text>',
        '<rect x="968" y="478" width="20" height="20" fill="#00A67A"/>',
        '<text x="998" y="495" fill="#123B5E" font-size="20" font-family="Microsoft YaHei">闭环效率指数</text>',
    ]


def bar_chart() -> list[str]:
    return [
        '<rect x="80" y="200" width="1120" height="380" rx="14" fill="#FFFFFF" stroke="#C9DCEC"/>',
        '<line x1="150" y1="540" x2="1120" y2="540" stroke="#7CA4C5" stroke-width="2"/>',
        '<line x1="150" y1="250" x2="150" y2="540" stroke="#7CA4C5" stroke-width="2"/>',
        '<rect x="220" y="420" width="90" height="120" fill="#A8D5FF"/>',
        '<rect x="340" y="340" width="90" height="200" fill="#0074CC"/>',
        '<rect x="520" y="380" width="90" height="160" fill="#9DEACC"/>',
        '<rect x="640" y="280" width="90" height="260" fill="#00A67A"/>',
        '<rect x="820" y="460" width="90" height="80" fill="#FFD3DA"/>',
        '<rect x="940" y="360" width="90" height="180" fill="#DE002B"/>',
        '<text x="224" y="575" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">可用率</text>',
        '<text x="526" y="575" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">闭环率</text>',
        '<text x="826" y="575" fill="#355B7D" font-size="20" font-family="Microsoft YaHei">故障率↓</text>',
        '<text x="232" y="410" fill="#0F3E68" font-size="22" font-family="Arial">71</text>',
        '<text x="352" y="330" fill="#0F3E68" font-size="22" font-family="Arial">93</text>',
        '<text x="532" y="370" fill="#0F3E68" font-size="22" font-family="Arial">64</text>',
        '<text x="652" y="270" fill="#0F3E68" font-size="22" font-family="Arial">88</text>',
        '<text x="832" y="450" fill="#0F3E68" font-size="22" font-family="Arial">4.8</text>',
        '<text x="952" y="350" fill="#0F3E68" font-size="22" font-family="Arial">2.1</text>',
        '<rect x="1040" y="240" width="20" height="20" fill="#A8D5FF"/>',
        '<text x="1070" y="257" fill="#123B5E" font-size="18" font-family="Microsoft YaHei">基线</text>',
        '<rect x="1040" y="274" width="20" height="20" fill="#0074CC"/>',
        '<text x="1070" y="291" fill="#123B5E" font-size="18" font-family="Microsoft YaHei">目标</text>',
    ]


def matrix_page() -> list[str]:
    return [
        '<rect x="100" y="190" width="1040" height="430" rx="16" fill="#FFFFFF" stroke="#C9DCEC"/>',
        '<line x1="620" y1="220" x2="620" y2="590" stroke="#A9CCE9" stroke-width="2"/>',
        '<line x1="130" y1="405" x2="1110" y2="405" stroke="#A9CCE9" stroke-width="2"/>',
        '<text x="190" y="265" fill="#004C8B" font-size="30" font-family="Microsoft YaHei" font-weight="700">高价值 / 快实施</text>',
        '<text x="190" y="308" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">主数据治理、告警去噪、值班看板统一</text>',
        '<text x="680" y="265" fill="#004C8B" font-size="30" font-family="Microsoft YaHei" font-weight="700">高价值 / 慢实施</text>',
        '<text x="680" y="308" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">设备机理模型、预测性检修、跨平台联控</text>',
        '<text x="190" y="470" fill="#004C8B" font-size="30" font-family="Microsoft YaHei" font-weight="700">低价值 / 快实施</text>',
        '<text x="190" y="513" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">模板报表、标准SOP复用、例行巡检优化</text>',
        '<text x="680" y="470" fill="#004C8B" font-size="30" font-family="Microsoft YaHei" font-weight="700">低价值 / 慢实施</text>',
        '<text x="680" y="513" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">零散需求、收益不明确功能后置规划</text>',
    ]


def roadmap_page() -> list[str]:
    return [
        '<line x1="120" y1="360" x2="1160" y2="360" stroke="#7CA4C5" stroke-width="8"/>',
        '<circle cx="220" cy="360" r="24" fill="#0074CC"/>',
        '<circle cx="500" cy="360" r="24" fill="#0074CC"/>',
        '<circle cx="780" cy="360" r="24" fill="#0074CC"/>',
        '<circle cx="1060" cy="360" r="24" fill="#DE002B"/>',
        '<rect x="140" y="190" width="160" height="120" rx="14" fill="#EAF4FD" stroke="#A9CCE9"/>',
        '<text x="164" y="236" fill="#0F3E68" font-size="28" font-family="Arial" font-weight="700">Q1</text>',
        '<text x="164" y="274" fill="#123B5E" font-size="20" font-family="Microsoft YaHei">资产台账</text>',
        '<rect x="420" y="190" width="160" height="120" rx="14" fill="#EAF4FD" stroke="#A9CCE9"/>',
        '<text x="444" y="236" fill="#0F3E68" font-size="28" font-family="Arial" font-weight="700">Q2</text>',
        '<text x="444" y="274" fill="#123B5E" font-size="20" font-family="Microsoft YaHei">规则引擎</text>',
        '<rect x="700" y="190" width="160" height="120" rx="14" fill="#EAF4FD" stroke="#A9CCE9"/>',
        '<text x="724" y="236" fill="#0F3E68" font-size="28" font-family="Arial" font-weight="700">Q3</text>',
        '<text x="724" y="274" fill="#123B5E" font-size="20" font-family="Microsoft YaHei">联动闭环</text>',
        '<rect x="980" y="190" width="160" height="120" rx="14" fill="#FFECEF" stroke="#F4B6C2"/>',
        '<text x="1004" y="236" fill="#7D1E31" font-size="28" font-family="Arial" font-weight="700">Q4</text>',
        '<text x="1004" y="274" fill="#7D1E31" font-size="20" font-family="Microsoft YaHei">规模推广</text>',
        '<text x="120" y="470" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">里程碑交付：标准数据域、事件体系、指标看板、运营机制四件套同步落地。</text>',
        '<text x="120" y="516" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">组织保障：平台团队 + 工艺专家 + 现场骨干联合例会，周跟踪、月复盘、季审计。</text>',
    ]


def governance_page() -> list[str]:
    return [
        '<rect x="90" y="186" width="350" height="420" rx="18" fill="#FFFFFF" stroke="#C9DCEC"/>',
        '<rect x="466" y="186" width="350" height="420" rx="18" fill="#FFFFFF" stroke="#C9DCEC"/>',
        '<rect x="842" y="186" width="350" height="420" rx="18" fill="#FFFFFF" stroke="#C9DCEC"/>',
        '<text x="126" y="236" fill="#004C8B" font-size="30" font-family="Microsoft YaHei" font-weight="700">组织治理</text>',
        '<text x="126" y="288" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">1) 设立联合治理委员会</text>',
        '<text x="126" y="330" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">2) 明确指标口径与归口</text>',
        '<text x="126" y="372" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">3) 固化考核与复盘机制</text>',
        '<text x="502" y="236" fill="#004C8B" font-size="30" font-family="Microsoft YaHei" font-weight="700">技术治理</text>',
        '<text x="502" y="288" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">1) 数据质量门禁与审计</text>',
        '<text x="502" y="330" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">2) 模型版本管理与回滚</text>',
        '<text x="502" y="372" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">3) 变更发布分级审批</text>',
        '<text x="878" y="236" fill="#004C8B" font-size="30" font-family="Microsoft YaHei" font-weight="700">运营治理</text>',
        '<text x="878" y="288" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">1) 周报月报自动生成</text>',
        '<text x="878" y="330" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">2) 重大风险红黄灯管理</text>',
        '<text x="878" y="372" fill="#123B5E" font-size="22" font-family="Microsoft YaHei">3) 跨区域经验复制推广</text>',
    ]


def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    project_name = f"haiyou_masterpiece_{stamp}"
    source_md = Path(f"/tmp/{project_name}_source.md")

    source_md.write_text(
        "# 海上油气平台智能运维与安全生产一体化建设方案\n\n"
        "## 项目背景\n"
        "- 面向海上复杂工况，统一设备运行数据与作业流程数据\n"
        "- 以安全红线为底座，构建可持续优化的运营体系\n\n"
        "## 建设目标\n"
        "- 建立统一指标体系与一体化运营看板\n"
        "- 降低非计划停机率、缩短故障闭环时长、提升隐患整改质量\n\n"
        "## 建设原则\n"
        "- 业务牵引、数据驱动、场景闭环、分层治理\n"
        "- 优先落地高价值可复制场景，逐步规模推广\n",
        encoding="utf-8",
    )

    init_out = run([PYTHON, str(SCRIPTS_ROOT / "project_manager.py"), "init", project_name, "--format", "ppt169"])
    m = re.search(r"Project created:\s*(projects/\S+)", init_out)
    if not m:
        raise RuntimeError("Cannot parse project path")
    project_path = Path(m.group(1))
    if not project_path.is_absolute():
        project_path = Path.cwd() / project_path

    run([PYTHON, str(SCRIPTS_ROOT / "project_manager.py"), "import-sources", str(project_path), str(source_md), "--move"])

    for asset in LAYOUT_ROOT.iterdir():
        if asset.suffix.lower() in (".png", ".jpg", ".jpeg"):
            shutil.copy2(asset, project_path / "images" / asset.name)
        if asset.suffix.lower() == ".svg":
            shutil.copy2(asset, project_path / "templates" / asset.name)

    (project_path / "design_spec.md").write_text(
        "# Design Spec\n\n"
        "- Theme: 海上油气平台智能运维\n"
        "- Layout Family: haiyou_pure_svg\n"
        "- Style: 专业、稳重、信息密度中高\n"
        "- Quality Goal: 不越界、可编辑、可讲述\n",
        encoding="utf-8",
    )

    (project_path / "spec_lock.md").write_text(
        "## canvas\n- viewBox: 0 0 1280 720\n\n"
        "## colors\n- primary: #004C8B\n- accent: #DE002B\n- blue: #0074CC\n- green: #00A67A\n"
        "- text: #123B5E\n- title_dark: #0F3E68\n- soft_bg: #EAF4FD\n- border: #C9DCEC\n\n"
        "## typography\n- font_family: Microsoft YaHei\n- numeral_family: Arial\n"
        "- title: 40\n- body: 22\n- body_large: 24\n- metric: 64\n\n"
        "## forbidden\n- <foreignObject>\n- <style>\n- class=\n",
        encoding="utf-8",
    )

    svg_output = project_path / "svg_output"
    if svg_output.exists():
        shutil.rmtree(svg_output)
    svg_output.mkdir(parents=True, exist_ok=True)
    for asset in (project_path / "images").iterdir():
        if asset.suffix.lower() in (".png", ".jpg", ".jpeg"):
            shutil.copy2(asset, svg_output / asset.name)

    pages: list[tuple[str, str, dict[str, str], list[str]]] = [
        ("01_cover.svg", "01_cover.svg", {"{{TITLE}}": "海上油气平台智能运维与安全生产一体化方案", "{{SUBTITLE}}": "高质量成片示例（haiyou_pure_svg）", "{{AUTHOR}}": "DeerFlow · PPT Master", "{{DATE}}": datetime.now().strftime("%Y-%m-%d")}, []),
        ("02_toc.svg", "02_toc.svg", {"{{TOC_ITEM_1_TITLE}}": "业务现状与目标", "{{TOC_ITEM_2_TITLE}}": "方案设计与实施路径", "{{TOC_ITEM_3_TITLE}}": "收益评估与治理机制"}, []),
        ("03_chapter.svg", "02_chapter.svg", {"{{CHAPTER_NUM}}": "01", "{{CHAPTER_TITLE}}": "业务现状与目标"}, []),
        ("04_overview.svg", "03_content.svg", {"{{PAGE_TITLE}}": "现状痛点与建设必要性", "{{CONTENT_AREA_LINE}}": "多系统割裂、响应链路长、经验复用弱，亟需统一数据与运营闭环"}, [
            '<text x="86" y="214" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">• 告警噪声高：人工筛选成本高，跨系统归因效率低</text>',
            '<text x="86" y="258" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">• 指标口径不一：经营分析与生产分析难以同频</text>',
            '<text x="86" y="302" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">• 处置协同不足：班组间经验传递存在断点</text>',
            '<rect x="78" y="346" width="1124" height="238" rx="16" fill="#FFFFFF" stroke="#C9DCEC"/>',
            '<text x="108" y="400" fill="#0F3E68" font-size="30" font-family="Microsoft YaHei" font-weight="700">目标：建立“监测-诊断-处置-复盘”一体化运营体系</text>',
            '<text x="108" y="448" fill="#123B5E" font-size="24" font-family="Microsoft YaHei">12个月内实现关键设备停机损失明显下降，形成可复制的标准作业模型。</text>',
        ]),
        ("05_kpi.svg", "03_content.svg", {"{{PAGE_TITLE}}": "年度KPI与经营价值锚点", "{{CONTENT_AREA_LINE}}": "以安全、效率、稳定三维指标牵引建设优先级与资源投入"}, kpi_cards()),
        ("06_chapter.svg", "02_chapter.svg", {"{{CHAPTER_NUM}}": "02", "{{CHAPTER_TITLE}}": "方案设计与实施路径"}, []),
        ("07_arch.svg", "03_content.svg", {"{{PAGE_TITLE}}": "总体架构：四层协同、闭环驱动", "{{CONTENT_AREA_LINE}}": "感知层、平台层、应用层、运营层协同，保障业务稳定与持续优化"}, [
            '<rect x="94" y="194" width="1092" height="86" rx="14" fill="#EAF4FD" stroke="#A9CCE9"/>',
            '<text x="128" y="247" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">感知层：边缘采集、协议适配、设备侧质量校验</text>',
            '<rect x="94" y="296" width="1092" height="86" rx="14" fill="#EAF4FD" stroke="#A9CCE9"/>',
            '<text x="128" y="349" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">平台层：主数据、事件总线、规则引擎、模型服务</text>',
            '<rect x="94" y="398" width="1092" height="86" rx="14" fill="#EAF4FD" stroke="#A9CCE9"/>',
            '<text x="128" y="451" fill="#0F3E68" font-size="28" font-family="Microsoft YaHei" font-weight="700">应用层：智能诊断、风险预警、工单协同、可视化看板</text>',
            '<rect x="94" y="500" width="1092" height="86" rx="14" fill="#FFECEF" stroke="#F4B6C2"/>',
            '<text x="128" y="553" fill="#7D1E31" font-size="28" font-family="Microsoft YaHei" font-weight="700">运营层：周跟踪、月复盘、季审计、跨区域复制推广</text>',
        ]),
        ("08_trend.svg", "03_content.svg", {"{{PAGE_TITLE}}": "关键指标趋势（双折线）", "{{CONTENT_AREA_LINE}}": "可靠性指数与闭环效率指数连续7个周期改善，验证方案可持续有效"}, trend_chart()),
        ("09_bar.svg", "03_content.svg", {"{{PAGE_TITLE}}": "基线与目标对比（柱状图）", "{{CONTENT_AREA_LINE}}": "核心经营指标均达到预期区间，风险指标显著下降"}, bar_chart()),
        ("10_matrix.svg", "03_content.svg", {"{{PAGE_TITLE}}": "场景价值优先级矩阵", "{{CONTENT_AREA_LINE}}": "按“价值 × 实施复杂度”分区推进，先打样、后复制、再规模化"}, matrix_page()),
        ("11_roadmap.svg", "03_content.svg", {"{{PAGE_TITLE}}": "实施路线与组织保障", "{{CONTENT_AREA_LINE}}": "分季度推进关键交付，确保业务连续性与建设节奏同步"}, roadmap_page() + governance_page()),
        ("12_ending.svg", "04_ending.svg", {"{{THANK_YOU}}": "谢谢"}, []),
    ]

    for out_name, base_name, mapping, nodes in pages:
        base = (LAYOUT_ROOT / base_name).read_text(encoding="utf-8")
        base = fill(base, mapping)
        if nodes:
            base = add_nodes(base, nodes)
        (svg_output / out_name).write_text(base, encoding="utf-8")

    notes = project_path / "notes" / "total.md"
    notes.parent.mkdir(parents=True, exist_ok=True)
    notes.write_text(
        "\n\n".join(
            [
                f"## Page {i}\n讲解重点：围绕业务价值、实施路径与治理机制展开，突出可执行性与可复制性。"
                for i in range(1, 13)
            ]
        ),
        encoding="utf-8",
    )

    run([PYTHON, str(SCRIPTS_ROOT / "svg_quality_checker.py"), str(project_path)])
    run([PYTHON, str(SCRIPTS_ROOT / "total_md_split.py"), str(project_path)])
    run([PYTHON, str(SCRIPTS_ROOT / "finalize_svg.py"), str(project_path)])
    export_out = run([PYTHON, str(SCRIPTS_ROOT / "svg_to_pptx.py"), str(project_path), "-s", "final"])
    exports = re.findall(r"(/app/skills/public/ppt-master/projects/.+?\\.pptx)", export_out)
    print(json.dumps({"project_path": str(project_path), "exports": exports}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
