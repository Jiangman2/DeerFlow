"""Tests for the authenticated DataLake image preview proxy."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.auth_middleware import _is_public
from app.gateway.routers import datalake


@pytest.mark.parametrize(
    "source_url",
    (
        "https://10.77.124.142:30900/img-preview/a.png",
        "http://10.77.124.142:30901/img-preview/a.png",
        "http://user@10.77.124.142:30900/img-preview/a.png",
        "http://10.77.124.142:30900/not-preview/a.png",
        "http://example.invalid:30900/img-preview/a.png",
        "http://10.77.124.142:30900/img-preview/../secret.png",
        "http://10.77.124.142:30900/img-preview/%2e%2e/secret.png",
        "http://10.77.124.142:30900/img-preview/%252e%252e/secret.png",
        "http://10.77.124.142:30900/img-preview/%5csecret.png",
        "http://10.77.124.142:30900/img-preview/a.png#fragment",
    ),
)
def test_reviewed_preview_url_rejects_every_unapproved_origin_or_path(source_url: str):
    with pytest.raises(HTTPException) as exc_info:
        datalake._reviewed_preview_url(source_url)

    assert exc_info.value.status_code == 400


def test_reviewed_preview_url_accepts_the_exact_internal_preview_origin():
    source_url = "http://10.77.124.142:30900/img-preview/%E4%BA%95/a.png"

    assert datalake._reviewed_preview_url(source_url) == source_url


@pytest.mark.anyio
async def test_proxy_streams_only_safe_images_without_environment_proxy(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png", "content-length": "6"}

        async def aiter_bytes(self):
            yield b"abc"
            yield b"def"

        async def aclose(self):
            captured["response_closed"] = True

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        def build_request(self, method: str, url: str, *, headers: dict[str, str]):
            captured["request"] = (method, url, headers)
            return SimpleNamespace()

        async def send(self, request, *, stream: bool):
            captured["stream"] = stream
            return FakeResponse()

        async def aclose(self):
            captured["client_closed"] = True

    monkeypatch.setattr(datalake.httpx, "AsyncClient", FakeClient)

    response = await datalake.proxy_image_preview("http://10.77.124.142:30900/img-preview/document/image.png")
    body = b"".join([chunk async for chunk in response.body_iterator])

    assert body == b"abcdef"
    assert response.media_type == "image/png"
    assert captured["client_kwargs"] == {
        "follow_redirects": False,
        "timeout": datalake._TIMEOUT_SECONDS,
        "trust_env": False,
    }
    assert captured["request"] == (
        "GET",
        "http://10.77.124.142:30900/img-preview/document/image.png",
        {"Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif"},
    )
    assert captured["stream"] is True
    assert captured["response_closed"] is True
    assert captured["client_closed"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status_code", "headers", "expected_status"),
    (
        (302, {"content-type": "image/png"}, 502),
        (200, {"content-type": "text/html"}, 502),
        (
            200,
            {
                "content-type": "image/png",
                "content-length": str(datalake._MAX_IMAGE_BYTES + 1),
            },
            413,
        ),
    ),
)
async def test_proxy_rejects_unsafe_upstream_responses_and_closes_resources(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    headers: dict[str, str],
    expected_status: int,
):
    captured: dict[str, bool] = {}

    class FakeResponse:
        async def aclose(self):
            captured["response_closed"] = True

    response = FakeResponse()
    response.status_code = status_code
    response.headers = headers

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def build_request(self, *_args, **_kwargs):
            return SimpleNamespace()

        async def send(self, _request, *, stream: bool):
            assert stream is True
            return response

        async def aclose(self):
            captured["client_closed"] = True

    monkeypatch.setattr(datalake.httpx, "AsyncClient", FakeClient)

    with pytest.raises(HTTPException) as exc_info:
        await datalake.proxy_image_preview("http://10.77.124.142:30900/img-preview/document/image.png")

    assert exc_info.value.status_code == expected_status
    assert captured["response_closed"] is True
    assert captured["client_closed"] is True


@pytest.mark.anyio
async def test_proxy_closes_resources_when_a_chunk_exceeds_the_stream_limit(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, bool] = {}

    class FakeResponse:
        status_code = 200
        headers = {"content-type": "image/png"}

        async def aiter_bytes(self):
            yield b"1234"
            yield b"5678"

        async def aclose(self):
            captured["response_closed"] = True

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def build_request(self, *_args, **_kwargs):
            return SimpleNamespace()

        async def send(self, _request, *, stream: bool):
            assert stream is True
            return FakeResponse()

        async def aclose(self):
            captured["client_closed"] = True

    monkeypatch.setattr(datalake.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(datalake, "_MAX_IMAGE_BYTES", 6)

    response = await datalake.proxy_image_preview("http://10.77.124.142:30900/img-preview/document/image.png")
    with pytest.raises(RuntimeError, match="streaming size limit"):
        _ = [chunk async for chunk in response.body_iterator]

    assert captured["response_closed"] is True
    assert captured["client_closed"] is True


def test_proxy_router_uses_the_expected_same_origin_path():
    assert any(route.path == "/api/datalake/image-preview" for route in datalake.router.routes)


def test_proxy_route_remains_protected_by_gateway_session_authentication():
    assert _is_public("/api/datalake/image-preview") is False
