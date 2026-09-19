import asyncio
import base64
import html
import inspect
import io
import logging
import mimetypes
import os
import re
import threading
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response

from app.gateway.authz import require_permission
from app.gateway.file_security import pdf_file_requires_attachment, pdf_payload_has_active_content
from app.gateway.internal_auth import get_trusted_internal_owner_user_id
from app.gateway.path_utils import resolve_thread_virtual_path
from deerflow.config.paths import make_safe_user_id
from deerflow.sandbox.sandbox_provider import get_sandbox_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["artifacts"])

# Exact matches only; ``_is_active_content_mime_type`` also treats every
# ``+xml`` subtype as active content.
ACTIVE_CONTENT_MIME_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "image/svg+xml",
    "text/xml",
    "application/xml",
    "text/xsl",
}

TEXT_CONTENT_MIME_TYPES = {
    "application/javascript",
    "application/json",
    "application/ld+json",
    "application/sql",
    "application/xml",
    "application/yaml",
}

SAFE_INLINE_UPLOAD_MIME_TYPES = {
    "application/json",
    "text/csv",
    "text/plain",
}

MAX_SKILL_ARCHIVE_MEMBER_BYTES = 16 * 1024 * 1024
_SKILL_ARCHIVE_READ_CHUNK_SIZE = 64 * 1024
MAX_PDF_PREVIEW_BYTES = 64 * 1024 * 1024
MAX_PDF_PREVIEW_PAGES = 100
_PDF_PREVIEW_RENDER_SCALE = 1.5
_PDF_PREVIEW_LOCK = threading.Lock()


def _is_active_content_mime_type(mime_type: str | None) -> bool:
    """Return whether a browser can execute content rendered for *mime_type*.

    Browsers treat HTML plus every XML document type as active content.  XML
    includes the two base MIME types, XSL, and any structured ``+xml`` subtype;
    those documents can carry XHTML-namespaced scripts when served from the
    application origin.
    """
    if mime_type is None:
        return False
    normalized = mime_type.lower()
    return normalized in ACTIVE_CONTENT_MIME_TYPES or normalized.endswith("+xml")


# ``server.py --live`` currently listens on 5050 inside the AIO Sandbox.  Do
# not turn this into an arbitrary localhost proxy: the port is an explicit
# contract of the PPT Live Preview workflow.
LIVE_PREVIEW_PORT = 5050
MAX_LIVE_PREVIEW_BYTES = 64 * 1024 * 1024
PPT_MASTER_RUNTIME_PROFILE = "ppt-master-runner"
PPT_MASTER_RUNNER_INTERNAL_URL = os.environ.get(
    "PPT_MASTER_RUNNER_INTERNAL_URL",
    "http://127.0.0.1:8003",
).rstrip("/")
_LIVE_PREVIEW_TEXT_TYPES = {
    "text/html",
    "application/xhtml+xml",
    "application/javascript",
    "text/javascript",
    "text/css",
}
_LIVE_PREVIEW_ROOT_URL_RE = re.compile(r"([\"'`(])/(api|static)(?=/|[\"'`)\s])")
_LIVE_PREVIEW_HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

PASSIVE_FILE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

INLINE_PDF_HEADERS = {
    **PASSIVE_FILE_HEADERS,
    "Content-Security-Policy": "sandbox; default-src 'none'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'",
}

PDF_PREVIEW_HTML_HEADERS = {
    **PASSIVE_FILE_HEADERS,
    "Content-Security-Policy": ("sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"),
    "Cache-Control": "private, no-store",
}

SECURE_ATTACHMENT_HEADERS = {
    **INLINE_PDF_HEADERS,
    "Cache-Control": "private, no-store",
    "X-Download-Options": "noopen",
}


def _live_preview_prefix(thread_id: str) -> str:
    return f"/api/threads/{quote(thread_id, safe='')}/live-preview/{LIVE_PREVIEW_PORT}"


