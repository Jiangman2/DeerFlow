import pytest

from app.gateway.file_security import (
    UploadSecurityError,
    pdf_payload_has_active_content,
    validate_pdf_payload,
    validate_upload_extension,
)


@pytest.mark.parametrize(
    "filename",
    [
        "probe.HTML",
        "probe.php",
        "probe.jsp",
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
