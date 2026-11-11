import asyncio
from io import BytesIO
from unittest.mock import AsyncMock
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook
from reportlab.lib.pdfencrypt import StandardEncryption
from reportlab.pdfgen import canvas

from app.gateway import upload_document_parser
from app.gateway.file_security import UploadSecurityError
from app.integrations.work import file_content
from app.integrations.work.client import WorkError


def _workbook() -> bytes:
    output = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "季度预算"
    sheet.append(["项目", None, "金额"])
    sheet.append(["研发", "=10+12", 42])
    workbook.create_sheet("明细").append(["办公"])
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _pdf(*pages: str, encrypted: bool = False) -> bytes:
    output = BytesIO()
    password = StandardEncryption("private-password") if encrypted else None
    document = canvas.Canvas(output, encrypt=password)
    for page in pages:
        if page:
            document.drawString(40, 750, page)
        document.showPage()
    document.save()
    return output.getvalue()


def _zip(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, mode="w", compression=ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return output.getvalue()


def _docx(body: str, *, extras: dict[str, bytes] | None = None) -> bytes:
    types = b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    document = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + "</w:body></w:document>").encode()
    return _zip({"[Content_Types].xml": types, "word/document.xml": document, **(extras or {})})


@pytest.fixture(autouse=True)
def _disable_real_ocr(monkeypatch):
    monkeypatch.setattr(upload_document_parser, "conversation_document_parser_endpoint", lambda: None)
    monkeypatch.setattr(upload_document_parser, "parse_conversation_document", AsyncMock(side_effect=AssertionError("Unexpected OCR call")))


@pytest.mark.asyncio
async def test_xlsx_extracts_cells_and_ignores_uncached_formulas():
    result = await file_content.parse_downloaded_file(_workbook(), filename="folder\\预算\x00.xlsx", content_type="application/octet-stream")

    assert result.title == "预算.xlsx"
    assert '工作表 "季度预算"' in result.text
    assert '["项目", "", "金额"]' in result.text
    assert '["研发", "", "42"]' in result.text
    assert '工作表 "明细"' in result.text
    assert "=10+12" not in result.text
    assert not result.truncated


@pytest.mark.asyncio
async def test_xlsx_reads_workbooks_with_known_column_width_extension():
    with ZipFile(BytesIO(_workbook())) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    sheet = "xl/worksheets/sheet1.xml"
    marker = b"<sheetFormatPr "
    assert marker in members[sheet]
    members[sheet] = members[sheet].replace(marker, marker + b'defaultColWidthPt="64" ', 1)

    result = await file_content.parse_downloaded_file(_zip(members))

    assert '["研发", "", "42"]' in result.text


@pytest.mark.asyncio
async def test_xlsx_discloses_sheet_row_and_column_limits(monkeypatch):
    monkeypatch.setattr(file_content, "_MAX_XLSX_SHEETS", 1)
    monkeypatch.setattr(file_content, "_MAX_XLSX_ROWS", 1)
    monkeypatch.setattr(file_content, "_MAX_XLSX_COLUMNS", 2)

    result = await file_content.parse_downloaded_file(_workbook())

    assert result.truncated
    assert "项目" in result.text
    assert all(value not in result.text for value in ("金额", "研发", "明细"))


@pytest.mark.asyncio
async def test_xlsx_rejects_expanded_size_above_limit(monkeypatch):
    monkeypatch.setattr(file_content, "_MAX_OFFICE_EXPANDED_BYTES", 8192)
    compressed = _zip({"xl/sharedStrings.xml": b"x" * 32_768})
    assert len(compressed) < 8192

    with pytest.raises(WorkError, match="file_too_large"):
        await file_content.parse_downloaded_file(compressed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entries", "stage"),
    [
        ({"xl/vbaProject.bin": b"fake macro"}, "file_type_unsupported"),
        ({"xl/worksheets/sheet1.xml": "<!DOCTYPE foo><foo/>".encode("utf-16")}, "file_invalid"),
        ({"../../workbook.xml": b"<workbook/>"}, "file_invalid"),
        ({"ordinary.txt": b"not a workbook"}, "file_type_unsupported"),
    ],
)
async def test_xlsx_rejects_unsupported_and_unsafe_archives(entries, stage):
    with pytest.raises(WorkError, match=stage):
        await file_content.parse_downloaded_file(_zip(entries), filename="renamed.xlsx")


