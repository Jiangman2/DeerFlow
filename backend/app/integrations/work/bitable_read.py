"""Bounded read-only queries for Work Bitable data tables."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .client import WORK_BITABLE_TABLE_RE, WorkError, valid_page_token

if TYPE_CHECKING:
    from .file_read import WorkFileRef

_TABLE_PAGES = 3
_TABLE_LIMIT = 3
_RECORD_LIMIT = 100
_FIELD_LIMIT = 24
_CELL_LIMIT = 300
_TEXT_LIMIT = 20_000


def _items(response: dict, stage: str) -> tuple[list[dict], bool, object]:
    data = response.get("data")
    items = data.get("items", []) if isinstance(data, dict) else None
    more = data.get("has_more", False) if isinstance(data, dict) else None
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items) or type(more) is not bool:
        raise WorkError(stage)
    return items, more, data.get("page_token")


async def _resolve_tables(ref: WorkFileRef, request) -> tuple[list[tuple[str, str]], bool]:
    tables = {}
    page_token = None
    seen = set()
    for _ in range(_TABLE_PAGES):
        response = await request("bitable_tables", document_id=ref.token, page_token=page_token)
        items, more, next_token = _items(response, "bitable_tables_invalid")
        for item in items:
            if item.get("hidden") is True or item.get("is_hidden") is True:
                continue
            table_id, name = item.get("table_id"), item.get("name")
            if not isinstance(table_id, str) or not WORK_BITABLE_TABLE_RE.fullmatch(table_id):
                raise WorkError("bitable_tables_invalid")
            name = " ".join(name[:120].split()) if isinstance(name, str) else ""
            tables[table_id] = name or "数据表"
        if ref.table_id and ref.table_id in tables:
            return [(ref.table_id, tables[ref.table_id])], False
        if ref.view_id and not ref.table_id and len(tables) > 1:
            raise WorkError("bitable_target_required")
        if not ref.table_id and not ref.view_id and len(tables) >= _TABLE_LIMIT:
            return list(tables.items())[:_TABLE_LIMIT], more or len(tables) > _TABLE_LIMIT
        if not more:
            break
        if not valid_page_token(next_token) or next_token in seen:
            raise WorkError("bitable_tables_invalid")
        seen.add(next_token)
        page_token = next_token
    else:
        if not ref.table_id and not ref.view_id and tables:
            return list(tables.items()), True
        raise WorkError("bitable_table_scan_limit")
    if ref.table_id or not tables:
        raise WorkError("bitable_table_not_found")
    return list(tables.items()), False


def _cell_text(value: object) -> tuple[str, bool]:
    if value is None:
        return "", False
    if isinstance(value, (str, int, float, bool)):
        raw = str(value)
        return " ".join(raw[:_CELL_LIMIT].split()), len(raw) > _CELL_LIMIT
    if isinstance(value, list):
        values = value[:10]
        parts = []
        truncated = len(value) > len(values)
        for item in values:
            # Some rich-text segments include a URL. Use the visible text only.
            if isinstance(item, dict):
                item = item.get("text")
            if not isinstance(item, (str, int, float, bool)):
                return "[复杂字段]", True
            text, clipped = _cell_text(item)
            parts.append(text)
            truncated |= clipped
        text = " / ".join(parts)
        return text[:_CELL_LIMIT], truncated or len(text) > _CELL_LIMIT
    return "[复杂字段]", True


def _record_lines(response: dict, limit: int) -> tuple[list[str], bool]:
    records, more, _ = _items(response, "bitable_records_invalid")
    lines = []
    truncated = more or len(records) > limit
    for number, item in enumerate(records[:limit], start=1):
        fields = item.get("fields")
        if not isinstance(fields, dict) or any(not isinstance(name, str) for name in fields):
            raise WorkError("bitable_records_invalid")
        truncated |= len(fields) > _FIELD_LIMIT
        cells = []
        for index, (name, value) in enumerate(fields.items()):
            if index == _FIELD_LIMIT:
                break
            text, clipped = _cell_text(value)
            truncated |= clipped or len(name) > 120
            if text:
                cells.append([" ".join(name[:120].split()), text])
        if cells:
            lines.append(f"第 {number} 条 " + json.dumps(cells, ensure_ascii=False))
    return lines, truncated


async def read_bitable(ref: WorkFileRef, request) -> tuple[str, str, bool]:
    tables, truncated = await _resolve_tables(ref, request)
    limit = _RECORD_LIMIT // len(tables)
    sections = []
    for table_id, title in tables:
        size = {"page_size": limit} if limit < _RECORD_LIMIT else {}
        response = await request("bitable_records", document_id=ref.token, block_id=table_id, view_id=ref.view_id or None, **size)
        lines, clipped = _record_lines(response, limit)
        truncated |= clipped
        if lines:
            sections.append(f"数据表：{title}\n" + "\n".join(lines))
    if not sections:
        raise WorkError("bitable_no_readable_text")
    text = "\n\n".join(sections)
    title = f"Work 多维表格 · {tables[0][1]}" if len(tables) == 1 else "Work 多维表格"
    return title, text[:_TEXT_LIMIT], truncated or len(text) > _TEXT_LIMIT
