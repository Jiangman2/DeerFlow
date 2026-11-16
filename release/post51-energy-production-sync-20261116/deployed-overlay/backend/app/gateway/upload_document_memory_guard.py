"""Bounded, file-backed conversation parsing; preserves the existing parser contract."""
from __future__ import annotations

import asyncio
import contextvars
import json
import mimetypes
import os
from pathlib import Path
import stat
import threading
import time
import uuid

import httpx

from app.gateway import upload_document_parser as parser

# Per Python process, not a cluster-wide worker budget. Waiting files stay on disk.
ACTIVE_LIMIT = 2
WAITER_LIMIT = 4
ADMISSION_SECONDS = 2.0
RESPONSE_LIMIT = 64 * 1024 * 1024
ERROR_RESPONSE_LIMIT = 64 * 1024
CHUNK_BYTES = 64 * 1024
_LOCK = threading.Lock()
_active = 0
_waiting = 0


async def _acquire() -> None:
    global _active, _waiting
    with _LOCK:
        if _active < ACTIVE_LIMIT:
            _active += 1
            return
        if _waiting >= WAITER_LIMIT:
            raise parser.UploadDocumentParseError("Document parser is busy; please retry later")
        _waiting += 1
    deadline = time.monotonic() + ADMISSION_SECONDS
    try:
        while True:
            with _LOCK:
                if _active < ACTIVE_LIMIT:
                    _active += 1
                    return
            if time.monotonic() >= deadline:
                raise parser.UploadDocumentParseError("Document parser admission timed out; please retry later")
            await asyncio.sleep(0.02)
    finally:
        with _LOCK:
            _waiting -= 1


def _release() -> None:
    global _active
    with _LOCK:
        _active -= 1
        if _active < 0:
            raise RuntimeError("Parser admission ownership error")


async def _owned_io(function, *args):
    """Do not close a file or release its slot while a cancelled IO still runs."""
    context = contextvars.copy_context()
    future = asyncio.get_running_loop().run_in_executor(None, context.run, function, *args)
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve worker failures without replacing the caller's cancellation.
        try:
            future.result()
        except BaseException:
            pass
        raise asyncio.CancelledError
    return future.result()


async def _owned_parse(coroutine, timeout):
    task = asyncio.create_task(coroutine)
    cancelled = False; expired = False
    try:
        done, _ = await asyncio.wait([task], timeout=timeout)
        if not done:
            expired = True
            task.cancel()
    except asyncio.CancelledError:
        cancelled = True
        task.cancel()
    # Repeated caller cancellations must not release the permit ahead of IO cleanup.
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if not task.done() or asyncio.current_task().cancelling():
                cancelled = True
        except Exception:
            break
    if cancelled or expired:
        try:
            task.result()
        except BaseException:
            pass
        if cancelled:
            raise asyncio.CancelledError
        raise asyncio.TimeoutError
    return task.result()


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open_owned(path, holder):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise parser.UploadDocumentParseError("Document parser input is not a non-empty regular file")
        stream = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    holder.append(stream)  # The caller can close it even if opening was cancelled.
    return info


def _header(filename, media_type, boundary):
    name = Path(filename).name.replace("\\", "\\\\").replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")
    return ("--" + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="' + name
            + '"\r\nContent-Type: ' + media_type + "\r\n\r\n").encode("utf8")


def _decode_response(status, headers, raw):
    response = httpx.Response(status, headers=headers, content=raw)
    if status != 200:
        detail = parser._parser_error_detail(response)
        suffix = ": " + detail if detail else ""
        raise parser.UploadDocumentParseError("Document parser returned HTTP " + str(status) + suffix)
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise parser.UploadDocumentParseError("Document parser returned invalid JSON") from exc
    return parser.extract_parser_markdown(payload)


async def _parse_owned(endpoint, filename, file_path, timeout_seconds):
    holder = []
    try:
        before = await _owned_io(_open_owned, file_path, holder)
        stream = holder[0]
        media_type = parser._DOCUMENT_MEDIA_TYPES.get(Path(filename).suffix.lower())
        if media_type is None:
            media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        boundary = "deerflow-" + uuid.uuid4().hex
        prefix = _header(filename, media_type, boundary)
        suffix = ("\r\n--" + boundary + "--\r\n").encode("ascii")

        async def body():
            yield prefix
            remaining = before.st_size
            while remaining:
                chunk = await _owned_io(stream.read, min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise parser.UploadDocumentParseError("Document parser input changed while reading")
                remaining -= len(chunk)
                yield chunk
            after = await _owned_io(os.fstat, stream.fileno())
            if _identity(after) != _identity(before):
                raise parser.UploadDocumentParseError("Document parser input changed while reading")
            yield suffix

        headers = {
            "X-DeerFlow-Parse-Priority": "interactive",
            "Content-Type": "multipart/form-data; boundary=" + boundary,
            "Content-Length": str(len(prefix) + before.st_size + len(suffix)),
            "Accept-Encoding": "identity",
        }
        response_limit = min(RESPONSE_LIMIT, parser._int_env(
            "DEERFLOW_UPLOAD_DOCUMENT_PARSE_MAX_RESPONSE_BYTES",
            parser._int_env("DEERFLOW_UPLOAD_PDF_PARSE_MAX_RESPONSE_BYTES", RESPONSE_LIMIT)))
        async with parser._get_parser_client().stream(
            "POST", endpoint, headers=headers, content=body(),
            timeout=httpx.Timeout(timeout_seconds), follow_redirects=False,
        ) as response:
            if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                raise parser.UploadDocumentParseError("Document parser returned an unsupported content encoding")
            limit = response_limit if response.status_code == 200 else min(response_limit, ERROR_RESPONSE_LIMIT)
            length = response.headers.get("content-length")
            if length is not None and (not length.isdigit() or int(length) > limit):
                raise parser.UploadDocumentParseError("Document parser response is too large or has invalid length")
            chunks = []; total = 0
            async for chunk in response.aiter_raw(chunk_size=CHUNK_BYTES):
                total += len(chunk)
                if total > limit:
                    raise parser.UploadDocumentParseError("Document parser response is too large")
                chunks.append(chunk)
            raw = b"".join(chunks)
            chunks.clear()
            return await _owned_io(_decode_response, response.status_code, dict(response.headers), raw)
    finally:
        if holder:
            await _owned_io(holder[0].close)


async def parse_conversation_document_file(*, endpoint: str, filename: str, file_path: Path) -> str:
    """Acquire capacity before file IO, then stream the already validated upload."""
    started = time.monotonic()
    timeout_seconds = min(570.0, parser._float_env(
        "DEERFLOW_UPLOAD_DOCUMENT_PARSE_TIMEOUT_SECONDS",
        parser._float_env("DEERFLOW_UPLOAD_PDF_PARSE_TIMEOUT_SECONDS", parser._DEFAULT_TIMEOUT_SECONDS)))
    await _acquire()
    try:
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise parser.UploadDocumentParseError("Document parser deadline expired during admission")
        return await _owned_parse(_parse_owned(endpoint, filename, file_path, remaining), remaining)
    except asyncio.TimeoutError as exc:
        raise parser.UploadDocumentParseError("Document parser total deadline exceeded") from exc
    except (httpx.HTTPError, OSError) as exc:
        raise parser.UploadDocumentParseError("Document parser request failed: " + type(exc).__name__) from exc
    finally:
        _release()
