"""Authenticated, same-origin proxy for reviewed DataLake preview images."""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/datalake", tags=["datalake"])

_PREVIEW_SCHEME = "http"
_PREVIEW_HOST = "10.77.124.142"
_PREVIEW_PORT = 30900
_PREVIEW_PATH_PREFIX = "/img-preview/"
_TIMEOUT_SECONDS = 20.0
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_SAFE_IMAGE_TYPES = frozenset(
    {"image/avif", "image/gif", "image/jpeg", "image/png", "image/webp"}
)


def _contains_unsafe_path_segment(path: str) -> bool:
    """Reject traversal and separator ambiguity after bounded URL decoding."""

    decoded = path
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if "\\" in decoded or "\x00" in decoded:
        return True
    return any(segment in {".", ".."} for segment in decoded.split("/"))


def _reviewed_preview_url(source_url: str) -> str:
    """Validate the one DataLake origin and path this proxy may reach."""

    try:
        parsed = urlsplit(source_url)
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid DataLake image URL") from exc
    if (
        parsed.scheme.lower() != _PREVIEW_SCHEME
        or parsed.hostname != _PREVIEW_HOST
        or port != _PREVIEW_PORT
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith(_PREVIEW_PATH_PREFIX)
        or _contains_unsafe_path_segment(parsed.path)
    ):
        raise HTTPException(status_code=400, detail="Unapproved DataLake image URL")
    return source_url


def _image_content_type(value: str | None) -> str:
    content_type = (value or "").split(";", 1)[0].strip().lower()
    if content_type not in _SAFE_IMAGE_TYPES:
        raise HTTPException(
            status_code=502,
            detail="DataLake returned an unsupported image type",
        )
    return content_type


@router.get("/image-preview")
async def proxy_image_preview(
    src: str = Query(min_length=1, max_length=4096),
) -> StreamingResponse:
    """Stream one reviewed image without exposing its HTTP origin to browsers.

    Normal Gateway session middleware protects this route. The proxy does not
    store or transform the upstream bytes, does not follow redirects, and does
    not use environment proxy settings.
    """

    source_url = _reviewed_preview_url(src)
    client = httpx.AsyncClient(
        follow_redirects=False,
        timeout=_TIMEOUT_SECONDS,
        trust_env=False,
    )
    try:
        request = client.build_request(
            "GET",
            source_url,
            headers={"Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif"},
        )
        response = await client.send(request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail="DataLake image service is unavailable",
        ) from exc

    if response.status_code < 200 or response.status_code >= 300:
        await response.aclose()
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail="DataLake image service returned an error",
        )

    try:
        content_type = _image_content_type(response.headers.get("content-type"))
        try:
            content_length = int(response.headers.get("content-length", "0"))
        except ValueError:
            content_length = 0
        if content_length > _MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="DataLake image exceeds the proxy size limit",
            )
    except HTTPException:
        await response.aclose()
        await client.aclose()
        raise

    async def body() -> AsyncIterator[bytes]:
        sent = 0
        try:
            async for chunk in response.aiter_bytes():
                if sent + len(chunk) > _MAX_IMAGE_BYTES:
                    raise RuntimeError("DataLake image exceeded the streaming size limit")
                sent += len(chunk)
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    return StreamingResponse(
        body(),
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=300",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )
