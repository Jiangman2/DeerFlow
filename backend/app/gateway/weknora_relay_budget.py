"""Shared ASGI admission and bounded JSON transport for embedding relays."""

import asyncio
import json
import os
import weakref

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

LIMITERS = weakref.WeakKeyDictionary()
MAX_CONCURRENCY = 8
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
BODY_READ_SECONDS = 30


def embedding_limiter():
    loop = asyncio.get_running_loop()
    configured = LIMITERS.get(loop)
    if configured is None:
        try:
            concurrency = int(os.getenv("WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY", "8"))
        except ValueError:
            concurrency = MAX_CONCURRENCY
        concurrency = max(1, min(concurrency, MAX_CONCURRENCY))
        configured = (concurrency, asyncio.Semaphore(concurrency))
        LIMITERS[loop] = configured
    return configured[1]


class EmbeddingBudgetRoute(APIRoute):
    """Keep admission through response send, including slow downstream clients."""

    async def handle(self, scope, receive, send):
        if scope["method"] != "POST":
            return await super().handle(scope, receive, send)
        limiter = embedding_limiter()
        if limiter.locked():
            response = JSONResponse(
                {"detail": "Embedding relay busy; retry later"},
                status_code=429,
                headers={"Retry-After": "2"},
            )
            return await response(scope, receive, send)
        # No suspension between the availability check and immediate acquisition.
        # A full limiter never queues a request or reads its body.
        await limiter.acquire()
        try:
            return await super().handle(scope, receive, send)
        finally:
            limiter.release()


async def bounded_request_json(request, *, label, max_bytes):
    ceiling = max(1, min(max_bytes, MAX_REQUEST_BYTES))
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            size = int(declared)
        except ValueError:
            raise HTTPException(400, f"{label} invalid Content-Length") from None
        if size < 0:
            raise HTTPException(400, f"{label} invalid Content-Length")
        if size > ceiling:
            raise HTTPException(413, f"{label} request too large")
    raw = bytearray()
    try:
        try:
            async with asyncio.timeout(BODY_READ_SECONDS):
                async for chunk in request.stream():
                    if len(chunk) > ceiling - len(raw):
                        raise HTTPException(413, f"{label} request too large")
                    raw.extend(chunk)
        except TimeoutError:
            raise HTTPException(408, f"{label} request body timed out") from None
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise HTTPException(400, f"{label} invalid JSON") from None
        if not isinstance(data, dict):
            raise HTTPException(400, f"{label} body must be an object")
        return data
    finally:
        raw.clear()


class _JSONReply:
    def __init__(self, status_code, raw):
        self.status_code = status_code
        self.raw = raw

    def json(self):
        try:
            return json.loads(self.raw)
        except RecursionError:
            raise ValueError("Embedding response JSON nesting too deep") from None
        finally:
            self.raw.clear()


async def bounded_post(client, url, *, headers, **kwargs):
    headers = {**headers, "Accept-Encoding": "identity"}
    async with client.stream("POST", url, headers=headers, **kwargs) as response:
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        if encoding not in ("", "identity"):
            raise HTTPException(502, "Embedding upstream content encoding unsupported")
        try:
            size = int(response.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(502, "Embedding upstream Content-Length invalid") from None
        if not 0 <= size <= MAX_RESPONSE_BYTES:
            raise HTTPException(502, "Embedding upstream response too large")
        raw = bytearray()
        async for chunk in response.aiter_raw(chunk_size=16384):
            if len(chunk) > MAX_RESPONSE_BYTES - len(raw):
                raise HTTPException(502, "Embedding upstream response too large")
            raw.extend(chunk)
        return _JSONReply(response.status_code, raw)
