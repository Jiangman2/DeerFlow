"""Translate WeKnora's MinerU protocol to the enterprise PDF parsing API."""

from __future__ import annotations

import base64
import binascii
import ctypes
import hashlib
import io
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
import xml.etree.ElementTree as ET
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
_POSIX_PROCESS_GROUPS = os.name == "posix"
_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)
_CHILD_SUBREAPER_ENABLED = False
_PR_SET_CHILD_SUBREAPER = 36

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
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_NATIVE_IMAGE_DATA_RE = re.compile(
    r"^data:(?P<mime>image/[a-z0-9.+-]+);base64,(?P<data>.*)$",
    re.IGNORECASE | re.DOTALL,
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
_IMAGE_MIME_BY_SUFFIX = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
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


@dataclass(frozen=True)
class DOCXWorkload:
    """Central-directory workload facts used before entering a DOCX parser."""

    compressed_bytes: int
    uncompressed_bytes: int
    document_xml_bytes: int
    entry_count: int

    @property
    def expansion_ratio(self) -> float:
        return self.uncompressed_bytes / max(self.compressed_bytes, 1)


class ParseCoordinator:
    """Admit at most one native parse while leaving probes responsive.

    PDFium and the bundled DocReader stack load native libraries that are not
    safe to enter concurrently in one process. The HTTP server remains threaded
    so health probes and upload-body reads do not block. Queuing belongs to the
    dispatcher/Redis layer; a worker that is already parsing rejects another
    request instead of accumulating an invisible in-process queue.
    """

    def __init__(self) -> None:
        self._parse_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active = 0
        self._rejected = 0

    def snapshot(self) -> dict[str, int]:
        with self._state_lock:
            return {
                "active": self._active,
                "waiting": 0,
                "rejected": self._rejected,
            }

    def run(
        self,
        operation: Callable[[], dict[str, Any]],
        *,
        filename: str,
        content_bytes: int,
    ) -> dict[str, Any]:
        request_id = uuid.uuid4().hex[:12]
        if not self._parse_lock.acquire(blocking=False):
            with self._state_lock:
                self._rejected += 1
                rejected = self._rejected
            logger.warning(
                "Parse rejected because worker is busy request_id=%s filename=%r bytes=%d rejected_total=%d",
                request_id,
                filename,
                content_bytes,
                rejected,
            )
            raise ParseWorkerBusy("Document parser worker is already processing a file")

        with self._state_lock:
            self._active = 1

        started_at = time.monotonic()
        logger.info(
            "Parse started request_id=%s filename=%r bytes=%d",
            request_id,
            filename,
            content_bytes,
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
            self._parse_lock.release()
            logger.info(
                "Parse finished request_id=%s filename=%r status=%s elapsed_seconds=%.3f",
                request_id,
                filename,
                status,
                elapsed,
            )


_PARSE_COORDINATOR = ParseCoordinator()


class DispatchQueueFull(RuntimeError):
    """Raised when the bounded dispatcher queue cannot accept more uploads."""


class DispatchUnavailable(RuntimeError):
    """Raised when no healthy serialized parser worker becomes available."""


class ParseWorkerBusy(RuntimeError):
    """Raised when a direct request reaches a worker that is already parsing."""


class ParseDispatcher:
    """Priority-aware dispatcher for the serialized parser worker Pods.

    Each worker process remains strictly single-parse.  The dispatcher owns the
    cross-Pod queue so Kubernetes connection reuse cannot accidentally pin most
    of the document traffic to one Pod.  Interactive DeerFlow chat uploads may
    move ahead of queued knowledge-base batch work, but never interrupt a parse
    that has already started.
    """

    _PRIORITY_RANK = {"interactive": 0, "bulk": 1}

    def __init__(
        self,
        *,
        backend_host: str,
        backend_port: int,
        maximum_waiting: int,
        minimum_backends: int,
        wait_timeout_seconds: float,
        request_timeout_seconds: float,
        interactive_request_timeout_seconds: float | None = None,
        failure_cooldown_seconds: float = 60.0,
        probe_timeout_seconds: float = 0.25,
        busy_retry_limit: int = 3,
        session: Any | None = None,
        resolver: Callable[[str], list[str]] | None = None,
        prober: Callable[[str, str], bool] | None = None,
    ) -> None:
        if backend_port <= 0:
            raise ValueError("Dispatcher backend port must be greater than zero")
        if maximum_waiting <= 0:
            raise ValueError("Dispatcher queue size must be greater than zero")
        if minimum_backends <= 0:
            raise ValueError("Dispatcher minimum backend count must be positive")
        self.backend_host = backend_host
        self.backend_port = backend_port
        self.maximum_waiting = maximum_waiting
        self.minimum_backends = minimum_backends
        self.wait_timeout_seconds = wait_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.interactive_request_timeout_seconds = request_timeout_seconds if interactive_request_timeout_seconds is None else interactive_request_timeout_seconds
        self.failure_cooldown_seconds = max(1.0, failure_cooldown_seconds)
        self.probe_timeout_seconds = max(0.05, probe_timeout_seconds)
        self.busy_retry_limit = max(0, busy_retry_limit)
        self._condition = threading.Condition()
        self._pending: list[tuple[int, int, str]] = []
        self._busy: set[str] = set()
        # A failed/ambiguous request remains quarantined until the worker's
        # /ready endpoint proves that its parser is idle. The timestamp is the
        # earliest next probe, not an automatic quarantine expiry.
        self._quarantined: dict[str, float] = {}
        self._known: tuple[str, ...] = ()
        self._sequence = 0
        self._cursor = 0
        self._resolver = resolver or self._resolve_ipv4
        self._prober = prober
        if session is None:
            session = requests.Session()
            session.trust_env = False
        self._session = session

    @staticmethod
    def _resolve_ipv4(host: str) -> list[str]:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(
                host,
                None,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }
        return sorted(addresses)

    def _discover(self) -> tuple[str, ...]:
        try:
            addresses = tuple(sorted(set(self._resolver(self.backend_host))))
        except OSError as exc:
            logger.warning(
                "MinerU dispatcher backend discovery failed host=%s error=%s",
                self.backend_host,
                type(exc).__name__,
            )
            addresses = ()
        with self._condition:
            self._known = addresses
            known = set(addresses)
            self._quarantined = {address: deadline for address, deadline in self._quarantined.items() if address in known}
        return addresses

    def _probe_backend(self, backend: str, path: str) -> bool:
        if self._prober is not None:
            return bool(self._prober(backend, path))
        try:
            response = self._session.get(
                f"http://{backend}:{self.backend_port}{path}",
                timeout=(self.probe_timeout_seconds, self.probe_timeout_seconds),
            )
        except requests.RequestException:
            return False
        if int(response.status_code) != HTTPStatus.OK:
            return False
        if path != "/ready":
            return True
        try:
            payload = response.json()
        except (TypeError, ValueError):
            return False
        parser = payload.get("parser") if isinstance(payload, dict) else None
        return bool(payload.get("role") == "worker" and isinstance(parser, dict) and int(parser.get("active", 1)) == 0 and int(parser.get("waiting", 1)) == 0)

    def _ready_backends(self, candidates: tuple[str, ...]) -> tuple[str, ...]:
        ready: list[str] = []
        now = time.monotonic()
        for backend in candidates:
            with self._condition:
                retry_at = self._quarantined.get(backend)
            if retry_at is not None and retry_at > now:
                continue
            if self._probe_backend(backend, "/ready"):
                with self._condition:
                    self._quarantined.pop(backend, None)
                ready.append(backend)
            elif retry_at is not None:
                with self._condition:
                    self._quarantined[backend] = time.monotonic() + self.failure_cooldown_seconds
        return tuple(ready)

    def _mark_backend_unhealthy(self, backend: str, *, reason: str) -> None:
        with self._condition:
            deadline = time.monotonic() + self.failure_cooldown_seconds
            self._quarantined[backend] = deadline
            self._condition.notify_all()
        logger.warning(
            "MinerU dispatcher quarantined backend=%s cooldown_seconds=%.1f reason=%s",
            backend,
            self.failure_cooldown_seconds,
            reason,
        )

    def snapshot(self) -> dict[str, Any]:
        discovered = self._discover()
        # A quarantined worker must be able to recover even while the
        # dispatcher is not receiving parse requests.  Otherwise Kubernetes
        # could remove the dispatcher from Service endpoints after the last
        # usable worker is quarantined, leaving no request path that calls
        # _ready_backends() and clears the quarantine.
        now = time.monotonic()
        with self._condition:
            due_for_reprobe = tuple(address for address, retry_at in self._quarantined.items() if address in discovered and retry_at <= now)
        if due_for_reprobe:
            self._ready_backends(due_for_reprobe)
        live = tuple(address for address in discovered if self._probe_backend(address, "/live"))
        with self._condition:
            usable_live = tuple(address for address in live if address not in self._quarantined)
            return {
                "discovered": len(discovered),
                "minimum": self.minimum_backends,
                "busy": len(self._busy),
                "waiting": len(self._pending),
                "live": len(live),
                "quarantined": len(self._quarantined),
                # Continue serving in degraded mode while at least one worker
                # is usable. Requiring the full target capacity here turned a
                # single worker restart into an outage of the dispatcher Pod.
                "ready": bool(usable_live),
                "capacity_target_met": len(usable_live) >= self.minimum_backends,
            }

    def _acquire_backend(self, priority: str) -> tuple[str, str, float]:
        normalized_priority = priority.strip().lower()
        rank = self._PRIORITY_RANK.get(normalized_priority, 1)
        ticket_id = uuid.uuid4().hex[:12]
        queued_at = time.monotonic()
        deadline = queued_at + self.wait_timeout_seconds
        with self._condition:
            if len(self._pending) >= self.maximum_waiting:
                raise DispatchQueueFull("Document parser dispatcher queue is full; retry later")
            self._sequence += 1
            ticket = (rank, self._sequence, ticket_id)
            self._pending.append(ticket)

        while True:
            self._discover()
            with self._condition:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    if ticket in self._pending:
                        self._pending.remove(ticket)
                    self._condition.notify_all()
                    raise DispatchUnavailable("Timed out waiting for an available document parser worker")
                head = min(self._pending) if self._pending else None
                candidates = tuple(address for address in self._known if address not in self._busy)
            if head == ticket and candidates:
                start = self._cursor % len(candidates)
                ordered = candidates[start:] + candidates[:start]
                for backend in ordered:
                    # Stop at the first ready worker. In the healthy case this
                    # adds one small same-node readiness request rather than
                    # probing all six workers before every parse assignment.
                    if not self._ready_backends((backend,)):
                        continue
                    with self._condition:
                        # Revalidate the ticket and busy state after the
                        # network probe because another dispatcher thread may
                        # have assigned this worker in the meantime.
                        head = min(self._pending) if self._pending else None
                        if head == ticket and backend in self._known and backend not in self._busy:
                            self._cursor += 1
                            self._pending.remove(ticket)
                            self._busy.add(backend)
                            return backend, ticket_id, time.monotonic() - queued_at
            with self._condition:
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    self._condition.wait(timeout=min(0.5, remaining))

    def _release_backend(self, backend: str) -> None:
        with self._condition:
            self._busy.discard(backend)
            self._condition.notify_all()

    def forward(
        self,
        *,
        body: bytes,
        content_type: str,
        priority: str,
    ) -> tuple[int, str, bytes, str]:
        normalized_priority = priority.strip().lower()
        request_timeout_seconds = self.interactive_request_timeout_seconds if normalized_priority == "interactive" else self.request_timeout_seconds
        parse_profile = "conversation" if normalized_priority == "interactive" else "knowledge"
        for busy_attempt in range(self.busy_retry_limit + 1):
            backend, request_id, wait_seconds = self._acquire_backend(normalized_priority)
            backend_url = f"http://{backend}:{self.backend_port}/file_parse"
            logger.info(
                "Dispatch started request_id=%s priority=%s backend=%s wait_seconds=%.3f bytes=%d busy_attempt=%d",
                request_id,
                priority,
                backend,
                wait_seconds,
                len(body),
                busy_attempt,
            )
            started_at = time.monotonic()
            retry_busy = False
            try:
                response = self._session.post(
                    backend_url,
                    headers={
                        "Content-Type": content_type,
                        "X-DeerFlow-Parse-Profile": parse_profile,
                    },
                    data=body,
                    timeout=(5.0, request_timeout_seconds),
                )
                status_code = int(response.status_code)
                if status_code == HTTPStatus.TOO_MANY_REQUESTS:
                    # The worker returns 429 only when its non-blocking parse
                    # lock rejected the request before any work began. This is
                    # the only response that can be retried safely elsewhere.
                    self._mark_backend_unhealthy(
                        backend,
                        reason="HTTP 429 busy admission race",
                    )
                    retry_busy = busy_attempt < self.busy_retry_limit
                elif status_code >= 500:
                    self._mark_backend_unhealthy(
                        backend,
                        reason=f"HTTP {status_code}",
                    )
                if not retry_busy:
                    return (
                        status_code,
                        response.headers.get(
                            "Content-Type",
                            "application/json; charset=utf-8",
                        ),
                        bytes(response.content),
                        backend,
                    )
            except requests.RequestException as exc:
                self._mark_backend_unhealthy(
                    backend,
                    reason=type(exc).__name__,
                )
                logger.warning(
                    "Dispatch transport failure request_id=%s backend=%s error=%s",
                    request_id,
                    backend,
                    type(exc).__name__,
                )
                # A transport failure after delivery is ambiguous. Never
                # resubmit it because that could duplicate a provider job.
                raise DispatchUnavailable(f"Document parser worker transport failed: {type(exc).__name__}") from exc
            finally:
                self._release_backend(backend)
                logger.info(
                    "Dispatch finished request_id=%s backend=%s elapsed_seconds=%.3f",
                    request_id,
                    backend,
                    time.monotonic() - started_at,
                )
            if retry_busy:
                logger.info(
                    "Dispatch retrying request_id=%s after worker busy response",
                    request_id,
                )

        raise DispatchUnavailable("No parser worker accepted the document")


_PARSE_DISPATCHER: ParseDispatcher | None = None
_PARSE_DISPATCHER_LOCK = threading.Lock()


def adapter_mode() -> str:
    mode = os.getenv("MINERU_ADAPTER_MODE", "worker").strip().lower()
    if mode not in {"worker", "dispatcher"}:
        raise ValueError("MINERU_ADAPTER_MODE must be worker or dispatcher")
    return mode


def parse_dispatcher() -> ParseDispatcher:
    global _PARSE_DISPATCHER
    with _PARSE_DISPATCHER_LOCK:
        if _PARSE_DISPATCHER is None:
            _PARSE_DISPATCHER = ParseDispatcher(
                backend_host=os.getenv(
                    "MINERU_DISPATCH_BACKEND_HOST",
                    "deer-flow-mineru-adapter-workers",
                ).strip(),
                backend_port=_int_env("MINERU_DISPATCH_BACKEND_PORT", 50052),
                maximum_waiting=_int_env("MINERU_DISPATCH_MAX_WAITING", 16),
                minimum_backends=_int_env("MINERU_DISPATCH_MIN_BACKENDS", 3),
                wait_timeout_seconds=_float_env(
                    "MINERU_DISPATCH_WAIT_TIMEOUT_SECONDS",
                    45.0,
                ),
                request_timeout_seconds=_float_env(
                    "MINERU_DISPATCH_REQUEST_TIMEOUT_SECONDS",
                    1800.0,
                ),
                interactive_request_timeout_seconds=_float_env(
                    "MINERU_DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS",
                    550.0,
                ),
                failure_cooldown_seconds=_float_env(
                    "MINERU_DISPATCH_FAILURE_COOLDOWN_SECONDS",
                    60.0,
                ),
                probe_timeout_seconds=_float_env(
                    "MINERU_DISPATCH_PROBE_TIMEOUT_SECONDS",
                    0.25,
                ),
                busy_retry_limit=_int_env(
                    "MINERU_DISPATCH_BUSY_RETRY_LIMIT",
                    3,
                ),
            )
        return _PARSE_DISPATCHER


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


def _parse_word_with_builtin_in_process(
    content: bytes,
    filename: str,
    suffix: str,
    *,
    parser_factory: Callable[[], Any] | None = None,
    bypass_markitdown: bool = False,
) -> tuple[str, dict[str, str]]:
    """Parse Word with DocReader's native first-success parser chain.

    DocReader already routes DOCX through its ``FirstParser`` implementation:
    MarkItDown is authoritative when it returns readable content and the
    image-aware DOCX parser is only a fallback for an empty/failed primary
    result. Running ``DocxParser`` a second time here defeated that contract,
    doubled work, and exposed successful text parses to its process-pool
    failure mode.
    """
    if bypass_markitdown:
        if suffix.lower() != ".docx":
            raise ValueError("MarkItDown bypass is only valid for DOCX")
        if parser_factory is None:
            from docreader.parser.docx_parser import DocxParser

            parser = DocxParser(file_name=filename, file_type="docx")
        else:
            parser = parser_factory()
        document = parser.parse(content)
        markdown = str(getattr(document, "content", "") or "").strip()
        images = getattr(document, "images", None)
        return markdown, dict(images) if isinstance(images, dict) else {}

    if parser_factory is None:
        from docreader.parser import Parser

        parser_factory = Parser

    return _document_from_parser(
        content=content,
        filename=filename,
        file_type=suffix.lstrip("."),
        parser_factory=parser_factory,
    )


def parse_word_with_builtin(
    content: bytes,
    filename: str,
    suffix: str,
    *,
    parser_factory: Callable[[], Any] | None = None,
    bypass_markitdown: bool = False,
) -> tuple[str, dict[str, str]]:
    """Run DocReader in a bounded child unless a test supplies its parser."""

    if parser_factory is not None:
        return _parse_word_with_builtin_in_process(
            content,
            filename,
            suffix,
            parser_factory=parser_factory,
            bypass_markitdown=bypass_markitdown,
        )

    timeout = _float_env("DEERFLOW_DOCREADER_WORD_TIMEOUT_SECONDS", 120.0)
    maximum_output_bytes = _int_env("DEERFLOW_DOCREADER_WORD_MAX_OUTPUT_BYTES", 104_857_600)
    worker = Path(
        os.getenv(
            "DEERFLOW_DOCREADER_WORKER_PATH",
            str(Path(__file__).with_name("docreader_worker.py")),
        )
    )
    if not worker.is_file():
        raise RuntimeError(f"DocReader worker is missing: {worker}")

    with tempfile.TemporaryDirectory(prefix="deerflow-docreader-") as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / f"input{suffix.lower()}"
        output = workspace / "result.json"
        source.write_bytes(content)
        source.chmod(0o600)
        mode = "native-docx" if bypass_markitdown else "first-success"
        command = [sys.executable, str(worker), mode, str(source), str(output)]
        try:
            completed = _run_converter_command(
                command,
                timeout=timeout,
                env=_isolated_local_parser_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"DocReader timed out after {timeout:g}s while parsing {filename}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "<empty>").strip().replace("\n", " ")[:500]
            raise RuntimeError(f"DocReader could not parse {filename}: returncode={completed.returncode}; stderr={detail}")
        if not output.is_file():
            raise RuntimeError(f"DocReader did not produce a result for {filename}")
        output_size = output.stat().st_size
        if output_size <= 0 or output_size > maximum_output_bytes:
            raise RuntimeError(f"DocReader produced an invalid result size for {filename}: bytes={output_size}")
        try:
            payload = json.loads(output.read_text(encoding="utf-8"))
            markdown = payload["markdown"]
            images = payload["images"]
        except (KeyError, TypeError, ValueError, UnicodeError) as exc:
            raise RuntimeError(f"DocReader produced an invalid result for {filename}") from exc
        if not isinstance(markdown, str) or not isinstance(images, dict):
            raise RuntimeError(f"DocReader produced an invalid result for {filename}")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in images.items()):
            raise RuntimeError(f"DocReader produced invalid image data for {filename}")
        return markdown, images


def inspect_docx_workload(content: bytes) -> DOCXWorkload:
    """Inspect OOXML sizes without expanding document parts into memory."""

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("Invalid DOCX container") from exc

    document_parts = [entry for entry in entries if entry.filename == "word/document.xml"]
    if len(document_parts) != 1:
        raise ValueError("DOCX must contain exactly one word/document.xml part")
    if any(entry.flag_bits & 0x1 for entry in entries):
        raise ValueError("Encrypted DOCX containers are not supported")

    return DOCXWorkload(
        compressed_bytes=len(content),
        uncompressed_bytes=sum(entry.file_size for entry in entries),
        document_xml_bytes=document_parts[0].file_size,
        entry_count=len(entries),
    )


def docx_requires_large_document_route(workload: DOCXWorkload) -> bool:
    """Keep structurally large OOXML away from unbounded MarkItDown parsing."""

    return (
        workload.compressed_bytes > _int_env("DEERFLOW_DOCX_MARKITDOWN_MAX_INPUT_BYTES", 16 * 1024 * 1024)
        or workload.uncompressed_bytes
        > _int_env("DEERFLOW_DOCX_MARKITDOWN_MAX_UNCOMPRESSED_BYTES", 64 * 1024 * 1024)
        or workload.document_xml_bytes
        > _int_env("DEERFLOW_DOCX_MARKITDOWN_MAX_DOCUMENT_XML_BYTES", 32 * 1024 * 1024)
    )


def _parse_docx_streaming_in_process(content: bytes) -> tuple[str, dict[str, str]]:
    """Recover readable DOCX text with bounded-memory XML streaming.

    This is deliberately a last-resort text recovery path. Rich parsing stays
    owned by official DocReader/AnyDoc; this path prevents one pathological
    OOXML tree from turning a retained upload into a permanent parse failure.
    """

    maximum_xml_bytes = _int_env("DEERFLOW_DOCX_STREAM_MAX_DOCUMENT_XML_BYTES", 512 * 1024 * 1024)
    maximum_text_characters = _int_env("DEERFLOW_DOCX_STREAM_MAX_TEXT_CHARACTERS", 20_000_000)
    workload = inspect_docx_workload(content)
    if workload.document_xml_bytes > maximum_xml_bytes:
        raise ValueError(
            "DOCX document.xml exceeds the emergency streaming safety limit: "
            f"bytes={workload.document_xml_bytes}"
        )

    paragraphs: list[str] = []
    current: list[str] = []
    character_count = 0
    word_namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    text_tag = f"{word_namespace}t"
    paragraph_tag = f"{word_namespace}p"
    tab_tag = f"{word_namespace}tab"
    break_tags = {f"{word_namespace}br", f"{word_namespace}cr"}

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        with archive.open("word/document.xml") as source:
            for _event, element in ET.iterparse(source, events=("end",)):
                if element.tag == text_tag:
                    value = element.text or ""
                    character_count += len(value)
                    if character_count > maximum_text_characters:
                        raise ValueError("DOCX emergency text output exceeds its safety limit")
                    current.append(value)
                elif element.tag == tab_tag:
                    current.append("\t")
                elif element.tag in break_tags:
                    current.append("\n")
                elif element.tag == paragraph_tag:
                    paragraph = "".join(current).strip()
                    if paragraph:
                        paragraphs.append(paragraph)
                    current.clear()
                element.clear()

    markdown = "\n\n".join(paragraphs).strip()
    if not markdown:
        raise ValueError("DOCX emergency streaming recovery returned no readable text")
    return markdown, {}


def parse_docx_with_streaming_recovery(content: bytes, filename: str) -> tuple[str, dict[str, str]]:
    """Run last-resort DOCX text recovery in a separately killable child."""

    timeout = _float_env("DEERFLOW_DOCX_STREAM_TIMEOUT_SECONDS", 60.0)
    maximum_output_bytes = _int_env("DEERFLOW_DOCREADER_WORD_MAX_OUTPUT_BYTES", 104_857_600)
    worker = Path(
        os.getenv(
            "DEERFLOW_DOCREADER_WORKER_PATH",
            str(Path(__file__).with_name("docreader_worker.py")),
        )
    )
    if not worker.is_file():
        raise RuntimeError(f"DocReader worker is missing: {worker}")

    with tempfile.TemporaryDirectory(prefix="deerflow-docx-stream-") as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / "input.docx"
        output = workspace / "result.json"
        source.write_bytes(content)
        source.chmod(0o600)
        command = [sys.executable, str(worker), "streaming-docx", str(source), str(output)]
        try:
            completed = _run_converter_command(
                command,
                timeout=timeout,
                env=_isolated_local_parser_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"DOCX streaming recovery timed out after {timeout:g}s for {filename}") from exc
        if completed.returncode != 0 or not output.is_file():
            detail = (completed.stderr or "<empty>").strip().replace("\n", " ")[:500]
            raise RuntimeError(
                f"DOCX streaming recovery failed for {filename}: "
                f"returncode={completed.returncode}; stderr={detail}"
            )
        if output.stat().st_size <= 0 or output.stat().st_size > maximum_output_bytes:
            raise RuntimeError(f"DOCX streaming recovery produced an invalid result size for {filename}")
        payload = json.loads(output.read_text(encoding="utf-8"))
        markdown = payload.get("markdown")
        images = payload.get("images")
        if not isinstance(markdown, str) or not markdown.strip() or not isinstance(images, dict):
            raise RuntimeError(f"DOCX streaming recovery produced an invalid result for {filename}")
        return markdown, images


def _anydoc_word_extensions() -> set[str]:
    configured = os.getenv("DEERFLOW_ANYDOC_WORD_EXTENSIONS", "docx")
    return {f".{item.strip().lower().lstrip('.')}" for item in configured.split(",") if item.strip()}


def _anydoc_word_enabled(
    suffix: str,
    parse_profile: str,
    *,
    structurally_large_docx: bool = False,
) -> bool:
    if not _bool_env("DEERFLOW_ANYDOC_WORD_ENABLED", False) or suffix.lower() not in _anydoc_word_extensions():
        return False
    if parse_profile == "conversation":
        return True
    return (
        parse_profile == "knowledge"
        and structurally_large_docx
        and _bool_env("DEERFLOW_ANYDOC_KNOWLEDGE_WORD_ENABLED", False)
    )


def _isolated_local_parser_environment() -> dict[str, str]:
    """Pass only runtime essentials to a local parser subprocess."""

    allowed = (
        "HOME",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "PATH",
        "TMPDIR",
        "TZ",
    )
    environment = {name: os.environ[name] for name in allowed if os.environ.get(name)}
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _isolated_anydoc_environment() -> dict[str, str]:
    return _isolated_local_parser_environment()


def _visible_markdown_character_count(markdown: str) -> int:
    without_images = _MARKDOWN_IMAGE_RE.sub("", markdown)
    without_html = _HTML_TAG_RE.sub("", without_images)
    without_markup = re.sub(r"[`*_#>|~\[\](){}\-+]", "", without_html)
    return sum(1 for character in without_markup if not character.isspace())


def parse_word_with_anydoc(
    content: bytes,
    filename: str,
    suffix: str,
) -> tuple[str, dict[str, str]]:
    """Parse DOCX through a bounded, credential-isolated AnyDoc process."""

    normalized_suffix = suffix.lower()
    if normalized_suffix not in _anydoc_word_extensions():
        raise ValueError(f"AnyDoc Word parsing is not enabled for {normalized_suffix}")

    timeout = _float_env("DEERFLOW_ANYDOC_WORD_TIMEOUT_SECONDS", 120.0)
    minimum_text_characters = _int_env("DEERFLOW_ANYDOC_WORD_MIN_TEXT_CHARACTERS", 32)
    maximum_output_bytes = _int_env("DEERFLOW_ANYDOC_WORD_MAX_OUTPUT_BYTES", 104_857_600)
    worker = Path(
        os.getenv(
            "DEERFLOW_ANYDOC_WORKER_PATH",
            str(Path(__file__).with_name("anydoc_worker.py")),
        )
    )
    if not worker.is_file():
        raise RuntimeError(f"AnyDoc worker is missing: {worker}")

    with tempfile.TemporaryDirectory(prefix="deerflow-anydoc-") as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / f"input{normalized_suffix}"
        output = workspace / "result.md"
        source.write_bytes(content)
        source.chmod(0o600)
        command = [sys.executable, str(worker), str(source), str(output)]
        try:
            completed = _run_converter_command(
                command,
                timeout=timeout,
                env=_isolated_anydoc_environment(),
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"AnyDoc timed out after {timeout:g}s while parsing {filename}") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or "<empty>").strip().replace("\n", " ")[:500]
            raise RuntimeError(f"AnyDoc could not parse {filename}: returncode={completed.returncode}; stderr={detail}")
        if not output.is_file():
            raise RuntimeError(f"AnyDoc did not produce Markdown for {filename}")
        output_size = output.stat().st_size
        if output_size <= 0 or output_size > maximum_output_bytes:
            raise RuntimeError(f"AnyDoc produced an invalid Markdown size for {filename}: bytes={output_size}")
        markdown = output.read_text(encoding="utf-8")
        visible_characters = _visible_markdown_character_count(markdown)
        if visible_characters < minimum_text_characters:
            raise ValueError(f"AnyDoc output did not meet the readable text threshold for {filename}: visible_characters={visible_characters} minimum={minimum_text_characters}")
        logger.info(
            "AnyDoc Word parse succeeded filename=%s input_bytes=%d output_bytes=%d visible_characters=%d",
            filename,
            len(content),
            output_size,
            visible_characters,
        )
        return markdown, {}


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

    fat_sector_ids = [int.from_bytes(content[offset : offset + 4], "little") for offset in range(76, 512, 4)]
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
        fat_sector_ids.extend(int.from_bytes(difat_sector[offset : offset + 4], "little") for offset in range(0, sector_size - 4, 4) if int.from_bytes(difat_sector[offset : offset + 4], "little") != _CFB_FREE_SECTOR)
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
        fat.extend(int.from_bytes(sector[offset : offset + 4], "little") for offset in range(0, sector_size, 4))

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


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = 5.0,
) -> tuple[str, str]:
    """Terminate and reap a converter plus every descendant in its process group."""

    def signal_group(sig: int) -> None:
        if _POSIX_PROCESS_GROUPS:
            try:
                # start_new_session=True makes the child PID its process-group
                # ID. Keep using that stable ID even if the direct launcher
                # exits while LibreOffice descendants are still alive.
                os.killpg(process.pid, sig)
            except ProcessLookupError:
                return
        elif sig == signal.SIGTERM:
            process.terminate()
        else:
            process.kill()

    signal_group(signal.SIGTERM)
    try:
        output = process.communicate(timeout=max(0.1, grace_seconds))
    except subprocess.TimeoutExpired:
        signal_group(_SIGKILL)
        output = process.communicate()
    _reap_process_group(process.pid, timeout_seconds=grace_seconds)
    return output


