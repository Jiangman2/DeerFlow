"""Redis Streams-backed stream bridge."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from typing import Any, TypeVar
from uuid import uuid4

try:
    from redis.asyncio import Redis
    from redis.exceptions import ConnectionError as RedisConnectionError
    from redis.exceptions import RedisError, ResponseError
    from redis.exceptions import TimeoutError as RedisTimeoutError
except ImportError:  # pragma: no cover - only hit when the optional extra is missing
    # ``redis`` is an optional extra (mirrors the ``postgres``/asyncpg path in
    # persistence/engine.py). This module is imported lazily from
    # ``make_stream_bridge`` only when ``stream_bridge.type == "redis"``, so the
    # hint surfaces exactly when a Redis bridge is requested without the package.
    raise ImportError(
        "stream_bridge.type is set to 'redis' but the redis package is not installed.\n"
        "Install it with:\n"
        "    cd backend && uv sync --all-packages --extra redis\n"
        "On the next `make dev` the redis extra is auto-detected from config.yaml\n"
        "(stream_bridge.type: redis) and reinstalled, so it will not be wiped again.\n"
        "Or switch to stream_bridge.type: memory in config.yaml for single-process deployment."
    ) from None

from .base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridge, StreamEvent, StreamReplayGapError

logger = logging.getLogger(__name__)

_KIND_EVENT = "event"
_KIND_END = "end"
_REDIS_STREAM_ID_RE = re.compile(r"[0-9]{1,20}(-[0-9]{1,20})?")

# Batch size for ``XREAD``. Reading more than one entry per round-trip collapses
# a large ``Last-Event-ID`` replay into far fewer calls; live tailing still
# yields each event as it arrives because the consume loop returns mid-batch on
# the end marker.
_XREAD_COUNT = 64

# Maximum consecutive transient Redis errors (``ConnectionError``,
# ``TimeoutError``, etc.) tolerated during ``subscribe`` before the error
# propagates to the caller.  Brief blips are retried with exponential backoff
# capped at ``heartbeat_interval``.
_MAX_SUBSCRIBE_RETRIES = 3

# A lost write response does not reveal whether XADD committed. Probe
# the retained stream for this publication's private id before retrying it.
_MAX_PUBLISH_ATTEMPTS = 3
_MAX_PUBLISH_PROBES = 5
_PUBLISH_RETRY_DELAY_SECONDS = 0.25
_PUBLISH_ID_FIELD = "publish_id"
_BATCH_ID_FIELD = "batch_id"
_BATCH_INDEX_FIELD = "batch_index"
_MAX_INFLIGHT_WRITES = 32
_WRITE_TIMING_LOG_EVERY = 1000
_WriteResult = TypeVar("_WriteResult")

# Keep every SSE event in its own Redis Stream entry (and therefore keep its
# Last-Event-ID), while avoiding MULTI/XADD/EXPIRE/EXEC on every token.  The
# server checks TTL in the same atomic operation, so a recreated/evicted key
# can never be left without the leak-safety expiry.
_XADD_RETAINED_SCRIPT = """
local id = redis.call('XADD', KEYS[1], 'MAXLEN', '=', ARGV[1], '*', unpack(ARGV, 5))
local ttl = tonumber(ARGV[2])
local slack = tonumber(ARGV[3])
if ARGV[4] == '1' then
    redis.call('EXPIRE', KEYS[1], ttl)
else
    local remaining = redis.call('TTL', KEYS[1])
    if remaining <= ttl or remaining > ttl + slack then
        redis.call('EXPIRE', KEYS[1], ttl + slack)
    end
end
return id
"""

# A lost reply cannot be resolved by a read on another connection: that read
# may overtake the original write. Check and append in the same server-side
# operation instead. Each Run has one serial writer, so a retry of this batch
# sees either its own final entry, its own partial entry, or the preceding batch.
_XADD_BATCH_SCRIPT = """
local count = tonumber(ARGV[4])
local latest = redis.call('XREVRANGE', KEYS[1], '+', '-', 'COUNT', 1)
local predecessor = '0-0'
if #latest > 0 then
    predecessor = latest[1][1]
    local fields = latest[1][2]
    local batch_id = nil
    local batch_index = nil
    for i = 1, #fields, 2 do
        if fields[i] == 'batch_id' then batch_id = fields[i + 1] end
        if fields[i] == 'batch_index' then batch_index = tonumber(fields[i + 1]) end
    end
    if batch_id == ARGV[3] then
        if batch_index == count then return 'already_committed' end
        return redis.error_reply('partial stream bridge batch; refusing replay')
    end
