"""Declarative local processing. No scripts, expressions, shell or network I/O.

Only current-thread regular files are opened, through no-follow directory FDs.
New output directories never overwrite Skill-owned or user-provided artifacts.
"""

import csv
import io
import json
import math
import os
import re
import stat
import threading
import uuid
import zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Literal
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime

MAX_BYTES = 8 * 1024 * 1024
MAX_CELLS = 100000
_slots = threading.BoundedSemaphore(2)


class LocalDataJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str | None = Field(default=None, max_length=512)
    columns: list[str] = Field(default_factory=list, max_length=64)
    rows: list[list[str | int | float | None]] = Field(default_factory=list, max_length=20000)
    group_by: str | None = Field(default=None, max_length=128)
    value_column: str | None = Field(default=None, max_length=128)
    aggregation: Literal["none", "sum", "mean", "count", "min", "max"] = "none"
    title: str = Field(default="数据分析", max_length=128)
    paragraphs: list[str] = Field(default_factory=list, max_length=40)
    formats: list[Literal["csv", "xlsx", "docx", "svg"]] = Field(default_factory=lambda: ["xlsx"], min_length=1, max_length=4)


def _text(value) -> str:
    result = "" if value is None else str(value)
    if len(result) > 8000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", result):
        raise ValueError("cell_or_paragraph_too_large_or_invalid")
    return result


def _bounded_table(columns, rows):
    if not isinstance(columns, list) or not isinstance(rows, list) or any(not isinstance(c, str) for c in columns):
        raise ValueError("table_requires_column_and_row_arrays")
    if len(columns) > 64 or len(rows) > 20000 or len(rows) * max(1, len(columns)) > MAX_CELLS:
        raise ValueError("table_limit_exceeded")
    columns = [_text(c) for c in columns]
    if len(set(columns)) != len(columns) or any(not c for c in columns):
        raise ValueError("column_names_must_be_unique")
    if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
        raise ValueError("row_width_mismatch")
    for row in rows:
        for value in row:
            if value is not None and type(value) not in (str, int, float):
                raise ValueError("scalar_cell_value_required")
            _text(value)
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("finite_numeric_value_required")
    return columns, rows


