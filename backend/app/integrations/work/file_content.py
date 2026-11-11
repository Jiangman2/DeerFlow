"""Extract bounded text from the bytes of an authorized Work drive download."""

import asyncio
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from unicodedata import category
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

from app.integrations.work.client import WorkError

MAX_FILE_BYTES = 12 * 1024 * 1024
_MAX_OFFICE_ENTRIES = 512
_MAX_OFFICE_EXPANDED_BYTES = 32 * 1024 * 1024
_MAX_DOCX_DOCUMENT_BYTES = 4 * 1024 * 1024
_MAX_XLSX_SHEETS = 8
_MAX_XLSX_ROWS = 100
_MAX_XLSX_COLUMNS = 24
_MAX_CELL_CHARS = 160
_MAX_PDF_PAGES = 40
_MAX_TEXT_CHARS = 20_000
_PDF_OCR_TIMEOUT_SECONDS = 90
_XLSX_CONTENT_TYPE = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_DOCX_CONTENT_TYPE = b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
_WORD_NAMESPACES = ("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}", "{http://purl.oclc.org/ooxml/wordprocessingml/main}")


@dataclass(frozen=True, slots=True)
class ParsedWorkFile:
    title: str
    text: str
    truncated: bool = False


def _display_title(filename: str, fallback: str) -> str:
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(char for char in name if not category(char).startswith("C"))[:120].strip() or fallback


def _office_members(archive: ZipFile) -> tuple[dict[str, ZipInfo], bytes]:
    members = archive.infolist()
    if len(members) > _MAX_OFFICE_ENTRIES or sum(member.file_size for member in members) > _MAX_OFFICE_EXPANDED_BYTES:
        raise WorkError("file_too_large")

    by_name = {}
    content_types = b""
    for member in members:
        name = member.filename.casefold()
        path = PurePosixPath(name)
        if name in by_name or path.is_absolute() or ".." in path.parts or "\\" in name:
            raise WorkError("file_invalid")
        if member.flag_bits & 1:
            raise WorkError("file_encrypted")
        if member.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
            raise WorkError("file_type_unsupported")
        if "vbaproject" in name or name.startswith(("xl/activex/", "xl/embeddings/", "word/activex/", "word/embeddings/")):
            raise WorkError("file_type_unsupported")
        if name == "word/document.xml" and member.file_size > _MAX_DOCX_DOCUMENT_BYTES:
            raise WorkError("file_too_large")
        by_name[name] = member
        if not name.endswith((".xml", ".rels")):
            continue
        # Normalize UTF-16/32 NUL padding before checking declarations.
        xml = archive.read(member).replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
            raise WorkError("file_invalid")
        if name == "[content_types].xml":
            if b"MACROENABLED" in xml or b"VBAPROJECT" in xml:
                raise WorkError("file_type_unsupported")
            content_types = xml
    return by_name, content_types


def _word_text_parts(node, ns):
    """Extract body text without resolving relationships or opening embedded objects."""
    tag = node.tag.removeprefix(ns) if node.tag.startswith(ns) else ""
    if not tag or tag in {"del", "moveFrom", "drawing", "pict", "object", "altChunk", "pPr", "rPr", "tblPr", "tcPr", "trPr", "sectPr"}:
        return
    if tag == "r":
        style = node.find(ns + "rPr")
        if style is not None:
            hidden = style.find(ns + "vanish")
            if hidden is not None and hidden.attrib.get(ns + "val", "true") not in {"0", "false", "off"}:
                return
    if tag == "t":
        yield node.text or ""
    elif tag in {"br", "cr", "tab"}:
        yield "\t" if tag == "tab" else "\n"
    else:
        for child in node:
            yield from _word_text_parts(child, ns)
        if tag == "p":
            yield "\n"


def _docx_text(archive: ZipFile, member: ZipInfo) -> tuple[str, bool]:
    root = ElementTree.fromstring(archive.read(member))
    namespace = next((ns for ns in _WORD_NAMESPACES if root.tag == ns + "document"), "")
    body = root.find(namespace + "body") if namespace else None
    if body is None:
        raise WorkError("file_invalid")
    parts = []
    remaining = _MAX_TEXT_CHARS
    for part in _word_text_parts(body, namespace):
        if len(part) > remaining:
            parts.append(part[:remaining])
            return "".join(parts).strip(), True
        parts.append(part)
        remaining -= len(part)
    return "".join(parts).strip(), False


def _xlsx_text(content: bytes) -> tuple[str, bool]:
    from openpyxl import load_workbook

    from deerflow.utils.file_conversion import _install_openpyxl_default_col_width_pt_compat

    _install_openpyxl_default_col_width_pt_compat()
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    blocks = []
    truncated = False
    try:
        sheets = workbook.worksheets
        truncated = len(sheets) > _MAX_XLSX_SHEETS
        for sheet in sheets[:_MAX_XLSX_SHEETS]:
            if sheet.max_row is None or sheet.max_column is None or sheet.max_row > _MAX_XLSX_ROWS or sheet.max_column > _MAX_XLSX_COLUMNS:
                truncated = True
            lines = []
            for row_number, row in enumerate(
                sheet.iter_rows(max_row=_MAX_XLSX_ROWS + 1, max_col=_MAX_XLSX_COLUMNS + 1, values_only=True),
                start=1,
            ):
                if any(value is not None for value in row[_MAX_XLSX_COLUMNS:]):
                    truncated = True
                if row_number > _MAX_XLSX_ROWS:
                    truncated |= any(value is not None for value in row)
                    break
                cells = [str(value) if value is not None else "" for value in row[:_MAX_XLSX_COLUMNS]]
                while cells and not cells[-1]:
                    cells.pop()
                if cells:
                    truncated |= any(len(cell) > _MAX_CELL_CHARS for cell in cells)
                    cells = [cell[:_MAX_CELL_CHARS] for cell in cells]
                    lines.append(f"第 {row_number} 行 {json.dumps(cells, ensure_ascii=False)}")
            if lines:
                sheet_title = json.dumps(sheet.title, ensure_ascii=False)
                blocks.append(f"工作表 {sheet_title}\n" + "\n".join(lines))
    finally:
        workbook.close()

    return "\n\n".join(blocks), truncated


