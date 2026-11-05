"""Security policy for files entering or leaving the Gateway."""

from __future__ import annotations

import io
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


class UploadSecurityError(ValueError):
    """Raised when an uploaded file violates the server-side file policy."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class UploadSecurityResult:
    """Result of validating one staged upload before publication."""

    sanitized: bool = False
    findings: tuple[str, ...] = ()


# These formats can become active content in a browser or executable content on
# a web/application host. DeerFlow accepts data and business documents, not
# deployable server scripts. Source code that must be reviewed can be uploaded
# as a plain-text file or inside a reviewed archive.
BLOCKED_UPLOAD_EXTENSIONS = frozenset(
    {
        ".asp",
        ".aspx",
        ".ashx",
        ".bat",
        ".bash",
        ".cgi",
        ".cmd",
        ".com",
        ".csh",
        ".dll",
        ".dylib",
        ".exe",
        ".hta",
        ".htm",
        ".html",
        ".jar",
        ".js",
        ".jsp",
        ".jspx",
        ".ksh",
        ".mjs",
        ".msi",
        ".phtml",
        ".phar",
        ".php",
        ".php3",
        ".php4",
        ".php5",
        ".php7",
        ".pl",
        ".ps1",
        ".py",
        ".pyw",
        ".rb",
        ".scr",
        ".sh",
        ".so",
        ".svg",
        ".war",
        ".xhtml",
        ".zsh",
    }
)

MAX_PDF_SECURITY_SCAN_BYTES = 128 * 1024 * 1024
_PDF_MAGIC = b"%PDF-"
_PDF_NAME_ESCAPE_RE = re.compile(rb"#([0-9A-Fa-f]{2})")
_PDF_OPEN_ACTION_RE = re.compile(rb"/OpenAction(?![A-Za-z0-9#])", re.IGNORECASE)
_PDF_SAFE_PAGE_DESTINATION_OPEN_ACTION_RE = re.compile(
    rb"/OpenAction\s*"
    rb"\[\s*\d+\s+\d+\s+R\s+"
    rb"/(?:XYZ|Fit|FitH|FitV|FitR|FitB|FitBH|FitBV)"
    rb"(?:\s+(?:null|[-+]?(?:\d+(?:\.\d*)?|\.\d+))){0,4}\s*\]",
    re.IGNORECASE,
)
_PDF_ENCRYPT_RE = re.compile(rb"/Encrypt(?![A-Za-z0-9#])", re.IGNORECASE)
_PDF_STREAM_RE = re.compile(rb"(?ms)(?<![A-Za-z])stream\r?\n.*?\r?\nendstream(?![A-Za-z])")
_PDF_FINDING_PATTERNS = (
    # Compressed object streams may contain action dictionaries that cannot be
    # inspected safely by a raw structural scan. Treat them as uninspectable
    # and flatten them in sanitize mode instead of silently skipping them.
    ("compressed object streams", re.compile(rb"/ObjStm(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("additional actions", re.compile(rb"/AA(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("embedded files", re.compile(rb"/(?:EmbeddedFile|EmbeddedFiles)(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("file-attachment annotations", re.compile(rb"/FileAttachment(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("data-import actions", re.compile(rb"/ImportData(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("JavaScript", re.compile(rb"/(?:JavaScript|JS)(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("launch actions", re.compile(rb"/Launch(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("movie content", re.compile(rb"/Movie(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("rich-media content", re.compile(rb"/(?:RichMedia|RichMediaExecute)(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("sound actions", re.compile(rb"/Sound(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("form-submit actions", re.compile(rb"/SubmitForm(?![A-Za-z0-9#])", re.IGNORECASE)),
    ("XFA forms", re.compile(rb"/XFA(?![A-Za-z0-9#])", re.IGNORECASE)),
)


def validate_upload_extension(filename: str) -> None:
    """Reject browser-active and server-executable upload extensions."""
    extension = Path(filename).suffix.lower()
    if extension in BLOCKED_UPLOAD_EXTENSIONS:
        raise UploadSecurityError(415, f"File type is not allowed: {extension}")


def _decode_pdf_name_escapes(payload: bytes) -> bytes:
    """Decode PDF ``#HH`` name escapes so obfuscated action names are visible."""

    def replace(match: re.Match[bytes]) -> bytes:
        return bytes((int(match.group(1), 16),))

    return _PDF_NAME_ESCAPE_RE.sub(replace, payload)


def _pdf_structural_bytes(payload: bytes) -> bytes:
    """Return PDF object syntax without opaque stream payload bytes.

    Raw image/font streams can coincidentally contain byte sequences such as
    ``/JS``. Scanning those payloads produced false 422 responses even though
    executable PDF actions live in object dictionaries outside the streams.
    """

    return _PDF_STREAM_RE.sub(b"stream\nendstream", payload)


def pdf_payload_findings(payload: bytes) -> tuple[str, ...]:
    """Return precise active/uninspectable PDF findings for user feedback."""

    if len(payload) > MAX_PDF_SECURITY_SCAN_BYTES:
        return ("file too large for security inspection",)
    normalized = _decode_pdf_name_escapes(_pdf_structural_bytes(payload))
    findings = [label for label, pattern in _PDF_FINDING_PATTERNS if pattern.search(normalized)]
    if _PDF_ENCRYPT_RE.search(normalized):
        findings.append("encryption")

    without_safe_page_destinations = _PDF_SAFE_PAGE_DESTINATION_OPEN_ACTION_RE.sub(
        b"",
        normalized,
    )
    if _PDF_OPEN_ACTION_RE.search(without_safe_page_destinations):
        findings.append("open actions")
    return tuple(dict.fromkeys(findings))


