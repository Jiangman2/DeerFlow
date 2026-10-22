"""Document parsing bridge for DeerFlow conversation uploads."""

from __future__ import annotations

import json
import mimetypes
import os
import re
import threading
from pathlib import Path
from typing import Any

import httpx

_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_DEFAULT_TIMEOUT_SECONDS = 320.0
_DEFAULT_MAX_RESPONSE_BYTES = 100 * 1024 * 1024
_DEFAULT_MAX_CONNECTIONS = 32
_DEFAULT_MAX_KEEPALIVE_CONNECTIONS = 16
_PARSER_CLIENT: httpx.AsyncClient | None = None
_PARSER_CLIENT_LOCK = threading.Lock()
_MAX_ERROR_DETAIL_CHARS = 300
_DOCUMENT_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class UploadDocumentParseError(RuntimeError):
    """Raised when the enterprise document parser cannot return safe text."""


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def conversation_document_parser_endpoint() -> str | None:
    """Return the in-cluster parser endpoint for supported conversation documents."""
    endpoint = (os.getenv("DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENDPOINT") or os.getenv("DEERFLOW_UPLOAD_PDF_PARSER_ENDPOINT") or os.getenv("WEKNORA_MINERU_ENDPOINT") or "").strip()
    if not endpoint:
        return None
    enabled_name = "DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED" if os.getenv("DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED") is not None else "DEERFLOW_UPLOAD_PDF_PARSER_ENABLED"
    if not _truthy_env(enabled_name, True):
        return None
    return endpoint.rstrip("/") + "/file_parse"


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _get_parser_client() -> httpx.AsyncClient:
    """Return the process-wide parser client and its bounded connection pool."""
    global _PARSER_CLIENT
    with _PARSER_CLIENT_LOCK:
        if _PARSER_CLIENT is None or _PARSER_CLIENT.is_closed:
            _PARSER_CLIENT = httpx.AsyncClient(
                trust_env=False,
                limits=httpx.Limits(
                    max_connections=_int_env(
                        "DEERFLOW_UPLOAD_DOCUMENT_PARSE_MAX_CONNECTIONS",
                        _DEFAULT_MAX_CONNECTIONS,
                    ),
                    max_keepalive_connections=_int_env(
                        "DEERFLOW_UPLOAD_DOCUMENT_PARSE_MAX_KEEPALIVE_CONNECTIONS",
                        _DEFAULT_MAX_KEEPALIVE_CONNECTIONS,
                    ),
                    keepalive_expiry=30.0,
                ),
            )
        return _PARSER_CLIENT


async def close_conversation_document_parser_client() -> None:
    """Close the shared parser client during Gateway lifespan shutdown."""
    global _PARSER_CLIENT
    with _PARSER_CLIENT_LOCK:
        client = _PARSER_CLIENT
        _PARSER_CLIENT = None
    if client is not None and not client.is_closed:
        await client.aclose()


def _parser_error_detail(response: httpx.Response) -> str | None:
    """Return a bounded, single-line diagnostic from the trusted adapter."""
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None

    for key in ("detail", "error", "message"):
        value = payload.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            try:
                value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except (TypeError, ValueError):
                continue
        normalized = re.sub(r"\s+", " ", value).strip()
        if normalized:
            return normalized[:_MAX_ERROR_DETAIL_CHARS]
    return None


def extract_parser_markdown(payload: Any) -> str:
    """Extract and normalize the WeKnora/MinerU response document text."""
    try:
        markdown = payload["results"]["document"]["md_content"]
    except (KeyError, TypeError) as exc:
        raise UploadDocumentParseError("Document parser response does not contain results.document.md_content") from exc
    if not isinstance(markdown, str) or not markdown.strip():
        raise UploadDocumentParseError("Document parser returned no readable text")

    # The conversation upload path currently publishes one Markdown companion,
    # not WeKnora's image map. Preserve each image caption as explicit evidence
    # instead of leaving broken relative image links in the agent input.
    normalized = _MARKDOWN_IMAGE_RE.sub(
        lambda match: f"[文档图像：{match.group(1).strip() or '未命名图像'}]",
        markdown,
    )
    return normalized.strip()


async def parse_conversation_document(
    *,
    endpoint: str,
    filename: str,
    content: bytes,
) -> str:
    """Parse PDF/Word/PowerPoint through the enterprise WeKnora adapter."""
    timeout_seconds = _float_env(
        "DEERFLOW_UPLOAD_DOCUMENT_PARSE_TIMEOUT_SECONDS",
        _float_env(
            "DEERFLOW_UPLOAD_PDF_PARSE_TIMEOUT_SECONDS",
            _DEFAULT_TIMEOUT_SECONDS,
        ),
    )
    max_response_bytes = _int_env(
        "DEERFLOW_UPLOAD_DOCUMENT_PARSE_MAX_RESPONSE_BYTES",
        _int_env(
            "DEERFLOW_UPLOAD_PDF_PARSE_MAX_RESPONSE_BYTES",
            _DEFAULT_MAX_RESPONSE_BYTES,
        ),
    )
    timeout = httpx.Timeout(timeout_seconds)
    # Slim images may omit the OS MIME database. This is adapter metadata;
    # upload validation and document-content checks remain with their owners.
    media_type = _DOCUMENT_MEDIA_TYPES.get(Path(filename).suffix.lower())
    if media_type is None:
        media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        response = await _get_parser_client().post(
            endpoint,
            headers={"X-DeerFlow-Parse-Priority": "interactive"},
            files={
                "file": (
                    Path(filename).name,
                    content,
                    media_type,
                )
            },
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise UploadDocumentParseError(f"Document parser request failed: {exc.__class__.__name__}") from exc

    if len(response.content) > max_response_bytes:
        raise UploadDocumentParseError("Document parser response is too large")
    if response.status_code != 200:
        detail = _parser_error_detail(response)
        suffix = f": {detail}" if detail else ""
        raise UploadDocumentParseError(f"Document parser returned HTTP {response.status_code}{suffix}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise UploadDocumentParseError("Document parser returned invalid JSON") from exc
    return extract_parser_markdown(payload)