@pytest.mark.asyncio
async def test_docx_extracts_body_and_table_text_without_embedded_or_external_parts():
    payload = _docx(
        "<w:p><w:r><w:t>季度预算</w:t><w:tab/><w:t>42</w:t></w:r>"
        "<w:r><w:rPr><w:vanish/></w:rPr><w:t>hidden</w:t></w:r><w:del><w:r><w:delText>deleted</w:delText></w:r></w:del></w:p>"
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>研发支出</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
        "<w:p><w:r><w:instrText>external-command</w:instrText></w:r></w:p><w:altChunk/>",
        extras={
            "word/_rels/document.xml.rels": b'<Relationships><Relationship TargetMode="External" Target="https://example.test/not-fetched"/></Relationships>',
            "word/header1.xml": b"<header>outside-body</header>",
        },
    )
    result = await file_content.parse_downloaded_file(payload, filename="folder\\测试.docx", content_type="application/octet-stream")
    assert result.title == "测试.docx"
    assert result.text == "季度预算\t42\n研发支出"
    assert not result.truncated
    upload_document_parser.parse_conversation_document.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("extras", "stage"),
    [
        ({"word/vbaProject.bin": b"not a macro"}, "file_type_unsupported"),
        ({"word/document.xml": "<!DOCTYPE secret><secret/>".encode("utf-16")}, "file_invalid"),
        ({"word/document.xml": b"<not-word/>"}, "file_invalid"),
    ],
)
async def test_docx_rejects_active_or_malformed_packages(extras, stage):
    with pytest.raises(WorkError, match=stage):
        await file_content.parse_downloaded_file(_docx("", extras=extras))


@pytest.mark.asyncio
async def test_docx_text_limits_and_empty_body(monkeypatch):
    monkeypatch.setattr(file_content, "_MAX_TEXT_CHARS", 20)
    payload = _docx("<w:p><w:r><w:t>" + "研" * 60 + "</w:t></w:r></w:p>")
    result = await file_content.parse_downloaded_file(payload)
    assert result.text == "研" * 20 and result.truncated
    with pytest.raises(WorkError, match="file_no_readable_text"):
        await file_content.parse_downloaded_file(_docx("<w:p><w:r><w:drawing/></w:r></w:p>"))
    monkeypatch.setattr(file_content, "_MAX_DOCX_DOCUMENT_BYTES", 100)
    with pytest.raises(WorkError, match="file_too_large"):
        await file_content.parse_downloaded_file(payload)
    upload_document_parser.parse_conversation_document.assert_not_called()


@pytest.mark.asyncio
async def test_pdf_extracts_only_text_and_indicates_unreadable_pages():
    result = await file_content.parse_downloaded_file(_pdf("Quarterly budget 42", "", "Third page"), filename="budget.pdf", content_type="text/html")

    assert result.title == "budget.pdf"
    assert "第 1 页\nQuarterly budget 42" in result.text
    assert "第 3 页\nThird page" in result.text
    assert result.truncated


@pytest.mark.asyncio
async def test_pdf_page_limit_sets_truncated(monkeypatch):
    monkeypatch.setattr(file_content, "_MAX_PDF_PAGES", 1)

    result = await file_content.parse_downloaded_file(_pdf("First", "Second"))

    assert "First" in result.text
    assert "Second" not in result.text
    assert result.truncated


