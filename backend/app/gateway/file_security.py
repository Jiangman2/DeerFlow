"""Security policy for files entering or leaving the Gateway."""

from __future__ import annotations

import re
from pathlib import Path


class UploadSecurityError(ValueError):
    """Raised when an uploaded file violates the server-side file policy."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


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
_PDF_ACTIVE_NAME_RE = re.compile(
    rb"/(?:"
    rb"AA|"
    rb"EmbeddedFile|EmbeddedFiles|"
    rb"FileAttachment|"
    rb"ImportData|"
    rb"JavaScript|JS|"
    rb"Launch|"
    rb"Movie|"
    rb"RichMedia|RichMediaExecute|"
    rb"Sound|"
    rb"SubmitForm|"
    rb"XFA"
    rb")(?![A-Za-z0-9#])",
    re.IGNORECASE,
)
_PDF_OPEN_ACTION_RE = re.compile(rb"/OpenAction(?![A-Za-z0-9#])", re.IGNORECASE)
_PDF_SAFE_PAGE_DESTINATION_OPEN_ACTION_RE = re.compile(
    rb"/OpenAction\s*"
    rb"\[\s*\d+\s+\d+\s+R\s+"
    rb"/(?:XYZ|Fit|FitH|FitV|FitR|FitB|FitBH|FitBV)"
    rb"(?:\s+(?:null|[-+]?(?:\d+(?:\.\d*)?|\.\d+))){0,4}\s*\]",
    re.IGNORECASE,
)
_PDF_ENCRYPT_RE = re.compile(rb"/Encrypt(?![A-Za-z0-9#])", re.IGNORECASE)


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


def pdf_payload_has_active_content(payload: bytes) -> bool:
    """Return whether a PDF payload contains active or uninspectable features."""
    if len(payload) > MAX_PDF_SECURITY_SCAN_BYTES:
        return True
    normalized = _decode_pdf_name_escapes(payload)
    if _PDF_ACTIVE_NAME_RE.search(normalized) or _PDF_ENCRYPT_RE.search(normalized):
        return True

    # LibreOffice writes a direct page-destination OpenAction so a converted
    # presentation opens on its first slide.  It is navigation metadata, not
    # an executable PDF action.  Remove only that tightly shaped array before
    # checking for any remaining OpenAction; indirect action dictionaries and
    # malformed/unknown forms still fail closed.
    without_safe_page_destinations = _PDF_SAFE_PAGE_DESTINATION_OPEN_ACTION_RE.sub(b"", normalized)
    return bool(_PDF_OPEN_ACTION_RE.search(without_safe_page_destinations))


def validate_pdf_payload(payload: bytes) -> None:
    """Validate PDF identity and reject active-content structures."""
    if not payload[:1024].lstrip().startswith(_PDF_MAGIC):
        raise UploadSecurityError(415, "The uploaded .pdf file does not contain a valid PDF signature")
    if pdf_payload_has_active_content(payload):
        raise UploadSecurityError(422, "PDF active content, embedded files, actions, or encryption are not allowed")


def validate_staged_upload(path: Path, filename: str) -> None:
    """Validate staged bytes before they atomically replace the destination."""
    validate_upload_extension(filename)
    if Path(filename).suffix.lower() == ".pdf":
        validate_pdf_payload(path.read_bytes())


def pdf_file_requires_attachment(path: Path) -> bool:
    """Fail safely when a PDF cannot be trusted for an inline response."""
    try:
        payload = path.read_bytes()
    except OSError:
        return True
    if not payload[:1024].lstrip().startswith(_PDF_MAGIC):
        return True
    return pdf_payload_has_active_content(payload)