def _read_at(root: Path, virtual: str) -> bytes:
    path = PurePosixPath(virtual)
    parts = path.parts
    if parts[:3] != ("/", "mnt", "user-data") or len(parts) < 5 or parts[3] not in {"workspace", "uploads", "outputs"} or any(p in {".", ".."} or "\\" in p for p in parts[3:]):
        raise ValueError("current_thread_file_required")
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[3:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            info = os.fstat(file_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise ValueError("regular_bounded_single_link_file_required")
            with os.fdopen(file_fd, "rb", closefd=False) as stream:
                data = stream.read(MAX_BYTES + 1)
            after = os.fstat(file_fd)
            if len(data) > MAX_BYTES or (info.st_size, info.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("source_changed_or_too_large")
            return data
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def _xlsx(data):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > 32 * 1024 * 1024 or any(i.file_size > MAX_BYTES for i in infos):
            raise ValueError("xlsx_expansion_limit")

        def xml(name):
            raw = archive.read(name).decode("utf-8-sig")
            if "<!DOCTYPE" in raw.upper() or "<!ENTITY" in raw.upper() or "\x00" in raw:
                raise ValueError("xml_entities_not_allowed")
            return ET.fromstring(raw)

        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared = ["".join(x.itertext()) for x in xml("xl/sharedStrings.xml").findall("s:si", ns)]
        # Fixed first worksheet; external links, macros and formula evaluation
        # are never loaded. Missing formula caches require a saved-value file.
        rows = []
        count = 0
        for row in xml("xl/worksheets/sheet1.xml").findall(".//s:sheetData/s:row", ns):
            values = []
            for cell in row.findall("s:c", ns):
                count += 1
                if count > MAX_CELLS:
                    raise ValueError("table_limit_exceeded")
                match = re.fullmatch(r"([A-Z]{1,2})[0-9]+", cell.get("r", ""))
                if not match:
                    raise ValueError("invalid_cell_reference")
                col = 0
                for char in match[1]:
                    col = col * 26 + ord(char) - 64
                if col > 64:
                    raise ValueError("column_limit_exceeded")
                while len(values) < col:
                    values.append("")
                text = cell.findtext("s:v", default="", namespaces=ns)
                if cell.get("t") == "s":
                    text = shared[int(text)]
                elif cell.get("t") == "inlineStr":
                    text = "".join(cell.find("s:is", ns).itertext())
                elif cell.find("s:f", ns) is not None and not text:
                    raise ValueError("formula_requires_cached_value")
                values[col - 1] = text
            rows.append(values)
        if not rows:
            raise ValueError("empty_worksheet")
        width = max(map(len, rows))
        rows = [row + [""] * (width - len(row)) for row in rows]
        return rows[0], rows[1:]


def _load(root, job):
    if not job.source_path:
        return _bounded_table(job.columns, job.rows)
    if job.rows or job.columns:
        raise ValueError("choose_file_or_inline_table")
    data = _read_at(root, job.source_path)
    suffix = PurePosixPath(job.source_path).suffix.lower()
    if suffix == ".csv":
        rows = []
        for row in csv.reader(io.StringIO(data.decode("utf-8-sig"))):
            if len(rows) >= 20001 or len(row) > 64:
                raise ValueError("table_limit_exceeded")
            rows.append(row)
        if not rows:
            raise ValueError("empty_csv")
        return _bounded_table(rows[0], rows[1:])
    if suffix == ".json":
        value = json.loads(data)
        if not isinstance(value, dict) or set(value) != {"columns", "rows"}:
            raise ValueError("json_requires_columns_and_rows")
        return _bounded_table(value["columns"], value["rows"])
    if suffix == ".xlsx":
        return _bounded_table(*_xlsx(data))
    raise ValueError("use_csv_xlsx_or_json_table")


def _number(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("finite_numeric_value_required")
    return result


def _aggregate(columns, rows, job):
    if job.aggregation == "none":
        return columns, rows
    group = columns.index(job.group_by) if job.group_by else None
    value = columns.index(job.value_column) if job.aggregation != "count" else None
    groups = defaultdict(list)
    for row in rows:
        groups[_text(row[group]) if group is not None else "合计"].append(_number(row[value]) if value is not None else 1)
    operations = {"sum": sum, "count": len, "mean": lambda xs: sum(xs) / len(xs), "min": min, "max": max}
    result = [[key, operations[job.aggregation](values)] for key, values in groups.items()]
    for _, number in result:
        _number(number)
    return [job.group_by or "项目", job.aggregation], result


def _package(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _export(columns, rows, job, kind):
    def x(value):
        return escape(_text(value))

    if kind == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        for row in [columns, *rows]:
            writer.writerow(["'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")) else v for v in row])
        return output.getvalue().encode("utf-8-sig")
    if kind == "svg":
        if len(columns) < 2 or len(rows) > 100 or not rows:
            raise ValueError("chart_requires_two_columns_and_1_to_100_rows")
        numbers = [_number(row[1]) for row in rows]
        maximum = max(abs(v) for v in numbers) or 1
        bars = []
        for i, row in enumerate(rows):
            y = 80 + i * 36
            width = abs(numbers[i]) / maximum * 210
            left = 510 - width if numbers[i] < 0 else 510
            bars.append(
                f'<text x="16" y="{y + 18}" font-size="14">{x(str(row[0])[:24])}</text>'
                f'<rect x="{left:.2f}" y="{y}" width="{width:.2f}" height="24" fill="#1675ad"/>'
                f'<line x1="510" x2="510" y1="{y}" y2="{y + 24}" stroke="#789"/>'
                f'<text x="750" y="{y + 18}" font-size="14">{x(row[1])}</text>'
            )
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{110 + len(rows) * 36}"><rect width="100%" height="100%" fill="white"/><text x="16" y="38" font-size="24">{x(job.title)}</text>{"".join(bars)}</svg>').encode()
    content_types = (
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>{}</Types>'
    )
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="{}"/></Relationships>'
    if kind == "docx":
        if len(rows) > 1000:
            raise ValueError("word_table_limit_1000_rows_use_aggregation_or_excel")

        def paragraph(value):
            return f'<w:p><w:r><w:rPr><w:color w:val="000000"/></w:rPr><w:t xml:space="preserve">{x(value)}</w:t></w:r></w:p>'

        table = (
            ""
            if not columns
            else '<w:tbl><w:tblPr><w:tblBorders><w:bottom w:val="single"/><w:insideH w:val="single"/></w:tblBorders></w:tblPr>'
            + "".join("<w:tr>" + "".join("<w:tc>" + paragraph(v) + "</w:tc>" for v in row) + "</w:tr>" for row in [columns, *rows])
            + "</w:tbl>"
        )
        document = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + paragraph(job.title) + "".join(paragraph(p) for p in job.paragraphs) + table + "<w:sectPr/></w:body></w:document>"
        return _package(
            {
                "[Content_Types].xml": content_types.format('<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'),
                "_rels/.rels": rels.format("word/document.xml"),
                "word/document.xml": document,
            }
        )
    sheet = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for r, row in enumerate([columns, *rows], 1):
        sheet += f'<row r="{r}">'
        for c, value in enumerate(row, 1):
            column = (chr(64 + (c - 1) // 26) if c > 26 else "") + chr(65 + (c - 1) % 26)
            if isinstance(value, (int, float)) and math.isfinite(value):
                sheet += f'<c r="{column}{r}"><v>{value}</v></c>'
            else:
                sheet += f'<c r="{column}{r}" t="inlineStr"><is><t xml:space="preserve">{x(value)}</t></is></c>'
        sheet += "</row>"
    sheet += "</sheetData></worksheet>"
    return _package(
        {
            "[Content_Types].xml": content_types.format(
                '<Override PartName="/xl/workbook.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                '<Override PartName="/xl/worksheets/sheet1.xml" '
                'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            ),
            "_rels/.rels": rels.format("xl/workbook.xml"),
            "xl/workbook.xml": (
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="分析结果" sheetId="1" r:id="rId1"/></sheets></workbook>'
            ),
            "xl/_rels/workbook.xml.rels": (
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
                'Target="worksheets/sheet1.xml"/></Relationships>'
            ),
            "xl/worksheets/sheet1.xml": sheet,
        }
    )


def process_job(root: Path, job: LocalDataJob):
    if os.name != "posix":
        raise ValueError("local_processing_requires_posix_file_guards")
    if len(job.model_dump_json().encode()) > 512 * 1024:
        raise ValueError("inline_input_limit_use_a_file")
    columns, rows = _aggregate(*_load(root, job), job)
    outputs = {kind: _export(columns, rows, job, kind) for kind in set(job.formats)}
    if sum(map(len, outputs.values())) > 32 * 1024 * 1024:
        raise ValueError("output_limit_exceeded")
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    workspace_fd = None
    directory_fd = None
    directory = "local-processing-" + uuid.uuid4().hex
    try:
        workspace_fd = os.open("workspace", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        os.mkdir(directory, mode=0o700, dir_fd=workspace_fd)
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=workspace_fd)
        for kind, payload in outputs.items():
            fd = os.open("分析结果." + kind, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory_fd)
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
    finally:
        for fd in (directory_fd, workspace_fd, root_fd):
            if fd is not None:
                os.close(fd)
    return {"status": "local_processing_complete", "rows": len(rows), "files": [f"/mnt/user-data/workspace/{directory}/分析结果.{kind}" for kind in outputs], "next_step": "Use present_files for authorized delivery and Office previews."}


@tool("process_local_data", parse_docstring=True)
def process_local_data(runtime: Runtime, job: LocalDataJob) -> str:
    """Process current-thread data without scripts or network access.

    Read CSV, the first XLSX worksheet, or JSON {columns,rows}; alternatively
    supply a bounded inline table. Aggregate sum/mean/count/min/max by a column.
    Export CSV/XLSX, a black-text Word report, or a bar chart SVG. Use paragraphs
    for report prose and present_files for final delivery. No Python expressions,
    shell commands, URLs, templates or external resources are accepted.

    Args:
        job: Declarative input table, aggregation and output formats. source_path
            must be in this thread's /mnt/user-data/uploads, workspace or outputs.
    """
    if not _slots.acquire(blocking=False):
        return json.dumps({"error": "local_processing_busy", "retryable": True})
    try:
        context = runtime.context or {}
        thread_id = context.get("thread_id") or (getattr(runtime, "config", None) or {}).get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ValueError("thread_identity_required")
        root = get_paths().sandbox_user_data_dir(thread_id, user_id=resolve_runtime_user_id(runtime))
        return json.dumps(process_job(root, job), ensure_ascii=False)
    except Exception as error:
        # No raw filesystem paths, document content or credentials in errors.
        code = str(error) if type(error) is ValueError and re.fullmatch(r"[a-z0-9_]+", str(error)) else "local_processing_invalid_input_or_unavailable"
        return json.dumps({"error": code, "retryable": False, "next_step": "Correct the input or use a smaller current-thread CSV/JSON table; do not retry the same request unchanged."})
    finally:
        _slots.release()
