"""The sync ownership store and async stream bridge share Sentinel discovery."""

from __future__ import annotations

from urllib.parse import quote

import pytest

from deerflow.runtime.redis_endpoint import make_async_redis_client, make_sync_redis_client, parse_sentinel_endpoint


def _url(password: str = "s e&cret") -> str:
    return f"sentinel://redis-0:26379,redis-1:26379,redis-2:26379/deerflow/6?password={quote(password, safe='')}"


def test_sentinel_endpoint_requires_three_unique_servers_and_decodes_secret() -> None:
    endpoint = parse_sentinel_endpoint(_url())
    assert endpoint is not None
    assert endpoint.servers == (("redis-0", 26379), ("redis-1", 26379), ("redis-2", 26379))
    assert endpoint.master_name == "deerflow"
    assert endpoint.db == 6
    assert endpoint.password == "s e&cret"
    assert parse_sentinel_endpoint("redis://localhost:6379/6") is None


@pytest.mark.parametrize(
    "url",
    [
        "sentinel://redis-0:26379,redis-1:26379/deerflow/6?password=x",
        "sentinel://redis-0:26379,redis-0:26379,redis-2:26379/deerflow/6?password=x",
        "sentinel://redis-0:26379,redis-1:26379,redis-2:26379/deerflow/6",
        "sentinel://redis-0:26379,redis-1:26379,redis-2:26379/deerflow/6?password=x&password=y",
        "sentinel://redis-0:26379,redis-1:26379,redis-2:26379/deerflow/16?password=x",
        "sentinel://redis-0:0,redis-1:26379,redis-2:26379/deerflow/6?password=x",
        "sentinel://redis-0:26379,redis-1:26379,redis-2:26379/deerflow/6?password=x#fragment",
        "sentinel://redis-0:26379,redis-1:26379,redis-2:26379/deerflow/6?password=x&unknown=y",
        "sentinel://redis-0:secret@26379,redis-1:26379,redis-2:26379/deerflow/6?password=x",
    ],
)
def test_malformed_sentinel_endpoint_fails_closed_without_printing_secret(url: str) -> None:
    with pytest.raises(ValueError, match="Invalid Sentinel Redis endpoint configuration") as exc_info:
        parse_sentinel_endpoint(url)
    assert "password=" not in str(exc_info.value)
    assert exc_info.value.__cause__ is None


def test_sync_client_uses_sentinel_master_not_fixed_redis_address(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = {}
    expected = object()

    class FakeSentinel:
        def __init__(self, servers, *, sentinel_kwargs):
            observed["servers"] = servers
            observed["sentinel_kwargs"] = sentinel_kwargs

        def master_for(self, master_name, **kwargs):
            observed["master_name"] = master_name
            observed["kwargs"] = kwargs
            return expected

    monkeypatch.setattr("redis.sentinel.Sentinel", FakeSentinel)
    assert make_sync_redis_client(_url(), socket_timeout=5.0) is expected
    assert observed["servers"] == (("redis-0", 26379), ("redis-1", 26379), ("redis-2", 26379))
    assert observed["master_name"] == "deerflow"
    assert observed["kwargs"]["db"] == 6
    assert observed["kwargs"]["password"] == "s e&cret"
    assert observed["kwargs"]["socket_timeout"] == 5.0


def test_async_client_uses_same_sentinel_primary(monkeypatch: pytest.MonkeyPatch) -> None:
    observed = {}
    expected = object()

    class FakeSentinel:
        def __init__(self, servers, *, sentinel_kwargs):
            observed["servers"] = servers
            observed["sentinel_kwargs"] = sentinel_kwargs

        def master_for(self, master_name, **kwargs):
            observed["master_name"] = master_name
            observed["kwargs"] = kwargs
            return expected

    monkeypatch.setattr("redis.asyncio.sentinel.Sentinel", FakeSentinel)
    assert make_async_redis_client(_url(), max_connections=3000) is expected
    assert observed["master_name"] == "deerflow"
    assert observed["kwargs"]["db"] == 6
    assert observed["kwargs"]["password"] == "s e&cret"
    assert observed["kwargs"]["max_connections"] == 3000


def test_real_redis_py_clients_construct_without_contacting_sentinels() -> None:
    """Catch redis-py API drift without requiring any running Redis server."""
    from redis import Redis as SyncRedis
    from redis.asyncio import Redis as AsyncRedis

    sync_client = make_sync_redis_client(_url(), socket_timeout=0.5)
    async_client = make_async_redis_client(_url(), max_connections=32)
    assert isinstance(sync_client, SyncRedis)
    assert isinstance(async_client, AsyncRedis)
    assert sync_client.connection_pool.connection_kwargs["db"] == 6
    assert async_client.connection_pool.connection_kwargs["db"] == 6


@pytest.mark.asyncio
async def test_ownership_and_stream_bridge_use_sentinel_clients() -> None:
    """The production-facing constructors must route through the shared adapter."""
    from redis.asyncio.sentinel import SentinelConnectionPool as AsyncSentinelPool
    from redis.sentinel import SentinelConnectionPool as SyncSentinelPool

    from deerflow.community.aio_sandbox.ownership.redis import RedisOwnershipStore
    from deerflow.runtime.stream_bridge.redis import RedisStreamBridge

    store = RedisOwnershipStore(owner_id="test-worker", redis_url=_url(), ttl_seconds=60)
    bridge = RedisStreamBridge(redis_url=_url(), max_connections=32)
    try:
        assert isinstance(store._redis.connection_pool, SyncSentinelPool)
        assert isinstance(bridge._redis.connection_pool, AsyncSentinelPool)
    finally:
        store.close()
        await bridge.close()