def _rewrite_live_preview_root_urls(payload: bytes, thread_id: str) -> bytes:
    """Keep the preview app's root-relative assets inside the proxy route.

    The upstream preview uses paths such as ``/static/app.js`` and
    ``/api/slides``.  Without rewriting, the browser would request those paths
    from DeerFlow itself instead of the Sandbox.
    """
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload
    prefix = _live_preview_prefix(thread_id)
    rewritten = _LIVE_PREVIEW_ROOT_URL_RE.sub(rf"\1{prefix}/\2", text)
    return rewritten.encode("utf-8")


def _rewrite_live_preview_location(location: str, thread_id: str) -> str:
    """Rewrite upstream root-relative redirects to the authenticated proxy."""
    if location.startswith(("/api/", "/static/")):
        return f"{_live_preview_prefix(thread_id)}{location}"
    return location


def _live_preview_target(sandbox: object, path: str) -> tuple[str, dict[str, str]] | None:
    """Build the internal Sandbox URL and trusted preview-port header."""
    normalized_path = "/" + path.lstrip("/")
    if "\x00" in normalized_path or any(part == ".." for part in normalized_path.split("/")):
        raise HTTPException(status_code=400, detail="Invalid Live Preview path")

    base_url = getattr(sandbox, "base_url", None)
    sandbox_id = str(getattr(sandbox, "id", ""))
    if not isinstance(base_url, str) or not base_url:
        # LocalSandbox runs commands on the Gateway host, so its local preview
        # server is already reachable from the Gateway process.
        if sandbox_id == "local" or sandbox_id.startswith("local:"):
            return f"http://127.0.0.1:{LIVE_PREVIEW_PORT}{normalized_path}", {}
        return None

    parsed = urlsplit(base_url)
    if not parsed.scheme or not parsed.netloc or not parsed.hostname:
        return None
    target = f"{parsed.scheme}://{parsed.netloc}{normalized_path}"
    # AIO Sandbox formally supports header-based routing for a trusted reverse
    # proxy.  This reaches a process that intentionally binds only to the Pod's
    # loopback interface without depending on a synthetic subdomain Host value.
    # Never copy this header from the browser: it selects an internal port and
    # must remain fixed at DeerFlow's single allowlisted preview port.
    return target, {"x-aio-proxy-port": str(LIVE_PREVIEW_PORT)}


