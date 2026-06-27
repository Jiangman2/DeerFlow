#!/usr/bin/env python3
"""Run the complete tech-intel-daily pipeline.

The goal of this wrapper is to make the DeerFlow skill deterministic: a single
command performs collection, filtering, knowledge-base writing, enrichment,
HTML rendering, and diagnostics generation.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date as date_cls
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
SOURCE_CATALOG = SKILL_DIR / "references" / "source-catalog.md"

TECH_KEYWORDS = [
    "科技管理",
    "科技政策",
    "科技创新",
    "科技成果",
    "成果转化",
    "科研管理",
    "项目管理",
    "创新平台",
    "重点实验室",
    "知识产权",
    "标准",
    "数据资产",
    "数据要素",
    "人工智能",
    "大模型",
    "智能体",
    "数智化",
    "数字化转型",
    "能源安全",
    "油气",
    "海洋",
    "海上",
    "勘探",
    "开发",
    "增储上产",
    "绿色低碳",
    "新质生产力",
]

BROAD_KEYWORDS = {"人工智能", "大模型", "数据要素", "数据资产", "数字化转型", "绿色低碳", "新质生产力"}

WEAK_KEYWORDS = {"标准", "开发", "海洋", "海上", "平台", "安全", "生产", "项目", "科技"}

STRONG_KEYWORDS = {
    "科技管理",
    "科技政策",
    "科技创新",
    "科技成果",
    "成果转化",
    "科研管理",
    "创新平台",
    "重点实验室",
    "知识产权",
    "数据资产",
    "数据要素",
    "人工智能",
    "大模型",
    "智能体",
    "数智化",
    "数字化转型",
    "能源安全",
    "油气",
    "勘探",
    "增储上产",
    "绿色低碳",
    "新质生产力",
}

DOMAIN_QUALIFIERS = [
    "能源",
    "油气",
    "海洋",
    "海上",
    "勘探",
    "开发",
    "企业",
    "科研",
    "科技",
    "标准",
    "知识产权",
    "项目",
    "安全",
    "生产",
]

FOCUS_RULES = [
    ("国家政策", ["通知", "意见", "办法", "规划", "方案", "政策", "标准", "规范"]),
    ("国家部署工作或任务", ["部署", "推动", "组织开展", "加快", "加强", "完善", "构建", "促进"]),
    ("政策导向", ["高质量发展", "科技自立自强", "新质生产力", "绿色低碳", "数智化", "人工智能"]),
    ("潜在资源", ["数据资产", "数据要素", "平台", "标准体系", "科技成果", "知识产权", "算力"]),
    ("潜在机遇", ["试点", "示范", "未来产业", "成果转化", "产业链", "应用场景"]),
    ("潜在风险", ["风险", "挑战", "安全", "监管", "合规", "保密", "责任"]),
    ("优秀做法/经验", ["经验", "做法", "典型", "案例", "示范", "试点"]),
]

PIPELINE_TODOS = [
    ("initialize", "初始化日报目录和基础库文件"),
    ("load_requirements", "加载需求文档、信源目录、筛选规则和分类体系"),
    ("collect_candidates", "采集候选文章并写入候选池"),
    ("filter_knowledge_base", "按日期、信源类型和强相关评分筛选入选文章"),
    ("enrich_full_text", "抓取/补全文本并生成段落级全文标注"),
    ("render_daily_report", "渲染唯一交付物 daily_report.html"),
    ("finalize", "嵌入诊断和执行清单并准备展示"),
]


@dataclass
class SourceCheck:
    source: str
    url: str
    status: str = "pending"
    candidate_links: int = 0
    fetched_candidates: int = 0
    selected: int = 0
    rejected_by_reason: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def reject(self, reason: str) -> None:
        self.rejected_by_reason[reason] = self.rejected_by_reason.get(reason, 0) + 1


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []
        self.title = ""
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text_parts = []
        elif tag.lower() == "title":
            self._in_title = True
            self._title_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            text = clean_text("".join(self._text_parts))
            if text and len(text) >= 2:
                self.links.append({"title": text, "url": urljoin(self.base_url, self._href)})
            self._href = None
            self._text_parts = []
        elif tag.lower() == "title":
            self._in_title = False
            self.title = clean_text("".join(self._title_parts))

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)
        if self._in_title:
            self._title_parts.append(data)


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + ("\n" if rows else ""), encoding="utf-8")


def cleanup_legacy_artifacts(day_dir: Path, target_date: str) -> None:
    for name in [
        "daily_report_standalone.html",
        f"tech_intel_daily_{target_date}.zip",
        "collection_diagnostics.md",
        "daily_report.md",
        "execution_todo.md",
    ]:
        path = day_dir / name
        if path.exists() and path.is_file():
            path.unlink()
    articles_dir = day_dir / "articles"
    if articles_dir.exists() and articles_dir.is_dir():
        for child in articles_dir.rglob("*"):
            if child.is_file():
                child.unlink()
        for child in sorted(articles_dir.rglob("*"), reverse=True):
            if child.is_dir():
                child.rmdir()
        articles_dir.rmdir()


def cleanup_internal_sidecars(day_dir: Path) -> None:
    for name in [
        "business_tag_library.json",
        "collection_candidates.jsonl",
        "collection_diagnostics.json",
        "collection_diagnostics.md",
        "daily_report.md",
        "execution_todo.json",
        "execution_todo.md",
        "focus_standard_library.json",
        "keyword_library.json",
        "knowledge_base.jsonl",
        "present_files_manifest.json",
        "taxonomy_updates.jsonl",
    ]:
        path = day_dir / name
        if path.exists() and path.is_file():
            path.unlink()


def initialize_execution_todos() -> list[dict[str, Any]]:
    now = datetime.now().isoformat(timespec="seconds")
    return [
        {
            "id": step_id,
            "content": content,
            "status": "pending",
            "created_at": now,
            "started_at": None,
            "completed_at": None,
            "note": "",
        }
        for step_id, content in PIPELINE_TODOS
    ]


def write_execution_todos(day_dir: Path, todos: list[dict[str, Any]]) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "execution_todo.json").write_text(json.dumps(todos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 执行清单", ""]
    status_label = {
        "pending": "待执行",
        "in_progress": "执行中",
        "completed": "已完成",
        "failed": "失败",
    }
    for item in todos:
        label = status_label.get(str(item.get("status")), str(item.get("status")))
        note = f"：{item['note']}" if item.get("note") else ""
        lines.append(f"- [{label}] {item['content']}{note}")
    (day_dir / "execution_todo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_execution_todo(day_dir: Path, todos: list[dict[str, Any]], step_id: str, status: str, note: str = "") -> None:
    now = datetime.now().isoformat(timespec="seconds")
    for item in todos:
        if item["id"] != step_id:
            continue
        item["status"] = status
        if status == "in_progress" and not item.get("started_at"):
            item["started_at"] = now
        if status in {"completed", "failed"}:
            item["completed_at"] = now
        if note:
            item["note"] = note
        break
    write_execution_todos(day_dir, todos)


def fetch_url(url: str, timeout: int = 20) -> tuple[str, str | None]:
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        data = response.read()
        charset = response.headers.get_content_charset()
    if charset:
        return data.decode(charset, errors="replace"), charset
    for encoding in ("utf-8", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), None


def parse_catalog_urls(target_date: str) -> list[dict[str, str]]:
    md = SOURCE_CATALOG.read_text(encoding="utf-8")
    sources: list[dict[str, str]] = []
    heading = ""
    for line in md.splitlines():
        if line.startswith("### "):
            heading = line.removeprefix("### ").strip()
        urls = re.findall(r"https?://[^\s|)]+", line)
        if not urls:
            continue
        label = heading or "信源"
        if line.startswith("|"):
            cols = [col.strip() for col in line.strip("|").split("|")]
            if len(cols) >= 2 and cols[0] not in {"Source", "---"}:
                label = cols[0]
        elif ":" in line:
            label = f"{heading}-{line.split(':', 1)[0].lstrip('- ').strip()}" if heading else line.split(":", 1)[0].lstrip("- ").strip()
        for url in urls:
            sources.append({"source": label, "url": url})

    y, m, d = target_date.split("-")
    people_url = f"http://paper.people.com.cn/rmrb/pc/layout/{y}{m}/{d}/node_01.html"
    sources.insert(0, {"source": "人民日报-目标日期版面", "url": people_url})

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sources:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduped.append(item)
    return deduped


def same_domain_or_path(base_url: str, candidate_url: str) -> bool:
    base = urlparse(base_url)
    cand = urlparse(candidate_url)
    return cand.scheme in {"http", "https"} and cand.netloc == base.netloc


def likely_article_url(url: str) -> bool:
    lowered = url.lower()
    if any(skip in lowered for skip in ["javascript:", "mailto:", "#", ".pdf", ".doc", ".xls"]):
        return False
    return bool(
        re.search(r"/\d{4}[-/]?\d{2}[-/]?\d{2}/", lowered)
        or re.search(r"/\d{6}/\d{2}/", lowered)
        or re.search(r"\d{8}", lowered)
        or lowered.endswith(".html")
        or lowered.endswith(".htm")
    )


def is_article_page_url(url: str) -> bool:
    lowered = url.lower()
    if any(marker in lowered for marker in ["/layout/", "/node_", "index.html", "index.htm", "homepage?"]):
        return False
    return likely_article_url(url)


def extract_links(page_html: str, base_url: str) -> tuple[str, list[dict[str, str]]]:
    parser = LinkExtractor(base_url)
    parser.feed(page_html)
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for link in parser.links:
        url = link["url"].split("#", 1)[0]
        if url in seen or not same_domain_or_path(base_url, url) or not is_article_page_url(url):
            continue
        seen.add(url)
        links.append({"title": link["title"], "url": url})
    return parser.title, links


def html_to_text(page_html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", page_html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return clean_text(text)


def extract_paragraphs(page_html: str) -> list[str]:
    rough = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", page_html)
    paragraphs = re.findall(r"(?is)<p[^>]*>(.*?)</p>", rough)
    cleaned: list[str] = []
    for para in paragraphs:
        text = clean_text(re.sub(r"(?s)<[^>]+>", " ", para))
        if len(text) >= 20 and not text.startswith(("责任编辑", "来源：", "分享到")):
            cleaned.append(text)
    if not cleaned:
        text = html_to_text(page_html)
        cleaned = [part.strip() for part in re.split(r"(?<=[。！？；;])", text) if len(part.strip()) >= 30]
    deduped: list[str] = []
    seen: set[str] = set()
    for para in cleaned:
        if para not in seen:
            seen.add(para)
            deduped.append(para)
    return deduped[:80]


def extract_title(page_html: str, fallback: str) -> str:
    title_match = re.search(r"(?is)<h1[^>]*>(.*?)</h1>", page_html) or re.search(r"(?is)<title[^>]*>(.*?)</title>", page_html)
    if not title_match:
        return fallback
    title = clean_text(re.sub(r"(?s)<[^>]+>", " ", title_match.group(1)))
    title = re.sub(r"[-_].*$", "", title).strip()
    return title or fallback


def extract_date_from_text_or_url(text: str, url: str) -> str | None:
    patterns = [
        (r"(20\d{2})年(\d{1,2})月(\d{1,2})日", "{0}-{1:0>2}-{2:0>2}"),
        (r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", "{0}-{1:0>2}-{2:0>2}"),
        (r"(20\d{2})(\d{2})(\d{2})", "{0}-{1}-{2}"),
        (r"/(20\d{2})(\d{2})/(\d{2})/", "{0}-{1}-{2}"),
    ]
    haystack = f"{url} {text[:2500]}"
    for pattern, fmt in patterns:
        match = re.search(pattern, haystack)
        if match:
            return fmt.format(*match.groups())
    return None


def matched_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if term and term in text]


def load_terms(day_dir: Path) -> tuple[list[str], list[str]]:
    keyword_rows = read_json(day_dir / "keyword_library.json", [])
    keywords = [row.get("term", "") for row in keyword_rows if isinstance(row, dict)]
    if not keywords:
        keywords = TECH_KEYWORDS
    tag_rows = read_json(day_dir / "business_tag_library.json", [])
    tags: list[str] = []
    for row in tag_rows:
        if isinstance(row, dict):
            tags.extend(row.get("tags") or [])
            if row.get("category"):
                tags.append(row["category"])
            if row.get("direction") and row.get("direction") != "-":
                tags.append(row["direction"])
    return list(dict.fromkeys(keywords + TECH_KEYWORDS)), list(dict.fromkeys(tags))


def classify_focus(text: str) -> list[str]:
    focus = [label for label, terms in FOCUS_RULES if any(term in text for term in terms)]
    return focus or ["政策导向"]


def classify_source_type(source: str) -> str:
    if any(name in source for name in ["发展改革委", "科学技术部", "工业和信息化部", "国资委", "能源局", "自然资源部"]):
        return "政策/政策解读类信息"
    if any(name in source for name in ["中国海油", "研究总院"]):
        return "领导讲话/批示指示类信息"
    return "资讯类信息"


def compact_text(text: str, limit: int = 180) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip("，。；、 ") + "…"


def pick_evidence_context(paragraphs: list[str], terms: list[str], fallback: str) -> str:
    for term in terms[:8]:
        for paragraph in paragraphs:
            if term and term in paragraph:
                return paragraph
    return next((paragraph for paragraph in paragraphs if paragraph.strip()), fallback)


def build_article_analysis(
    *,
    title: str,
    source: str,
    source_type: str,
    evidence_context: str,
    matched_keywords: list[str],
    matched_tags: list[str],
    focus_types: list[str],
) -> tuple[str, list[str]]:
    primary_keyword = matched_keywords[0] if matched_keywords else "科技管理相关议题"
    keyword_text = "、".join(matched_keywords[:5]) if matched_keywords else "未记录明确关键词"
    tag_text = "、".join(matched_tags[:4]) if matched_tags else "科技管理"
    focus_text = "、".join(focus_types[:3]) if focus_types else "政策导向"
    evidence = compact_text(evidence_context, 180) or title

    summary = (
        f"本文来自{source}，属于{source_type}，核心关注“{primary_keyword}”。"
        f"结合原文证据“{evidence}”，可判断其与{tag_text}相关，重点体现{focus_text}。"
    )

    implications = [
        f"业务关联：围绕《{title}》命中的{keyword_text}，优先映射到{tag_text}相关业务场景，判断其是否影响现有科技项目、数据资产或专业研究任务。",
        f"管理启示：该文来自{source}，应围绕{focus_text}提取可落地要求，重点核对原文证据“{evidence}”中涉及的责任主体、时间节点、资源配置、标准规范或示范场景。",
        f"后续动作：将“{primary_keyword}”作为《{title}》的知识库线索，结合原文链接复核全文证据，必要时补充到科技管理台账、项目策划或成果转化跟踪清单。",
    ]
    return summary, implications


def relevance_score(source: str, title: str, text: str, matches: list[str]) -> tuple[int, list[str]]:
    haystack = f"{title}\n{text}"
    score = 0
    reasons: list[str] = []

    strong_matches = [term for term in matches if term in STRONG_KEYWORDS]
    weak_only = bool(matches) and not strong_matches
    if strong_matches:
        score += min(len(strong_matches), 4) * 2
        reasons.append("命中强相关词：" + "、".join(strong_matches[:6]))
    if weak_only:
        score -= 3
        reasons.append("仅命中宽泛词：" + "、".join(matches[:6]))

    if any(term in haystack for term in ["油气", "海上油气", "勘探开发", "增储上产", "海洋能源", "海上风电", "深海", "中国海油", "中海油"]):
        score += 4
        reasons.append("具备油气/海洋能源场景")

    if any(term in haystack for term in ["科技管理", "科研管理", "科技项目", "成果转化", "知识产权", "标准体系", "数据资产", "数据要素", "人工智能", "数智化"]):
        score += 4
        reasons.append("具备科技管理/数智化治理信号")

    if any(term in source for term in ["能源局", "国资委", "科学技术部", "国家数据局", "中国海油", "研究总院"]):
        score += 2
        reasons.append("来自重点信源")

    if any(term in haystack for term in ["会见", "致电", "贺电", "党建文选", "政协会议开幕", "人大常委会会议在京举行"]):
        score -= 5
        reasons.append("泛政治/会议新闻，缺少直接科技管理对象")

    return score, reasons


def reject_reason(
    candidate_date: str | None,
    target_date: str,
    matches: list[str],
    text: str,
    *,
    source: str,
    title: str,
) -> str | None:
    if not candidate_date:
        return "日期无法识别"
    if candidate_date != target_date:
        return "日期不匹配"
    if not matches:
        return "未命中科技管理/油气业务关键词"
    if any(term in matches for term in BROAD_KEYWORDS) and not any(term in text for term in DOMAIN_QUALIFIERS):
        return "宽泛主题缺少油气/海洋/科技管理限定"
    score, _ = relevance_score(source, title, text, matches)
    if score < 5:
        return f"强相关评分不足（{score}分）"
    return None


def make_record(
    *,
    target_date: str,
    source: str,
    url: str,
    title: str,
    page_html: str,
    candidate_date: str,
    keywords: list[str],
    tags: list[str],
) -> dict[str, Any]:
    paragraphs = extract_paragraphs(page_html)
    text = "\n".join(paragraphs)
    matched_keywords = matched_terms(f"{title}\n{text}", keywords)
    matched_tags = matched_terms(f"{title}\n{text}", tags)
    focus_types = classify_focus(f"{title}\n{text}")
    source_type = classify_source_type(source)
    evidence_context = pick_evidence_context(paragraphs, matched_keywords + matched_tags, title)
    summary, implications = build_article_analysis(
        title=title,
        source=source,
        source_type=source_type,
        evidence_context=evidence_context,
        matched_keywords=matched_keywords,
        matched_tags=matched_tags,
        focus_types=focus_types,
    )
    return {
        "title": title,
        "source": source,
        "source_type": source_type,
        "date": candidate_date,
        "edition_or_section": "",
        "url": url,
        "summary": summary,
        "keywords": matched_keywords[:12],
        "business_tags": matched_tags[:8] or ["科技管理"],
        "focus_types": focus_types,
        "management_implications": implications,
        "evidence_passages": [{"context": evidence_context, "marks": [{"type": focus_types[0], "text": evidence_context[:120]}]}],
        "collection_batch_date": target_date,
    }


def run_command(args: list[str]) -> None:
    completed = subprocess.run(args, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {' '.join(args)}\nSTDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )


def merge_records(existing: list[dict[str, Any]], selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_url: dict[str, dict[str, Any]] = {}
    for row in existing + selected:
        key = row.get("url") or f"{row.get('source')}::{row.get('title')}"
        by_url[str(key)] = row
    return list(by_url.values())


def write_diagnostics(day_dir: Path, diagnostics: dict[str, Any]) -> None:
    json_path = day_dir / "collection_diagnostics.json"
    md_path = day_dir / "collection_diagnostics.md"
    json_path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# 采集诊断（{diagnostics['date']}）",
        "",
        "## 总览",
        f"- 检查信源：{diagnostics['summary']['sources_checked']}",
        f"- 发现候选链接：{diagnostics['summary']['candidate_links_found']}",
        f"- 抓取候选文章页：{diagnostics['summary']['candidate_pages_fetched']}",
        f"- 入选文章：{diagnostics['summary']['selected_count']}",
        f"- 被过滤文章：{diagnostics['summary']['rejected_count']}",
        f"- 访问错误：{diagnostics['summary']['errors_count']}",
        "",
        "## 过滤原因",
    ]
    if diagnostics["summary"]["rejected_by_reason"]:
        for reason, count in diagnostics["summary"]["rejected_by_reason"].items():
            lines.append(f"- {reason}：{count}")
    else:
        lines.append("- 无")
    lines.extend(["", "## 信源明细"])
    for source in diagnostics["sources"]:
        lines.append(
            f"- {source['source']}：{source['status']}；候选 {source['candidate_links']}；抓取 {source['fetched_candidates']}；入选 {source['selected']}"
        )
        for note in source.get("notes") or []:
            lines.append(f"  - {note}")
        for reason, count in (source.get("rejected_by_reason") or {}).items():
            lines.append(f"  - 过滤：{reason} {count} 篇")
    lines.extend(["", "## 入选文章"])
    if diagnostics["selected"]:
        for row in diagnostics["selected"]:
            lines.append(f"- [{row['title']}]({row['url']})｜{row['source']}｜{row['date']}")
    else:
        lines.append("- 无入选文章。请优先查看“信源明细”和“过滤原因”，判断是未采到、访问受限、日期不匹配，还是筛选规则太严。")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_chat_summary(date: str, report_path: Path, diagnostics: dict[str, Any]) -> str:
    summary = diagnostics.get("summary") or {}
    selected = diagnostics.get("selected") or []
    lines = [
        f"日报已生成：{report_path.resolve()}",
        f"入选文章：{summary.get('selected_count', 0)} 篇。",
    ]
    if selected:
        lines.append("本次入选文章：")
        for row in selected:
            title = row.get("title") or "未命名文章"
            source = row.get("source") or "未知信源"
            row_date = row.get("date") or date
            url = row.get("url") or ""
            lines.append(f"- {title}｜{source}｜{row_date}｜{url}")
    else:
        lines.append("本次没有入选文章，请在日报内查看采集诊断和过滤原因。")
    lines.append(
        "采集诊断："
        f"检查信源 {summary.get('sources_checked', 0)} 个，"
        f"发现候选链接 {summary.get('candidate_links_found', 0)} 个，"
        f"抓取候选页 {summary.get('candidate_pages_fetched', 0)} 个，"
        f"过滤 {summary.get('rejected_count', 0)} 篇，"
        f"访问错误 {summary.get('errors_count', 0)} 个。"
    )
    lines.append("以上摘要仅基于本次脚本采集结果和 daily_report.html 内容。")
    return "\n".join(lines)


def collect(target_date: str, day_dir: Path, max_sources: int, max_candidates_per_source: int) -> dict[str, Any]:
    keywords, tags = load_terms(day_dir)
    sources = parse_catalog_urls(target_date)[:max_sources]
    source_checks: list[SourceCheck] = []
    candidates: list[dict[str, Any]] = []
    selected: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    errors_count = 0

    for source_item in sources:
        check = SourceCheck(source=source_item["source"], url=source_item["url"])
        source_checks.append(check)
        try:
            html, _ = fetch_url(source_item["url"])
            check.status = "fetched"
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors_count += 1
            check.status = "access_error"
            check.notes.append(str(exc))
            continue

        page_title, links = extract_links(html, source_item["url"])
        if is_article_page_url(source_item["url"]):
            links.insert(0, {"title": page_title or source_item["source"], "url": source_item["url"]})
        check.candidate_links = len(links)
        if not links:
            check.status = "fetched_no_candidate_links"
            check.notes.append("页面可访问，但没有识别到候选文章链接。")

        for link in links[:max_candidates_per_source]:
            candidate = {"source": source_item["source"], "title": link["title"], "url": link["url"]}
            candidates.append(candidate)
            try:
                article_html, _ = fetch_url(link["url"])
                check.fetched_candidates += 1
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                errors_count += 1
                check.reject("候选页访问失败")
                rejections.append({**candidate, "reason": "候选页访问失败", "detail": str(exc)})
                continue

            title = extract_title(article_html, link["title"])
            text = html_to_text(article_html)
            candidate_date = extract_date_from_text_or_url(text, link["url"])
            matches = matched_terms(f"{title}\n{text}", keywords)
            score, score_reasons = relevance_score(source_item["source"], title, text, matches)
            reason = reject_reason(candidate_date, target_date, matches, f"{title}\n{text}", source=source_item["source"], title=title)
            if reason:
                check.reject(reason)
                rejections.append(
                    {
                        **candidate,
                        "title": title,
                        "reason": reason,
                        "date": candidate_date,
                        "matched_keywords": matches[:10],
                        "relevance_score": score,
                        "score_reasons": score_reasons,
                    }
                )
                continue

            record = make_record(
                target_date=target_date,
                source=source_item["source"],
                url=link["url"],
                title=title,
                page_html=article_html,
                candidate_date=candidate_date,
                keywords=keywords,
                tags=tags,
            )
            record["relevance_score"] = score
            record["relevance_reasons"] = score_reasons
            selected.append(record)
            check.selected += 1

    write_jsonl(day_dir / "collection_candidates.jsonl", candidates)
    write_jsonl(day_dir / "knowledge_base.jsonl", selected)

    rejected_by_reason: dict[str, int] = {}
    for row in rejections:
        reason = str(row.get("reason") or "未知")
        rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1

    diagnostics = {
        "date": target_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "day_dir": str(day_dir.resolve()),
        "summary": {
            "sources_checked": len(source_checks),
            "candidate_links_found": sum(item.candidate_links for item in source_checks),
            "candidate_pages_fetched": sum(item.fetched_candidates for item in source_checks),
            "selected_count": len(selected),
            "rejected_count": len(rejections),
            "errors_count": errors_count,
            "rejected_by_reason": rejected_by_reason,
        },
        "sources": [
            {
                "source": item.source,
                "url": item.url,
                "status": item.status,
                "candidate_links": item.candidate_links,
                "fetched_candidates": item.fetched_candidates,
                "selected": item.selected,
                "rejected_by_reason": item.rejected_by_reason,
                "notes": item.notes,
            }
            for item in source_checks
        ],
        "selected": [
            {"title": row["title"], "url": row["url"], "source": row["source"], "date": row["date"], "matched_keywords": row["keywords"]}
            for row in selected
        ],
        "rejections": rejections[:200],
        "files": {
            "candidates": str((day_dir / "collection_candidates.jsonl").resolve()),
            "knowledge_base": str((day_dir / "knowledge_base.jsonl").resolve()),
            "diagnostics_json": str((day_dir / "collection_diagnostics.json").resolve()),
            "diagnostics_md": str((day_dir / "collection_diagnostics.md").resolve()),
        },
    }
    write_diagnostics(day_dir, diagnostics)
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full tech-intel-daily collection and rendering pipeline.")
    parser.add_argument("--date", default=date_cls.today().isoformat(), help="Target publication date, YYYY-MM-DD.")
    parser.add_argument("--output-root", default="outputs/tech-intel-daily", help="Root output directory.")
    parser.add_argument("--max-sources", type=int, default=24, help="Maximum source entry pages to check.")
    parser.add_argument("--max-candidates-per-source", type=int, default=8, help="Maximum candidate article pages to fetch per source.")
    args = parser.parse_args()

    day_dir = Path(args.output_root) / args.date
    day_dir.mkdir(parents=True, exist_ok=True)
    todos = initialize_execution_todos()
    write_execution_todos(day_dir, todos)
    try:
        update_execution_todo(day_dir, todos, "initialize", "in_progress")
        run_command([sys.executable, str(SCRIPT_DIR / "init_state.py"), "--date", args.date, "--output-root", args.output_root])
        cleanup_legacy_artifacts(day_dir, args.date)
        update_execution_todo(day_dir, todos, "initialize", "completed")

        update_execution_todo(day_dir, todos, "load_requirements", "in_progress")
        _ = SOURCE_CATALOG.read_text(encoding="utf-8")
        update_execution_todo(day_dir, todos, "load_requirements", "completed")

        update_execution_todo(day_dir, todos, "collect_candidates", "in_progress")
        diagnostics = collect(args.date, day_dir, args.max_sources, args.max_candidates_per_source)
        update_execution_todo(
            day_dir,
            todos,
            "collect_candidates",
            "completed",
            f"发现候选链接 {diagnostics['summary']['candidate_links_found']} 个，抓取候选页 {diagnostics['summary']['candidate_pages_fetched']} 个。",
        )
        update_execution_todo(day_dir, todos, "filter_knowledge_base", "in_progress")
        update_execution_todo(
            day_dir,
            todos,
            "filter_knowledge_base",
            "completed",
            f"入选 {diagnostics['summary']['selected_count']} 篇，过滤 {diagnostics['summary']['rejected_count']} 篇。",
        )

        update_execution_todo(day_dir, todos, "enrich_full_text", "in_progress")
        run_command([sys.executable, str(SCRIPT_DIR / "enrich_full_text.py"), "--day-dir", str(day_dir)])
        update_execution_todo(day_dir, todos, "enrich_full_text", "completed")

        update_execution_todo(day_dir, todos, "render_daily_report", "in_progress")
        run_command(
            [
                sys.executable,
                str(SCRIPT_DIR / "render_html_report.py"),
                "--date",
                args.date,
                "--day-dir",
                str(day_dir),
                "--diagnostics",
                str(day_dir / "collection_diagnostics.json"),
            ]
        )
        update_execution_todo(day_dir, todos, "render_daily_report", "completed")

        report_path = day_dir / "daily_report.html"
        if not report_path.exists() or report_path.stat().st_size == 0:
            raise RuntimeError(f"daily_report.html was not generated: {report_path}")

        update_execution_todo(day_dir, todos, "finalize", "in_progress")
        update_execution_todo(day_dir, todos, "finalize", "completed")
        run_command(
            [
                sys.executable,
                str(SCRIPT_DIR / "render_html_report.py"),
                "--date",
                args.date,
                "--day-dir",
                str(day_dir),
                "--diagnostics",
                str(day_dir / "collection_diagnostics.json"),
            ]
        )
        cleanup_internal_sidecars(day_dir)
    except Exception as exc:
        active = next((item for item in todos if item.get("status") == "in_progress"), None)
        if active:
            update_execution_todo(day_dir, todos, str(active["id"]), "failed", str(exc))
        raise

    result = {
        "status": "success",
        "date": args.date,
        "daily_report": str(report_path.resolve()),
        "summary": diagnostics["summary"],
        "selected_articles": diagnostics.get("selected", []),
        "chat_summary": build_chat_summary(args.date, report_path, diagnostics),
    }
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
