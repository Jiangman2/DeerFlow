"""Typed, read-only Work Docs, Sheets, Bitable, and Drive resources."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, urlencode, urlsplit

from .bitable_read import read_bitable
from .capabilities import MAX_DOCUMENT_CHARS
from .client import DOC_ID_RE, WORK_BITABLE_TABLE_RE, WORK_BITABLE_VIEW_RE, WORK_FILE_ID_RE, WORK_SHEET_ID_RE, WorkError, sheet_bounds
from .scopes import DOCUMENT_SCOPE

FILE_PLAN_TARGET = "workfiletarget"
FILE_JOB_PROTOCOL = 3
_SHEET_LIMIT = 3
_SHEET_ROWS = 100
_SHEET_COLUMNS = 16
_TEXT_LIMIT = 20_000
_RANGE_IN_REQUEST = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]{1,4}[0-9]{1,8})\s*[:：]\s*([A-Za-z]{1,4}[0-9]{1,8})(?![A-Za-z0-9_])")
_BITABLE_READ_SCOPES = frozenset({"bitable:app:readonly", "base:table:read", "base:record:retrieve"})


@dataclass(frozen=True)
class WorkFileSettings:
    sheets_scope: str = ""
    drive_scope: str = ""
    bitable_scope: str = ""

    @classmethod
    def from_env(cls, profile: str) -> WorkFileSettings:
        if os.getenv("DEER_FLOW_WORK_FILE_READ_ENABLED") != "true":
            return cls()
        prefix = "DEER_FLOW_WORK_" + profile.upper() + "_"
        return cls(
            _read_scopes(os.getenv(prefix + "SHEETS_READ_SCOPE", ""), "sheets"),
            _read_scopes(os.getenv(prefix + "DRIVE_READ_SCOPE", ""), "drive"),
            _bitable_scopes(os.getenv(prefix + "BITABLE_READ_SCOPE", "")),
        )

    def scope_for(self, kind: str) -> str:
        return {"docx": DOCUMENT_SCOPE, "sheet": self.sheets_scope, "drive": self.drive_scope, "bitable": self.bitable_scope}.get(kind, "")


def _bitable_scopes(value: str) -> str:
    scopes = value.split()
    granted = set(scopes)
    if not 1 <= len(scopes) <= 3 or not granted.issubset(_BITABLE_READ_SCOPES):
        return ""
    if "bitable:app:readonly" not in granted and granted != {"base:table:read", "base:record:retrieve"}:
        return ""
    return " ".join(dict.fromkeys(scopes))


def _read_scopes(value: str, family: str) -> str:
    """An administrator must supply approved, narrowly read-only scopes."""
    scopes = value.split()
    pattern = rf"{family}:[a-z_]+(?::[a-z_]+)*:(?:readonly|read|download)"
    if not 1 <= len(scopes) <= 3 or any(len(scope) > 100 or not re.fullmatch(pattern, scope) for scope in scopes):
        return ""
    return " ".join(dict.fromkeys(scopes))


@dataclass(frozen=True)
class WorkFileRef:
    kind: str
    token: str
    sheet_id: str = ""
    cell_range: str = ""
    table_id: str = ""
    view_id: str = ""

    def intent(self) -> dict:
        if self.kind == "docx":
            return {"operation": "file_read", "kind": self.kind, "token": self.token}
        if self.kind == "bitable":
            return {"operation": "file_read", "kind": self.kind, "token": self.token, "table_id": self.table_id, "view_id": self.view_id}
        return {"operation": "file_read", "kind": self.kind, "token": self.token, "sheet_id": self.sheet_id, "cell_range": self.cell_range}

    def url(self, host: str) -> str:
        path = {"docx": "docx", "sheet": "sheets", "drive": "file", "bitable": "base"}[self.kind]
        selection = (("table", self.table_id), ("view", self.view_id)) if self.kind == "bitable" else (("sheet", self.sheet_id), ("range", self.cell_range))
        query = urlencode({key: value for key, value in selection if value})
        suffix = f"?{query}" if query else ""
        return f"https://{host}/{path}/{self.token}{suffix}"

    @classmethod
    def from_intent(cls, intent: dict | None) -> WorkFileRef | None:
        if not isinstance(intent, dict) or intent.get("operation") != "file_read":
            return None
        kind, token, sheet, cell_range = intent.get("kind"), intent.get("token"), intent.get("sheet_id"), intent.get("cell_range")
        if kind == "docx":
            if set(intent) != {"operation", "kind", "token"} or not isinstance(token, str) or not DOC_ID_RE.fullmatch(token):
                raise WorkError("invalid_file_reference")
            return cls(kind, token)
        if kind == "bitable":
            table, view = intent.get("table_id"), intent.get("view_id")
            if (
                set(intent) != {"operation", "kind", "token", "table_id", "view_id"}
                or not isinstance(token, str)
                or not WORK_FILE_ID_RE.fullmatch(token)
                or not isinstance(table, str)
                or (table and not WORK_BITABLE_TABLE_RE.fullmatch(table))
                or not isinstance(view, str)
                or (view and not WORK_BITABLE_VIEW_RE.fullmatch(view))
            ):
                raise WorkError("invalid_file_reference")
            return cls(kind, token, table_id=table, view_id=view)
        if (
            set(intent) != {"operation", "kind", "token", "sheet_id", "cell_range"}
            or not isinstance(kind, str)
            or kind not in {"sheet", "drive"}
            or not isinstance(token, str)
            or not WORK_FILE_ID_RE.fullmatch(token)
            or not isinstance(sheet, str)
            or (sheet and (kind != "sheet" or not WORK_SHEET_ID_RE.fullmatch(sheet)))
            or not isinstance(cell_range, str)
            or (cell_range and kind != "sheet")
        ):
            raise WorkError("invalid_file_reference")
        if cell_range:
            sheet_bounds(cell_range)
        return cls(kind, token, sheet, cell_range)


def file_links(text: str, host: str) -> list[WorkFileRef]:
    refs = []
    for value in re.findall(r"https?://[^\s<>\"'，。；）)\]]+", text):
        try:
            parsed = urlsplit(value)
            if parsed.scheme != "https" or parsed.netloc != host:
                continue
            match = re.fullmatch(r"/(sheets|file|base)/([A-Za-z0-9_-]{5,128})/?", parsed.path)
            if not match:
                continue
            kind = {"sheets": "sheet", "file": "drive", "base": "bitable"}[match[1]]
            query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=32) if kind != "drive" else {}
            if kind == "bitable":
                table, view = query.get("table", []), query.get("view", [])
                if (table and (len(table) != 1 or not WORK_BITABLE_TABLE_RE.fullmatch(table[0]))) or (view and (len(view) != 1 or not WORK_BITABLE_VIEW_RE.fullmatch(view[0]))):
                    continue
                ref = WorkFileRef(kind, match[2], table_id=table[0] if table else "", view_id=view[0] if view else "")
            else:
                sheet, ranges = query.get("sheet", []), query.get("range", [])
                if sheet and (len(sheet) != 1 or not WORK_SHEET_ID_RE.fullmatch(sheet[0])):
                    continue
                # Keep invalid explicit ranges recognizable so an invalid request
                # can never silently become a preview of the default range.
                cell_range = ranges[0].upper() if len(ranges) == 1 and ranges[0] else "INVALID" if ranges else ""
                ref = WorkFileRef(kind, match[2], sheet[0] if sheet else "", cell_range)
            if ref not in refs:
                refs.append(ref)
            if len(refs) >= 2:
                break
        except ValueError:
            continue
    return refs


def requested_range(ref: WorkFileRef, text: str) -> WorkFileRef:
    if ref.kind != "sheet":
        return ref
    ranges = {left.upper() + ":" + right.upper() for left, right in _RANGE_IN_REQUEST.findall(text)}
    if ref.cell_range:
        ranges.add(ref.cell_range)
    if not ranges:
        return ref
    if len(ranges) != 1:
        raise WorkError("ambiguous_sheet_range")
    cell_range = ranges.pop()
    sheet_bounds(cell_range)
    return replace(ref, cell_range=cell_range)


async def read_file(ref: WorkFileRef, request) -> tuple[str, str, bool]:
    """Read via the caller's authority-checked Work API, never from a page URL."""
    if ref.kind == "docx":
        metadata = (await request("document_info", document_id=ref.token)).get("data")
        doc = metadata.get("document") if isinstance(metadata, dict) else None
        title = doc.get("title") if isinstance(doc, dict) else None
        if not isinstance(title, str) or doc.get("document_id", ref.token) != ref.token:
            raise WorkError("document_info_invalid")
        data = (await request("document_content", document_id=ref.token)).get("data")
        content = data.get("content") if isinstance(data, dict) else None
        if not isinstance(content, str) or len(content) > MAX_DOCUMENT_CHARS:
            raise WorkError("document_content_invalid_or_too_large")
        return title[:200], content[:_TEXT_LIMIT], len(content) > _TEXT_LIMIT
    if ref.kind == "bitable":
        return await read_bitable(ref, request)
    if ref.kind == "drive":
        from .file_content import parse_downloaded_file

        data = await request("drive_file_download", document_id=ref.token)
        parsed = await parse_downloaded_file(data["content"], filename=data["filename"], content_type=data["content_type"])
        return parsed.title, parsed.text, parsed.truncated

    result = await request("sheet_tabs", document_id=ref.token)
    data = result.get("data")
    sheets = data.get("sheets") if isinstance(data, dict) else None
    if not isinstance(sheets, list):
        raise WorkError("sheet_tabs_invalid")
    if any(not isinstance(row, dict) for row in sheets):
        raise WorkError("sheet_tabs_invalid")
    sheets = [row for row in sheets if row.get("hidden") is not True]
    if ref.sheet_id:
        sheets = [row for row in sheets if row.get("sheet_id") == ref.sheet_id]
        if not sheets:
            raise WorkError("sheet_not_found")
    if not sheets:
        raise WorkError("sheet_no_readable_text")
    if ref.cell_range and len(sheets) != 1:
        raise WorkError("sheet_target_required")
    truncated = len(sheets) > _SHEET_LIMIT
    sections = []
    for sheet in sheets[:_SHEET_LIMIT]:
        sheet_id = sheet.get("sheet_id")
        if not isinstance(sheet_id, str) or not WORK_SHEET_ID_RE.fullmatch(sheet_id):
            raise WorkError("sheet_tabs_invalid")
        props = sheet.get("grid_properties") or {}
        rows = props.get("row_count") if isinstance(props, dict) else None
        columns = props.get("column_count") if isinstance(props, dict) else None
        if type(rows) is not int or rows < 1 or type(columns) is not int or columns < 1:
            raise WorkError("sheet_tabs_invalid")
        cell_range = ref.cell_range
        if not cell_range:
            truncated |= rows > _SHEET_ROWS or columns > _SHEET_COLUMNS
            end = chr(ord("A") + min(columns, _SHEET_COLUMNS) - 1) + str(min(rows, _SHEET_ROWS))
            cell_range = f"A1:{end}"
        left, top, right, bottom = sheet_bounds(cell_range)
        if right > columns or bottom > rows:
            raise WorkError("sheet_range_outside_grid")
        row_limit, column_limit = bottom - top + 1, right - left + 1
        values_result = await request("sheet_values", document_id=ref.token, block_id=sheet_id, sheet_range=cell_range)
        data = values_result.get("data")
        value_range = data.get("valueRange") if isinstance(data, dict) else None
        values = value_range.get("values", []) if isinstance(value_range, dict) else None
        if not isinstance(values, list) or any(not isinstance(row, list) for row in values):
            raise WorkError("sheet_values_invalid")
        returned_range = value_range.get("range")
        if returned_range is not None and returned_range not in (cell_range, f"{sheet_id}!{cell_range}"):
            raise WorkError("sheet_values_invalid")
        truncated |= len(values) > row_limit or any(len(row) > column_limit for row in values)
        title = sheet.get("title")
        title = " ".join(title.split())[:120] if isinstance(title, str) else sheet_id
        lines = []
        for number, row in enumerate(values[:row_limit], start=top):
            cells = []
            for cell in row[:column_limit]:
                value = "" if cell is None else str(cell) if isinstance(cell, (str, int, float, bool)) else "[复杂单元格]"
                truncated |= len(value) > 300
                cells.append(" ".join(value[:300].split()))
            if any(cells):
                lines.append(f"第{number}行\t" + "\t".join(cells))
        if lines:
            sections.append(f"工作表：{title}（{cell_range}）\n" + "\n".join(lines))
    text = "\n\n".join(sections)
    if not text:
        raise WorkError("sheet_no_readable_text")
    return "Work 在线表格", text[:_TEXT_LIMIT], truncated or len(text) > _TEXT_LIMIT