def _enable_child_subreaper() -> None:
    """Adopt and reap daemonized converter descendants when Linux permits it."""

    global _CHILD_SUBREAPER_ENABLED
    if not _POSIX_PROCESS_GROUPS or _CHILD_SUBREAPER_ENABLED:
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        if result != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_CHILD_SUBREAPER) failed")
    except (AttributeError, OSError) as exc:
        logger.warning("Unable to enable child subreaper: %s", exc)
        return
    _CHILD_SUBREAPER_ENABLED = True
    logger.info("LibreOffice child subreaper enabled")


def _reap_process_group(process_group_id: int, *, timeout_seconds: float) -> None:
    if not (_POSIX_PROCESS_GROUPS and _CHILD_SUBREAPER_ENABLED):
        return
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        try:
            waited_pid, _status = os.waitpid(-process_group_id, os.WNOHANG)
        except ChildProcessError:
            return
        if waited_pid > 0:
            continue
        if time.monotonic() >= deadline:
            return
        time.sleep(0.05)


def _cleanup_lingering_process_group(process_group_id: int) -> None:
    """Remove a daemonized converter that outlived a successful launcher."""

    if not _POSIX_PROCESS_GROUPS:
        return
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except ProcessLookupError:
        _reap_process_group(process_group_id, timeout_seconds=0)
        return
    _reap_process_group(process_group_id, timeout_seconds=0.5)
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return
    try:
        os.killpg(process_group_id, _SIGKILL)
    except ProcessLookupError:
        return
    _reap_process_group(process_group_id, timeout_seconds=0.5)


