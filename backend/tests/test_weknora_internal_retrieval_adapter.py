import asyncio
import json
import math

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.gateway.routers import weknora


def _request(path: str, payload: object, api_key: str | None = None) -> Request:
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

    return Request({"type": "http", "method": "POST", "path": path, "headers": headers}, receive=receive)


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


@pytest.fixture
def internal_keys(monkeypatch):
    monkeypatch.setenv("WEKNORA_INTERNAL_MODEL_RELAY_KEY", "relay-secret")
    monkeypatch.setenv("CNOOC_RETRIEVAL_API_KEY", "upstream-secret")


@pytest.fixture(autouse=True)
def reset_internal_embedding_limiters():
    weknora._INTERNAL_EMBEDDING_LIMITERS.clear()
    yield
    weknora._INTERNAL_EMBEDDING_LIMITERS.clear()


@pytest.mark.asyncio
async def test_internal_embedding_relay_defaults_to_approved_concurrency(monkeypatch):
    monkeypatch.delenv("WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY", raising=False)

    limiter = weknora._internal_embedding_limiter()

    assert limiter._value == 200


@pytest.mark.asyncio
async def test_internal_embedding_relay_clamps_concurrency_to_approved_limit(monkeypatch):
    monkeypatch.setenv("WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY", "999")

    limiter = weknora._internal_embedding_limiter()

    assert limiter._value == 200


@pytest.mark.asyncio
async def test_internal_embedding_relay_normalizes_weknora_request(monkeypatch, internal_keys):
    calls = []
    upstream = {
        "data": [
            {"index": 0, "embedding": [0.1] * 4096},
            {"index": 1, "embedding": [0.2] * 4096},
        ],
        "model": "Qwen3-Embedding-8B",
    }
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(_Response(200, upstream), calls),
    )
    payload = {
        "model": "Qwen3-Embedding-8B",
        "input": ["first", "second"],
        "encoding_format": "float",
        "dimensions": 1024,
        "truncate_prompt_tokens": 511,
    }

    response = await weknora.cnooc_embeddings(
        _request("/api/weknora/internal-models/v1/embeddings", payload, "relay-secret")
    )

    response_payload = json.loads(response.body)
    assert [len(row["embedding"]) for row in response_payload["data"]] == [1024, 1024]
    assert math.isclose(
        math.sqrt(sum(value * value for value in response_payload["data"][0]["embedding"])),
        1.0,
    )
    url, kwargs = calls[0]
    assert url == "http://model.ai.cnooc/member1/qwen3-embedding-8b/v1/embeddings"
    assert kwargs["headers"]["Authorization"] == "Bearer upstream-secret"
    assert kwargs["json"] == {
        "input": ["first", "second"],
        "encoding_format": "float",
        "dimensions": 1024,
    }


@pytest.mark.asyncio
async def test_internal_embedding_relay_preserves_native_1024_vectors(monkeypatch, internal_keys):
    calls = []
    upstream = {
        "data": [{"index": 0, "embedding": [0.25] * 1024}],
        "model": "Qwen3-Embedding-8B",
    }
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(_Response(200, upstream), calls),
    )

    response = await weknora.cnooc_embeddings(
        _request(
            "/api/weknora/internal-models/v1/embeddings",
            {
                "model": "Qwen3-Embedding-8B",
                "input": "first",
                "dimensions": 1024,
            },
            "relay-secret",
        )
    )

    assert json.loads(response.body)["data"][0]["embedding"] == [0.25] * 1024
    assert calls[0][1]["json"]["dimensions"] == 1024


@pytest.mark.asyncio
async def test_internal_embedding_relay_serializes_upstream_calls(monkeypatch, internal_keys):
    active = 0
    max_active = 0

    class _TrackingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            try:
                await asyncio.sleep(0.01)
                return _Response(
                    200,
                    {
                        "data": [{"index": 0, "embedding": [0.25] * 1024}],
                        "model": "Qwen3-Embedding-8B",
                    },
                )
            finally:
                active -= 1

    monkeypatch.setenv("WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY", "1")
    monkeypatch.setenv("WEKNORA_INTERNAL_EMBEDDING_BUSY_RETRIES", "0")
    monkeypatch.setattr(weknora.httpx, "AsyncClient", lambda **kwargs: _TrackingClient())

    requests = [
        _request(
            "/api/weknora/internal-models/v1/embeddings",
            {
                "model": "Qwen3-Embedding-8B",
                "input": [f"request-{index}"],
                "dimensions": 1024,
            },
            "relay-secret",
        )
        for index in range(2)
    ]
    responses = await asyncio.gather(*(weknora.cnooc_embeddings(request) for request in requests))

    assert len(responses) == 2
    assert max_active == 1