def _pdf_text(content: bytes) -> tuple[str, bool]:
    import pypdfium2 as pdfium

    from app.gateway.file_security import PDFIUM_LOCK

    remaining = _MAX_TEXT_CHARS
    blocks = []
    with PDFIUM_LOCK:
        try:
            document = pdfium.PdfDocument(content)
        except pdfium.PdfiumError:
            if pdfium.raw.FPDF_GetLastError() == pdfium.raw.FPDF_ERR_PASSWORD:
                raise WorkError("file_encrypted") from None
            raise WorkError("file_invalid") from None
        try:
            if pdfium.raw.FPDF_GetSecurityHandlerRevision(document) != -1:
                raise WorkError("file_encrypted")
            page_count = len(document)
            truncated = page_count > _MAX_PDF_PAGES
            for index in range(min(page_count, _MAX_PDF_PAGES)):
                if remaining <= 0:
                    truncated = True
                    break
                page = document[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        count = text_page.count_chars()
                        text = text_page.get_text_range(index=0, count=min(count, remaining)).strip() if count else ""
                        truncated |= count > remaining
                    finally:
                        text_page.close()
                finally:
                    page.close()
                if text:
                    block = f"第 {index + 1} 页\n{text}"
                    remaining -= len(block) + 2
                    blocks.append(block)
                else:
                    # Scanned pages can coexist with searchable pages.
                    truncated = True
        finally:
            document.close()

    if not blocks and page_count > _MAX_PDF_PAGES:
        raise WorkError("file_too_large")
    return "\n\n".join(blocks), truncated


async def _ocr_textless_pdf(content: bytes) -> str:
    from app.gateway.file_security import UploadSecurityError, validate_pdf_payload
    from app.gateway.upload_document_parser import (
        UploadDocumentParseError,
        conversation_document_parser_endpoint,
        parse_conversation_document,
    )

    endpoint = conversation_document_parser_endpoint()
    if endpoint is None:
        raise WorkError("file_no_readable_text")
    try:
        await asyncio.to_thread(validate_pdf_payload, content)
    except UploadSecurityError:
        raise WorkError("file_invalid") from None
    try:
        async with asyncio.timeout(_PDF_OCR_TIMEOUT_SECONDS):
            return await parse_conversation_document(endpoint=endpoint, filename="work-document.pdf", content=content)
    except (TimeoutError, UploadDocumentParseError):
        raise WorkError("file_ocr_failed") from None


def _parse(content: bytes, filename: str) -> ParsedWorkFile:
    if len(content) > MAX_FILE_BYTES:
        raise WorkError("file_too_large")
    try:
        if content.startswith(b"%PDF-"):
            text, truncated = _pdf_text(content)
            fallback = "PDF 文件"
        elif content.startswith(b"PK\x03\x04"):
            with ZipFile(BytesIO(content)) as archive:
                members, content_types = _office_members(archive)
                xlsx = _XLSX_CONTENT_TYPE.upper() in content_types and "xl/workbook.xml" in members
                docx = _DOCX_CONTENT_TYPE.upper() in content_types and "word/document.xml" in members
                if xlsx and not docx:
                    text, truncated = _xlsx_text(content)
                    fallback = "Excel 工作簿"
                elif docx and not xlsx:
                    text, truncated = _docx_text(archive, members["word/document.xml"])
                    fallback = "Word 文档"
                else:
                    raise WorkError("file_type_unsupported")
            if not text:
                raise WorkError("file_no_readable_text")
        else:
            raise WorkError("file_type_unsupported")
    except WorkError:
        raise
    except Exception:
        # Parser errors may contain attacker-controlled file content.
        raise WorkError("file_invalid") from None
    return ParsedWorkFile(_display_title(filename, fallback), text[:_MAX_TEXT_CHARS], truncated or len(text) > _MAX_TEXT_CHARS)


async def parse_downloaded_file(content: bytes, *, filename: str = "", content_type: str = "") -> ParsedWorkFile:
    """Use file structure to select the parser, independent of Work download MIME metadata."""
    parsed = await asyncio.to_thread(_parse, content, filename)
    if parsed.text:
        return parsed
    text = await _ocr_textless_pdf(content)
    # The shared parser preserves image captions even when OCR found no text.
    if not re.sub(r"\[文档图像：[^\]]*\]", "", text).strip():
        raise WorkError("file_no_readable_text")
    return ParsedWorkFile(parsed.title, text[:_MAX_TEXT_CHARS], len(text) > _MAX_TEXT_CHARS)