@pytest.mark.asyncio
async def test_textless_pdf_uses_only_configured_ocr_with_a_fixed_name_and_bounded_result(monkeypatch):
    endpoint = "http://configured-parser/file_parse"
    parser = AsyncMock(return_value="识别文本" * 6_000)
    monkeypatch.setattr(upload_document_parser, "conversation_document_parser_endpoint", lambda: endpoint)
    monkeypatch.setattr(upload_document_parser, "parse_conversation_document", parser)
    payload = _pdf("")

    result = await file_content.parse_downloaded_file(payload, filename="Secret original title.pdf")

    parser.assert_awaited_once_with(endpoint=endpoint, filename="work-document.pdf", content=payload)
    assert result.title == "Secret original title.pdf"
    assert result.text.startswith("识别文本")
    assert len(result.text) == file_content._MAX_TEXT_CHARS
    assert result.truncated


@pytest.mark.asyncio
async def test_image_only_ocr_output_is_not_reported_as_readable_text(monkeypatch):
    monkeypatch.setattr(upload_document_parser, "conversation_document_parser_endpoint", lambda: "http://configured-parser/file_parse")
    monkeypatch.setattr(upload_document_parser, "parse_conversation_document", AsyncMock(return_value="[文档图像：未命名图像]\n\n[文档图像：第二页]"))

    with pytest.raises(WorkError, match="file_no_readable_text"):
        await file_content.parse_downloaded_file(_pdf(""))


@pytest.mark.asyncio
async def test_readable_or_oversize_pdf_never_goes_to_ocr(monkeypatch):
    monkeypatch.setattr(upload_document_parser, "conversation_document_parser_endpoint", lambda: "http://configured-parser/file_parse")
    monkeypatch.setattr(file_content, "_MAX_PDF_PAGES", 1)

    result = await file_content.parse_downloaded_file(_pdf("Native text", ""))
    assert "Native text" in result.text
    with pytest.raises(WorkError, match="file_too_large"):
        await file_content.parse_downloaded_file(_pdf("", ""))

    upload_document_parser.parse_conversation_document.assert_not_called()


@pytest.mark.asyncio
async def test_pdf_security_validation_precedes_ocr(monkeypatch):
    from app.gateway import file_security

    def reject_active_pdf(_payload):
        raise UploadSecurityError(422, "private data")

    monkeypatch.setattr(upload_document_parser, "conversation_document_parser_endpoint", lambda: "http://configured-parser/file_parse")
    monkeypatch.setattr(file_security, "validate_pdf_payload", reject_active_pdf)

    with pytest.raises(WorkError, match="file_invalid"):
        await file_content.parse_downloaded_file(_pdf(""))

    upload_document_parser.parse_conversation_document.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [False, True])
async def test_ocr_errors_do_not_reveal_parser_diagnostics(monkeypatch, timeout):
    async def parse(**_kwargs):
        if timeout:
            await asyncio.sleep(5)
        raise upload_document_parser.UploadDocumentParseError("private data")

    monkeypatch.setattr(upload_document_parser, "conversation_document_parser_endpoint", lambda: "http://configured-parser/file_parse")
    monkeypatch.setattr(upload_document_parser, "parse_conversation_document", parse)
    monkeypatch.setattr(file_content, "_PDF_OCR_TIMEOUT_SECONDS", 0.001)

    with pytest.raises(WorkError, match="^Work operation failed at file_ocr_failed$"):
        await file_content.parse_downloaded_file(_pdf(""))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "stage"),
    [
        (b"<html>invalid file</html>", "file_type_unsupported"),
        (b"%PDF-1.4\nPrivate data", "file_invalid"),
        (_pdf(""), "file_no_readable_text"),
        (_pdf("Secret", encrypted=True), "file_encrypted"),
    ],
)
async def test_file_errors_expose_only_fixed_stage(payload, stage):
    with pytest.raises(WorkError) as error:
        await file_content.parse_downloaded_file(payload, filename="Internal budget.pdf", content_type="application/pdf")

    assert error.value.stage == stage
    assert str(error.value) == f"Work operation failed at {stage}"


@pytest.mark.asyncio
async def test_download_byte_limit_precedes_parsing(monkeypatch):
    monkeypatch.setattr(file_content, "MAX_FILE_BYTES", 2)

    with pytest.raises(WorkError, match="file_too_large"):
        await file_content.parse_downloaded_file(b"abc")