end
if predecessor ~= ARGV[5] then
    return redis.error_reply('stream advanced before batch commit; refusing stale write')
end
for i = 1, count do
    local offset = 6 + (i - 1) * 3
    redis.call('XADD', KEYS[1], 'MAXLEN', '=', ARGV[1], '*',
        'kind', 'event', 'event', ARGV[offset], 'data', ARGV[offset + 1],
        'publish_id', ARGV[offset + 2], 'batch_id', ARGV[3], 'batch_index', tostring(i))
end
if tonumber(ARGV[2]) > 0 then redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2])) end
return 'committed'
"""


class RedisStreamBridge(StreamBridge):
    """Per-run stream bridge backed by Redis Streams.

    Each run is stored in one Redis Stream and subscribers read directly with
    ``XREAD``.  This keeps the SSE bridge usable across multiple gateway
    worker processes while preserving ``Last-Event-ID`` replay semantics.
    """

    supports_cross_process = True
    supports_batched_publish = True

    def __init__(
        self,
        *,
        redis_url: str,
        queue_maxsize: int = 256,
        key_prefix: str = "deerflow:stream_bridge",
        max_connections: int | None = None,
        stream_ttl_seconds: int | None = 86400,
        client: Redis | None = None,
    ) -> None:
        self._redis_url = redis_url
        self._maxsize = max(1, queue_maxsize)
        self._key_prefix = key_prefix.rstrip(":")
        if stream_ttl_seconds is not None and stream_ttl_seconds > 0:
            self._stream_ttl_seconds = stream_ttl_seconds
        else:
            self._stream_ttl_seconds = None
        # The added slack guarantees at least the configured TTL after the
        # most recent publish without extending the expiry on every chunk.
        # Short TTLs retain the existing per-publish expiry behavior.
        self._ttl_refresh_slack_seconds = min(60, self._stream_ttl_seconds // 2) if self._stream_ttl_seconds else 0
        # Each live SSE subscriber holds one pooled connection blocked in
        # ``XREAD ... BLOCK`` for up to ``heartbeat_interval``. ``max_connections``
        # caps that pool; ``None`` keeps redis-py's effectively-unbounded default.
        self._redis = client if client is not None else Redis.from_url(redis_url, decode_responses=True, max_connections=max_connections)
        self._owns_client = client is None
        # Different runs may write concurrently, but bound the number of
        # simultaneous Redis write requests made by this Gateway process.
        self._write_slots = asyncio.Semaphore(_MAX_INFLIGHT_WRITES)
        # Diagnostic counters are local to this event loop and contain no event data.
        self._write_timing: dict[str, dict[str, int]] = {}
        self.max_publish_batch_size = min(64, self._maxsize)
        register_script = getattr(self._redis, "register_script", None)
        self._xadd_retained_script = register_script(_XADD_RETAINED_SCRIPT) if self._stream_ttl_seconds and callable(register_script) else None
        self._xadd_batch_script = register_script(_XADD_BATCH_SCRIPT) if callable(register_script) else None
        self.supports_batched_publish = self._xadd_batch_script is not None

    def _stream_key(self, run_id: str) -> str:
        return f"{self._key_prefix}:{run_id}"

    async def _timed_write(self, operation: str, call: Callable[[], Awaitable[_WriteResult]]) -> _WriteResult:
        """Separate local write-slot queueing from the Redis client await."""
        started = time.monotonic()
        acquired: float | None = None
        failed = False
        try:
            async with self._write_slots:
                acquired = time.monotonic()
                return await call()
        except BaseException:
            failed = True
            raise
        finally:
            finished = time.monotonic()
            slot_wait_ms = round(((acquired if acquired is not None else finished) - started) * 1000)
            redis_await_ms = round((finished - acquired) * 1000) if acquired is not None else 0
            self._record_write_timing(operation, slot_wait_ms, redis_await_ms, failed=failed)

    def _record_write_timing(self, operation: str, slot_wait_ms: int, redis_await_ms: int, *, failed: bool) -> None:
        stats = self._write_timing.get(operation)
        if stats is None:
            stats = {
                "calls": 0,
                "slot_wait_total_ms": 0,
                "slot_wait_max_ms": 0,
                "slot_wait_over_100ms": 0,
                "redis_await_total_ms": 0,
                "redis_await_max_ms": 0,
                "redis_await_over_100ms": 0,
                "failed": 0,
            }
            self._write_timing[operation] = stats
        stats["calls"] += 1
        stats["slot_wait_total_ms"] += slot_wait_ms
        stats["slot_wait_max_ms"] = max(stats["slot_wait_max_ms"], slot_wait_ms)
        stats["slot_wait_over_100ms"] += slot_wait_ms >= 100
        stats["redis_await_total_ms"] += redis_await_ms
        stats["redis_await_max_ms"] = max(stats["redis_await_max_ms"], redis_await_ms)
        stats["redis_await_over_100ms"] += redis_await_ms >= 100
        stats["failed"] += failed
        if stats["calls"] >= _WRITE_TIMING_LOG_EVERY:
            logger.info(
                "REDIS_STREAM_WRITE_TIMING operation=%s calls=%d slot_wait_total_ms=%d slot_wait_max_ms=%d slot_wait_over_100ms=%d redis_await_total_ms=%d redis_await_max_ms=%d redis_await_over_100ms=%d failed=%d",
                operation,
                stats["calls"],
                stats["slot_wait_total_ms"],
                stats["slot_wait_max_ms"],
                stats["slot_wait_over_100ms"],
                stats["redis_await_total_ms"],
                stats["redis_await_max_ms"],
                stats["redis_await_over_100ms"],
                stats["failed"],
            )
            del self._write_timing[operation]

    async def _xadd_retained(self, key: str, fields: dict[str, str], *, maxlen: int, terminal: bool) -> None:
        if self._stream_ttl_seconds is None:
            await self._redis.xadd(
                key,
                fields,
                maxlen=maxlen,
                approximate=False,
            )
            return

        if self._xadd_retained_script is not None:
            field_args = [part for name, value in fields.items() for part in (name, value)]
            await self._xadd_retained_script(
                keys=[key],
                args=[maxlen, self._stream_ttl_seconds, self._ttl_refresh_slack_seconds, int(terminal), *field_args],
            )
            return

        # Lightweight test doubles without scripting retain the old path.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xadd(
                key,
                fields,
                maxlen=maxlen,
                approximate=False,
            )
            pipe.expire(key, self._stream_ttl_seconds)
            await pipe.execute()

    async def _publish_retained(self, key: str, fields: dict[str, str], *, maxlen: int, terminal: bool = False) -> None:
        # The marker is internal to Redis and is never included in StreamEvent.
        # A fresh marker per publish distinguishes identical consecutive chunks.
        fields = {**fields, _PUBLISH_ID_FIELD: uuid4().hex}
        publish_id = fields[_PUBLISH_ID_FIELD]
        for attempt in range(1, _MAX_PUBLISH_ATTEMPTS + 1):
            try:
                await self._timed_write("single_write", lambda: self._xadd_retained(key, fields, maxlen=maxlen, terminal=terminal))
                return
            except (RedisConnectionError, RedisTimeoutError):
                # Do not replay an ambiguous write until a read on a fresh
                # connection proves its marker is absent. The run writer
                # serializes its publishes, so the event remains in the exact
                # retained window while this call waits for confirmation.
                for probe in range(1, _MAX_PUBLISH_PROBES + 1):
                    try:
                        entries = await self._redis.xrevrange(key, count=maxlen)
                        break
                    except (RedisConnectionError, RedisTimeoutError):
                        if probe == _MAX_PUBLISH_PROBES:
                            raise
                        await asyncio.sleep(_PUBLISH_RETRY_DELAY_SECONDS * probe)

                if any(self._normalise_fields(entry_fields).get(_PUBLISH_ID_FIELD) == publish_id for _, entry_fields in entries):
                    logger.warning("Redis stream bridge publish response lost; retained event confirmed after attempt %d", attempt)
                    return
                if attempt == _MAX_PUBLISH_ATTEMPTS:
                    raise
                logger.warning("Redis stream bridge publish was not committed; retrying attempt %d/%d", attempt + 1, _MAX_PUBLISH_ATTEMPTS)
                await asyncio.sleep(_PUBLISH_RETRY_DELAY_SECONDS * attempt)

    async def _publish_retained_batch(self, key: str, fields: Sequence[dict[str, str]]) -> None:
        """Append an ordered batch once, including after an ambiguous reply."""
        assert self._xadd_batch_script is not None
        batch_id = uuid4().hex
        for probe in range(1, _MAX_PUBLISH_PROBES + 1):
            try:
                latest = await self._timed_write("batch_predecessor", lambda: self._redis.xrevrange(key, count=1))
                break
            except (RedisConnectionError, RedisTimeoutError):
                if probe == _MAX_PUBLISH_PROBES:
                    raise
                await asyncio.sleep(_PUBLISH_RETRY_DELAY_SECONDS * probe)
        predecessor = self._decode(latest[0][0]) if latest else "0-0"
        args: list[str | int] = [self._maxsize, (self._stream_ttl_seconds or 0) + self._ttl_refresh_slack_seconds, batch_id, len(fields), predecessor]
        for entry in fields:
            args.extend((entry["event"], entry["data"], uuid4().hex))
        for attempt in range(1, _MAX_PUBLISH_ATTEMPTS + 1):
            try:
                result = await self._timed_write("batch_write", lambda: self._xadd_batch_script(keys=[key], args=args))
                outcome = self._decode(result)
                if outcome not in {"committed", "already_committed"}:
                    raise RedisError("Unexpected Redis stream bridge batch result")
                if outcome == "already_committed":
                    logger.warning("Redis stream bridge batch response lost; server confirmed prior commit after attempt %d", attempt)
                return
            except (RedisConnectionError, RedisTimeoutError):
                if attempt == _MAX_PUBLISH_ATTEMPTS:
                    raise
                logger.warning("Redis stream bridge batch reply ambiguous; safely retrying attempt %d/%d", attempt + 1, _MAX_PUBLISH_ATTEMPTS)
                await asyncio.sleep(_PUBLISH_RETRY_DELAY_SECONDS * attempt)

    @staticmethod
    def _decode(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")
        return str(value)

    @classmethod
    def _normalise_fields(cls, fields: Mapping[Any, Any]) -> dict[str, str]:
        return {cls._decode(key): cls._decode(value) for key, value in fields.items()}

    @staticmethod
    def _encode_data(data: Any) -> str:
        return json.dumps(data, default=str, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _decode_data(raw: str | None) -> Any:
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Redis stream bridge received non-JSON event data")
            return raw

    def _entry_from_redis(self, event_id: str, fields: Mapping[Any, Any]) -> StreamEvent:
        payload = self._normalise_fields(fields)
        kind = payload.get("kind", _KIND_EVENT)
        if kind == _KIND_END:
            return END_SENTINEL
        return StreamEvent(
            id=event_id,
            event=payload.get("event", "message"),
            data=self._decode_data(payload.get("data")),
        )

    async def publish(self, run_id: str, event: str, data: Any) -> None:
        key = self._stream_key(run_id)
        await self._publish_retained(
            key,
            {
                "kind": _KIND_EVENT,
                "event": event,
                "data": self._encode_data(data),
            },
            maxlen=self._maxsize,
        )

    async def publish_many(self, run_id: str, events: Sequence[tuple[str, Any]]) -> None:
        if self._xadd_batch_script is None:
            await super().publish_many(run_id, events)
            return
        for offset in range(0, len(events), self.max_publish_batch_size):
            group = events[offset : offset + self.max_publish_batch_size]
            if len(group) == 1:
                event, data = group[0]
                await self.publish(run_id, event, data)
                continue
            fields = [{"kind": _KIND_EVENT, "event": event, "data": self._encode_data(data)} for event, data in group]
            await self._publish_retained_batch(self._stream_key(run_id), fields)

    async def publish_end(self, run_id: str) -> None:
        # Keep the configured number of data events plus the internal end marker.
        key = self._stream_key(run_id)
        await self._publish_retained(
            key,
            {"kind": _KIND_END},
            maxlen=self._maxsize + 1,
            terminal=True,
        )

    async def stream_exists(self, run_id: str) -> bool:
        """Return whether Redis still has retained stream data for *run_id*."""
        return bool(await self._redis.exists(self._stream_key(run_id)))

    async def _resolve_start_stream_id(self, key: str, last_event_id: str | None) -> str:
        if last_event_id is None:
            return "0-0"
        if _REDIS_STREAM_ID_RE.fullmatch(last_event_id):
            return last_event_id
        entries = await self._redis.xrevrange(key, count=1)
        if not entries:
            return "0-0"
        event_id, fields = entries[0]
        payload = self._normalise_fields(fields)
        if payload.get("kind") == _KIND_END:
            return "0-0"
        return self._decode(event_id)

    async def _check_replay_window(self, key: str, last_event_id: str | None) -> None:
        # A single check at reconnect adds no Redis work to the live token
        # path. A stale cursor cannot be replayed safely from a trimmed stream.
        if last_event_id is None or last_event_id in {"0", "0-0"} or not _REDIS_STREAM_ID_RE.fullmatch(last_event_id):
            return
        earliest = await self._redis.xrange(key, count=1)
        if not earliest:
            return
        first_id = self._decode(earliest[0][0])
        cursor = tuple(int(part) for part in (last_event_id.split("-", 1) if "-" in last_event_id else (last_event_id, "0")))
        first = tuple(int(part) for part in first_id.split("-", 1))
        if cursor < first:
            raise StreamReplayGapError("Requested stream cursor is older than the retained history")

    async def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        key = self._stream_key(run_id)
        stream_id = await self._resolve_start_stream_id(key, last_event_id)
        await self._check_replay_window(key, last_event_id)
        block_ms = max(1, int(heartbeat_interval * 1000)) if heartbeat_interval > 0 else 1
        consecutive_errors = 0

        while True:
            try:
                response = await self._redis.xread({key: stream_id}, count=_XREAD_COUNT, block=block_ms)
            except ResponseError:
                # Last-Event-ID is client-controlled and validated before XREAD.
                # If Redis still rejects the id, fail instead of resetting to
                # 0-0, which would replay the whole retained buffer on reconnect.
                logger.warning(
                    "Redis rejected stream id %r for stream bridge subscription",
                    stream_id,
                    exc_info=True,
                )
                raise
            except RedisError:
                consecutive_errors += 1
                if consecutive_errors > _MAX_SUBSCRIBE_RETRIES:
                    raise
                delay = min(2**consecutive_errors, heartbeat_interval)
                logger.warning(
                    "Transient Redis error in stream bridge subscriber (retry %d/%d); backing off %.1fs",
                    consecutive_errors,
                    _MAX_SUBSCRIBE_RETRIES,
                    delay,
                    exc_info=True,
                )
                await asyncio.sleep(delay)
                continue
            else:
                consecutive_errors = 0

            if not response:
                yield HEARTBEAT_SENTINEL
                continue

            for _stream_name, entries in response:
                for event_id, fields in entries:
                    event_id = self._decode(event_id)
                    stream_id = event_id
                    entry = self._entry_from_redis(event_id, fields)
                    if entry is END_SENTINEL:
                        yield END_SENTINEL
                        return
                    yield entry

    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        await self._redis.delete(self._stream_key(run_id))

    async def close(self) -> None:
        if not self._owns_client:
            return
        close = getattr(self._redis, "aclose", None) or getattr(self._redis, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result