def _live_preview_response_headers(upstream: httpx.Response, thread_id: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name, value in upstream.headers.items():
        lower_name = name.lower()
        if lower_name in _LIVE_PREVIEW_HOP_BY_HOP_HEADERS or lower_name in {"content-length", "content-encoding"}:
            continue
        if lower_name == "location":
            value = _rewrite_live_preview_location(value, thread_id)
        headers[name] = value
    headers["Cache-Control"] = "no-store"
    headers["X-Content-Type-Options"] = "nosniff"
    return headers


async def _proxy_live_preview(
    thread_id: str,
    port: int,
    path: str,
    request: Request,
    *,
    user_can_write: bool,
) -> Response:
    del user_can_write  # The decorator on each route owns authorization.
    if port != LIVE_PREVIEW_PORT:
        raise HTTPException(status_code=404, detail="Unsupported Live Preview port")
    owner_user_id = await _resolve_artifact_owner_user_id(request, thread_id)
    runtime_profile = await _resolve_thread_runtime_profile(request, thread_id)
    if runtime_profile == PPT_MASTER_RUNTIME_PROFILE and os.environ.get("DEER_FLOW_RUNTIME_PROFILE") != PPT_MASTER_RUNTIME_PROFILE:
        # The isolated Runner owns the Sandbox lifecycle. Forward the browser
        # request to its internal Service instead of discovering that Sandbox
        # through the main Gateway's provider: discovery would make the main
        # process eligible to release/destroy a Sandbox that is still executing
        # in the Runner process.
        return await _forward_live_preview_to_runner(request, thread_id)

    provider = get_sandbox_provider()
    sandbox = await asyncio.to_thread(provider.get_for_thread, thread_id, user_id=owner_user_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Live Preview sandbox is no longer available")

    target_info = _live_preview_target(sandbox, path)
    if target_info is None:
        raise HTTPException(status_code=404, detail="Live Preview is unavailable for this sandbox")
    target_url, sandbox_headers = target_info

    forward_headers: dict[str, str] = dict(sandbox_headers)
    for header_name in ("accept", "content-type", "if-none-match", "if-modified-since"):
        value = request.headers.get(header_name)
        if value:
            forward_headers[header_name] = value
    body = await request.body() if request.method not in {"GET", "HEAD", "OPTIONS"} else None
    try:
        timeout = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            async with client.stream(
                request.method,
                target_url,
                params=request.query_params,
                headers=forward_headers,
                content=body,
            ) as upstream:
                content_length = upstream.headers.get("content-length")
                if content_length and int(content_length) > MAX_LIVE_PREVIEW_BYTES:
                    raise HTTPException(status_code=413, detail="Live Preview response is too large")
                chunks: list[bytes] = []
                total = 0
                async for chunk in upstream.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_LIVE_PREVIEW_BYTES:
                        raise HTTPException(status_code=413, detail="Live Preview response is too large")
                    chunks.append(chunk)
                payload = b"".join(chunks)
                media_type = upstream.headers.get("content-type", "").split(";", 1)[0].lower()
                if media_type in _LIVE_PREVIEW_TEXT_TYPES:
                    payload = _rewrite_live_preview_root_urls(payload, thread_id)
                return Response(
                    content=payload,
                    status_code=upstream.status_code,
                    headers=_live_preview_response_headers(upstream, thread_id),
                    media_type=None,
                )
    except httpx.HTTPError as exc:
        logger.warning("Live Preview proxy failed for thread %s: %s", thread_id, exc)
        raise HTTPException(status_code=502, detail="Live Preview server is not reachable") from exc


async def _forward_live_preview_to_runner(
    request: Request,
    thread_id: str,
) -> Response:
    """Forward a Runner-owned preview to the isolated internal Service."""
    target_url = f"{PPT_MASTER_RUNNER_INTERNAL_URL}{request.url.path}"
    forward_headers: dict[str, str] = {}
    for header_name in (
        "accept",
        "content-type",
        "cookie",
        "forwarded",
        "host",
        "if-modified-since",
        "if-none-match",
        "origin",
        "sec-fetch-site",
        "user-agent",
        "x-csrf-token",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-port",
        "x-forwarded-proto",
    ):
        value = request.headers.get(header_name)
        if value:
            forward_headers[header_name] = value

    body = await request.body() if request.method not in {"GET", "HEAD", "OPTIONS"} else None
    try:
        timeout = httpx.Timeout(connect=5.0, read=35.0, write=10.0, pool=5.0)
        async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
            async with client.stream(
                request.method,
                target_url,
                params=request.query_params,
                headers=forward_headers,
                content=body,
            ) as upstream:
                content_length = upstream.headers.get("content-length")
                if content_length and int(content_length) > MAX_LIVE_PREVIEW_BYTES:
                    raise HTTPException(status_code=413, detail="Live Preview response is too large")
                chunks: list[bytes] = []
                total = 0
                async for chunk in upstream.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_LIVE_PREVIEW_BYTES:
                        raise HTTPException(status_code=413, detail="Live Preview response is too large")
                    chunks.append(chunk)
                return Response(
                    content=b"".join(chunks),
                    status_code=upstream.status_code,
                    headers=_live_preview_response_headers(upstream, thread_id),
                    media_type=None,
                )
    except httpx.HTTPError as exc:
        logger.warning(
            "PPT Runner Live Preview bridge failed for thread %s: %s",
            thread_id,
            exc,
        )
        raise HTTPException(status_code=502, detail="PPT Runner Live Preview is not reachable") from exc


@router.api_route(
    "/threads/{thread_id}/live-preview/{port}/{path:path}",
    methods=["GET", "HEAD", "OPTIONS"],
    summary="Proxy Sandbox Live Preview",
    description="Serve the authenticated Live Preview process from the active thread sandbox.",
)
@require_permission("threads", "read", owner_check=True)
async def get_live_preview(
    thread_id: str,
    port: int,
    path: str,
    request: Request,
) -> Response:
    return await _proxy_live_preview(thread_id, port, path, request, user_can_write=False)


@router.api_route(
    "/threads/{thread_id}/live-preview/{port}/{path:path}",
    methods=["POST", "PUT", "PATCH", "DELETE"],
    summary="Update Sandbox Live Preview",
    description="Proxy authenticated mutation requests made by the Live Preview editor.",
)
@require_permission("threads", "write", owner_check=True)
async def mutate_live_preview(
    thread_id: str,
    port: int,
    path: str,
    request: Request,
) -> Response:
    return await _proxy_live_preview(thread_id, port, path, request, user_can_write=True)


async def _resolve_artifact_owner_user_id(request: Request | None, thread_id: str) -> str | None:
    """Resolve artifact storage from trusted thread ownership metadata.

    Authorization is enforced by ``require_permission`` before this helper is
    called.  Once access is granted, browser requests (including administrator
    read-only review) must read from the thread owner's storage bucket instead
    of the current viewer's bucket.  The owner cannot be supplied by the
    browser: it comes from ``threads_meta``.  Trusted internal callers retain
    their owner-header fallback for legacy threads without metadata rows.
    """
    trusted_owner = get_trusted_internal_owner_user_id(request) if request is not None else None
    metadata_owner: str | None = None

    app = request.scope.get("app") if request is not None else None
    thread_store = getattr(getattr(app, "state", None), "thread_store", None)
    get_thread = getattr(thread_store, "get", None)
    if callable(get_thread):
        record_or_awaitable = get_thread(thread_id, user_id=None)
        if inspect.isawaitable(record_or_awaitable):
            record = await record_or_awaitable
            if isinstance(record, dict):
                raw_metadata_owner = record.get("user_id")
                if raw_metadata_owner is not None:
                    metadata_owner = str(raw_metadata_owner).strip() or None

    raw_owner = metadata_owner or trusted_owner
    return make_safe_user_id(raw_owner) if raw_owner else None


async def _resolve_thread_runtime_profile(request: Request | None, thread_id: str) -> str | None:
    """Read the durable runtime owner used to route Sidecar-owned previews."""
    app = request.scope.get("app") if request is not None else None
    thread_store = getattr(getattr(app, "state", None), "thread_store", None)
    get_thread = getattr(thread_store, "get", None)
    if not callable(get_thread):
        return None

    record_or_awaitable = get_thread(thread_id, user_id=None)
    record = await record_or_awaitable if inspect.isawaitable(record_or_awaitable) else record_or_awaitable
    if not isinstance(record, dict):
        return None
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return None
    raw_profile = metadata.get("runtime_profile")
    return str(raw_profile).strip() or None if raw_profile is not None else None


def _build_content_disposition(disposition_type: str, filename: str) -> str:
    """Build an RFC 5987 encoded Content-Disposition header value."""
    return f"{disposition_type}; filename*=UTF-8''{quote(filename)}"


def _build_attachment_headers(filename: str, extra_headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        **SECURE_ATTACHMENT_HEADERS,
        "Content-Disposition": _build_content_disposition("attachment", filename),
    }
    if extra_headers:
        headers.update(extra_headers)
    return headers


def is_text_file_by_content(path: Path, sample_size: int = 8192) -> bool:
    """Check if file is text by examining content for null bytes."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(sample_size)
            # Text files shouldn't contain null bytes
            return b"\x00" not in chunk
    except Exception:
        return False


def _is_safe_inline_upload_mime(mime_type: str | None) -> bool:
    if not mime_type:
        return False
    return mime_type in SAFE_INLINE_UPLOAD_MIME_TYPES or mime_type.startswith(("image/", "audio/", "video/"))


def _read_skill_archive_member(zip_ref: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Read a .skill archive member while enforcing an uncompressed size cap."""
    if info.file_size > MAX_SKILL_ARCHIVE_MEMBER_BYTES:
        raise HTTPException(status_code=413, detail="Skill archive member is too large to preview")

    chunks: list[bytes] = []
    total_read = 0
    with zip_ref.open(info, "r") as src:
        while chunk := src.read(_SKILL_ARCHIVE_READ_CHUNK_SIZE):
            total_read += len(chunk)
            if total_read > MAX_SKILL_ARCHIVE_MEMBER_BYTES:
                raise HTTPException(status_code=413, detail="Skill archive member is too large to preview")
            chunks.append(chunk)
    return b"".join(chunks)


def _extract_file_from_skill_archive(zip_path: Path, internal_path: str) -> bytes | None:
    """Extract a file from a .skill ZIP archive.

    Args:
        zip_path: Path to the .skill file (ZIP archive).
        internal_path: Path to the file inside the archive (e.g., "SKILL.md").

    Returns:
        The file content as bytes, or None if not found.
    """
    if not zipfile.is_zipfile(zip_path):
        return None

    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            # List all files in the archive
            infos_by_name = {info.filename: info for info in zip_ref.infolist()}

            # Try direct path first
            if internal_path in infos_by_name:
                return _read_skill_archive_member(zip_ref, infos_by_name[internal_path])

            # Try with any top-level directory prefix (e.g., "skill-name/SKILL.md")
            for name, info in infos_by_name.items():
                if name.endswith("/" + internal_path) or name == internal_path:
                    return _read_skill_archive_member(zip_ref, info)

            # Not found
            return None
    except (zipfile.BadZipFile, KeyError):
        return None


def _load_skill_archive_member(actual_skill_path: Path, skill_file_path: str, internal_path: str) -> tuple[bytes, str | None]:
    """Worker-thread body for the ``.skill`` branch of ``get_artifact``.

    The ``exists`` / ``is_file`` probes, the ZIP open+extract, and the MIME
    sniff (``mimetypes`` lazily stats the system MIME database on first use) are
    blocking filesystem IO and must stay off the event loop. Raised
    ``HTTPException``s propagate through ``asyncio.to_thread`` unchanged,
    preserving status codes.
    """
    if not actual_skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill file not found: {skill_file_path}")
    if not actual_skill_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {skill_file_path}")
    content = _extract_file_from_skill_archive(actual_skill_path, internal_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File '{internal_path}' not found in skill archive")
    mime_type, _ = mimetypes.guess_type(internal_path)
    return content, mime_type


def _read_artifact_payload(actual_path: Path, path: str, download: bool) -> tuple[str, str | None, bytes | str | None]:
    """Worker-thread body for the regular branch of ``get_artifact``.

    Stat probes, MIME sniffing (``mimetypes`` lazily stats the system MIME
    database on first use), and full-file reads are all blocking filesystem IO.
    Returns a ``(kind, mime_type, payload)`` plan the handler turns into a
    response on the loop: ``("attachment", mime, None)`` or
    ``("inline_file", mime, None)`` (let ``FileResponse`` stream it),
    ``("text", mime, str)``, or ``("bytes", mime, bytes)``.
    """
    if not actual_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    if not actual_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")
    mime_type, _ = mimetypes.guess_type(actual_path)
    # Active content / explicit download is streamed by FileResponse — no read here.
    normalized_virtual_path = "/" + path.replace("\\", "/").lstrip("/")
    is_user_upload = normalized_virtual_path.startswith("/mnt/user-data/uploads/")
    if download or (is_user_upload and not _is_safe_inline_upload_mime(mime_type)) or _is_active_content_mime_type(mime_type):
        return ("attachment", mime_type, None)
    if mime_type == "application/pdf" and pdf_file_requires_attachment(actual_path):
        return ("attachment", mime_type, None)
    # Audio and video artifacts can be hundreds of MiB.  Stream them from disk
    # instead of materializing the whole payload in the Gateway process.
    if mime_type and mime_type.startswith(("audio/", "video/")):
        return ("inline_file", mime_type, None)
    if mime_type and (mime_type.startswith("text/") or mime_type in TEXT_CONTENT_MIME_TYPES or mime_type.endswith("+json") or mime_type.endswith("+xml")):
        return ("text", mime_type, actual_path.read_text(encoding="utf-8"))
    # A known non-text MIME type is authoritative. Content heuristics such as
    # "contains no NUL byte" are unsafe for PDFs and other binary formats whose
    # first block can legitimately contain no zero bytes.
    if mime_type:
        return ("bytes", mime_type, actual_path.read_bytes())
    if is_text_file_by_content(actual_path):
        return ("text", mime_type, actual_path.read_text(encoding="utf-8"))
    return ("bytes", mime_type, actual_path.read_bytes())


def _validate_pdf_preview_artifact(actual_path: Path, path: str) -> None:
    normalized_virtual_path = "/" + path.replace("\\", "/").lstrip("/")
    is_outputs_pdf = normalized_virtual_path.startswith("/mnt/user-data/outputs/") and normalized_virtual_path.lower().endswith(".pdf")
    if not is_outputs_pdf:
        raise HTTPException(status_code=400, detail="Only generated output PDFs can use this preview endpoint")
    if not actual_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact not found: {path}")
    if not actual_path.is_file():
        raise HTTPException(status_code=400, detail=f"Path is not a file: {path}")
    if actual_path.stat().st_size > MAX_PDF_PREVIEW_BYTES:
        raise HTTPException(status_code=413, detail="PDF preview is too large")
    if pdf_file_requires_attachment(actual_path):
        raise HTTPException(status_code=400, detail="PDF preview contains unsupported active or encrypted content")


def _load_pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:  # pragma: no cover - release dependency guard
        raise HTTPException(status_code=503, detail="PDF preview renderer is unavailable") from exc
    return pdfium


def _inspect_pdf_preview(actual_path: Path) -> int:
    pdfium = _load_pdfium()
    with _PDF_PREVIEW_LOCK:
        try:
            document = pdfium.PdfDocument(str(actual_path))
            try:
                page_count = len(document)
            finally:
                document.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="PDF preview cannot be opened") from exc
    if page_count < 1:
        raise HTTPException(status_code=422, detail="PDF preview has no pages")
    if page_count > MAX_PDF_PREVIEW_PAGES:
        raise HTTPException(status_code=413, detail=f"PDF preview exceeds {MAX_PDF_PREVIEW_PAGES} pages")
    return page_count


def _render_pdf_preview_page(actual_path: Path, page_index: int) -> bytes:
    pdfium = _load_pdfium()
    with _PDF_PREVIEW_LOCK:
        try:
            document = pdfium.PdfDocument(str(actual_path))
            try:
                page_count = len(document)
                if page_index < 0 or page_index >= page_count or page_index >= MAX_PDF_PREVIEW_PAGES:
                    raise HTTPException(status_code=404, detail="PDF preview page not found")
                page = document[page_index]
                try:
                    bitmap = page.render(scale=_PDF_PREVIEW_RENDER_SCALE)
                    try:
                        image = bitmap.to_pil()
                        output = io.BytesIO()
                        image.save(output, format="PNG", optimize=True)
                        return output.getvalue()
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                document.close()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail="PDF preview page cannot be rendered") from exc


def _render_pdf_preview_pages(actual_path: Path, page_count: int) -> list[bytes]:
    return [_render_pdf_preview_page(actual_path, page_index) for page_index in range(page_count)]


def _build_embedded_pdf_preview_gallery(path: str, page_payloads: list[bytes]) -> str:
    title = html.escape(Path(path).name, quote=True)
    pages = "".join(f'<figure><img src="data:image/png;base64,{base64.b64encode(payload).decode("ascii")}" alt="{title} 第 {page_index + 1} 页" loading="lazy"></figure>' for page_index, payload in enumerate(page_payloads))
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>"
        "html,body{margin:0;background:#eef4fb;color:#172033}body{padding:16px}"
        "main{display:grid;gap:16px;max-width:1200px;margin:0 auto}figure{margin:0;background:#fff;"
        "box-shadow:0 2px 12px rgba(15,38,70,.14)}img{display:block;width:100%;height:auto}"
        "</style></head><body><main>"
        f"{pages}</main></body></html>"
    )


