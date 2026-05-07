#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path


PROJECT = Path("/app/projects/native_fullchain_showcase_ppt169_20260504")
SKILL = Path("/app/skills/public/ppt-master")
TPL = PROJECT / "templates"
SVG_OUT = PROJECT / "svg_output"
NOTES = PROJECT / "notes"
CHARTS = SKILL / "templates" / "charts"


def read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def write(p: Path, s: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(s, encoding="utf-8")


def fill(t: str, m: dict[str, str]) -> str:
    out = t
    for k, v in m.items():
        out = out.replace(k, v)
    return out


def esc(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def svg_inner(raw: str) -> str:
    raw = re.sub(r"<\?xml[^>]*\?>", "", raw).strip()
    raw = re.sub(r"^<svg[^>]*>", "", raw)
    raw = re.sub(r"</svg>\s*$", "", raw)
    return raw.strip()


def wrap_lines(text: str, max_len: int = 18, max_lines: int = 2) -> list[str]:
    chars = list(text.strip())
    lines: list[str] = []
    cur: list[str] = []
    for ch in chars:
        cur.append(ch)
        if len(cur) >= max_len:
            lines.append("".join(cur))
            cur = []
            if len(lines) >= max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append("".join(cur))
    if not lines:
        lines = ["重点说明"]
    return lines[:max_lines]


def build_content_page(base: str, page_no: int, title: str, key_message: str, chart_key: str, bullets: list[str]) -> str:
    chart = svg_inner(read(CHARTS / f"{chart_key}.svg"))
    left_cards: list[str] = []
    y = 124
    icon_names = ["tabler-filled/bolt", "tabler-filled/database", "tabler-filled/clock", "tabler-filled/shield", "tabler-filled/target"]
    for i, b in enumerate(bullets[:4]):
        lines = wrap_lines(b, max_len=18, max_lines=2)
        card_h = 84
        left_cards.append(
            f'<rect x="66" y="{y}" width="470" height="{card_h}" rx="8" fill="#F8FAFC" stroke="#E5E7EB"/>'
            f'<use data-icon="{icon_names[i % len(icon_names)]}" x="82" y="{y+18}" width="22" height="22" fill="#2563EB"/>'
            f'<text x="116" y="{y+36}" fill="#0F172A" font-family="Microsoft YaHei" font-size="20" font-weight="700">{esc(lines[0])}</text>'
            f'{f"<text x=\"116\" y=\"{y+62}\" fill=\"#334155\" font-family=\"Microsoft YaHei\" font-size=\"16\">{esc(lines[1])}</text>" if len(lines) > 1 else ""}'
        )
        y += card_h + 12

    canvas = (
        '<g>'
        '<rect x="54" y="112" width="500" height="490" rx="10" fill="#FFFFFF" stroke="#E5E7EB"/>'
        + "".join(left_cards)
        + '<rect x="570" y="112" width="650" height="490" rx="10" fill="#FFFFFF" stroke="#E5E7EB"/>'
        + f'<svg x="584" y="126" width="622" height="462" viewBox="0 0 1280 720">{chart}</svg>'
        + f'<text x="58" y="640" fill="#6B7280" font-family="Microsoft YaHei" font-size="14">图表示例：{esc(chart_key)}</text>'
        + "</g>"
    )
    return fill(
        base,
        {
            "{{KEY_MESSAGE}}": esc(key_message),
            "{{CONTENT_AREA}}": canvas,
            "{{SOURCE}}": "内部测算与样例数据",
            "{{PAGE_NUM}}": str(page_no),
        },
    )


def build() -> None:
    cover_tpl = read(TPL / "01_cover.svg")
    toc_tpl = read(TPL / "02_toc.svg")
    chapter_tpl = read(TPL / "02_chapter.svg")
    content_tpl = read(TPL / "03_content.svg")
    ending_tpl = read(TPL / "04_ending.svg")

    topic = "企业级 AI 智能运营中台建设蓝图"
    subtitle = "从治理、架构、场景到成效的全链路落地方案"
    author = "汇报单位：企业数智化推进办公室"
    date = datetime.now().strftime("%Y-%m-%d")

    pages: list[str] = []
    pages.append(
        fill(
            cover_tpl,
            {
                "{{TITLE}}": topic,
                "{{SUBTITLE}}": subtitle,
                "{{PROJECT_ID}}": "PROJECT ID: AIOPS-2026-04",
                "{{DATE}}": date,
                "{{AUTHOR}}": author,
            },
        )
    )
    pages.append(
        fill(
            toc_tpl,
            {
                "{{TOC_ITEM_1_TITLE}}": "战略目标与价值锚点",
                "{{TOC_ITEM_1_DESC}}": "业务目标、价值闭环、里程碑",
                "{{TOC_ITEM_2_TITLE}}": "平台架构与能力中台",
                "{{TOC_ITEM_2_DESC}}": "数据、模型、工具链、治理体系",
                "{{TOC_ITEM_3_TITLE}}": "重点场景与实施路径",
                "{{TOC_ITEM_3_DESC}}": "试点场景、推广节奏、组织协同",
                "{{TOC_ITEM_4_TITLE}}": "成效评估与下一步计划",
                "{{TOC_ITEM_4_DESC}}": "效率质量收益、风险与保障",
                "{{TOC_ITEM_5_TITLE}}": "",
                "{{TOC_ITEM_6_TITLE}}": "",
                "{{DATE}}": date,
                "{{PAGE_NUM}}": "2",
            },
        )
    )

    pages.append(
        fill(
            chapter_tpl,
            {
                "{{CHAPTER_NUM}}": "01",
                "{{CHAPTER_TITLE}}": "战略目标与价值锚点",
                "{{CHAPTER_DESC}}": "回答为什么建、建成后业务价值在哪里",
            },
        )
    )
    pages.append(
        build_content_page(
            content_tpl,
            4,
            "业务痛点与机会窗口",
            "结论：不先做统一中台，业务 AI 规模化无法成立",
            "pareto_chart",
            [
                "跨系统数据口径不一，分析耗时高",
                "高频决策缺少统一指标看板",
                "经验沉淀分散，复用率低",
                "跨部门协同链路长，响应慢",
            ],
        )
    )
    pages.append(
        build_content_page(
            content_tpl,
            5,
            "目标体系与价值分解",
            "结论：围绕效率、质量、成本、风险建立四维目标体系",
            "kpi_cards",
            [
                "效率：报告产出周期缩短 40%",
                "质量：关键指标一致率达到 98%",
                "成本：重复分析工作量下降 35%",
                "风险：异常预警提前量提升 2 倍",
            ],
        )
    )

    pages.append(
        fill(
            chapter_tpl,
            {
                "{{CHAPTER_NUM}}": "02",
                "{{CHAPTER_TITLE}}": "平台架构与能力中台",
                "{{CHAPTER_DESC}}": "明确技术架构、能力分层与治理机制",
            },
        )
    )
    pages.append(
        build_content_page(
            content_tpl,
            7,
            "总体架构",
            "结论：数据底座 + 智能体编排 + 场景应用三层解耦",
            "layered_architecture",
            [
                "统一数据服务层：结构化+非结构化",
                "智能体中台：任务分解、工具编排、记忆",
                "场景层：问数、研报、PPT、知识助手",
                "治理层：质量、权限、审计、成本",
            ],
        )
    )
    pages.append(
        build_content_page(
            content_tpl,
            8,
            "关键能力矩阵",
            "结论：以场景优先级驱动能力建设节奏",
            "matrix_2x2",
            [
                "高价值低复杂场景优先上线",
                "中价值场景并行建设组件能力",
                "高复杂高价值场景分阶段推进",
                "低价值场景保持轻量试点",
            ],
        )
    )
    pages.append(
        build_content_page(
            content_tpl,
            9,
            "技术路线与迭代机制",
            "结论：采用季度化路线图 + 双周迭代评审",
            "roadmap_vertical",
            [
                "Q2：完成数据资产统一接入",
                "Q3：完成 6 个关键场景试点",
                "Q4：完成全域运营看板上线",
                "持续：模型评测与成本优化闭环",
            ],
        )
    )

    pages.append(
        fill(
            chapter_tpl,
            {
                "{{CHAPTER_NUM}}": "03",
                "{{CHAPTER_TITLE}}": "重点场景与实施路径",
                "{{CHAPTER_DESC}}": "展示业务场景落地与推广方法",
            },
        )
    )
    pages.append(
        build_content_page(
            content_tpl,
            11,
            "场景 1：经营分析自动化",
            "结论：从取数到结论输出实现端到端自动化",
            "process_flow",
            [
                "自动采集多系统数据并校验",
                "自动生成多粒度经营指标分析",
                "自动产出管理层汇报摘要",
                "全链路可追溯可审计",
            ],
        )
    )
    pages.append(
        build_content_page(
            content_tpl,
            12,
            "场景 2：风险预警与处置",
            "结论：构建预警-研判-处置-复盘闭环",
            "sankey_chart",
            [
                "异常信号聚合并智能打标",
                "多因子研判输出优先级",
                "处置动作自动分发与跟踪",
                "复盘结果反哺规则与模型",
            ],
        )
    )

    pages.append(
        build_content_page(
            content_tpl,
            13,
            "成效评估与下一步",
            "结论：试点已验证价值，下一步进入规模化复制",
            "grouped_bar_chart",
            [
                "试点场景平均效率提升 42%",
                "跨部门协同时延下降 37%",
                "管理层决策准备时间下降 45%",
                "下一步聚焦分公司推广与运营体系",
            ],
        )
    )

    pages.append(
        fill(
            ending_tpl,
            {
                "{{THANK_YOU}}": "THANK YOU",
                "{{ENDING_SUBTITLE}}": "让智能体真正成为生产力",
                "{{CONTACT_NAME}}": "企业数智化推进办公室",
                "{{CONTACT_INFO}}": "contact@enterprise.ai",
                "{{COPYRIGHT}}": "2026 Enterprise AI Program",
            },
        )
    )

    for i, svg in enumerate(pages, start=1):
        write(SVG_OUT / f"page_{i:02d}.svg", svg)

    note_lines = []
    for i in range(1, len(pages) + 1):
        note_lines.append(
            f"## Page {i}\n- 讲述重点：围绕该页核心结论展开，先结论后证据。\n- 建议时长：45-70秒。\n"
        )
    write(NOTES / "total.md", "\n".join(note_lines))

    write(
        PROJECT / "design_spec.md",
        "# Design Spec\n\n- Theme: Enterprise AI Ops Platform\n- Style: Exhibit (dark executive style)\n- Page count: 14\n- Visual strategy: conclusion-first + chart evidence + action bullets\n",
    )
    write(
        PROJECT / "spec_lock.md",
        json.dumps(
            {
                "canvas": {"viewBox": "0 0 1280 720"},
                "font": {"family": "Microsoft YaHei, Arial"},
                "palette": {
                    "primary": "#1E40AF",
                    "accent": "#D4AF37",
                    "text": "#0F172A",
                    "muted": "#6B7280",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
    )

    print(f"[OK] Generated {len(pages)} pages at {SVG_OUT}")


if __name__ == "__main__":
    build()

