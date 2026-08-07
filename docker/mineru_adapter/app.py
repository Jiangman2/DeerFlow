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
import threading
import time
import uuid
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
_MARKDOWN_IMAGE_REF_RE = re.compile(
    r"""!\[(?P<alt>[^\]]*)]\(\s*(?P<path>images/[^)\s]+)\s*\)""",
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
_MULTILINGUAL_FIGURE_CAPTION_RE = re.compile(
    r"^(?:(?:Figure|Fig\.?)\s+\d+(?:[.\-]\d+)*|(?:图表|图)\s*[A-Za-z]?\s*\d+(?:[.\-—]\d+)*)\b",
    re.IGNORECASE,
)
_MULTILINGUAL_FIGURE_CAPTION_SEARCH_RE = re.compile(
    r"(?:\b(?:Figure|Fig\.?)\s+\d+(?:[.\-]\d+)*\b|(?:^|[\s：:（(])(?:图表|图)\s*[A-Za-z]?\s*\d+(?:[.\-—]\d+)*)",
    re.IGNORECASE | re.MULTILINE,
)
_ENGLISH_FIGURE_GUARD_RE = re.compile(r"\bFigure\s+\d+", re.IGNORECASE)


@dataclass(frozen=True)
class PDFRoutingDecision:
    """Result of inspecting a PDF for pages that need OCR."""

    page_count: int
    scanned_pages: tuple[int, ...]

    @property
    def use_mineru(self) -> bool:
        return bool(self.scanned_pages)


@dataclass(frozen=True)
class VisualPageEvidence:
    """A searchable page that should retain a rendered visual counterpart."""

    page_number: int
    context: str


class ParseCoordinator:
    """Serialize native document parsing while leaving HTTP probes responsive.

    PDFium and the bundled DocReader stack load native libraries that are not
    safe to enter concurrently in one process. The HTTP server remains threaded
    so health probes and upload-body reads do not block, but every call into the
    parsing pipeline passes through this single coordinator.
    """

    def __init__(self) -> None:
        self._parse_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active = 0
        self._waiting = 0

    def snapshot(self) -> dict[str, int]:
        with self._state_lock:
            return {
                "active": self._active,
                "waiting": self._waiting,
            }

    def run(
        self,
        operation: Callable[[], dict[str, Any]],
        *,
        filename: str,
        content_bytes: int,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex[:12]
        queued_at = time.monotonic()
        with self._state_lock:
            self._waiting += 1
            queue_depth = self._active + self._waiting

        logger.info(
            "Parse queued request_id=%s filename=%r bytes=%d queue_depth=%d",
            request_id,
            filename,
            content_bytes,
            queue_depth,
        )

        self._parse_lock.acquire()
        with self._state_lock:
            self._waiting -= 1
            self._active = 1
            waiting = self._waiting

        started_at = time.monotonic()
        logger.info(
            "Parse started request_id=%s filename=%r wait_seconds=%.3f waiting=%d",
            request_id,
            filename,
            started_at - queued_at,
            waiting,
        )

        status = "failed"
        try:
            result = operation()
            status = "completed"
            return result
        finally:
            elapsed = time.monotonic() - started_at
            with self._state_lock:
                self._active = 0
                waiting = self._waiting
            self._parse_lock.release()
            logger.info(
                "Parse finished request_id=%s filename=%r status=%s elapsed_seconds=%.3f waiting=%d",
                request_id,
                filename,
                status,
                elapsed,
                waiting,
            )


_PARSE_COORDINATOR = ParseCoordinator()


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


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if callable(close):
        close()


def configure_builtin_pdf_visual_capture(pdf_parser_module: Any | None = None) -> None:
    """Extend the bundled PDF parser's vector-figure trigger to Chinese captions.

    WeKnora v0.7.0 already knows how to crop and persist vector figures, but its
    fast-path guard only recognises ``Figure N``. The adapter owns this runtime
    compatibility shim so the upstream package remains untouched.
    """
    if not _bool_env("MINERU_MULTILINGUAL_FIGURE_CAPTURE", True):
        return
    if pdf_parser_module is None:
        from docreader.parser import pdf_parser as pdf_parser_module

    pdf_parser_module._FIGURE_CAPTION_RE = _MULTILINGUAL_FIGURE_CAPTION_RE
    pdf_parser_module._FIGURE_CAPTION_SEARCH_RE = _MULTILINGUAL_FIGURE_CAPTION_SEARCH_RE

    extractor = pdf_parser_module._extract_vector_figure_clips
    if getattr(extractor, "_deerflow_multilingual_caption_guard", False):
        return

    def multilingual_extractor(
        page: Any,
        page_index: int,
        plain_text: str,
        raw: Any,
        base_name: str,
        scale: float,
        quality: int,
        max_edge: int,
    ) -> list[Any]:
        guarded_text = plain_text
        if _MULTILINGUAL_FIGURE_CAPTION_SEARCH_RE.search(plain_text or "") and not _ENGLISH_FIGURE_GUARD_RE.search(plain_text or ""):
            # The bundled function has one preliminary hard-coded English
            # guard. Its actual caption/geometry work uses the module regexes
            # replaced above, so a synthetic guard line is sufficient and is
            # never included in the returned document text.
            guarded_text = f"Figure 0\n{plain_text}"
        return extractor(
            page,
            page_index,
            guarded_text,
            raw,
            base_name,
            scale,
            quality,
            max_edge,
        )

    multilingual_extractor._deerflow_multilingual_caption_guard = True
    pdf_parser_module._extract_vector_figure_clips = multilingual_extractor


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


def _visual_page_context(text: str, maximum_chars: int = 180) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
    caption = next(
        (line for line in lines if _MULTILINGUAL_FIGURE_CAPTION_SEARCH_RE.search(line)),
        "",
    )
    context = caption or " ".join(lines[:3]) or "文档视觉内容"
    return context[:maximum_chars].rstrip()


def inspect_pdf_visual_pages(
    content: bytes,
    *,
    pdfium_module: Any | None = None,
    raw_module: Any | None = None,
) -> tuple[VisualPageEvidence, ...]:
    """Find searchable pages whose pictures or vector diagrams need page evidence."""
    if pdfium_module is None:
        import pypdfium2 as pdfium_module
    if raw_module is None:
        import pypdfium2.raw as raw_module

    minimum_image_ratio = _float_env("MINERU_VISUAL_PAGE_MIN_IMAGE_RATIO", 0.01)
    minimum_vector_objects = _int_env("MINERU_VISUAL_PAGE_MIN_VECTOR_OBJECTS", 12)
    minimum_vector_ratio = _float_env("MINERU_VISUAL_PAGE_MIN_VECTOR_AREA_RATIO", 0.05)
    maximum_pages = _int_env("MINERU_MAX_SEARCHABLE_VISUAL_PAGES", 80)

    document = pdfium_module.PdfDocument(content)
    evidence: list[VisualPageEvidence] = []
    try:
        for page_index in range(len(document)):
            if len(evidence) >= maximum_pages:
                logger.warning(
                    "Only the first %d searchable visual pages will be retained",
                    maximum_pages,
                )
                break
            page = document[page_index]
            text_page = None
            try:
                width, height = page.get_size()
                page_area = max(float(width) * float(height), 1.0)
                text_page = page.get_textpage()
                text = text_page.get_text_range() or ""
                has_caption = bool(_MULTILINGUAL_FIGURE_CAPTION_SEARCH_RE.search(text))

                image_area = 0.0
                vector_bounds: list[tuple[float, float, float, float]] = []
                for page_object in page.get_objects():
                    object_type = getattr(page_object, "type", None)
                    if object_type not in {
                        raw_module.FPDF_PAGEOBJ_IMAGE,
                        raw_module.FPDF_PAGEOBJ_PATH,
                    }:
                        continue
                    try:
                        left, bottom, right, top = page_object.get_bounds()
                    except Exception:
                        continue
                    bounds = (
                        min(float(left), float(right)),
                        min(float(bottom), float(top)),
                        max(float(left), float(right)),
                        max(float(bottom), float(top)),
                    )
                    area = max(bounds[2] - bounds[0], 0.0) * max(
                        bounds[3] - bounds[1],
                        0.0,
                    )
                    if object_type == raw_module.FPDF_PAGEOBJ_IMAGE:
                        image_area += area
                    elif area / page_area >= 0.0002:
                        vector_bounds.append(bounds)

                image_ratio = min(image_area / page_area, 1.0)
                vector_ratio = 0.0
                if vector_bounds:
                    vector_bbox = (
                        min(item[0] for item in vector_bounds),
                        min(item[1] for item in vector_bounds),
                        max(item[2] for item in vector_bounds),
                        max(item[3] for item in vector_bounds),
                    )
                    vector_ratio = min(
                        (max(vector_bbox[2] - vector_bbox[0], 0.0) * max(vector_bbox[3] - vector_bbox[1], 0.0)) / page_area,
                        1.0,
                    )

                has_visual = has_caption or image_ratio >= minimum_image_ratio or (len(vector_bounds) >= minimum_vector_objects and vector_ratio >= minimum_vector_ratio)
                if has_visual:
                    evidence.append(
                        VisualPageEvidence(
                            page_number=page_index + 1,
                            context=_visual_page_context(text),
                        )
                    )
            finally:
                _close_resource(text_page)
                _close_resource(page)
    finally:
        _close_resource(document)

    return tuple(evidence)


def parse_pdf_with_builtin(
    content: bytes,
    filename: str,
    *,
    parser_factory: Callable[[], Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """Parse a searchable PDF with the WeKnora parser bundled in this image."""
    if parser_factory is None:
        from docreader.parser import Parser

        configure_builtin_pdf_visual_capture()
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


def _document_from_parser(
    *,
    content: bytes,
    filename: str,
    file_type: str,
    parser_factory: Callable[[], Any],
) -> tuple[str, dict[str, str]]:
    document = parser_factory().parse_file(
        file_name=filename,
        file_type=file_type,
        content=content,
        parser_engine="builtin",
    )
    markdown = str(getattr(document, "content", "") or "").strip()
    images = getattr(document, "images", None)
    return markdown, dict(images) if isinstance(images, dict) else {}


def merge_document_visuals(
    markdown: str,
    images: dict[str, str],
    visual_markdown: str,
    visual_images: dict[str, str],
) -> tuple[str, dict[str, str]]:
    """Merge parser-produced visual evidence without duplicating image payloads."""
    merged_images = dict(images)
    known_payloads = set(merged_images.values())
    added_paths: list[str] = []
    for path, encoded in visual_images.items():
        if path in merged_images or encoded in known_payloads:
            continue
        merged_images[path] = encoded
        known_payloads.add(encoded)
        added_paths.append(path)

    if not added_paths:
        return markdown, merged_images

    lines = visual_markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    references: list[str] = []
    for path in added_paths:
        reference_index = next(
            (index for index, line in enumerate(lines) if path in line and ("![" in line or "<img" in line.lower())),
            None,
        )
        if reference_index is None:
            references.append(f"![文档视觉内容]({path})")
            continue

        reference = lines[reference_index].strip()
        heading = ""
        for offset in (-1, -2):
            candidate_index = reference_index + offset
            if candidate_index < 0:
                continue
            candidate = lines[candidate_index].strip()
            if candidate.startswith("#"):
                heading = candidate
                break
        caption = ""
        for offset in (1, 2, -1, -2):
            candidate_index = reference_index + offset
            if candidate_index < 0 or candidate_index >= len(lines):
                continue
            candidate = lines[candidate_index].strip()
            if candidate and _MULTILINGUAL_FIGURE_CAPTION_SEARCH_RE.search(candidate):
                caption = candidate
                break
        block = "\n\n".join(part for part in (heading, reference, caption) if part)
        references.append(block)

    supplement = "\n\n".join(references)
    merged_markdown = f"{markdown}\n\n## 文档视觉内容\n\n{supplement}" if markdown else f"## 文档视觉内容\n\n{supplement}"
    return merged_markdown, merged_images


def parse_word_with_builtin(
    content: bytes,
    filename: str,
    suffix: str,
    *,
    parser_factory: Callable[[], Any] | None = None,
    docx_parser_factory: Callable[..., Any] | None = None,
) -> tuple[str, dict[str, str]]:
    """Keep native Word structure and explicitly recover DOCX inline images."""
    if parser_factory is None:
        from docreader.parser import Parser

        parser_factory = Parser

    markdown, images = _document_from_parser(
        content=content,
        filename=filename,
        file_type=suffix.lstrip("."),
        parser_factory=parser_factory,
    )
    if suffix != ".docx":
        return markdown, images

    # The bundled DOCX chain tries MarkItDown first. It is excellent for text
    # but may finish before the image-aware parser runs, so execute that parser
    # once as a visual supplement while retaining the primary text.
    if docx_parser_factory is None:
        from docreader.parser.docx_parser import DocxParser

        docx_parser_factory = DocxParser
    try:
        visual_document = docx_parser_factory(
            file_name=filename,
            file_type="docx",
        ).parse(content)
        visual_markdown = str(getattr(visual_document, "content", "") or "").strip()
        visual_images = getattr(visual_document, "images", None)
        return merge_document_visuals(
            markdown,
            images,
            visual_markdown,
            dict(visual_images) if isinstance(visual_images, dict) else {},
        )
    except Exception:
        logger.warning(
            "Image-aware DOCX parsing failed for %s; continuing with primary Word text.",
            filename,
            exc_info=True,
        )
        return markdown, images


_CFB_FREE_SECTOR = 0xFFFFFFFF
_CFB_END_OF_CHAIN = 0xFFFFFFFE
_CFB_NO_STREAM = 0xFFFFFFFF


def _compound_document_root_streams(content: bytes) -> set[str]:
    """Read root stream names from a bounded CFB/OLE2 directory.

    This deliberately implements only the structural subset needed to
    identify legacy Word and PowerPoint files. It never opens stream payloads,
    follows MiniFAT chains, executes macros, or trusts unbounded sector links.
    """
    if len(content) < 512 or content[:8] != bytes.fromhex("D0CF11E0A1B11AE1"):
        raise ValueError("Invalid legacy Office compound document header")
    if int.from_bytes(content[28:30], "little") != 0xFFFE:
        raise ValueError("Unsupported compound document byte order")

    sector_shift = int.from_bytes(content[30:32], "little")
    if sector_shift not in {9, 12}:
        raise ValueError("Unsupported compound document sector size")
    sector_size = 1 << sector_shift
    if len(content) < sector_size:
        raise ValueError("Truncated compound document header")
    sector_count = (len(content) - sector_size) // sector_size
    if sector_count <= 0:
        raise ValueError("Compound document has no sectors")

    def read_sector(sector_id: int) -> bytes:
        if sector_id >= sector_count:
            raise ValueError("Compound document sector is out of range")
        start = (sector_id + 1) * sector_size
        end = start + sector_size
        if end > len(content):
            raise ValueError("Truncated compound document sector")
        return content[start:end]

    fat_sector_count = int.from_bytes(content[44:48], "little")
    if fat_sector_count <= 0 or fat_sector_count > sector_count:
        raise ValueError("Invalid compound document FAT size")

    fat_sector_ids = [
        int.from_bytes(content[offset : offset + 4], "little")
        for offset in range(76, 512, 4)
    ]
    fat_sector_ids = [sector_id for sector_id in fat_sector_ids if sector_id != _CFB_FREE_SECTOR]

    difat_sector_id = int.from_bytes(content[68:72], "little")
    difat_sector_count = int.from_bytes(content[72:76], "little")
    seen_difat: set[int] = set()
    for _ in range(min(difat_sector_count, sector_count)):
        if difat_sector_id in {_CFB_END_OF_CHAIN, _CFB_FREE_SECTOR}:
            break
        if difat_sector_id in seen_difat:
            raise ValueError("Compound document DIFAT contains a cycle")
        seen_difat.add(difat_sector_id)
        difat_sector = read_sector(difat_sector_id)
        fat_sector_ids.extend(
            int.from_bytes(difat_sector[offset : offset + 4], "little")
            for offset in range(0, sector_size - 4, 4)
            if int.from_bytes(difat_sector[offset : offset + 4], "little") != _CFB_FREE_SECTOR
        )
        difat_sector_id = int.from_bytes(difat_sector[-4:], "little")
    if difat_sector_count > len(seen_difat) and difat_sector_id not in {
        _CFB_END_OF_CHAIN,
        _CFB_FREE_SECTOR,
    }:
        raise ValueError("Compound document DIFAT exceeds file bounds")

    if len(fat_sector_ids) < fat_sector_count:
        raise ValueError("Compound document FAT is incomplete")
    fat: list[int] = []
    for sector_id in fat_sector_ids[:fat_sector_count]:
        sector = read_sector(sector_id)
        fat.extend(
            int.from_bytes(sector[offset : offset + 4], "little")
            for offset in range(0, sector_size, 4)
        )

    directory_sector_id = int.from_bytes(content[48:52], "little")
    directory_data = bytearray()
    seen_directory: set[int] = set()
    while directory_sector_id != _CFB_END_OF_CHAIN:
        if directory_sector_id in seen_directory:
            raise ValueError("Compound document directory contains a cycle")
        if directory_sector_id >= len(fat) or len(seen_directory) >= sector_count:
            raise ValueError("Compound document directory exceeds file bounds")
        seen_directory.add(directory_sector_id)
        directory_data.extend(read_sector(directory_sector_id))
        directory_sector_id = fat[directory_sector_id]

    entries: list[tuple[str, int, int, int, int]] = []
    for offset in range(0, len(directory_data), 128):
        entry = directory_data[offset : offset + 128]
        if len(entry) < 128:
            break
        name_bytes = int.from_bytes(entry[64:66], "little")
        object_type = entry[66]
        if name_bytes < 2 or name_bytes > 64 or name_bytes % 2:
            name = ""
        else:
            try:
                name = bytes(entry[: name_bytes - 2]).decode("utf-16le")
            except UnicodeDecodeError as exc:
                raise ValueError("Compound document directory name is invalid") from exc
        entries.append(
            (
                name,
                object_type,
                int.from_bytes(entry[68:72], "little"),
                int.from_bytes(entry[72:76], "little"),
                int.from_bytes(entry[76:80], "little"),
            )
        )

    root_index = next((index for index, entry in enumerate(entries) if entry[1] == 5), None)
    if root_index is None:
        raise ValueError("Compound document has no root storage")

    root_streams: set[str] = set()
    pending = [entries[root_index][4]]
    visited: set[int] = set()
    while pending:
        entry_index = pending.pop()
        if entry_index == _CFB_NO_STREAM:
            continue
        if entry_index >= len(entries) or entry_index in visited:
            if entry_index in visited:
                raise ValueError("Compound document directory tree contains a cycle")
            raise ValueError("Compound document directory entry is out of range")
        visited.add(entry_index)
        name, object_type, left, right, _child = entries[entry_index]
        pending.extend((left, right))
        if object_type == 2 and name:
            root_streams.add(name)
    return root_streams


def _detect_legacy_office_suffix(content: bytes) -> str:
    """Identify a legacy Word/PowerPoint compound document by its root stream.

    WeKnora 0.7.0 may forward an uploaded legacy Office document with the
    generic multipart filename ``document``. Unlike OOXML ZIP containers, the
    OLE2 signature alone cannot distinguish Word from PowerPoint. Their root
    streams can: Word uses ``WordDocument`` and PowerPoint uses
    ``PowerPoint Document``. Nested streams are intentionally ignored because
    a Word document may embed a PowerPoint object (and vice versa).
    """
    root_streams = _compound_document_root_streams(content)

    if "WordDocument" in root_streams:
        return ".doc"
    if "PowerPoint Document" in root_streams:
        return ".ppt"
    raise ValueError("Unsupported or encrypted legacy Office compound document")


def detect_input_suffix(content: bytes, filename: str | None = None) -> str:
    """Identify the supported document type without trusting the filename."""
    if content.startswith(b"%PDF-"):
        return ".pdf"
    if content.startswith(bytes.fromhex("D0CF11E0A1B11AE1")):
        hinted_suffix = Path(filename or "").suffix.lower()
        try:
            return _detect_legacy_office_suffix(content)
        except ValueError:
            # Preserve support for valid legacy files whose directory cannot
            # be inspected (for example, unusual but explicitly named input).
            # Generic WeKnora names must still be identified from content.
            if hinted_suffix in {".doc", ".ppt"}:
                return hinted_suffix
            raise
    if content.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                if "ppt/presentation.xml" in names:
                    return ".pptx"
                if "word/document.xml" in names:
                    return ".docx"
        except zipfile.BadZipFile:
            pass
    raise ValueError("Only PDF, DOC, DOCX, PPT, and PPTX files are supported")


def convert_to_pdf(content: bytes, filename: str) -> bytes:
    """Convert an Office document to PDF; pass validated PDF input through."""
    suffix = detect_input_suffix(content, filename)
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


def preserve_searchable_pdf_visuals(
    pdf: bytes,
    markdown: str,
    images: dict[str, str],
) -> tuple[str, dict[str, str]]:
    """Attach retrievable page evidence for figures on searchable PDF pages."""
    try:
        evidence = inspect_pdf_visual_pages(pdf)
        if not evidence:
            return markdown, images
        _generic_markdown, page_images = render_pdf_page_images(
            pdf,
            tuple(item.page_number for item in evidence),
        )
    except Exception:
        logger.warning(
            "Searchable visual-page preservation failed; keeping parsed text and images.",
            exc_info=True,
        )
        return markdown, images

    blocks: list[str] = []
    for item in evidence:
        path = f"images/source-page-{item.page_number:04d}.jpg"
        if path not in page_images:
            continue
        safe_context = item.context.replace("[", " ").replace("]", " ").strip()
        blocks.append(
            "\n\n".join(
                (
                    f"### 第 {item.page_number} 页视觉证据",
                    f"![{safe_context}]({path})",
                    item.context,
                )
            )
        )
    if not blocks:
        return markdown, images
    return merge_document_visuals(
        markdown,
        images,
        "\n\n".join(blocks),
        page_images,
    )


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
    """Preserve searchable documents locally and OCR image-dominated input."""
    suffix = detect_input_suffix(content, filename)
    decision: PDFRoutingDecision | None = None
    searchable_document = suffix in {".pdf", ".doc", ".docx"}
    native_word: tuple[str, dict[str, str]] | None = None
    if suffix in {".doc", ".docx"}:
        try:
            native_word = parse_word_with_builtin(content, filename, suffix)
        except Exception:
            logger.warning(
                "Native Word parsing failed for %s; continuing through PDF visual fallback.",
                filename,
                exc_info=True,
            )
    if suffix == ".pdf":
        pdf = content
        pdf_filename = filename
    else:
        pdf = convert_to_pdf(content, filename)
        pdf_filename = f"{Path(filename).stem}.pdf"

    def finalize_searchable(
        parsed_markdown: str,
        parsed_images: dict[str, str],
    ) -> dict[str, Any]:
        visual_markdown, visual_images = preserve_searchable_pdf_visuals(
            pdf,
            parsed_markdown,
            parsed_images,
        )
        if native_word is None:
            return weknora_response(visual_markdown, visual_images)
        merged_markdown, merged_images = merge_document_visuals(
            native_word[0],
            native_word[1],
            visual_markdown,
            visual_images,
        )
        return weknora_response(merged_markdown, merged_images)

    if searchable_document:
        try:
            decision = inspect_pdf_routing(pdf)
        except Exception as exc:
            logger.warning(
                "PDF routing inspection failed for %s; trying the builtin parser before MinerU: %s",
                filename,
                exc,
            )
            try:
                markdown, images = parse_pdf_with_builtin(pdf, pdf_filename)
                return finalize_searchable(markdown, images)
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
                    markdown, images = parse_pdf_with_builtin(pdf, pdf_filename)
                    return finalize_searchable(markdown, images)
                except Exception as exc:
                    logger.warning(
                        "WeKnora builtin PDF parsing failed for %s; falling back to MinerU: %s",
                        filename,
                        exc,
                    )

    parsed = EnterpriseMinerUClient(
        api_url=os.environ["MINERU_API_URL"],
        api_token=os.environ["MINERU_API_TOKEN"],
        timeout_seconds=int(os.getenv("MINERU_TIMEOUT_SECONDS", "300")),
    ).parse_pdf(pdf, pdf_filename)
    markdown, images = mineru_pages_to_markdown(parsed)

    page_numbers = decision.scanned_pages if searchable_document and decision is not None and decision.use_mineru else None
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
    if native_word is not None:
        markdown, images = merge_document_visuals(
            native_word[0],
            native_word[1],
            markdown,
            images,
        )
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
        normalized_path = self.path.rstrip("/")
        if normalized_path == "/health":
            self._json(
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "parser": _PARSE_COORDINATOR.snapshot(),
                },
            )
            return
        if normalized_path in {"", "/docs"}:
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
            parsed = _PARSE_COORDINATOR.run(
                lambda: parse_upload(content, filename),
                filename=filename,
                content_bytes=len(content),
            )
            self._json(HTTPStatus.OK, parsed)
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