@router.get(
    "/threads/{thread_id}/artifact-previews/pdf/{path:path}",
    summary="Preview Generated PDF Artifact",
    description="Render a validated generated output PDF as a scriptless browser-compatible page gallery.",
)
@require_permission("threads", "read", owner_check=True)
async def get_pdf_artifact_preview(
    thread_id: str,
    path: str,
    request: Request,
    page: int | None = None,
) -> Response:
    owner_user_id = await _resolve_artifact_owner_user_id(request, thread_id)
    actual_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, path, user_id=owner_user_id)
    await asyncio.to_thread(_validate_pdf_preview_artifact, actual_path, path)

    if page is not None:
        payload = await asyncio.to_thread(_render_pdf_preview_page, actual_path, page)
        return Response(
            content=payload,
            media_type="image/png",
            headers={**PASSIVE_FILE_HEADERS, "Cache-Control": "private, max-age=300"},
        )

    page_count = await asyncio.to_thread(_inspect_pdf_preview, actual_path)
    page_payloads = await asyncio.to_thread(_render_pdf_preview_pages, actual_path, page_count)
    return Response(
        content=_build_embedded_pdf_preview_gallery(path, page_payloads),
        media_type="text/html",
        headers=PDF_PREVIEW_HTML_HEADERS,
    )


