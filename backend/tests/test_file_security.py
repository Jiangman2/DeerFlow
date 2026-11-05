import io

import pytest
from reportlab.pdfgen import canvas

from app.gateway import file_security
from app.gateway.file_security import (
    UploadSecurityError,
    pdf_payload_findings,
    pdf_payload_has_active_content,
    validate_pdf_payload,
    validate_staged_upload,
    validate_upload_extension,
)


@pytest.mark.parametrize(
    "filename",
    [
        "probe.HTML",
        "probe.php",
        "probe.jsp",
        "probe.jspf",
        "probe.JSPF",
        "probe.JsPf",
        "probe.jspx",
        "probe.tag",
        "probe.TAG",
        "probe.tagf",
        "probe.TAGF",
        "probe.tagx",
        "probe.TAGX",
        "probe.py",
        "probe.svg",
        "probe.exe",
    ],
)
def test_validate_upload_extension_blocks_active_formats(filename: str) -> None:
    with pytest.raises(UploadSecurityError) as exc_info:
        validate_upload_extension(filename)

    assert exc_info.value.status_code == 415


@pytest.mark.parametrize("filename", ["report.pdf", "table.xlsx", "image.png", "notes.txt", "bundle.zip"])
def test_validate_upload_extension_allows_business_inputs(filename: str) -> None:
    validate_upload_extension(filename)


def test_pdf_scanner_detects_hex_escaped_javascript_name() -> None:
    payload = b"%PDF-1.7\n1 0 obj << /Type /Catalog /OpenAction 2 0 R >> endobj\n2 0 obj << /S /Java#53cript /J#53 (app.alert('probe')) >> endobj\n%%EOF\n"

    assert pdf_payload_has_active_content(payload) is True
    assert pdf_payload_findings(payload) == ("JavaScript", "open actions")


def test_pdf_scanner_ignores_action_like_bytes_in_ordinary_streams() -> None:
    payload = b"%PDF-1.7\n1 0 obj << /Length 17 >> stream\nimage-/JavaScript\nendstream\nendobj\n%%EOF\n"

    assert pdf_payload_findings(payload) == ()


def test_pdf_scanner_treats_compressed_object_streams_as_uninspectable() -> None:
    payload = b"%PDF-1.7\n1 0 obj << /Type /ObjStm /Length 4 >> stream\ndata\nendstream\nendobj\n%%EOF\n"

    assert pdf_payload_findings(payload) == ("compressed object streams",)


def test_pdf_scanner_rejects_encrypted_document_as_uninspectable() -> None:
    payload = b"%PDF-1.7\ntrailer << /Encrypt 5 0 R >>\n%%EOF\n"

    with pytest.raises(UploadSecurityError) as exc_info:
        validate_pdf_payload(payload)

    assert exc_info.value.status_code == 422


def test_pdf_scanner_accepts_inert_document() -> None:
    payload = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"

    validate_pdf_payload(payload)
    assert pdf_payload_has_active_content(payload) is False


def test_pdf_scanner_rejects_renamed_non_pdf() -> None:
    with pytest.raises(UploadSecurityError) as exc_info:
        validate_pdf_payload(b"<html><script>probe()</script></html>")

    assert exc_info.value.status_code == 415


def test_staged_pdf_sanitize_mode_replaces_cleanable_active_pdf(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "active.pdf"
    path.write_bytes(b"%PDF-1.7\n1 0 obj << /Type /Catalog /OpenAction 2 0 R >> endobj\n2 0 obj << /S /JavaScript /JS (app.alert('probe')) >> endobj\n%%EOF\n")
    inert = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
    monkeypatch.setenv("DEERFLOW_UPLOAD_PDF_SECURITY_MODE", "sanitize")
    monkeypatch.setattr(
        file_security,
        "_replace_with_sanitized_pdf",
        lambda target, _payload: target.write_bytes(inert),
    )

    result = validate_staged_upload(path, path.name)

    assert result.sanitized is True
    assert result.findings == ("JavaScript", "open actions")
    assert path.read_bytes() == inert


def test_staged_pdf_does_not_allow_unsafe_disabled_mode(tmp_path, monkeypatch) -> None:
    path = tmp_path / "active.pdf"
    path.write_bytes(b"%PDF-1.7\n1 0 obj << /S /JavaScript >> endobj\n%%EOF\n")
    monkeypatch.setenv("DEERFLOW_UPLOAD_PDF_SECURITY_MODE", "off")

    with pytest.raises(UploadSecurityError, match="JavaScript"):
        validate_staged_upload(path, path.name)


def test_pdf_sanitizer_produces_inert_renderable_pdf() -> None:
    source = io.BytesIO()
    document = canvas.Canvas(source)
    document.drawString(72, 720, "sanitizer probe")
    document.save()

    sanitized = file_security._sanitize_pdf_to_inert_bytes(source.getvalue())

    assert sanitized.startswith(b"%PDF-")
    validate_pdf_payload(sanitized)