def _run_converter_command(
    command: list[str],
    *,
    timeout: float,
    termination_grace_seconds: float = 5.0,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a converter in a killable process group and bound its whole lifetime."""

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if _POSIX_PROCESS_GROUPS:
        popen_kwargs["start_new_session"] = True
    elif os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    if env is not None:
        popen_kwargs["env"] = env

    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = _terminate_process_group(
            process,
            grace_seconds=termination_grace_seconds,
        )
        raise subprocess.TimeoutExpired(
            command,
            timeout,
            output=stdout,
            stderr=stderr,
        ) from exc
    _cleanup_lingering_process_group(process.pid)
    return subprocess.CompletedProcess(
        args=command,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def convert_to_pdf(content: bytes, filename: str) -> bytes:
    """Convert an Office document to PDF; pass validated PDF input through."""
    suffix = detect_input_suffix(content, filename)
    if suffix == ".pdf":
        return content

    if suffix in {".doc", ".docx"}:
        timeout = int(
            os.getenv(
                "DEERFLOW_WORD_LIBREOFFICE_TIMEOUT_SECONDS",
                os.getenv("LIBREOFFICE_TIMEOUT_SECONDS", "180"),
            )
        )
    else:
        timeout = int(os.getenv("LIBREOFFICE_TIMEOUT_SECONDS", "180"))
    with tempfile.TemporaryDirectory(prefix="deerflow-mineru-") as temp_dir:
        workspace = Path(temp_dir)
        source = workspace / f"input{suffix}"
        source.write_bytes(content)
        profile = (workspace / "libreoffice-profile").as_uri()
        command = [
            "libreoffice",
            "--headless",
            f"-env:UserInstallation={profile}",
            "--convert-to",
            "pdf",
            "--outdir",
            str(workspace),
            str(source),
        ]
        try:
            completed = _run_converter_command(command, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"LibreOffice timed out after {timeout}s while converting {filename}") from exc
        output = source.with_suffix(".pdf")
        if completed.returncode != 0 or not output.is_file():
            generated_files = ", ".join(f"{item.name}:{item.stat().st_size}" for item in sorted(workspace.iterdir(), key=lambda item: item.name) if item.is_file())
            stderr = completed.stderr.strip() or "<empty>"
            stdout = completed.stdout.strip() or "<empty>"
            raise RuntimeError(
                f"LibreOffice could not convert {filename}: returncode={completed.returncode}; output_exists={str(output.is_file()).lower()}; generated_files={generated_files[:300] or '<none>'}; stderr={stderr[:500]}; stdout={stdout[:500]}"
            )
        pdf = output.read_bytes()
        if not pdf.startswith(b"%PDF-"):
            raise RuntimeError(f"LibreOffice produced an invalid PDF for {filename}")
        return pdf


def split_pdf_for_sync(
    pdf: bytes,
    maximum_pages: int,
    *,
    pdfium_module: Any | None = None,
) -> tuple[tuple[bytes, int, int], ...]:
    """Split a large PDF into bounded, lossless page batches for sync parsing."""
    if maximum_pages <= 0:
        raise ValueError("MinerU synchronous batch size must be greater than zero")
    if pdfium_module is None:
        import pypdfium2 as pdfium_module

    source = pdfium_module.PdfDocument(pdf)
    try:
        page_count = len(source)
        if page_count <= maximum_pages:
            return ((pdf, 1, page_count),)

        batches: list[tuple[bytes, int, int]] = []
        for start_index in range(0, page_count, maximum_pages):
            end_index = min(start_index + maximum_pages, page_count)
            batch_document = pdfium_module.PdfDocument.new()
            try:
                batch_document.import_pages(
                    source,
                    pages=list(range(start_index, end_index)),
                )
                output = io.BytesIO()
                batch_document.save(output)
                batch_pdf = output.getvalue()
            finally:
                _close_resource(batch_document)
            if not batch_pdf.startswith(b"%PDF-"):
                raise RuntimeError("PDFium produced an invalid MinerU synchronous page batch")
            batches.append((batch_pdf, start_index + 1, end_index))
        return tuple(batches)
    finally:
        _close_resource(source)


class MinerUBusinessError(RuntimeError):
    """Structured business error returned inside an HTTP-success envelope."""

    def __init__(self, *, operation: str, code: str, message: str) -> None:
        self.operation = operation
        self.code = code
        self.message = message
        super().__init__(f"Enterprise MinerU business error during {operation}; code={code}; msg={message}")


class MinerUHTTPError(requests.HTTPError, RuntimeError):
    """HTTP failure with a bounded upstream diagnostic and stable status."""

    def __init__(self, *, operation: str, response: Any) -> None:
        self.operation = operation
        self.status_code = int(response.status_code)
        body = re.sub(r"\s+", " ", str(getattr(response, "text", ""))).strip()
        self.detail = body[:300] or "empty response"
        super().__init__(
            f"Enterprise MinerU HTTP {self.status_code} during {operation}: {self.detail}",
            response=response,
        )


class EnterpriseMinerUClient:
    """Client for the native enterprise MinerU RESTful API."""

    _TASK_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
    _TRANSIENT_POLL_BUSINESS_CODES = frozenset({"20006"})
    _TRANSIENT_POLL_HTTP_STATUSES = frozenset({HTTPStatus.NOT_FOUND})

    def __init__(
        self,
        *,
        api_url: str,
        api_token: str,
        timeout_seconds: int = 300,
        request_mode: str | None = None,
        poll_interval_seconds: float | None = None,
        transient_poll_retries: int | None = None,
        sync_transient_retries: int | None = None,
        sync_retry_delay_seconds: float | None = None,
        sync_retry_max_elapsed_seconds: float | None = None,
        session: Any | None = None,
    ) -> None:
        normalized_url = api_url.rstrip("/")
        for suffix in ("/file_parse", "/tasks"):
            if normalized_url.endswith(suffix):
                normalized_url = normalized_url[: -len(suffix)]
                break
        self.base_url = normalized_url
        self.api_url = f"{normalized_url}/file_parse"
        self.tasks_url = f"{normalized_url}/tasks"
        self.api_token = api_token
        if timeout_seconds <= 0:
            raise ValueError("MinerU timeout must be greater than zero")
        self.timeout_seconds = timeout_seconds
        self.request_mode = (request_mode or os.getenv("MINERU_REQUEST_MODE", "sync")).strip().lower()
        if self.request_mode not in {"sync", "async"}:
            raise ValueError("MINERU_REQUEST_MODE must be either 'sync' or 'async'")
        if poll_interval_seconds is None:
            poll_interval_seconds = _float_env("MINERU_POLL_INTERVAL_SECONDS", 3.0)
        if poll_interval_seconds <= 0:
            raise ValueError("MinerU poll interval must be greater than zero")
        self.poll_interval_seconds = poll_interval_seconds
        if transient_poll_retries is None:
            transient_poll_retries = _int_env("MINERU_TRANSIENT_POLL_RETRIES", 20)
        if transient_poll_retries < 0:
            raise ValueError("MinerU transient poll retries cannot be negative")
        self.transient_poll_retries = transient_poll_retries
        if sync_transient_retries is None:
            sync_transient_retries = _int_env("MINERU_SYNC_TRANSIENT_RETRIES", 2)
        if sync_transient_retries < 0:
            raise ValueError("MinerU synchronous retries cannot be negative")
        self.sync_transient_retries = sync_transient_retries
        if sync_retry_delay_seconds is None:
            sync_retry_delay_seconds = _float_env("MINERU_SYNC_RETRY_DELAY_SECONDS", 2.0)
        if sync_retry_delay_seconds < 0:
            raise ValueError("MinerU synchronous retry delay cannot be negative")
        self.sync_retry_delay_seconds = sync_retry_delay_seconds
        if sync_retry_max_elapsed_seconds is None:
            sync_retry_max_elapsed_seconds = _float_env("MINERU_SYNC_RETRY_MAX_ELAPSED_SECONDS", 600.0)
        if sync_retry_max_elapsed_seconds <= 0:
            raise ValueError("MinerU synchronous retry elapsed limit must be greater than zero")
        self.sync_retry_max_elapsed_seconds = sync_retry_max_elapsed_seconds
        if session is None:
            session = requests.Session()
            trust_env = _bool_env("MINERU_TRUST_ENV", False)
            session.trust_env = trust_env
            if not trust_env:
                ca_bundle = os.getenv("MINERU_CA_BUNDLE", "").strip() or os.getenv("REQUESTS_CA_BUNDLE", "").strip() or os.getenv("SSL_CERT_FILE", "").strip()
                if ca_bundle:
                    if not Path(ca_bundle).is_file():
                        raise RuntimeError(f"Configured MinerU CA bundle does not exist: {ca_bundle}")
                    # requests ignores REQUESTS_CA_BUNDLE when trust_env is
                    # disabled. Bind the reviewed enterprise CA explicitly so
                    # the direct internal route keeps certificate and hostname
                    # verification without inheriting proxy settings.
                    session.verify = ca_bundle
        self.session = session

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }

    @staticmethod
    def _raise_for_status(response: Any, operation: str) -> None:
        status_code = getattr(response, "status_code", None)
        if status_code is None:
            response.raise_for_status()
            return
        if int(status_code) >= 400:
            raise MinerUHTTPError(operation=operation, response=response)

    @staticmethod
    def _request_data() -> dict[str, str]:
        return {
            "backend": "pipeline",
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
            "return_md": "true",
            "return_images": "true",
            "response_format_zip": "false",
        }

    @staticmethod
    def _decode_json_response(response: Any, operation: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Enterprise MinerU returned a non-JSON response during {operation}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"Enterprise MinerU returned an invalid response during {operation}")
        if payload.get("success") is False:
            code = str(payload.get("code", "unknown"))[:32]
            message = str(payload.get("msg") or payload.get("message") or "business request failed")[:300]
            raise MinerUBusinessError(
                operation=operation,
                code=code,
                message=message,
            )
        return payload

    def _transient_poll_delay(
        self,
        *,
        task_id: str,
        operation: str,
        error: MinerUBusinessError,
        consecutive_errors: int,
        remaining: float,
    ) -> float:
        if error.code not in self._TRANSIENT_POLL_BUSINESS_CODES or consecutive_errors > self.transient_poll_retries:
            raise error
        delay = min(
            self.poll_interval_seconds * (2 ** min(consecutive_errors - 1, 3)),
            30.0,
            max(0.0, remaining),
        )
        if delay <= 0:
            raise TimeoutError(f"Enterprise MinerU {operation} for task {task_id} exceeded {self.timeout_seconds} seconds") from error
        logger.warning(
            "Transient MinerU poll failure task_id=%s operation=%s code=%s attempt=%d/%d retry_in_seconds=%.3f",
            task_id,
            operation,
            error.code,
            consecutive_errors,
            self.transient_poll_retries,
            delay,
        )
        return delay

    def _transient_http_poll_delay(
        self,
        *,
        task_id: str,
        operation: str,
        status_code: int,
        consecutive_errors: int,
        remaining: float,
    ) -> float | None:
        if status_code not in self._TRANSIENT_POLL_HTTP_STATUSES or consecutive_errors > self.transient_poll_retries:
            return None
        delay = min(
            self.poll_interval_seconds * (2 ** min(consecutive_errors - 1, 3)),
            30.0,
            max(0.0, remaining),
        )
        if delay <= 0:
            raise TimeoutError(f"Enterprise MinerU {operation} for task {task_id} exceeded {self.timeout_seconds} seconds")
        logger.warning(
            "Transient MinerU poll HTTP failure task_id=%s operation=%s status_code=%d attempt=%d/%d retry_in_seconds=%.3f",
            task_id,
            operation,
            status_code,
            consecutive_errors,
            self.transient_poll_retries,
            delay,
        )
        return delay

    def _transient_transport_poll_delay(
        self,
        *,
        task_id: str,
        operation: str,
        error: requests.RequestException,
        consecutive_errors: int,
        remaining: float,
    ) -> float:
        """Retry a status/result transport failure without resubmitting the task."""
        if not isinstance(error, (requests.ConnectionError, requests.Timeout)) or consecutive_errors > self.transient_poll_retries:
            raise error
        delay = min(
            self.poll_interval_seconds * (2 ** min(consecutive_errors - 1, 3)),
            30.0,
            max(0.0, remaining),
        )
        if delay <= 0:
            raise TimeoutError(f"Enterprise MinerU {operation} for task {task_id} exceeded {self.timeout_seconds} seconds") from error
        logger.warning(
            "Transient MinerU poll transport failure task_id=%s operation=%s error_type=%s attempt=%d/%d retry_in_seconds=%.3f",
            task_id,
            operation,
            type(error).__name__,
            consecutive_errors,
            self.transient_poll_retries,
            delay,
        )
        return delay

    @staticmethod
    def _task_fields(payload: dict[str, Any]) -> dict[str, Any]:
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    @staticmethod
    def _validate_result(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload.get("results"), dict) and not isinstance(payload.get("pages"), list):
            raise RuntimeError("Enterprise MinerU returned an invalid parse result")
        return payload

    @staticmethod
    def _retryable_sync_error(error: requests.RequestException) -> bool:
        if isinstance(error, (requests.ConnectionError, requests.Timeout)):
            return True
        response = getattr(error, "response", None)
        return getattr(response, "status_code", None) in {
            HTTPStatus.BAD_GATEWAY,
            HTTPStatus.SERVICE_UNAVAILABLE,
            HTTPStatus.GATEWAY_TIMEOUT,
        }

    def _parse_pdf_sync_single(
        self,
        pdf: bytes,
        filename: str,
    ) -> dict[str, Any]:
        for attempt in range(self.sync_transient_retries + 1):
            started = time.monotonic()
            try:
                response = self.session.post(
                    self.api_url,
                    headers=self._headers(),
                    files={
                        "files": (filename, pdf, "application/pdf"),
                    },
                    data=self._request_data(),
                    timeout=self.timeout_seconds,
                )
                self._raise_for_status(response, "synchronous parse")
                payload = self._task_fields(self._decode_json_response(response, "synchronous parse"))
                return self._validate_result(payload)
            except requests.RequestException as exc:
                elapsed = time.monotonic() - started
                should_retry = self._retryable_sync_error(exc) and attempt < self.sync_transient_retries and elapsed <= self.sync_retry_max_elapsed_seconds
                if not should_retry:
                    raise
                delay = self.sync_retry_delay_seconds * (2**attempt)
                logger.warning(
                    "Transient MinerU synchronous request failure filename=%s attempt=%d/%d elapsed_seconds=%.3f retry_in_seconds=%.3f: %s",
                    filename,
                    attempt + 1,
                    self.sync_transient_retries,
                    elapsed,
                    delay,
                    exc,
                )
                if delay:
                    time.sleep(delay)
        raise AssertionError("unreachable MinerU synchronous retry state")

    def _parse_pdf_sync(self, pdf: bytes, filename: str) -> dict[str, Any]:
        maximum_pages = _int_env("MINERU_SYNC_MAX_PAGES_PER_REQUEST", 20)
        try:
            batches = split_pdf_for_sync(pdf, maximum_pages)
        except Exception:
            logger.warning(
                "Could not split %s for bounded MinerU synchronous parsing; submitting the original PDF once.",
                filename,
                exc_info=True,
            )
            batches = ((pdf, 1, 0),)
        if len(batches) == 1:
            return self._parse_pdf_sync_single(pdf, filename)

        stem = Path(filename).stem or "document"
        combined_markdown: list[str] = []
        combined_images: dict[str, str] = {}
        logger.info(
            "Splitting MinerU synchronous parse filename=%s batches=%d maximum_pages_per_request=%d",
            filename,
            len(batches),
            maximum_pages,
        )
        for batch_pdf, first_page, last_page in batches:
            batch_filename = f"{stem}.pages-{first_page:04d}-{last_page:04d}.pdf"
            payload = self._parse_pdf_sync_single(batch_pdf, batch_filename)
            markdown, images = mineru_pages_to_markdown(
                payload,
                filename=batch_filename,
            )
            combined_markdown.append(f"## 原始第 {first_page}-{last_page} 页\n\n{markdown}")
            combined_images.update(images)
        return {
            "results": {
                filename: {
                    "md_content": "\n\n".join(combined_markdown),
                    "images": combined_images,
                }
            }
        }

    def _parse_pdf_async(self, pdf: bytes, filename: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        submission = self.session.post(
            self.tasks_url,
            headers=self._headers(),
            files={"files": (filename, pdf, "application/pdf")},
            data=self._request_data(),
            timeout=min(self.timeout_seconds, 120),
        )
        if submission.status_code not in {HTTPStatus.OK, HTTPStatus.ACCEPTED}:
            self._raise_for_status(submission, "asynchronous submission")
        submission_payload = self._task_fields(self._decode_json_response(submission, "asynchronous submission"))
        task_id = str(submission_payload.get("task_id", "")).strip()
        if not self._TASK_ID_RE.fullmatch(task_id):
            raise RuntimeError("Enterprise MinerU returned an invalid task identifier")

        status_url = f"{self.tasks_url}/{task_id}"
        result_url = f"{status_url}/result"
        consecutive_status_errors = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Enterprise MinerU task {task_id} exceeded {self.timeout_seconds} seconds")
            try:
                status_response = self.session.get(
                    status_url,
                    headers=self._headers(),
                    timeout=min(60.0, max(1.0, remaining)),
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                consecutive_status_errors += 1
                delay = self._transient_transport_poll_delay(
                    task_id=task_id,
                    operation="task status",
                    error=exc,
                    consecutive_errors=consecutive_status_errors,
                    remaining=deadline - time.monotonic(),
                )
                time.sleep(delay)
                continue
            if status_response.status_code in self._TRANSIENT_POLL_HTTP_STATUSES:
                consecutive_status_errors += 1
                delay = self._transient_http_poll_delay(
                    task_id=task_id,
                    operation="task status",
                    status_code=status_response.status_code,
                    consecutive_errors=consecutive_status_errors,
                    remaining=deadline - time.monotonic(),
                )
                if delay is not None:
                    time.sleep(delay)
                    continue
            self._raise_for_status(status_response, "task status")
            try:
                decoded_status = self._decode_json_response(status_response, "task status")
            except MinerUBusinessError as exc:
                consecutive_status_errors += 1
                delay = self._transient_poll_delay(
                    task_id=task_id,
                    operation="task status",
                    error=exc,
                    consecutive_errors=consecutive_status_errors,
                    remaining=deadline - time.monotonic(),
                )
                time.sleep(delay)
                continue
            consecutive_status_errors = 0
            status_payload = self._task_fields(decoded_status)
            status = str(status_payload.get("status", "")).strip().lower()
            if status == "completed":
                break
            if status == "failed":
                error = str(status_payload.get("error") or status_payload.get("message") or "unknown failure")[:300]
                raise RuntimeError(f"Enterprise MinerU asynchronous task failed: {error}")
            if status not in {"pending", "queued", "processing"}:
                raise RuntimeError(f"Enterprise MinerU returned unexpected task status: {status or '<empty>'}")
            time.sleep(min(self.poll_interval_seconds, remaining))

        consecutive_result_errors = 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"Enterprise MinerU result for task {task_id} was not ready within {self.timeout_seconds} seconds")
            try:
                result_response = self.session.get(
                    result_url,
                    headers=self._headers(),
                    timeout=min(60.0, max(1.0, remaining)),
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                consecutive_result_errors += 1
                delay = self._transient_transport_poll_delay(
                    task_id=task_id,
                    operation="task result",
                    error=exc,
                    consecutive_errors=consecutive_result_errors,
                    remaining=deadline - time.monotonic(),
                )
                time.sleep(delay)
                continue
            if result_response.status_code in {
                HTTPStatus.ACCEPTED,
                HTTPStatus.CONFLICT,
            }:
                # A valid not-ready response proves transport visibility has
                # recovered, so a later timeout starts a fresh bounded streak.
                consecutive_result_errors = 0
                time.sleep(min(self.poll_interval_seconds, remaining))
                continue
            if result_response.status_code in self._TRANSIENT_POLL_HTTP_STATUSES:
                consecutive_result_errors += 1
                delay = self._transient_http_poll_delay(
                    task_id=task_id,
                    operation="task result",
                    status_code=result_response.status_code,
                    consecutive_errors=consecutive_result_errors,
                    remaining=deadline - time.monotonic(),
                )
                if delay is not None:
                    time.sleep(delay)
                    continue
            self._raise_for_status(result_response, "task result")
            try:
                decoded_result = self._decode_json_response(result_response, "task result")
            except MinerUBusinessError as exc:
                consecutive_result_errors += 1
                delay = self._transient_poll_delay(
                    task_id=task_id,
                    operation="task result",
                    error=exc,
                    consecutive_errors=consecutive_result_errors,
                    remaining=deadline - time.monotonic(),
                )
                time.sleep(delay)
                continue
            consecutive_result_errors = 0
            result_payload = self._task_fields(decoded_result)
            return self._validate_result(result_payload)

    def parse_pdf(self, pdf: bytes, filename: str) -> dict[str, Any]:
        if self.request_mode == "async":
            return self._parse_pdf_async(pdf, filename)
        return self._parse_pdf_sync(pdf, filename)


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
    evidence = inspect_pdf_visual_pages(pdf)
    if not evidence:
        return markdown, images
    _generic_markdown, page_images = render_pdf_page_images(
        pdf,
        tuple(item.page_number for item in evidence),
    )

    blocks: list[str] = []
    for item in evidence:
        path = f"images/source-page-{item.page_number:04d}.jpg"
        if path not in page_images:
            raise RuntimeError(f"Visual evidence rendering omitted required source page {item.page_number}")
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
        raise RuntimeError("Visual evidence rendering returned no page evidence")
    return merge_document_visuals(
        markdown,
        images,
        "\n\n".join(blocks),
        page_images,
    )


def _native_mineru_document(
    payload: dict[str, Any],
    filename: str | None,
) -> dict[str, Any] | None:
    """Select one document from the native ``results`` response envelope."""
    results = payload.get("results")
    if not isinstance(results, dict):
        return None

    requested_name = Path(filename).name if filename else None
    if requested_name and isinstance(results.get(requested_name), dict):
        return results[requested_name]

    if requested_name:
        casefolded = {str(name).casefold(): document for name, document in results.items() if isinstance(document, dict)}
        matched = casefolded.get(requested_name.casefold())
        if matched is not None:
            return matched

    documents = [document for document in results.values() if isinstance(document, dict)]
    if len(documents) == 1:
        return documents[0]
    if requested_name:
        raise RuntimeError("Enterprise MinerU results do not contain the requested document")
    raise RuntimeError("Enterprise MinerU returned an ambiguous multi-document response")


def _normalize_native_mineru_images(
    markdown: str,
    raw_images: Any,
) -> tuple[str, dict[str, str]]:
    """Convert native MinerU image values into WeKnora persistent assets."""
    if raw_images is None:
        raw_images = {}
    if not isinstance(raw_images, dict):
        raise RuntimeError("Enterprise MinerU returned an invalid images field")

    images: dict[str, str] = {}
    for raw_name, raw_value in raw_images.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            logger.warning("Skipping malformed native MinerU image entry")
            continue
        data_match = _NATIVE_IMAGE_DATA_RE.match(raw_value.strip())
        if data_match is not None:
            mime = data_match.group("mime")
            encoded = data_match.group("data")
        else:
            mime = _IMAGE_MIME_BY_SUFFIX.get(Path(raw_name).suffix.lower(), "")
            encoded = raw_value
        if not mime:
            logger.warning("Skipping native MinerU image with unsupported type: %s", raw_name)
            continue
        extracted = _decode_inline_image(mime, encoded)
        if extracted is None:
            logger.warning("Skipping malformed native MinerU image: %s", raw_name)
            continue
        persistent_path, persistent_value = extracted
        images[persistent_path] = persistent_value
        references = {raw_name}
        if not raw_name.startswith("images/"):
            references.add(f"images/{raw_name}")
        for reference in sorted(references, key=len, reverse=True):
            markdown = markdown.replace(reference, persistent_path)

    markdown, inline_images = extract_inline_images(markdown)
    images.update(inline_images)
    return markdown, images


def mineru_pages_to_markdown(
    payload: dict[str, Any],
    *,
    filename: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Convert native or legacy MinerU responses into WeKnora assets."""
    native_document = _native_mineru_document(payload, filename)
    if native_document is not None:
        markdown = native_document.get("md_content")
        if not isinstance(markdown, str) or not markdown.strip():
            raise RuntimeError("Enterprise MinerU returned no parseable content")
        return _normalize_native_mineru_images(
            markdown.strip(),
            native_document.get("images"),
        )

    # Keep compatibility with the older page/block envelope during the
    # controlled cutover. New MinerU-2605 deployments use ``results`` above.
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


_ATX_HEADING_RE = re.compile(r"^(?P<indent> {0,3})#{1,6}[\t ]+(?P<body>.*)$")
_FENCE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})")


