"""Real compressed PDFs exercise the native expansion and security boundary."""

import asyncio
import io
import struct
import zlib

import pytest
from _router_auth_helpers import call_unwrapped
from fastapi import HTTPException
from reportlab.pdfgen import canvas
from starlette.requests import Request

from app.gateway.file_security import pdf_file_requires_attachment, pdf_payload_findings
from app.gateway.routers import artifacts


def compressed_pdf(catalog_extra: bytes = b"") -> bytes:
    # A self-contained PDF 1.7: Catalog, Pages, Page and Font are in an ObjStm.
    # Its xref stream points to those compressed objects as a normal writer does.
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R " + catalog_extra + b" >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    index = b""
    bodies = b""
    for number, body in objects.items():
        index += f"{number} {len(bodies)} ".encode()
        bodies += body + b"\n"
    packed = zlib.compress(index + bodies)
    content = b"BT /F1 18 Tf 25 100 Td (Compressed PDF preview) Tj ET"
    pdf = b"%PDF-1.7\n"
    offset4 = len(pdf)
    pdf += b"4 0 obj\n<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream\nendobj\n"
    offset6 = len(pdf)
    pdf += b"6 0 obj\n<< /Type /ObjStm /N 4 /First " + str(len(index)).encode() + b" /Filter /FlateDecode /Length " + str(len(packed)).encode() + b" >>\nstream\n" + packed + b"\nendstream\nendobj\n"
    offset7 = len(pdf)
    entries = [(0, 0, 65535), (2, 6, 0), (2, 6, 1), (2, 6, 2), (1, offset4, 0), (2, 6, 3), (1, offset6, 0), (1, offset7, 0)]
    xref = b"".join(struct.pack(">BIH", *entry) for entry in entries)
    pdf += b"7 0 obj\n<< /Type /XRef /Size 8 /Root 1 0 R /W [1 4 2] /Length " + str(len(xref)).encode() + b" >>\nstream\n" + xref + b"\nendstream\nendobj\nstartxref\n" + str(offset7).encode() + b"\n%%EOF\n"
    return pdf


@pytest.mark.parametrize("page", [None, 0])
def test_compressed_output_preview_renders_without_changing_original(tmp_path, monkeypatch, page):
    path = tmp_path / "quote.pdf"
    original = compressed_pdf()
    path.write_bytes(original)
    assert pdf_payload_findings(original) == ("compressed object streams",)
    monkeypatch.setattr(artifacts, "resolve_thread_virtual_path", lambda *_args, **_kwargs: path)
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    response = asyncio.run(call_unwrapped(artifacts.get_pdf_artifact_preview, "thread-1", "mnt/user-data/outputs/quote.pdf", request, page=page))
    assert response.status_code == 200
    if page is None:
        body = bytes(response.body).decode()
        assert "data:image/png;base64," in body
        assert 'http-equiv="Content-Security-Policy"' in body
        assert "img-src data:" in body
    else:
        assert response.media_type == "image/png"
        assert bytes(response.body).startswith(b"\x89PNG\r\n\x1a\n")
    assert path.read_bytes() == original
    # Preview eligibility does not grant inline delivery of original bytes.
    assert pdf_file_requires_attachment(path)


@pytest.mark.parametrize(
    "hidden",
    [
        b"/OpenAction << /S /JavaScript /JS (app.alert\\(1\\)) >>",
        b"/OpenAction << /S /Launch /F (app.exe) >>",
        b"/AA << /WC << /S /JavaScript /JS (app.alert\\(1\\)) >> >>",
        b"/Names << /EmbeddedFiles << /Names [] >> >>",
        b"/AcroForm << /XFA (form data) >>",
    ],
)
def test_compressed_active_objects_stay_blocked(tmp_path, hidden):
    path = tmp_path / "unsafe.pdf"
    payload = compressed_pdf(hidden)
    assert pdf_payload_findings(payload) == ("compressed object streams",)
    path.write_bytes(payload)
    with pytest.raises(HTTPException) as error:
        artifacts._validate_pdf_preview_artifact(path, "mnt/user-data/outputs/unsafe.pdf")
    assert error.value.status_code == 400
    assert path.read_bytes() == payload


def test_encrypted_output_stays_blocked(tmp_path):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, encrypt="secret")
    pdf.drawString(10, 10, "Encrypted")
    pdf.save()
    path = tmp_path / "encrypted.pdf"
    path.write_bytes(buffer.getvalue())
    with pytest.raises(HTTPException) as error:
        artifacts._validate_pdf_preview_artifact(path, "mnt/user-data/outputs/encrypted.pdf")
    assert error.value.status_code == 400


def test_compressed_upload_is_not_promoted_to_output_preview(tmp_path):
    path = tmp_path / "uploaded.pdf"
    path.write_bytes(compressed_pdf())
    with pytest.raises(HTTPException) as error:
        artifacts._validate_pdf_preview_artifact(path, "mnt/user-data/uploads/uploaded.pdf")
    assert error.value.status_code == 400


def test_expanded_pdf_size_is_bounded():
    payload = compressed_pdf()
    expanded = artifacts._normalize_compressed_pdf_preview(payload)
    assert len(expanded) > len(payload)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(artifacts, "MAX_PDF_PREVIEW_BYTES", len(payload) + 1)
        with pytest.raises(HTTPException) as error:
            artifacts._normalize_compressed_pdf_preview(payload)
    assert error.value.status_code == 413


def test_invalid_compressed_pdf_fails_closed():
    with pytest.raises(HTTPException) as error:
        artifacts._normalize_compressed_pdf_preview(b"%PDF-1.7\n/ObjStm\n%%EOF")
    assert error.value.status_code == 422
