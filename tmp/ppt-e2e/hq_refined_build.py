from __future__ import annotations

from pathlib import Path


PROJECT = Path("/app/projects/hq_refined_compare_ppt169_20260504")


def copy_pages() -> None:
    tpl_layout = Path("/app/skills/public/ppt-master/templates/layouts/exhibit")
    tpl_chart = Path("/app/skills/public/ppt-master/templates/charts")
    out = PROJECT / "svg_output"
    pages = {
        "page_01.svg": tpl_layout / "01_cover.svg",
        "page_02.svg": tpl_layout / "02_toc.svg",
        "page_03.svg": tpl_chart / "kpi_cards.svg",
        "page_04.svg": tpl_chart / "matrix_2x2.svg",
        "page_05.svg": tpl_chart / "process_flow.svg",
        "page_06.svg": tpl_chart / "layered_architecture.svg",
        "page_07.svg": tpl_chart / "roadmap_vertical.svg",
        "page_08.svg": tpl_chart / "sankey_chart.svg",
        "page_09.svg": tpl_layout / "03_content.svg",
        "page_10.svg": tpl_layout / "04_ending.svg",
    }
    for page, src in pages.items():
        (out / page).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def replace_text(path: Path, mapping: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    for old, new in mapping.items():
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def apply_content() -> None:
    out = PROJECT / "svg_output"

    replace_text(
        out / "page_01.svg",
        {
            "{{TITLE}}": "海上油气智能运营中台",
            "{{SUBTITLE}}": "高质量成片对照版（策略+图表+精修）",
            "{{PROJECT_ID}}": "PROJECT ID: HQ-REFINED-2026-05",
            "{{DATE}}": "2026-05-04",
            "{{AUTHOR}}": "PPT Master Native Pipeline",
        },
    )

    replace_text(
        out / "page_02.svg",
        {
            "{{TOC_ITEM_1_TITLE}}": "经营全景与目标",
            "{{TOC_ITEM_1_DESC}}": "核心 KPI 与年度目标差距",
            "{{TOC_ITEM_2_TITLE}}": "优先级矩阵",
            "{{TOC_ITEM_2_DESC}}": "按价值/复杂度重排建设事项",
            "{{TOC_ITEM_3_TITLE}}": "流程与架构",
            "{{TOC_ITEM_3_DESC}}": "业务闭环和技术分层落地",
            "{{TOC_ITEM_4_TITLE}}": "实施路线图",
            "{{TOC_ITEM_4_DESC}}": "三阶段推进节奏与里程碑",
            "{{TOC_ITEM_5_TITLE}}": "价值闭环",
            "{{TOC_ITEM_5_DESC}}": "投入-产出流向与收益机制",
            "{{TOC_ITEM_6_TITLE}}": "风险与治理建议",
            "{{TOC_ITEM_6_DESC}}": "组织、数据、技术三类风险应对",
            "{{DATE}}": "2026-05-04",
            "{{PAGE_NUM}}": "2",
        },
    )

    replace_text(
        out / "page_03.svg",
        {
            "Key Performance Indicators": "经营与运营关键指标",
            "FY 2025 · QUARTERLY BUSINESS REVIEW": "2026 年经营看板 · 月度滚动复盘",
            "Total Revenue": "综合产值",
            "Gross Billings": "含生产与服务收入",
            "$245.5": "¥318.6",
            "Active Users": "设备在线率",
            "Monthly Active Users": "核心装置有效在线",
            "1.28": "98.4",
            "Avg Order Value": "单井运维成本",
            "Per User Revenue": "含巡检、维保与停机损失",
            "$43.2": "¥37.1",
            "Conversion Rate": "异常闭环率",
            "Paid Conversion": "告警转工单并完成",
            "8.7%": "86.9%",
            "Source: Internal Financial Dashboard · As of Q4 2025": "Source: 生产经营系统、设备平台、工单系统（2026Q1）",
        },
    )

    replace_text(
        out / "page_04.svg",
        {
            "Action Priority Matrix": "建设事项优先级矩阵",
            "EFFORT VS IMPACT · FEATURE PRIORITIZATION": "价值影响 VS 实施复杂度 · 建设排序",
            "Business Impact vs Implementation Effort · Bubble size represents resource cost": "纵轴价值影响，横轴实施复杂度，气泡大小代表资源消耗",
            "Quick Wins": "高价值快赢",
            "Major Projects": "战略型项目",
            "Fill Ins": "机会型补充",
            "Thankless Tasks": "低收益事项",
            "Action Plan": "执行策略",
            "Quick Wins: DO": "快赢项：立即推进",
            "Projects: PLAN": "战略项：分期落地",
            "Fill Ins: DELEGATE": "补充项：轻量试点",
            "Thankless: DROP": "低收益：暂缓投入",
            "Source: Market Research Department": "Source: 业务访谈、价值评估与架构评审",
        },
    )

    replace_text(
        out / "page_05.svg",
        {
            "Business Process Flow": "智能运营闭环流程",
            "STANDARD OPERATING PROCEDURE · PIPELINE FLOWCHART": "SOP 标准流程 · 从告警到收益闭环",
            "Requirements": "需求与事件汇聚",
            "Solution Design": "处置方案设计",
            "Development": "策略编排执行",
            "Testing": "效果验证与复盘",
            "Complete": "闭环与沉淀",
            "Re-evaluate": "策略复核",
            "Time Metrics": "时效指标",
            "Total Cycle:": "平均闭环时长：",
            "13-24 Days": "6-10 小时",
            "Key Steps:": "关键节点：",
            "Decisions:": "决策闸口：",
            "Risk Level:": "当前风险：",
            "Low": "中低",
        },
    )

    replace_text(
        out / "page_06.svg",
        {
            "Layered Architecture": "智能运营中台分层架构",
            "PRESENTATION · SERVICE · DATA": "业务应用层 · 智能服务层 · 数据与治理层",
            "Source: Internal Technology Architecture Team": "Source: 架构委员会与平台建设组",
        },
    )

    replace_text(
        out / "page_07.svg",
        {
            "Strategic Roadmap": "三阶段实施路线图",
            "VERTICAL TIMELINE · KEY MILESTONES": "纵向里程碑 · 每阶段明确产出与验收",
            "Source: PMO Office": "Source: PMO 与业务条线联合计划",
        },
    )

    replace_text(
        out / "page_08.svg",
        {
            "Sankey Diagram": "投入-产出价值流向",
            "FLOW ANALYSIS · RESOURCE ALLOCATION": "资源投入、能力产出与价值回收",
            "Source: Data Analysis Team": "Source: 财务、运维与经营联合测算",
        },
    )

    replace_text(
        out / "page_09.svg",
        {
            "{{KEY_MESSAGE}}": "结论：优先做“高价值快赢 + 关键底座”，12 周形成可量化收益",
            "{{CONTENT_AREA}}": "建议采用“2周评估、4周试点、6周推广”节奏推进。每页文本密度控制在 3-5 行关键句，避免长段堆砌；图表页统一结论先行并给出可执行动作。",
            "{{SOURCE}}": "综合来源：业务访谈、生产系统、工单平台、财务系统",
            "{{PAGE_NUM}}": "9",
        },
    )

    replace_text(
        out / "page_10.svg",
        {
            "{{THANK_YOU}}": "THANK YOU",
            "{{ENDING_SUBTITLE}}": "高质量成片对照版（精修）",
            "{{CONTACT_NAME}}": "PPT Master Native QA",
            "{{CONTACT_INFO}}": "ops@example.com",
            "{{COPYRIGHT}}": "2026 HQ Refined Deck",
        },
    )


def write_specs() -> None:
    (PROJECT / "design_spec.md").write_text(
        """# Design Specification

## Objective
- Build a consultant-grade executive deck with conclusion-first flow and chart semantics.

## Style
- Consultant narrative, data-first evidence, high readability, low text clutter.

## Text Density Rule
- Content pages: max 5 lines in primary text blocks.
- No paragraph > 42 Chinese characters without manual wrap.

## Chart Semantics Rule
- Every chart page must include explicit business implication in title/subtitle.
- Values and labels must map to business entities, not placeholder names.
""",
        encoding="utf-8",
    )

    (PROJECT / "spec_lock.md").write_text(
        """## canvas
- viewBox: 0 0 1280 720
- format: PPT 16:9

## colors
- bg: #F8FAFC
- primary: #0F172A
- accent: #1D4ED8
- secondary_accent: #3B82F6
- text: #0F172A
- text_secondary: #64748B
- border: #E2E8F0
- positive: #10B981
- warning: #F59E0B
- negative: #F43F5E
- muted: #94A3B8
- white: #FFFFFF
- emerald_bg: #ECFDF5
- amber_bg: #FFFBEB
- rose_bg: #FFF1F2
- blue_bg: #EFF6FF

## typography
- font_family: "Microsoft YaHei", Arial, sans-serif
- title_family: "Microsoft YaHei", Arial, sans-serif
- body_family: "Microsoft YaHei", Arial, sans-serif
- emphasis_family: "Microsoft YaHei", Arial, sans-serif
- code_family: Consolas, "Courier New", monospace
- body: 22
- title: 34
- subtitle: 24
- annotation: 14

## icons
- library: tabler-outline
- stroke_width: 2
- inventory: chart-bar, target, activity, users

## page_rhythm
- P01: anchor
- P02: anchor
- P03: dense
- P04: dense
- P05: dense
- P06: dense
- P07: dense
- P08: dense
- P09: breathing
- P10: anchor

## forbidden
- rgba()
- <style>, class, <foreignObject>, textPath, @font-face, <animate*>, <script>, <iframe>, <symbol>+<use>
""",
        encoding="utf-8",
    )

    notes = []
    for i in range(1, 11):
        notes.append(f"# page_{i:02d}.svg\n- refined deck notes page {i}")
    (PROJECT / "notes" / "total.md").write_text("\n---\n".join(notes), encoding="utf-8")


def main() -> None:
    copy_pages()
    apply_content()
    write_specs()
    print("hq_refined_ready")


if __name__ == "__main__":
    main()
