#!/usr/bin/env python3
"""Fetch article full text and add paragraph-level marks for HTML rendering."""

from __future__ import annotations

import argparse
from html import unescape
from html.parser import HTMLParser
import json
import re
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - exercised in minimal runtime envs
    BeautifulSoup = None


RULES = [
    ("国家领导原话", ["习近平", "总书记", "重要讲话", "重要指示"]),
    ("国家政策", ["规划", "行动方案", "通知", "意见", "办法", "法律", "制度", "标准"]),
    ("国家部署工作或任务", ["提出", "要求", "部署", "组织开展", "推动", "支持", "加快", "加强", "促进", "构建", "完善"]),
    ("理论学习", ["理论", "学习", "认识", "规律", "方法论"]),
    ("政策导向", ["高质量发展", "海洋新能源", "绿色低碳", "科技自立自强", "新质生产力", "数智化", "人工智能"]),
    ("潜在资源", ["数据集", "数据要素", "算力", "场景", "标准体系", "科技成果", "知识产权", "平台", "海上风电", "储能"]),
    ("潜在机遇", ["未来产业", "未来能源", "人工智能", "氢能", "储能", "示范", "试点", "产业链", "成果转化"]),
    ("潜在风险", ["风险", "挑战", "安全", "保密", "真实性", "监管", "责任", "侵权"]),
    ("优秀做法/经验", ["经验", "示范", "试点", "典型", "案例", "做法"]),
]


class ParagraphTextExtractor(HTMLParser):
    """Small stdlib fallback when BeautifulSoup/lxml is unavailable."""

    block_tags = {"p", "div", "section", "article", "li", "br", "tr"}
    skip_tags = {"script", "style", "nav", "header", "footer", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.skip_tags:
            self._skip_depth += 1
            return
        if tag in self.block_tags:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.skip_tags:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self.block_tags:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = unescape(data)
        if text.strip():
            self._parts.append(text)

    def close(self) -> None:
        super().close()
        self._flush()

    def _flush(self) -> None:
        if not self._parts:
            return
        text = clean_text(" ".join(self._parts))
        self._parts = []
        if len(text) >= 20 and not text.startswith(("责任编辑", "来源：", "分享到")):
            self.paragraphs.append(text)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def fetch_html(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(req, timeout=25) as response:
        data = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="replace")


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_paragraphs(page_html: str) -> list[str]:
    candidates: list[str] = []
    if BeautifulSoup is not None:
        soup = BeautifulSoup(page_html, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        selectors = [
            "div.article p",
            "div.text_c p",
            "div.TRS_Editor p",
            "div.content p",
            "div.pages_content p",
            "article p",
            "p",
        ]
        for selector in selectors:
            found = [clean_text(p.get_text(" ", strip=True)) for p in soup.select(selector)]
            found = [p for p in found if len(p) >= 20 and not p.startswith(("责任编辑", "来源：", "分享到"))]
            if len(found) >= 3:
                candidates = found
                break
        if not candidates:
            text = clean_text(soup.get_text(" ", strip=True))
            candidates = [part for part in re.split(r"(?<=[。！？；;])", text) if len(part) >= 30]
    else:
        parser = ParagraphTextExtractor()
        parser.feed(page_html)
        parser.close()
        candidates = parser.paragraphs
    if not candidates:
        text = clean_text(re.sub(r"<[^>]+>", " ", page_html))
        candidates = [part for part in re.split(r"(?<=[。！？；;])", text) if len(part) >= 30]
    deduped: list[str] = []
    seen: set[str] = set()
    for paragraph in candidates:
        if paragraph not in seen:
            seen.add(paragraph)
            deduped.append(paragraph)
    return deduped[:80]


def sentence_containing(text: str, term: str) -> str:
    pattern = r"[^。！？；;]*" + re.escape(term) + r"[^。！？；;]*[。！？；;]?"
    match = re.search(pattern, text)
    if match:
        return match.group(0).strip()
    return term


def mark_paragraph(text: str) -> list[dict[str, str]]:
    marks: list[dict[str, str]] = []
    used: set[tuple[str, str]] = set()
    for mark_type, terms in RULES:
        for term in terms:
            if term not in text:
                continue
            mark_text = sentence_containing(text, term)
            key = (mark_type, mark_text)
            if key in used or len(mark_text) < 2:
                continue
            used.add(key)
            marks.append({"type": mark_type, "text": mark_text})
            if len(marks) >= 6:
                return marks
    return marks


def enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    url = record.get("url")
    paragraphs: list[str] = []
    fetch_error = None
    if url:
        try:
            paragraphs = extract_paragraphs(fetch_html(url))
        except Exception as exc:  # noqa: BLE001
            fetch_error = str(exc)
    if not paragraphs:
        for passage in record.get("evidence_passages", []):
            context = passage.get("context") or passage.get("text")
            if context:
                paragraphs.append(str(context))

    full_text: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        marks = mark_paragraph(paragraph)
        full_text.append({"context": paragraph, "marks": marks})

    record["full_text_paragraphs"] = full_text
    record["full_text_status"] = "fetched" if paragraphs and not fetch_error else "partial"
    if fetch_error:
        record["full_text_error"] = fetch_error
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich tech-intel knowledge_base.jsonl with full-text paragraphs.")
    parser.add_argument("--day-dir", required=True, help="Daily output directory containing knowledge_base.jsonl.")
    args = parser.parse_args()

    path = Path(args.day_dir) / "knowledge_base.jsonl"
    rows = [enrich_record(row) for row in read_jsonl(path)]
    write_jsonl(path, rows)
    print(f"enriched {len(rows)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
