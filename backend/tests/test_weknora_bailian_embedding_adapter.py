import json
import ssl

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.gateway.routers import weknora


def _request(payload: object, api_key: str | None = None) -> Request:
    body = json.dumps(payload).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if api_key is not None:
        headers.append((b"authorization", f"Bearer {api_key}".encode()))
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.request", "body": b"", "more_body": False}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/weknora/bailian/v1/embeddings",
            "headers": headers,
        },
        receive=receive,
    )


class _Response:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Client:
    def __init__(self, response: _Response, calls: list):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_bailian_tls_verify_uses_scoped_security_level_one_context(monkeypatch):
    class _Context:
        def __init__(self):
            self.ciphers = []

        def set_ciphers(self, value):
            self.ciphers.append(value)

    context = _Context()
    monkeypatch.setenv("WEKNORA_BAILIAN_TLS_COMPAT", "true")
    monkeypatch.setattr(weknora.ssl, "create_default_context", lambda: context)

    assert weknora._bailian_tls_verify() is context
    assert context.ciphers == ["DEFAULT:@SECLEVEL=1"]


def test_bailian_tls_verify_keeps_normal_verification_by_default(monkeypatch):
    monkeypatch.delenv("WEKNORA_BAILIAN_TLS_COMPAT", raising=False)
    monkeypatch.setattr(
        weknora.ssl,
        "create_default_context",
        lambda: pytest.fail("default mode must leave httpx verification unchanged"),
    )

    assert weknora._bailian_tls_verify() is True


@pytest.mark.asyncio
async def test_bailian_embedding_adapter_forwards_openai_request(monkeypatch):
    calls = []
    payload = {
        "model": "text-embedding-v4",
        "input": ["first chunk", "second chunk"],
        "dimensions": 1024,
    }
    upstream_payload = {
        "object": "list",
        "data": [
            {"object": "embedding", "index": 0, "embedding": [0.1, 0.2]},
            {"object": "embedding", "index": 1, "embedding": [0.3, 0.4]},
        ],
        "model": "text-embedding-v4",
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
    }
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(_Response(200, upstream_payload), calls),
    )

    response = await weknora.bailian_embeddings(_request(payload, "embedding-secret"))

    assert response.status_code == 200
    assert json.loads(response.body) == upstream_payload
    assert len(calls) == 1
    url, kwargs = calls[0]
    assert url == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
    assert kwargs["headers"]["Authorization"] == "Bearer embedding-secret"
    assert kwargs["json"] == payload


@pytest.mark.asyncio
async def test_bailian_embedding_adapter_rejects_missing_or_wrong_key(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    payload = {"model": "text-embedding-v4", "input": ["test"]}

    with pytest.raises(HTTPException) as missing:
        await weknora.bailian_embeddings(_request(payload))
    assert missing.value.status_code == 401

    with pytest.raises(HTTPException) as wrong:
        await weknora.bailian_embeddings(_request(payload, "wrong-secret"))
    assert wrong.value.status_code == 401


@pytest.mark.asyncio
async def test_bailian_embedding_adapter_rejects_wrong_model_and_oversized_body(monkeypatch):
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")

    with pytest.raises(HTTPException) as wrong_model:
        await weknora.bailian_embeddings(_request({"model": "other-model", "input": ["test"]}, "embedding-secret"))
    assert wrong_model.value.status_code == 400

    monkeypatch.setenv("WEKNORA_BAILIAN_EMBEDDING_MAX_BYTES", "4")
    with pytest.raises(HTTPException) as oversized:
        await weknora.bailian_embeddings(_request({"model": "text-embedding-v4", "input": ["test"]}, "embedding-secret"))
    assert oversized.value.status_code == 413


@pytest.mark.asyncio
async def test_bailian_embedding_adapter_maps_upstream_and_network_failures(monkeypatch):
    payload = {"model": "text-embedding-v4", "input": ["test"]}
    monkeypatch.setenv("EMBEDDING_API_KEY", "embedding-secret")
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(_Response(429, {"error": {"message": "rate limit"}}), []),
    )

    with pytest.raises(HTTPException) as upstream:
        await weknora.bailian_embeddings(_request(payload, "embedding-secret"))
    assert upstream.value.status_code == 502
    assert "429" in str(upstream.value.detail)

    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(weknora.httpx, "AsyncClient", lambda **kwargs: _FailingClient())
    with pytest.raises(HTTPException) as network:
        await weknora.bailian_embeddings(_request(payload, "embedding-secret"))
    assert network.value.status_code == 502