def pdf_payload_has_active_content(payload: bytes) -> bool:
    """Return whether a PDF payload contains active or uninspectable features."""
    return bool(pdf_payload_findings(payload))


def _pdf_rejection_detail(findings: tuple[str, ...]) -> str:
    joined = ", ".join(findings) if findings else "unknown active content"
    return f"PDF contains disallowed or uninspectable features: {joined}"


def _pdf_security_mode() -> str:
    """Return the secure PDF policy; an unsafe disabled mode is unsupported."""

    mode = os.getenv("DEERFLOW_UPLOAD_PDF_SECURITY_MODE", "strict").strip().lower()
    return mode if mode in {"strict", "sanitize"} else "strict"


def _sanitize_pdf_to_inert_bytes(payload: bytes) -> bytes:
    """Flatten a PDF into image-only pages using already-bundled libraries.

    Flattening intentionally sacrifices searchable text only for PDFs that
    contain active structures. The enterprise parser will OCR the sanitized
    copy, while scripts, actions, attachments, forms, and encryption cannot be
    carried into the newly generated document.
    """

    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    dpi = max(72, min(200, int(os.getenv("DEERFLOW_UPLOAD_PDF_SANITIZE_DPI", "120"))))
    max_pages = max(1, min(500, int(os.getenv("DEERFLOW_UPLOAD_PDF_SANITIZE_MAX_PAGES", "200"))))
    document = pdfium.PdfDocument(payload)
    try:
        page_count = len(document)
        if page_count <= 0:
            raise ValueError("PDF contains no pages")
        if page_count > max_pages:
            raise ValueError(f"PDF has {page_count} pages; sanitization limit is {max_pages}")

        output = io.BytesIO()
        pdf = canvas.Canvas(output, pageCompression=1)
        pdf.setTitle("Sanitized PDF")
        pdf.setCreator("CNOOC document upload security")
        scale = dpi / 72.0
        for page_index in range(page_count):
            page = document[page_index]
            bitmap = None
            try:
                width, height = page.get_size()
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil().convert("RGB")
                image_bytes = io.BytesIO()
                image.save(image_bytes, format="JPEG", quality=85, optimize=True)
                image_bytes.seek(0)
                pdf.setPageSize((width, height))
                pdf.drawImage(
                    ImageReader(image_bytes),
                    0,
                    0,
                    width=width,
                    height=height,
                    preserveAspectRatio=True,
                    anchor="c",
                )
                pdf.showPage()
            finally:
                if bitmap is not None:
                    bitmap.close()
                page.close()
        pdf.save()
        return output.getvalue()
    finally:
        document.close()


def _replace_with_sanitized_pdf(path: Path, payload: bytes) -> None:
    sanitized = _sanitize_pdf_to_inert_bytes(payload)
    if not sanitized[:1024].lstrip().startswith(_PDF_MAGIC):
        raise ValueError("sanitizer did not produce a PDF")
    temp_fd, temp_path_str = tempfile.mkstemp(
        prefix=f".{path.name}.sanitize-",
        suffix=".tmp",
        dir=path.parent,
    )
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(temp_fd, "wb") as handle:
            handle.write(sanitized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def validate_pdf_payload(payload: bytes) -> None:
    """Validate PDF identity and reject active-content structures."""
    if not payload[:1024].lstrip().startswith(_PDF_MAGIC):
        raise UploadSecurityError(415, "The uploaded .pdf file does not contain a valid PDF signature")
    findings = pdf_payload_findings(payload)
    if findings:
        raise UploadSecurityError(422, _pdf_rejection_detail(findings))


def validate_staged_upload(path: Path, filename: str) -> UploadSecurityResult:
    """Validate staged bytes before they atomically replace the destination."""
    validate_upload_extension(filename)
    if Path(filename).suffix.lower() != ".pdf":
        return UploadSecurityResult()

    payload = path.read_bytes()
    if not payload[:1024].lstrip().startswith(_PDF_MAGIC):
        raise UploadSecurityError(415, "The uploaded .pdf file does not contain a valid PDF signature")
    findings = pdf_payload_findings(payload)
    if not findings:
        return UploadSecurityResult()
    if _pdf_security_mode() != "sanitize":
        raise UploadSecurityError(422, _pdf_rejection_detail(findings))
    if "encryption" in findings or "file too large for security inspection" in findings:
        raise UploadSecurityError(422, _pdf_rejection_detail(findings))
    try:
        _replace_with_sanitized_pdf(path, payload)
    except Exception as exc:
        raise UploadSecurityError(
            422,
            f"PDF could not be safely sanitized ({', '.join(findings)}): {type(exc).__name__}",
        ) from exc
    return UploadSecurityResult(sanitized=True, findings=findings)


def pdf_file_requires_attachment(path: Path) -> bool:
    """Fail safely when a PDF cannot be trusted for an inline response."""
    try:
        payload = path.read_bytes()
    except OSError:
        return True
    if not payload[:1024].lstrip().startswith(_PDF_MAGIC):
        return True
    return pdf_payload_has_active_content(payload)
