from __future__ import annotations

import asyncio

import pytest

from app.gateway.dashboard_cache import DashboardResponseCache

pytestmark = pytest.mark.asyncio


async def test_dashboard_cache_collapses_concurrent_identical_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEER_FLOW_DASHBOARD_REDIS_URL", raising=False)
    monkeypatch.delenv("DEER_FLOW_STREAM_BRIDGE_REDIS_URL", raising=False)
    cache = DashboardResponseCache()
    calls = 0

    async def producer() -> dict[str, int]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {"value": 7}

    async def load():
        return await cache.get_or_create(
            "same-key",
            ttl_seconds=30,
            producer=producer,
            encode=lambda value: str(value["value"]),
            decode=lambda payload: {"value": int(payload)},
        )

    results = await asyncio.gather(*(load() for _ in range(12)))

    assert calls == 1
    assert [item.value for item in results] == [{"value": 7}] * 12
    assert {item.source for item in results} <= {"computed", "shared", "local"}


async def test_dashboard_cache_reuses_completed_value_without_recomputing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEER_FLOW_DASHBOARD_REDIS_URL", raising=False)
    monkeypatch.delenv("DEER_FLOW_STREAM_BRIDGE_REDIS_URL", raising=False)
    cache = DashboardResponseCache()
    calls = 0

    async def producer() -> str:
        nonlocal calls
        calls += 1
        return "fresh"

    first = await cache.get_or_create(
        "key",
        ttl_seconds=30,
        producer=producer,
        encode=str,
        decode=str,
    )
    second = await cache.get_or_create(
        "key",
        ttl_seconds=30,
        producer=producer,
        encode=str,
        decode=str,
    )

    assert calls == 1
    assert first.source == "computed"
    assert second.source == "local"
    assert second.value == "fresh"


async def test_dashboard_cache_does_not_cache_failed_computation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEER_FLOW_DASHBOARD_REDIS_URL", raising=False)
    monkeypatch.delenv("DEER_FLOW_STREAM_BRIDGE_REDIS_URL", raising=False)
    cache = DashboardResponseCache()
    attempts = 0

    async def producer() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary failure")
        return "recovered"

    with pytest.raises(RuntimeError, match="temporary failure"):
        await cache.get_or_create(
            "retryable",
            ttl_seconds=30,
            producer=producer,
            encode=str,
            decode=str,
        )

    result = await cache.get_or_create(
        "retryable",
        ttl_seconds=30,
        producer=producer,
        encode=str,
        decode=str,
    )

    assert attempts == 2
    assert result.value == "recovered"