def file_error_explanation(stage: str, http_status: int | None) -> str:
    if stage == "drive_file_download" and http_status in {403, 404}:
        return "；Work 未允许下载该文件。能在页面预览不一定有下载权限，请检查文件访问和下载设置。"
    return {
        "file_too_large": "；文件超过当前读取大小上限。请缩小文件后重试。",
        "file_type_unsupported": "；云盘读取目前支持 DOCX、XLSX 和 PDF。",
        "file_invalid": "；文件格式异常或超出安全解析范围。",
        "file_encrypted": "；文件已加密，请提供您有权限使用的可读取版本。",
        "file_no_readable_text": "；未提取到可读文本。若是扫描件，请核对企业扫描识别配置或发送带文字层的版本。",
        "file_ocr_failed": "；扫描件识别暂未成功，请重试或发送带文字层的版本。",
        "sheet_no_readable_text": "；所读范围没有可读取的数据。",
        "sheet_not_found": "；链接指定的工作表不存在或当前账号不可访问。",
        "sheet_target_required": "；文件包含多个可见工作表。请打开目标工作表，复制包含该工作表的链接，并重新发送读取范围。",
        "sheet_range_outside_grid": "；指定范围超出工作表大小，请核对工作表与单元格范围。",
        "bitable_target_required": "；这个视图链接缺少数据表信息。请打开目标数据表的视图，复制完整链接后重新发送。",
        "bitable_table_not_found": "；指定的数据表不存在或当前账号不可访问，请核对链接。",
        "bitable_table_scan_limit": "；多维表格的目录超出本次读取范围，请核对目标表或联系管理员。",
        "bitable_no_readable_text": "；指定的数据表或视图中没有可读取的记录，请检查视图与数据访问权限。",
    }.get(stage, "")