@router.api_route(
    "/threads/{thread_id}/artifacts/{path:path}",
    methods=["GET", "HEAD"],
    summary="Get Artifact File",
    description="Retrieve an artifact file generated by the AI agent. Text and binary files can be viewed inline, while active web content is always downloaded.",
)
@require_permission("threads", "read", owner_check=True)
async def get_artifact(thread_id: str, path: str, request: Request, download: bool = False) -> Response:
    """Get an artifact file by its path.

    The endpoint automatically detects file types and returns appropriate content types.
    Use the `download` query parameter to force file download for non-active content.

    Args:
        thread_id: The thread ID.
        path: The artifact path with virtual prefix (e.g., mnt/user-data/outputs/file.txt).
        request: FastAPI request object (automatically injected).

    Returns:
        The file content as a FileResponse with appropriate content type:
        - Active content (HTML/XHTML/SVG): Served as download attachment
        - Text files: Plain text with proper MIME type
        - Binary files: Inline display with download option

    Raises:
        HTTPException:
            - 400 if path is invalid or not a file
            - 403 if access denied (path traversal detected)
            - 404 if file not found

    Query Parameters:
        download (bool): If true, forces attachment download for file types that are
            otherwise returned inline or as plain text. Active HTML/XHTML/SVG content
            is always downloaded regardless of this flag.

    Example:
        - Get text file inline: `/api/threads/abc123/artifacts/mnt/user-data/outputs/notes.txt`
        - Download file: `/api/threads/abc123/artifacts/mnt/user-data/outputs/data.csv?download=true`
        - Active web content such as `.html`, `.xhtml`, and `.svg` artifacts is always downloaded
    """
    owner_user_id = await _resolve_artifact_owner_user_id(request, thread_id)

    # Check if this is a request for a file inside a .skill archive (e.g., xxx.skill/SKILL.md)
    if ".skill/" in path:
        # Split the path at ".skill/" to get the ZIP file path and internal path
        skill_marker = ".skill/"
        marker_pos = path.find(skill_marker)
        skill_file_path = path[: marker_pos + len(".skill")]  # e.g., "mnt/user-data/outputs/my-skill.skill"
        internal_path = path[marker_pos + len(skill_marker) :]  # e.g., "SKILL.md"

        actual_skill_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, skill_file_path, user_id=owner_user_id)

        # Offload the stat probes + ZIP open/extract + MIME sniff (blocking filesystem IO).
        content, mime_type = await asyncio.to_thread(_load_skill_archive_member, actual_skill_path, skill_file_path, internal_path)

        # Add cache headers to avoid repeated ZIP extraction (cache for 5 minutes)
        cache_headers = {"Cache-Control": "private, max-age=300"}
        download_name = Path(internal_path).name or actual_skill_path.stem
        normalized_skill_path = "/" + skill_file_path.replace("\\", "/").lstrip("/")
        if download or normalized_skill_path.startswith("/mnt/user-data/uploads/") or _is_active_content_mime_type(mime_type):
            return Response(
                content=content,
                media_type="application/octet-stream",
                headers=_build_attachment_headers(download_name, cache_headers),
            )

        if mime_type == "application/pdf":
            if pdf_payload_has_active_content(content):
                return Response(
                    content=content,
                    media_type="application/octet-stream",
                    headers=_build_attachment_headers(download_name, cache_headers),
                )
            return Response(
                content=content,
                media_type=mime_type,
                headers={
                    **INLINE_PDF_HEADERS,
                    **cache_headers,
                    "Content-Disposition": _build_content_disposition("inline", download_name),
                },
            )

        if mime_type and mime_type.startswith("text/"):
            return PlainTextResponse(
                content=content.decode("utf-8"),
                media_type=mime_type,
                headers={**PASSIVE_FILE_HEADERS, **cache_headers},
            )

        # Default to plain text for unknown types that look like text
        try:
            return PlainTextResponse(
                content=content.decode("utf-8"),
                media_type="text/plain",
                headers={**PASSIVE_FILE_HEADERS, **cache_headers},
            )
        except UnicodeDecodeError:
            return Response(
                content=content,
                media_type=mime_type or "application/octet-stream",
                headers={**PASSIVE_FILE_HEADERS, **cache_headers},
            )

    actual_path = await asyncio.to_thread(resolve_thread_virtual_path, thread_id, path, user_id=owner_user_id)

    logger.info(f"Resolving artifact path: thread_id={thread_id}, requested_path={path}, actual_path={actual_path}")

    # Offload path stat + MIME sniff + file reads (all blocking filesystem IO).
    # Active content and explicit downloads are streamed by FileResponse, so the
    # worker only reports the kind; inline text/binary payloads are read in-thread.
    kind, mime_type, payload = await asyncio.to_thread(_read_artifact_payload, actual_path, path, download)

    if kind == "attachment":
        # Always force download for active content types to prevent script
        # execution in the application origin when users open generated artifacts.
        return FileResponse(
            path=actual_path,
            filename=actual_path.name,
            media_type="application/octet-stream",
            headers=_build_attachment_headers(actual_path.name),
        )

    if kind == "inline_file":
        return FileResponse(
            path=actual_path,
            media_type=mime_type,
            headers={
                **PASSIVE_FILE_HEADERS,
                "Content-Disposition": _build_content_disposition("inline", actual_path.name),
            },
        )

    if kind == "text":
        return PlainTextResponse(content=payload, media_type=mime_type, headers=PASSIVE_FILE_HEADERS)

    headers = {
        **(INLINE_PDF_HEADERS if mime_type == "application/pdf" else PASSIVE_FILE_HEADERS),
        "Content-Disposition": _build_content_disposition("inline", actual_path.name),
    }
    return Response(content=payload, media_type=mime_type, headers=headers)
