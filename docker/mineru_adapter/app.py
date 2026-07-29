"""Translate WeKnora's MinerU protocol to the enterprise PDF parsing API."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger("deerflow.mineru_adapter")

_HTML_DATA_IMAGE_RE = re.compile(
    r"""<img\b(?P<before>[^>]*?)\bsrc\s*=\s*(?P<quote>["'])data:(?P<mime>image/[a-z0-9.+-]+);base64,(?P<data>[^"']*)(?P=quote)(?P<after>[^>]*)>""",
    re.IGNORECASE,
)
_MARKDOWN_DATA_IMAGE_RE = re.compile(
    r"""!\[(?P<alt>[^\]]*)]\(\s*data:(?P<mime>image/[a-z0-9.+-]+);base64,(?P<data>[^)]*)\)""",
    re.IGNORECASE,
)
_IMAGE_EXTENSIONS = {
    "image/bmp": "bmp",
    "image/gif": "gif",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/tiff": "tiff",
    "image/webp": "webp",
}


@dataclass(frozen=True)
class PDFRoutingDecision:
    """Result of inspecting a PDF for pages that need OCR."""

    page_count: int
    scanned_pages: tuple[int, ...]

    @property
    def use_mineru(self) -> bool:
        return bool(self.scanned_pages)


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Ignoring invalid float value for %s", name)
        return default


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Ignoring invalid integer value for %s", name)
        return default


def _close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def inspect_pdf_routing(
    content: bytes,
    *,
    pdfium_module: Any | None = None,
    raw_module: Any | None = None,
) -> PDFRoutingDecision:
    """Detect scanned/image-dominated pages without rendering the PDF."""
    if pdfium_module is None:
        import pypdfium2 as pdfium_module
    if raw_module is None:
        import pypdfium2.raw as raw_module

    full_page_image_ratio = _float_env("PDF_SCAN_IMAGE_RATIO", 0.5)
    minimum_text_chars = _int_env("PDF_SCAN_MIN_CHARS_PER_PAGE", 10)
    low_text_image_ratio = _float_env("PDF_SCAN_LOW_TEXT_IMAGE_RATIO", 0.1)

    document = pdfium_module.PdfDocument(content)
    scanned_pages: list[int] = []
    try:
        page_count = len(document)
        if page_count <= 0:
            raise ValueError("PDF contains no pages")

        for page_index in range(page_count):
            page = document[page_index]
            text_page = None
            try:
                width, height = page.get_size()
                page_area = max(float(width) * float(height), 1.0)

                text_page = page.get_textpage()
                text = text_page.get_text_range() or ""

                image_area = 0.0
                for page_object in page.get_objects():
                    if getattr(page_object, "type", None) != raw_module.FPDF_PAGEOBJ_IMAGE:
                        continue
                    left, bottom, right, top = page_object.get_bounds()
                    image_area += max(float(right) - float(left), 0.0) * max(
                        float(top) - float(bottom),
                        0.0,
                    )

                image_ratio = min(image_area / page_area, 1.0)
                text_chars = len(text.strip())
                is_scanned = image_ratio >= full_page_image_ratio or (text_chars < minimum_text_chars and image_ratio >= low_text_image_ratio)
                if is_scanned:
                    scanned_pages.append(page_index + 1)
            finally:
                _close_resource(text_page)
                _close_resource(page)
    finally:
        _close_resource(document)

    return PDFRoutingDecision(
        page_count=page_count,
        scanned_pages=tuple(scanned_pages),
    )


def parse_pdf_with_builtin(
    content: bytes,
    filename: str,
    *,
    parser_factory: Callable[[], Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """Parse a searchable PDF with the WeKnora parser bundled in this image."""
    if parser_factory is None:
        from docreader.parser import Parser

        parser_factory = Parser

    document = parser_factory().parse_file(
        file_name=filename,
        file_type="pdf",
        content=content,
        parser_engine="builtin",
    )
    markdown = str(getattr(document, "content", "") or "").strip()
    if not markdown:
        raise RuntimeError("WeKnora builtin parser returned no parseable PDF content")
    images = getattr(document, "images", None)
    return markdown, dict(images) if isinstance(images, dict) else {}


def detect_input_suffix(content: bytes) -> str:
    """Identify the supported document type without trusting the filename."""
    if content.startswith(b"%PDF-"):
        return ".pdf"
    if content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        return ".ppt"
    if content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if "ppt/presentation.xml" in archive.namelist():
                    return ".pptx"
        except zipfile.BadZipFile:
            pass
    raise ValueError("Only PDF, PPT, and PPTX files are supported")


def convert_to_pdf(content: bytes, filename: str) -> bytes:
    """Convert a presentation to PDF; pass validated PDF input through."""
    suffix = detect_input_suffix(content)
    if suffix == ".pdf":
        return content

    timeout = int(os.getenv("LIBREOFFICE_TIMEOUT_SECONDS", "180"))
    with tempfile.TemporaryDirectory(prefix="deerflow-mineru-") as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / f"input{suffix}"
        source.write_bytes(content)
        profile = (workspace / "libreoffice-profile").as_uri()
        completed = subprocess.run(
            [
                "libreoffice",
                "--headless",
                f"-env:UserInstallation={profile}",
                "--convert-to",
                "pdf",
                "--outdir",
                str(workspace),
                str(source),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = source.with_suffix(".pdf")
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or completed.stdout or "conversion failed").strip()
            raise RuntimeError(f"LibreOffice could not convert {filename}: {detail[:500]}")
        pdf = output.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise RuntimeError(f"LibreOffice produced an invalid PDF for {filename}")
        return pdf


class EnterpriseMinerUClient:
    """Small client matching the enterprise MinerU example script."""

    def __init__(
        self,
        *,
        api_url: str,
        api_token: str,
        timeout_seconds: int = 300,
        session: Any | None = None,
    ) -> None:
        self.api_url = api_url
        self.api_token = api_token
        self.timeout_seconds = timeout_seconds
        self.session = session or requests.Session()

    def parse_pdf(self, pdf: bytes, filename: str) -> dict[str, Any]:
        response = self.session.post(
            self.api_url,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            },
            json={
                "filename": filename,
                "pdf_base64": base64.b64encode(pdf).decode("ascii"),
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("pages"), list):
            raise RuntimeError("Enterprise MinerU returned an invalid response")
        return payload


def _block_content(block: dict[str, Any]) -> str:
    content = block.get("content", "")
    if isinstance(content, str):
        return content.strip()
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False, indent=2)


def _decode_inline_image(mime: str, encoded: str) -> tuple[str, str] | None:
    normalized_mime = mime.lower()
    extension = _IMAGE_EXTENSIONS.get(normalized_mime)
    if extension is None:
        return None

    compact = re.sub(r"\s+", "", encoded)
    maximum_bytes = _int_env("MINERU_MAX_INLINE_IMAGE_BYTES", 20 * 1024 * 1024)
    if len(compact) > ((maximum_bytes + 2) // 3) * 4:
        logger.warning("Skipping oversized MinerU inline image")
        return None

    try:
        content = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("Skipping malformed MinerU inline image")
        return None
    if not content or len(content) > maximum_bytes:
        return None

    digest = hashlib.sha256(content).hexdigest()
    path = f"images/mineru-{digest[:24]}.{extension}"
    return path, base64.b64encode(content).decode("ascii")


def extract_inline_images(markdown: str) -> tuple[str, dict[str, str]]:
    """Move inline MinerU images into WeKnora's persistent image contract."""
    images: dict[str, str] = {}

    def replace_html(match: re.Match[str]) -> str:
        extracted = _decode_inline_image(match.group("mime"), match.group("data"))
        if extracted is None:
            return "[图片]"
        path, encoded = extracted
        images[path] = encoded
        return f'<img{match.group("before")}src="{path}"{match.group("after")}>'

    def replace_markdown(match: re.Match[str]) -> str:
        extracted = _decode_inline_image(match.group("mime"), match.group("data"))
        if extracted is None:
            return "[图片]"
        path, encoded = extracted
        images[path] = encoded
        return f"![{match.group('alt')}]({path})"

    markdown = _HTML_DATA_IMAGE_RE.sub(replace_html, markdown)
    markdown = _MARKDOWN_DATA_IMAGE_RE.sub(replace_markdown, markdown)
    return markdown, images


def render_pdf_page_images(
    pdf: bytes,
    page_numbers: tuple[int, ...] | None,
    *,
    pdfium_module: Any | None = None,
) -> tuple[str, dict[str, str]]:
    """Render source pages so scanned pages and slides retain visual evidence."""
    if pdfium_module is None:
        import pypdfium2 as pdfium_module

    dpi = _int_env("MINERU_SOURCE_PAGE_DPI", 110)
    jpeg_quality = _int_env("MINERU_SOURCE_PAGE_JPEG_QUALITY", 82)
    maximum_pages = _int_env("MINERU_MAX_SOURCE_PAGE_IMAGES", 200)
    document = pdfium_module.PdfDocument(pdf)
    images: dict[str, str] = {}
    references: list[str] = []
    try:
        selected_pages = tuple(range(1, len(document) + 1)) if page_numbers is None else page_numbers
        if len(selected_pages) > maximum_pages:
            logger.warning(
                "Only the first %d source page images will be retained",
                maximum_pages,
            )
            selected_pages = selected_pages[:maximum_pages]

        for page_number in selected_pages:
            if page_number < 1 or page_number > len(document):
                continue
            page = document[page_number - 1]
            bitmap = None
            image = None
            try:
                bitmap = page.render(scale=max(dpi, 72) / 72)
                image = bitmap.to_pil().convert("RGB")
                output = io.BytesIO()
                image.save(
                    output,
                    format="JPEG",
                    quality=min(max(jpeg_quality, 40), 95),
                    optimize=True,
                )
                encoded = base64.b64encode(output.getvalue()).decode("ascii")
                path = f"images/source-page-{page_number:04d}.jpg"
                images[path] = encoded
                references.append(f"![原始第 {page_number} 页]({path})")
            finally:
                _close_resource(image)
                _close_resource(bitmap)
                _close_resource(page)
    finally:
        _close_resource(document)

    return "\n\n".join(references), images


def mineru_pages_to_markdown(
    payload: dict[str, Any],
) -> tuple[str, dict[str, str]]:
    """Convert the enterprise page/block response into WeKnora markdown/assets."""
    sections: list[str] = []
    for index, page in enumerate(payload.get("pages", []), start=1):
        if not isinstance(page, dict):
            continue
        page_number = page.get("page", index)
        blocks = page.get("blocks", [])
        contents = [content for block in blocks if isinstance(block, dict) and (content := _block_content(block))]
        sections.append(f"## 第 {page_number} 页\n\n" + "\n\n".join(contents))
    markdown = "\n\n".join(sections).strip()
    markdown, images = extract_inline_images(markdown)
    if not markdown:
        raise RuntimeError("Enterprise MinerU returned no parseable content")
    return markdown, images


def weknora_response(markdown: str, images: dict[str, str] | None = None) -> dict[str, Any]:
    """Wrap markdown in WeKnora's self-hosted MinerU response schema."""
    return {"results": {"document": {"md_content": markdown, "images": images or {}}}}


def parse_upload(content: bytes, filename: str) -> dict[str, Any]:
    """Route searchable PDFs locally and OCR presentations/scanned PDFs."""
    suffix = detect_input_suffix(content)
    decision: PDFRoutingDecision | None = None
    if suffix == ".pdf":
        try:
            decision = inspect_pdf_routing(content)
        except Exception as exc:
            logger.warning(
                "PDF routing inspection failed for %s; trying the builtin parser before MinerU: %s",
                filename,
                exc,
            )
            try:
                markdown, images = parse_pdf_with_builtin(content, filename)
                return weknora_response(markdown, images)
            except Exception as builtin_exc:
                logger.warning(
                    "WeKnora builtin PDF recovery failed for %s; falling back to MinerU: %s",
                    filename,
                    builtin_exc,
                )
        else:
            logger.info(
                "PDF routing filename=%s pages=%d scanned_pages=%s route=%s",
                filename,
                decision.page_count,
                ",".join(map(str, decision.scanned_pages)) or "none",
                "mineru" if decision.use_mineru else "builtin",
            )
            if not decision.use_mineru:
                try:
                    markdown, images = parse_pdf_with_builtin(content, filename)
                    return weknora_response(markdown, images)
                except Exception as exc:
                    logger.warning(
                        "WeKnora builtin PDF parsing failed for %s; falling back to MinerU: %s",
                        filename,
                        exc,
                    )
        pdf = content
        pdf_filename = filename
    else:
        pdf = convert_to_pdf(content, filename)
        pdf_filename = f"{Path(filename).stem}.pdf"

    parsed = EnterpriseMinerUClient(
        api_url=os.environ["MINERU_API_URL"],
        api_token=os.environ["MINERU_API_TOKEN"],
        timeout_seconds=int(os.getenv("MINERU_TIMEOUT_SECONDS", "300")),
    ).parse_pdf(pdf, pdf_filename)
    markdown, images = mineru_pages_to_markdown(parsed)

    page_numbers = decision.scanned_pages if suffix == ".pdf" and decision is not None and decision.use_mineru else None
    try:
        source_page_markdown, source_page_images = render_pdf_page_images(
            pdf,
            page_numbers,
        )
    except Exception as exc:
        logger.warning(
            "Source-page rendering failed for %s; preserving parsed text without page images: %s",
            filename,
            exc,
        )
        source_page_markdown, source_page_images = "", {}
    if source_page_markdown:
        markdown = f"{markdown}\n\n## 原始页面图像\n\n{source_page_markdown}"
    images.update(source_page_images)
    return weknora_response(markdown, images)


def parse_multipart_upload(content_type: str, body: bytes) -> tuple[str, bytes]:
    """Extract WeKnora's file part without relying on the deprecated cgi module."""
    if "multipart/form-data" not in content_type.lower():
        raise ValueError("Content-Type must be multipart/form-data")
    envelope = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + body
    message = BytesParser(policy=email_policy).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("Invalid multipart upload")
    for part in message.iter_parts():
        field_name = part.get_param("name", header="content-disposition")
        if field_name not in {"files", "file"}:
            continue
        filename = Path(part.get_filename() or "upload").name
        content = part.get_payload(decode=True) or b""
        if not content:
            raise ValueError("Uploaded file is empty")
        return filename, content
    raise ValueError("Multipart upload does not contain a file")


class MinerUAdapterHandler(BaseHTTPRequestHandler):
    server_version = "DeerFlowMinerUAdapter/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        # WeKnora's built-in MinerU availability check calls /docs, while the
        # Kubernetes probes use /health. Both are intentionally side-effect free.
        if self.path.rstrip("/") in {"", "/health", "/docs"}:
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/file_parse":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            max_bytes = int(os.getenv("MINERU_MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > max_bytes:
                raise ValueError("Upload is empty or exceeds the configured size limit")
            filename, content = parse_multipart_upload(
                self.headers.get("Content-Type", ""),
                self.rfile.read(content_length),
            )
            self._json(HTTPStatus.OK, parse_upload(content, filename))
        except (KeyError, ValueError) as exc:
            logger.warning("Rejected MinerU adapter request: %s", exc)
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception as exc:
            logger.exception("MinerU adapter request failed")
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)[:500]})


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if not os.getenv("MINERU_API_URL") or not os.getenv("MINERU_API_TOKEN"):
        raise SystemExit("MINERU_API_URL and MINERU_API_TOKEN are required")
    port = int(os.getenv("MINERU_LISTEN_PORT", "50052"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MinerUAdapterHandler)
    logger.info("MinerU compatibility adapter listening on port %d", port)
    server.serve_forever()


if __name__ == "__main__":
    main()
