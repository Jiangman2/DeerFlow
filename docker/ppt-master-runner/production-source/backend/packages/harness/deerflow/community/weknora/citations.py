"""Deterministic normalization for WeKnora knowledge citations."""

from __future__ import annotations

import html
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_NATIVE_KB_TAG_RE = re.compile(r"<kb\b(?P<attrs>[^>]*)/?>", re.IGNORECASE)
_ATTRIBUTE_RE = re.compile(r"""(?P<name>[A-Za-z_][\w-]*)\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""")
_BARE_CITATION_RE = re.compile(r"\[citation:\s*(?P<label>[^\]\n]+?)\](?!\s*\()", re.IGNORECASE)
_COMPLETE_CITATION_RE = re.compile(r"^\[citation:\s*(?P<label>[^\]\n]+?)\]\((?P<url>[^)\n]+)\)$", re.IGNORECASE)
_CANONICAL_CITATION_RE = re.compile(
    r"\[citation:\s*(?P<label>[^\]\n]+?)\]\((?P<url>/platform/knowledge-bases/[^)\s\n]+)\)",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"^\s*(?P<fence>`{3,}|~{3,})")
_INLINE_CODE_RE = re.compile(r"(?P<fence>`+)(?P<body>[^\n]*?)(?P=fence)")
_WEKNORA_CITATION_PATH = "/platform/knowledge-bases/"


@dataclass(frozen=True)
class _Citation:
    markdown: str
    url: str
    labels: tuple[str, ...]
    chunk_id: str
    knowledge_base_id: str


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _label_key(value: str) -> str:
    return " ".join(html.unescape(value).strip().split()).casefold()


def _safe_label(value: str) -> str:
    return value.replace("]", "）").strip()


def _citation_from_reference(reference: Mapping[str, Any]) -> _Citation | None:
    title = _text(reference.get("knowledge_title")) or _text(reference.get("knowledge_filename"))
    url = _text(reference.get("citation_url")) or _text(reference.get("source"))

    markdown = _text(reference.get("citation_markdown"))
    if markdown:
        match = _COMPLETE_CITATION_RE.match(markdown)
        if match:
            title = title or match.group("label").strip()
            url = url or match.group("url").strip()

    # WeKnora citations must remain same-origin knowledge links. Ignoring any
    # unexpected URL here prevents tool output from becoming an arbitrary link.
    if not title or not url.startswith(_WEKNORA_CITATION_PATH):
        return None

    labels: list[str] = []
    for candidate in (
        title,
        _text(reference.get("knowledge_title")),
        _text(reference.get("knowledge_filename")),
    ):
        key = _label_key(candidate)
        if key and key not in labels:
            labels.append(key)

    return _Citation(
        markdown=f"[citation:{_safe_label(title)}]({url})",
        url=url,
        labels=tuple(labels),
        chunk_id=_text(reference.get("chunk_id")) or _text(reference.get("id")),
        knowledge_base_id=_text(reference.get("knowledge_base_id")),
    )


def _unique_citations(references: Iterable[Mapping[str, Any]]) -> list[_Citation]:
    citations: list[_Citation] = []
    seen: set[tuple[str, str, str]] = set()
    for reference in references:
        citation = _citation_from_reference(reference)
        if citation is None:
            continue
        # Preserve separate chunks from the same document. Their public URL is
        # identical, but WeKnora native tags identify the exact chunk.
        identity = (citation.url, citation.markdown, citation.chunk_id)
        if identity in seen:
            continue
        seen.add(identity)
        citations.append(citation)
    return citations


def extract_canonical_weknora_references(text: str) -> list[dict[str, str]]:
    """Recover already-canonical citations from a delegated task result.

    The extractor accepts only same-origin WeKnora knowledge paths. It lets the
    lead agent repair a link it truncated while summarizing a subagent result,
    without trusting arbitrary Markdown links or searching historical turns.
    """

    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for match in _CANONICAL_CITATION_RE.finditer(text):
        title = match.group("label").strip()
        url = match.group("url").strip()
        identity = (_label_key(title), url)
        if not identity[0] or identity in seen:
            continue
        seen.add(identity)
        references.append(
            {
                "knowledge_title": title,
                "knowledge_filename": title,
                "citation_url": url,
                "citation_markdown": match.group(0),
            }
        )
    return references


def _attributes(tag: str) -> dict[str, str]:
    return {match.group("name").casefold(): html.unescape(match.group("value")).strip() for match in _ATTRIBUTE_RE.finditer(tag)}


def _single(citations: Iterable[_Citation]) -> _Citation | None:
    unique_by_url = {citation.url: citation for citation in citations}
    return next(iter(unique_by_url.values())) if len(unique_by_url) == 1 else None


def _rewrite_visible_text(text: str, citations: list[_Citation]) -> str:
    by_chunk: dict[str, list[_Citation]] = defaultdict(list)
    by_label: dict[str, list[_Citation]] = defaultdict(list)
    by_kb_and_label: dict[tuple[str, str], list[_Citation]] = defaultdict(list)
    for citation in citations:
        if citation.chunk_id:
            by_chunk[citation.chunk_id].append(citation)
        for label in citation.labels:
            by_label[label].append(citation)
            if citation.knowledge_base_id:
                by_kb_and_label[(citation.knowledge_base_id, label)].append(citation)

    def replace_native(match: re.Match[str]) -> str:
        attrs = _attributes(match.group(0))
        chunk_id = attrs.get("chunk_id", "")
        if chunk_id:
            citation = _single(by_chunk.get(chunk_id, ()))
            if citation is not None:
                return citation.markdown

        label = _label_key(attrs.get("doc", ""))
        kb_id = attrs.get("kb_id", "") or attrs.get("knowledge_base_id", "")
        if label and kb_id:
            citation = _single(by_kb_and_label.get((kb_id, label), ()))
            if citation is not None:
                return citation.markdown
        if label:
            citation = _single(by_label.get(label, ()))
            if citation is not None:
                return citation.markdown
        return match.group(0)

    def replace_bare(match: re.Match[str]) -> str:
        citation = _single(by_label.get(_label_key(match.group("label")), ()))
        return citation.markdown if citation is not None else match.group(0)

    return _BARE_CITATION_RE.sub(replace_bare, _NATIVE_KB_TAG_RE.sub(replace_native, text))


def _rewrite_outside_inline_code(line: str, citations: list[_Citation]) -> str:
    output: list[str] = []
    cursor = 0
    for match in _INLINE_CODE_RE.finditer(line):
        output.append(_rewrite_visible_text(line[cursor : match.start()], citations))
        output.append(match.group(0))
        cursor = match.end()
    output.append(_rewrite_visible_text(line[cursor:], citations))
    return "".join(output)


def normalize_weknora_citations(text: str, references: Iterable[Mapping[str, Any]]) -> str:
    """Convert native or incomplete WeKnora citations into canonical links.

    Only current references with a unique match are rewritten. Ambiguous
    same-named documents remain untouched instead of being guessed across
    knowledge bases or tenants.
    """

    citations = _unique_citations(references)
    if not text or not citations:
        return text

    output: list[str] = []
    active_fence_char = ""
    active_fence_length = 0
    for line in text.splitlines(keepends=True):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group("fence")
            if not active_fence_char:
                active_fence_char = marker[0]
                active_fence_length = len(marker)
            elif marker[0] == active_fence_char and len(marker) >= active_fence_length:
                active_fence_char = ""
                active_fence_length = 0
            output.append(line)
            continue
        if active_fence_char:
            output.append(line)
            continue
        output.append(_rewrite_outside_inline_code(line, citations))
    return "".join(output)