def normalize_markdown_for_weknora(markdown: str) -> str:
    """Prevent malformed giant headings from becoming repeated chunk context.

    WeKnora prepends the active Markdown heading breadcrumb to every child
    chunk before embedding. MinerU can occasionally label a full page of OCR
    text as one ATX heading, multiplying that page into every child request.
    Preserve all recovered text but demote only implausibly large headings to
    ordinary paragraphs. Fenced code blocks are left byte-for-byte unchanged.
    """

    lines = markdown.splitlines(keepends=True)
    normalized: list[str] = []
    fence_marker: str | None = None
    demoted = 0
    largest_heading_bytes = 0

    for line in lines:
        content = line.rstrip("\r\n")
        ending = line[len(content) :]
        fence_match = _FENCE_RE.match(content)
        if fence_match:
            marker = fence_match.group("marker")
            marker_char = marker[0]
            if fence_marker is None:
                fence_marker = marker
            elif marker_char == fence_marker[0] and len(marker) >= len(fence_marker):
                fence_marker = None
            normalized.append(line)
            continue

        if fence_marker is None:
            heading_match = _ATX_HEADING_RE.match(content)
            if heading_match:
                body = heading_match.group("body")
                body_bytes = len(body.encode("utf-8"))
                if len(body) > 512 or body_bytes > 2048:
                    normalized.append(f"{heading_match.group('indent')}{body}{ending}")
                    demoted += 1
                    largest_heading_bytes = max(largest_heading_bytes, body_bytes)
                    continue
        normalized.append(line)

    if demoted:
        logger.warning(
            "Demoted malformed MinerU headings before WeKnora chunking count=%d largest_heading_bytes=%d",
            demoted,
            largest_heading_bytes,
        )
    return "".join(normalized)


