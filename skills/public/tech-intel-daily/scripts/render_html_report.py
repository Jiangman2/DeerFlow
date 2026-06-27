#!/usr/bin/env python3
"""Render a single daily HTML report with expandable full-text annotation cards."""

from __future__ import annotations

import argparse
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


TYPE_CLASS = {
    "国家领导原话": "leader-quote",
    "国家政策": "national-policy",
    "政策解读": "national-policy",
    "国家部署工作": "national-task",
    "重点任务": "national-task",
    "理论学习": "theory-study",
    "政策导向": "policy-direction",
    "潜在资源": "potential-resource",
    "潜在机遇": "potential-opportunity",
    "潜在风险": "potential-risk",
    "优秀做法": "good-practice",
    "经验": "good-practice",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8-sig"))


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def slugify(value: str, index: int) -> str:
    slug = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", value).strip("-")
    return f"{index:02d}-{slug[:40] or 'article'}.html"


def class_for_type(type_name: str) -> str:
    for key, class_name in TYPE_CLASS.items():
        if key in type_name:
            return class_name
    return "normal-highlight"


def render_badges(values: list[str] | None) -> str:
    if not values:
        return '<span class="muted">无</span>'
    return "".join(f'<span class="tag">{esc(value)}</span>' for value in values)


def normalize_point_type(type_name: str) -> str:
    if "国家部署" in type_name or "重点任务" in type_name:
        return "重点任务"
    if "国家政策" in type_name or "政策解读" in type_name:
        return "国家政策"
    if "政策导向" in type_name:
        return "政策导向"
    if "潜在资源" in type_name:
        return "潜在资源"
    if "潜在机遇" in type_name:
        return "潜在机遇"
    if "潜在风险" in type_name:
        return "潜在风险"
    if "优秀做法" in type_name or "经验" in type_name:
        return "优秀做法/经验"
    if "国家领导" in type_name:
        return "国家领导原话"
    return type_name or "重点内容"


def collect_key_points(record: dict[str, Any], limit: int = 14) -> list[dict[str, str]]:
    points: list[dict[str, str]] = []
    seen: set[str] = set()
    paragraphs = record.get("full_text_paragraphs") or record.get("evidence_passages") or []
    for paragraph in paragraphs:
        for mark in paragraph.get("marks") or []:
            text = str(mark.get("text", "")).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            points.append({"type": normalize_point_type(str(mark.get("type", ""))), "text": text})
            if len(points) >= limit:
                return points
    return points


def render_key_points(record: dict[str, Any]) -> str:
    points = collect_key_points(record)
    if not points:
        return '<p class="muted">未提取到重点内容摘要。</p>'
    items = [f"<li><strong>{esc(point['type'])}：</strong>{esc(point['text'])}</li>" for point in points]
    return '<ul class="key-points">' + "".join(items) + "</ul>"


def mark_context(context: str, marks: list[dict[str, Any]]) -> str:
    spans: list[tuple[int, int, str, str]] = []
    for mark in marks:
        text = str(mark.get("text", "")).strip()
        if not text:
            continue
        start = context.find(text)
        if start < 0:
            compact_context = re.sub(r"\s+", "", context)
            compact_text = re.sub(r"\s+", "", text)
            compact_start = compact_context.find(compact_text)
            if compact_start < 0:
                continue
            prefix = compact_context[:compact_start]
            start = 0
            non_space_seen = 0
            for pos, char in enumerate(context):
                if not char.isspace():
                    if non_space_seen == len(prefix):
                        start = pos
                        break
                    non_space_seen += 1
            end = start
            non_space_seen = 0
            for pos in range(start, len(context)):
                if not context[pos].isspace():
                    non_space_seen += 1
                end = pos + 1
                if non_space_seen >= len(compact_text):
                    break
        else:
            end = start + len(text)
        if any(not (end <= old_start or start >= old_end) for old_start, old_end, _, _ in spans):
            continue
        spans.append((start, end, class_for_type(str(mark.get("type", ""))), str(mark.get("type", ""))))
    if not spans:
        return esc(context)
    spans.sort(key=lambda item: item[0])
    parts: list[str] = []
    cursor = 0
    for start, end, class_name, mark_type in spans:
        parts.append(esc(context[cursor:start]))
        parts.append(f'<span class="{class_name}" title="{esc(mark_type)}">{esc(context[start:end])}</span>')
        cursor = end
    parts.append(esc(context[cursor:]))
    return "".join(parts)


def css() -> str:
    return """
    :root {
      color-scheme: light;
      --ink: #1f2937;
      --muted: #64748b;
      --line: #d9e2ec;
      --panel: #ffffff;
      --bg: #f5f7fb;
      --accent: #0f766e;
      --yellow-strong: #ffe066;
      --yellow-soft: #fff3a3;
      --blue: #0969da;
      --green: #1a7f37;
      --green-bg: #dcfce7;
      --red: #cf222e;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif;
      line-height: 1.72;
    }
    header {
      padding: 32px 40px 24px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }
    main { max-width: 1120px; margin: 0 auto; padding: 28px 24px 48px; }
    h1 { margin: 0 0 8px; font-size: 30px; }
    h2 { margin: 0 0 8px; font-size: 22px; }
    h3 { margin: 22px 0 10px; font-size: 17px; }
    a { color: var(--accent); }
    table { width: 100%; border-collapse: collapse; background: #fff; }
    th, td { border: 1px solid var(--line); padding: 10px; text-align: left; vertical-align: top; }
    th { background: #eef6f5; }
    .muted { color: var(--muted); }
    .panel, .summary, .legend, .updates, .notes, .article-card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 20px;
      margin-bottom: 18px;
    }
    .legend-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; }
    .legend-item { border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; background: #fff; }
    .article-card { position: relative; }
    .article-card h2 a {
      color: inherit;
      text-decoration: none;
    }
    .article-card h2 a:hover {
      text-decoration: underline;
      text-underline-offset: 4px;
    }
    .article-index {
      position: absolute;
      top: 18px;
      right: 18px;
      width: 34px;
      height: 34px;
      border-radius: 50%;
      background: #e6fffb;
      color: #0f766e;
      display: grid;
      place-items: center;
      font-weight: 700;
    }
    .meta { color: var(--muted); margin-bottom: 12px; }
    .field { margin: 10px 0; }
    .tag {
      display: inline-block;
      border: 1px solid #c7d2fe;
      background: #eef2ff;
      border-radius: 999px;
      padding: 2px 9px;
      margin: 2px 4px 2px 0;
      font-size: 13px;
    }
    .analysis { margin-top: 14px; padding: 14px; background: #f8fafc; border-left: 4px solid var(--accent); }
    .key-points { padding-left: 22px; }
    .key-points li { margin: 0 0 8px; }
    .actions { margin-top: 14px; }
    .article-actions { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 14px; }
    .button {
      display: inline-block;
      padding: 8px 12px;
      border-radius: 6px;
      border: 1px solid #99d5cc;
      background: #e6fffb;
      color: #0f766e;
      text-decoration: none;
      font-weight: 650;
    }
    .inline-detail {
      margin-top: 16px;
      border-top: 1px solid var(--line);
      padding-top: 14px;
    }
    .inline-detail summary {
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border: 1px solid #99d5cc;
      background: #e6fffb;
      color: #0f766e;
      border-radius: 6px;
      padding: 8px 12px;
      font-weight: 700;
      list-style: none;
    }
    .inline-detail summary::-webkit-details-marker { display: none; }
    .inline-detail summary::before {
      content: "展开";
      font-size: 12px;
      color: #0f766e;
      border: 1px solid #99d5cc;
      border-radius: 999px;
      padding: 0 6px;
      background: #fff;
    }
    .inline-detail[open] summary::before { content: "收起"; }
    .inline-detail-content {
      margin-top: 14px;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfdff;
    }
    .inline-detail-content h3:first-child { margin-top: 0; }
    .article-body {
      max-width: 860px;
      margin: 0 auto;
      padding: 8px 0 4px;
      font-size: 17px;
      line-height: 1.95;
    }
    .article-body p { margin: 0 0 18px; text-align: justify; text-indent: 2em; }
    .leader-quote { font-weight: 800; }
    .national-policy { text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 3px; }
    .national-task { background: var(--yellow-strong); padding: 1px 3px; }
    .policy-direction { background: var(--yellow-soft); padding: 1px 3px; }
    .potential-resource { color: var(--blue); font-weight: 650; }
    .potential-opportunity { color: var(--green); font-weight: 650; }
    .potential-risk { color: var(--red); font-weight: 700; }
    .good-practice { background: var(--green-bg); color: var(--green); padding: 1px 3px; }
    .theory-study { border-bottom: 2px dotted #7c3aed; }
    """


def legend() -> str:
    return """
    <section class="legend">
      <h2>标注图例</h2>
      <div class="legend-grid">
        <div class="legend-item"><span class="leader-quote">国家领导原话：加粗</span></div>
        <div class="legend-item"><span class="national-policy">国家政策/政策解读：下划线</span></div>
        <div class="legend-item"><span class="national-task">国家部署工作或任务：高亮</span></div>
        <div class="legend-item"><span class="policy-direction">政策导向：浅黄</span></div>
        <div class="legend-item"><span class="potential-resource">潜在资源：蓝色</span></div>
        <div class="legend-item"><span class="potential-opportunity">潜在机遇：绿色</span></div>
        <div class="legend-item"><span class="potential-risk">潜在风险：红色</span></div>
        <div class="legend-item"><span class="good-practice">优秀做法/经验：绿色底纹</span></div>
      </div>
    </section>
    """


def article_page_name(record: dict[str, Any], index: int) -> str:
    return slugify(str(record.get("title", "article")), index)


def render_article_card(record: dict[str, Any], index: int) -> str:
    title = esc(record.get("title", "未命名文章"))
    url = str(record.get("url") or "").strip()
    title_html = f'<a href="{esc(url)}" target="_blank" rel="noreferrer">{title}</a>' if url else title
    source_link_html = (
        f'<div class="field"><strong>原文链接：</strong><a href="{esc(url)}" target="_blank" rel="noreferrer">{esc(url)}</a></div>'
        if url
        else ""
    )
    implications = record.get("management_implications") or []
    implication_html = "".join(f"<li>{esc(item)}</li>" for item in implications) or "<li>无</li>"
    para_count = len(record.get("full_text_paragraphs") or [])
    marked_count = sum(len(p.get("marks") or []) for p in record.get("full_text_paragraphs") or [])
    relevance_reasons = record.get("relevance_reasons") or []
    relevance_html = "；".join(esc(reason) for reason in relevance_reasons) or "未记录"
    return f"""
    <article class="article-card" id="article-{index}">
      <div class="article-index">{index}</div>
      <h2>{title_html}</h2>
      <div class="meta">文章来源、日期及版次：{esc(record.get("source"))} · {esc(record.get("date"))} · {esc(record.get("edition_or_section"))}</div>
      {source_link_html}
      <div class="field"><strong>关键词及重点内容：</strong>{render_badges(record.get("keywords"))}</div>
      <div class="field"><strong>业务标签：</strong>{render_badges(record.get("business_tags"))}</div>
      <div class="field"><strong>重点内容类型：</strong>{render_badges(record.get("focus_types"))}</div>
      <div class="field"><strong>入选依据：</strong>相关性评分 {esc(record.get("relevance_score", "未记录"))}；{relevance_html}</div>
      <section class="analysis">
        <strong>研判：</strong>{esc(record.get("summary"))}
        <ul>{implication_html}</ul>
      </section>
      <div class="meta">全文段落：{para_count}；标注点：{marked_count}</div>
      <details class="inline-detail" id="article-detail-{index}">
        <summary>在日报中展开全文标注与分析</summary>
        <div class="inline-detail-content">
          <h3>分析结果</h3>
          <p>{esc(record.get("summary"))}</p>
          <ul>{implication_html}</ul>
          <h3>重点内容摘要</h3>
          {render_key_points(record)}
          <h3>全文标注</h3>
          <p class="muted">以下为文章全文阅读版式，标注直接嵌入原文上下文中；未命中的文字保持普通正文。</p>
          {render_fulltext(record)}
        </div>
      </details>
    </article>
    """


def render_updates(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<p class="muted">本次未记录动态库更新。</p>'
    body = []
    for row in rows:
        body.append(
            f"<tr><td>{esc(row.get('status'))}</td><td>{esc(row.get('term'))}</td>"
            f"<td>{esc(row.get('category'))}</td><td><a href=\"{esc(row.get('evidence_url'))}\">{esc(row.get('evidence_title'))}</a></td>"
            f"<td>{esc(row.get('reason'))}</td></tr>"
        )
    return f"<table><thead><tr><th>状态</th><th>词条</th><th>类别</th><th>证据</th><th>更新理由</th></tr></thead><tbody>{''.join(body)}</tbody></table>"


def flatten_library_terms(rows: Any, *, limit: int = 160) -> list[str]:
    terms: list[str] = []
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, str):
                terms.append(row)
            elif isinstance(row, dict):
                if row.get("term"):
                    terms.append(str(row["term"]))
                if row.get("category"):
                    terms.append(str(row["category"]))
                if row.get("direction") and row.get("direction") != "-":
                    terms.append(str(row["direction"]))
                for tag in row.get("tags") or []:
                    terms.append(str(tag))
    return list(dict.fromkeys(term for term in terms if term))[:limit]


def render_dynamic_libraries(libraries: dict[str, Any]) -> str:
    keyword_terms = flatten_library_terms(libraries.get("keywords"), limit=180)
    business_terms = flatten_library_terms(libraries.get("business_tags"), limit=180)
    focus_terms = flatten_library_terms(libraries.get("focus_standards"), limit=80)
    return f"""
    <div class="field"><strong>关键词库：</strong>{render_badges(keyword_terms)}</div>
    <div class="field"><strong>业务标签库：</strong>{render_badges(business_terms)}</div>
    <div class="field"><strong>重点内容标准库：</strong>{render_badges(focus_terms)}</div>
    <p class="muted">动态库用于候选文章筛选、业务标签匹配和全文重点内容标注；下方“动态库更新”展示本次运行新增或强化的库项。</p>
    """


def read_diagnostics(path: Path | None) -> dict[str, Any] | None:
    if not path or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_execution_todos(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, list) else []


def render_execution_todos(todos: list[dict[str, Any]]) -> str:
    if not todos:
        return '<p class="muted">未生成执行清单。</p>'
    labels = {
        "pending": "待执行",
        "in_progress": "执行中",
        "completed": "已完成",
        "failed": "失败",
    }
    rows = []
    for item in todos:
        rows.append(
            "<tr>"
            f"<td>{esc(labels.get(str(item.get('status')), item.get('status')))}</td>"
            f"<td>{esc(item.get('content'))}</td>"
            f"<td>{esc(item.get('note'))}</td>"
            "</tr>"
        )
    return "<table><thead><tr><th>状态</th><th>步骤</th><th>说明</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"


def render_diagnostics(diagnostics: dict[str, Any] | None) -> str:
    if not diagnostics:
        return '<p class="muted">未生成采集诊断文件。</p>'
    summary = diagnostics.get("summary") or {}
    rejected = summary.get("rejected_by_reason") or {}
    rejected_rows = "".join(f"<li>{esc(reason)}：{esc(count)}</li>" for reason, count in rejected.items()) or "<li>无</li>"
    source_rows = []
    for item in diagnostics.get("sources") or []:
        source_rows.append(
            "<tr>"
            f"<td>{esc(item.get('source'))}</td>"
            f"<td>{esc(item.get('status'))}</td>"
            f"<td>{esc(item.get('candidate_links'))}</td>"
            f"<td>{esc(item.get('fetched_candidates'))}</td>"
            f"<td>{esc(item.get('selected'))}</td>"
            f"<td>{esc('; '.join(item.get('notes') or []))}</td>"
            "</tr>"
        )
    source_table = (
        "<table><thead><tr><th>信源</th><th>状态</th><th>候选链接</th><th>抓取候选</th><th>入选</th><th>说明</th></tr></thead>"
        f"<tbody>{''.join(source_rows)}</tbody></table>"
        if source_rows
        else '<p class="muted">无信源检查记录。</p>'
    )
    return f"""
    <div class="field"><strong>检查信源：</strong>{esc(summary.get('sources_checked', 0))}</div>
    <div class="field"><strong>发现候选链接：</strong>{esc(summary.get('candidate_links_found', 0))}</div>
    <div class="field"><strong>抓取候选文章页：</strong>{esc(summary.get('candidate_pages_fetched', 0))}</div>
    <div class="field"><strong>入选/过滤/访问错误：</strong>{esc(summary.get('selected_count', 0))} / {esc(summary.get('rejected_count', 0))} / {esc(summary.get('errors_count', 0))}</div>
    <h3>过滤原因</h3>
    <ul>{rejected_rows}</ul>
    <h3>信源明细</h3>
    {source_table}
    """


def html_shell(title: str, body: str) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>{css()}</style>
</head>
<body>
  <header>
    <h1>{esc(title)}</h1>
    <div class="muted">HTML 全文标注版；生成时间：{esc(generated_at)}</div>
  </header>
  <main>{body}</main>
</body>
</html>
"""


def render_index(
    date: str,
    records: list[dict[str, Any]],
    updates: list[dict[str, Any]],
    libraries: dict[str, Any],
    notes: str,
    diagnostics: dict[str, Any] | None = None,
    execution_todos: list[dict[str, Any]] | None = None,
) -> str:
    cards = "\n".join(render_article_card(record, i) for i, record in enumerate(records, 1))
    empty_note = "暂无入选文章。请检查目标日期、信源可访问性，或补充明确文章 URL 后重新运行。"
    body = f"""
    <section class="summary">
      <h2>今日概览</h2>
      <p>本页为科技管理情报日报首页。共入选 {len(records)} 篇文章；每张文章卡片可向下展开查看“全文标注与分析”。</p>
    </section>
    {legend()}
    <section><h2>入选文章（倒序）</h2>{cards or f'<p class="muted">{empty_note}</p>'}</section>
    <section class="updates"><h2>动态库当前内容</h2>{render_dynamic_libraries(libraries)}</section>
    <section class="updates"><h2>动态库更新</h2>{render_updates(updates)}</section>
    <section class="notes"><h2>采集诊断</h2>{render_diagnostics(diagnostics)}</section>
    <section class="notes"><h2>执行清单</h2>{render_execution_todos(execution_todos or [])}</section>
    <section class="notes"><h2>采集说明</h2><p>{esc(notes) if notes else '未记录额外采集说明。'}</p></section>
    """
    return html_shell(f"科技管理情报跟踪日报（{date}）", body)


def render_fulltext(record: dict[str, Any]) -> str:
    paragraphs = record.get("full_text_paragraphs") or record.get("evidence_passages") or []
    blocks = []
    for paragraph in paragraphs:
        context = str(paragraph.get("context") or paragraph.get("text") or "")
        marks = paragraph.get("marks") or []
        if context.strip():
            blocks.append(f"<p>{mark_context(context, marks)}</p>")
    if not blocks:
        return '<p class="muted">未获取到全文，仅保留摘要和元数据。</p>'
    return '<div class="article-body">\n' + "\n".join(blocks) + "\n</div>"


def main() -> int:
    parser = argparse.ArgumentParser(description="Render tech-intel daily single HTML report.")
    parser.add_argument("--date", required=True, help="Report date.")
    parser.add_argument("--day-dir", required=True, help="Daily output directory.")
    parser.add_argument("--output", default="daily_report.html", help="Output index HTML filename or path.")
    parser.add_argument("--notes", default="", help="Collection/access notes.")
    parser.add_argument("--diagnostics", default="", help="Optional collection_diagnostics.json path.")
    args = parser.parse_args()

    day_dir = Path(args.day_dir)
    records = read_jsonl(day_dir / "knowledge_base.jsonl")
    updates = read_jsonl(day_dir / "taxonomy_updates.jsonl")
    libraries = {
        "keywords": read_json(day_dir / "keyword_library.json", []),
        "business_tags": read_json(day_dir / "business_tag_library.json", []),
        "focus_standards": read_json(day_dir / "focus_standard_library.json", []),
    }
    diagnostics_path = Path(args.diagnostics) if args.diagnostics else day_dir / "collection_diagnostics.json"
    diagnostics = read_diagnostics(diagnostics_path)
    execution_todos = read_execution_todos(day_dir / "execution_todo.json")
    output = Path(args.output)
    if not output.is_absolute():
        output = day_dir / output
    output.write_text(render_index(args.date, records, updates, libraries, args.notes, diagnostics, execution_todos), encoding="utf-8")
    print(output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
