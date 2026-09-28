"""Bounded response cache and single-flight coordination for admin analytics.

Dashboard requests are read-only and identical for every administrator.  A
small local cache removes duplicate work inside one Gateway process, while an
optional Redis layer shares completed values between Gateway replicas.  Redis
is deliberately best-effort: analytics must remain available when the cache is
down, and the database remains the source of truth.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass(frozen=True)
class CacheResult[T]:
    value: T
    source: str


@dataclass
class _LocalEntry:
    payload: str
    expires_at: float


class DashboardResponseCache:
    """Cache JSON payloads without retaining ORM rows or unbounded results."""

    def __init__(self, *, max_entries: int = 32) -> None:
        self._max_entries = max(1, max_entries)
        self._entries: OrderedDict[str, _LocalEntry] = OrderedDict()
        self._inflight: dict[str, asyncio.Task[str]] = {}
        self._lock = asyncio.Lock()
        self._redis = None
        self._redis_disabled = False

    @staticmethod
    def _redis_url() -> str | None:
        return os.getenv("DEER_FLOW_DASHBOARD_REDIS_URL") or os.getenv("DEER_FLOW_STREAM_BRIDGE_REDIS_URL")

    @staticmethod
    def _redis_namespace() -> str:
        configured = os.getenv("DEER_FLOW_DASHBOARD_CACHE_NAMESPACE")
        if configured and configured.strip():
            return configured.strip()[:64]
        database_identity = os.getenv("DATABASE_URL", "default")
        return hashlib.sha256(database_identity.encode()).hexdigest()[:16]

    def _redis_key(self, key: str) -> str:
        return f"deerflow:dashboard:{self._redis_namespace()}:{key}"

    async def _redis_client(self):
        if self._redis_disabled or self._redis is not None:
            return self._redis
        redis_url = self._redis_url()
        if not redis_url:
            self._redis_disabled = True
            return None
        try:
            from redis.asyncio import Redis

            self._redis = Redis.from_url(
                redis_url,
                decode_responses=True,
                max_connections=8,
                socket_connect_timeout=0.5,
                socket_timeout=1.0,
            )
        except (ImportError, ValueError):
            logger.warning("Dashboard Redis cache is unavailable; using the bounded local cache", exc_info=True)
            self._redis_disabled = True
        return self._redis

    def _local_get(self, key: str, now: float) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= now:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return entry.payload

    def _local_put(self, key: str, payload: str, ttl_seconds: int) -> None:
        self._entries[key] = _LocalEntry(payload=payload, expires_at=monotonic() + ttl_seconds)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    async def _redis_get(self, key: str) -> str | None:
        client = await self._redis_client()
        if client is None:
            return None
        try:
            value = await client.get(self._redis_key(key))
            return value if isinstance(value, str) else None
        except Exception:
            logger.warning("Dashboard Redis cache read failed; continuing without it", exc_info=True)
            return None

    async def _redis_put(self, key: str, payload: str, ttl_seconds: int) -> None:
        client = await self._redis_client()
        if client is None:
            return
        try:
            await client.set(self._redis_key(key), payload, ex=ttl_seconds)
        except Exception:
            logger.warning("Dashboard Redis cache write failed; response remains available", exc_info=True)

    async def _discard_inflight(self, key: str, task: asyncio.Task[str]) -> None:
        # Retrieve failures even if the original HTTP waiter disconnected.
        if not task.cancelled():
            try:
                task.exception()
            except asyncio.CancelledError:
                pass
        async with self._lock:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def get_or_create(
        self,
        key: str,
        *,
        ttl_seconds: int,
        producer: Callable[[], Awaitable[T]],
        encode: Callable[[T], str],
        decode: Callable[[str], T],
        bypass: bool = False,
    ) -> CacheResult[T]:
        """Return a cached value or run one producer per key in this process."""

        ttl_seconds = max(1, ttl_seconds)
        if not bypass:
            payload = self._local_get(key, monotonic())
            if payload is not None:
                try:
                    return CacheResult(decode(payload), "local")
                except (TypeError, ValueError):
                    self._entries.pop(key, None)
                    logger.warning("Discarded an incompatible local dashboard cache value")
            payload = await self._redis_get(key)
            if payload is not None:
                try:
                    decoded = decode(payload)
                except (TypeError, ValueError):
                    logger.warning("Discarded an incompatible Redis dashboard cache value")
                else:
                    self._local_put(key, payload, ttl_seconds)
                    return CacheResult(decoded, "redis")

        async with self._lock:
            if not bypass:
                payload = self._local_get(key, monotonic())
                if payload is not None:
                    try:
                        return CacheResult(decode(payload), "local")
                    except (TypeError, ValueError):
                        self._entries.pop(key, None)
            task = self._inflight.get(key)
            source = "shared"
            if task is None:
                source = "computed"

                async def _produce() -> str:
                    value = await producer()
                    return encode(value)

                task = asyncio.create_task(_produce())
                self._inflight[key] = task
                task.add_done_callback(lambda completed, cache_key=key: asyncio.create_task(self._discard_inflight(cache_key, completed)))

        try:
            payload = await asyncio.shield(task)
        finally:
            if task.done():
                await self._discard_inflight(key, task)

        self._local_put(key, payload, ttl_seconds)
        await self._redis_put(key, payload, ttl_seconds)
        return CacheResult(decode(payload), source)

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()


dashboard_response_cache = DashboardResponseCache()