def weknora_response(markdown: str, images: dict[str, str] | None = None) -> dict[str, Any]:
    """Wrap markdown in WeKnora's self-hosted MinerU response schema."""
    normalized_markdown = normalize_markdown_for_weknora(markdown).strip()
    if not normalized_markdown:
        raise ValueError("Document parser returned no readable text")
    return {
        "results": {
            "document": {
                "md_content": normalized_markdown,
                "images": images or {},
            }
        }
    }


def parse_upload(
    content: bytes,
    filename: str,
    *,
    parse_profile: str = "knowledge",
) -> dict[str, Any]:
    """Preserve searchable documents locally and OCR image-dominated input."""
    suffix = detect_input_suffix(content, filename)
    decision: PDFRoutingDecision | None = None
    searchable_document = suffix in {".pdf", ".doc", ".docx"}
    native_word: tuple[str, dict[str, str]] | None = None
    word_route = "native-first-success"
    structurally_large_docx = False
    docx_workload: DOCXWorkload | None = None
    if suffix in {".doc", ".docx"}:
        if suffix == ".docx":
            docx_workload = inspect_docx_workload(content)
            structurally_large_docx = docx_requires_large_document_route(docx_workload)
            logger.info(
                "DOCX workload filename=%s profile=%s compressed_bytes=%d "
                "uncompressed_bytes=%d document_xml_bytes=%d entries=%d "
                "expansion_ratio=%.2f large_document_route=%s",
                filename,
                parse_profile,
                docx_workload.compressed_bytes,
                docx_workload.uncompressed_bytes,
                docx_workload.document_xml_bytes,
                docx_workload.entry_count,
                docx_workload.expansion_ratio,
                structurally_large_docx,
            )
        if _anydoc_word_enabled(
            suffix,
            parse_profile,
            structurally_large_docx=structurally_large_docx,
        ):
            try:
                native_word = parse_word_with_anydoc(content, filename, suffix)
                word_route = "anydoc-first-success"
            except Exception:
                logger.warning(
                    "AnyDoc Word parsing failed for %s; continuing through the existing native parser chain.",
                    filename,
                    exc_info=True,
                )
        try:
            if native_word is None:
                if structurally_large_docx:
                    native_word = parse_word_with_builtin(
                        content,
                        filename,
                        suffix,
                        bypass_markitdown=True,
                    )
                    word_route = "docreader-native-large-docx"
                else:
                    native_word = parse_word_with_builtin(content, filename, suffix)
            if not native_word[0].strip():
                logger.warning(
                    "Native Word parser returned no readable text for %s; continuing through PDF visual fallback.",
                    filename,
                )
                native_word = None
                if structurally_large_docx:
                    raise RuntimeError("Large DOCX native parser returned no readable text")
        except Exception:
            logger.warning(
                "Native Word parsing failed for %s; continuing through PDF visual fallback.",
                filename,
                exc_info=True,
            )
            if structurally_large_docx:
                try:
                    native_word = parse_docx_with_streaming_recovery(content, filename)
                    word_route = "emergency-streaming-large-docx"
                except Exception:
                    logger.warning(
                        "Emergency DOCX text recovery failed for %s; continuing through visual fallback.",
                        filename,
                        exc_info=True,
                    )
        if native_word is not None and not _bool_env(
            "MINERU_WORD_PDF_VISUAL_SUPPLEMENT_ENABLED",
            False,
        ):
            logger.info(
                "Word routing filename=%s route=%s",
                filename,
                word_route,
            )
            return weknora_response(native_word[0], native_word[1])
    if suffix == ".pdf":
        pdf = content
        pdf_filename = filename
    else:
        # Presentation parsing remains visual and strict. Word reaches this
        # branch only when native parsing was empty/failed, or when an operator
        # explicitly opts into the best-effort visual supplement.
        try:
            pdf = convert_to_pdf(content, filename)
        except Exception:
            if native_word is None:
                raise
            logger.warning(
                "Optional Word PDF visual supplement failed for %s; returning the successful native parse.",
                filename,
                exc_info=True,
            )
            return weknora_response(native_word[0], native_word[1])
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

    try:
        mineru_timeout_seconds = int(os.getenv("MINERU_TIMEOUT_SECONDS", "600"))
        if suffix in {".doc", ".docx"}:
            mineru_timeout_seconds = int(
                os.getenv(
                    "DEERFLOW_WORD_MINERU_FALLBACK_TIMEOUT_SECONDS",
                    str(mineru_timeout_seconds),
                )
            )
        parsed = EnterpriseMinerUClient(
            api_url=os.environ["MINERU_API_URL"],
            api_token=os.environ["MINERU_API_TOKEN"],
            timeout_seconds=mineru_timeout_seconds,
        ).parse_pdf(pdf, pdf_filename)
        markdown, images = mineru_pages_to_markdown(parsed, filename=pdf_filename)
    except Exception:
        if native_word is None:
            raise
        logger.warning(
            "Optional Word MinerU visual supplement failed for %s; returning the successful native parse.",
            filename,
            exc_info=True,
        )
        return weknora_response(native_word[0], native_word[1])

    page_numbers = decision.scanned_pages if searchable_document and decision is not None and decision.use_mineru else None
    source_page_markdown, source_page_images = render_pdf_page_images(
        pdf,
        page_numbers,
    )
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
        self._bytes(status.value, "application/json; charset=utf-8", encoded)

    def _bytes(self, status: int, content_type: str, content: bytes) -> None:
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        try:
            self.wfile.write(content)
        except (BrokenPipeError, ConnectionResetError):
            # Parsing already completed. A browser or proxy timeout can close
            # the socket before delivery; keep this out of parser error counts.
            logger.info("Parser response client disconnected before body delivery")

    def do_GET(self) -> None:  # noqa: N802
        # WeKnora's built-in MinerU availability check calls /docs, while the
        # Kubernetes probes use /health. Both are intentionally side-effect free.
        normalized_path = self.path.rstrip("/")
        if normalized_path == "/live":
            self._json(
                HTTPStatus.OK,
                {"status": "ok", "role": adapter_mode()},
            )
            return
        if normalized_path in {"/health", "/ready"}:
            if adapter_mode() == "dispatcher":
                dispatcher = parse_dispatcher().snapshot()
                self._json(
                    HTTPStatus.OK if dispatcher["ready"] else HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "status": "ok" if dispatcher["ready"] else "unavailable",
                        "role": "dispatcher",
                        "dispatcher": dispatcher,
                    },
                )
                return
            parser = _PARSE_COORDINATOR.snapshot()
            ready = parser["active"] == 0 and parser["waiting"] == 0
            status = HTTPStatus.OK
            payload_status = "ok"
            if normalized_path == "/ready" and not ready:
                status = HTTPStatus.SERVICE_UNAVAILABLE
                payload_status = "busy"
            self._json(
                status,
                {
                    "status": payload_status,
                    "role": "worker",
                    "parser": parser,
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
            content_type = self.headers.get("Content-Type", "")
            body = self.rfile.read(content_length)
            if adapter_mode() == "dispatcher":
                status, response_type, response_body, backend = parse_dispatcher().forward(
                    body=body,
                    content_type=content_type,
                    priority=self.headers.get(
                        "X-DeerFlow-Parse-Priority",
                        "bulk",
                    ),
                )
                logger.info(
                    "Dispatch response backend=%s status=%d bytes=%d",
                    backend,
                    status,
                    len(response_body),
                )
                self._bytes(status, response_type, response_body)
                return
            filename, content = parse_multipart_upload(
                content_type,
                body,
            )
            parsed = _PARSE_COORDINATOR.run(
                lambda: parse_upload(
                    content,
                    filename,
                    parse_profile=("conversation" if self.headers.get("X-DeerFlow-Parse-Profile", "").strip().lower() == "conversation" else "knowledge"),
                ),
                filename=filename,
                content_bytes=len(content),
            )
            self._json(HTTPStatus.OK, parsed)
        except (KeyError, ValueError) as exc:
            logger.warning("Rejected MinerU adapter request: %s", exc)
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except ParseWorkerBusy as exc:
            logger.warning("MinerU adapter worker busy: %s", exc)
            self._json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {
                    "error": str(exc),
                    "retryable": True,
                },
            )
        except DispatchQueueFull as exc:
            logger.warning("MinerU dispatcher queue full: %s", exc)
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": str(exc)})
        except DispatchUnavailable as exc:
            logger.warning("MinerU dispatcher unavailable: %s", exc)
            self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
        except MinerUHTTPError as exc:
            logger.warning("MinerU upstream request rejected: %s", exc)
            if exc.status_code == HTTPStatus.BAD_REQUEST and exc.operation in {
                "synchronous parse",
                "asynchronous submission",
            }:
                self._json(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    {
                        "error": "The document was rejected by the parser",
                        "detail": exc.detail,
                    },
                )
            else:
                self._json(
                    HTTPStatus.BAD_GATEWAY,
                    {
                        "error": "Upstream document parser failed",
                        "upstream_status": exc.status_code,
                    },
                )
        except Exception as exc:
            logger.exception("MinerU adapter request failed")
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)[:500]})


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    mode = adapter_mode()
    if mode == "worker" and (not os.getenv("MINERU_API_URL") or not os.getenv("MINERU_API_TOKEN")):
        raise SystemExit("MINERU_API_URL and MINERU_API_TOKEN are required")
    if mode == "dispatcher":
        snapshot = parse_dispatcher().snapshot()
        logger.info("MinerU dispatcher initial state: %s", snapshot)
    else:
        _enable_child_subreaper()
    port = int(os.getenv("MINERU_LISTEN_PORT", "50052"))
    server = ThreadingHTTPServer(("0.0.0.0", port), MinerUAdapterHandler)
    logger.info("MinerU compatibility adapter role=%s listening on port %d", mode, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