@pytest.mark.asyncio
async def test_internal_embedding_relay_retries_transient_10020(monkeypatch, internal_keys):
    responses = [
        _Response(200, {"success": False, "code": 10020}),
        _Response(
            200,
            {
                "data": [{"index": 0, "embedding": [0.25] * 1024}],
                "model": "Qwen3-Embedding-8B",
            },
        ),
    ]
    calls = []

    class _SequenceClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return responses.pop(0)

    monkeypatch.setenv("WEKNORA_INTERNAL_EMBEDDING_BUSY_RETRIES", "1")
    monkeypatch.setenv("WEKNORA_INTERNAL_EMBEDDING_BUSY_RETRY_SECONDS", "0")
    monkeypatch.setattr(weknora.httpx, "AsyncClient", lambda **kwargs: _SequenceClient())

    response = await weknora.cnooc_embeddings(
        _request(
            "/api/weknora/internal-models/v1/embeddings",
            {
                "model": "Qwen3-Embedding-8B",
                "input": ["first"],
                "dimensions": 1024,
            },
            "relay-secret",
        )
    )

    assert len(calls) == 2
    assert len(json.loads(response.body)["data"]) == 1


@pytest.mark.asyncio
async def test_internal_rerank_relay_normalizes_weknora_request(monkeypatch, internal_keys):
    calls = []
    upstream = {
        "results": [
            {"index": 1, "document": "beta", "relevance_score": 0.9},
            {"index": 0, "document": "alpha", "relevance_score": 0.3},
        ]
    }
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(_Response(200, upstream), calls),
    )
    payload = {
        "model": "Qwen3-Reranker-8B",
        "query": "beta",
        "documents": ["alpha", "beta"],
        "truncate_prompt_tokens": 511,
        "additional_data": {},
    }

    response = await weknora.cnooc_rerank(
        _request("/api/weknora/internal-models/v1/rerank", payload, "relay-secret")
    )

    assert json.loads(response.body) == upstream
    url, kwargs = calls[0]
    assert url == "http://model.ai.cnooc/member1/qwen3-reranker-8b/v1/rerank"
    assert kwargs["headers"]["Authorization"] == "Bearer upstream-secret"
    assert kwargs["json"] == {"query": "beta", "documents": ["alpha", "beta"]}


@pytest.mark.asyncio
async def test_internal_relays_reject_wrong_relay_key(internal_keys):
    with pytest.raises(HTTPException) as embedding:
        await weknora.cnooc_embeddings(
            _request(
                "/api/weknora/internal-models/v1/embeddings",
                {"model": "Qwen3-Embedding-8B", "input": ["test"]},
                "wrong",
            )
        )
    assert embedding.value.status_code == 401

    with pytest.raises(HTTPException) as rerank:
        await weknora.cnooc_rerank(
            _request(
                "/api/weknora/internal-models/v1/rerank",
                {"model": "Qwen3-Reranker-8B", "query": "a", "documents": ["a"]},
            )
        )
    assert rerank.value.status_code == 401


@pytest.mark.asyncio
async def test_internal_relays_validate_contract_and_business_failure(monkeypatch, internal_keys):
    with pytest.raises(HTTPException) as bad_model:
        await weknora.cnooc_embeddings(
            _request(
                "/api/weknora/internal-models/v1/embeddings",
                {"model": "other", "input": ["test"]},
                "relay-secret",
            )
        )
    assert bad_model.value.status_code == 400

    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(_Response(200, {"success": False, "code": 10003}), []),
    )
    with pytest.raises(HTTPException) as rejected:
        await weknora.cnooc_rerank(
            _request(
                "/api/weknora/internal-models/v1/rerank",
                {"model": "Qwen3-Reranker-8B", "query": "a", "documents": ["a"]},
                "relay-secret",
            )
        )
    assert rejected.value.status_code == 502
    assert "10003" in str(rejected.value.detail)


@pytest.mark.asyncio
async def test_internal_relay_maps_network_failure(monkeypatch, internal_keys):
    class _FailingClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, url, **kwargs):
            raise httpx.ConnectError("offline")

    monkeypatch.setattr(weknora.httpx, "AsyncClient", lambda **kwargs: _FailingClient())
    with pytest.raises(HTTPException) as failure:
        await weknora.cnooc_embeddings(
            _request(
                "/api/weknora/internal-models/v1/embeddings",
                {"model": "Qwen3-Embedding-8B", "input": ["test"]},
                "relay-secret",
            )
        )
    assert failure.value.status_code == 502
