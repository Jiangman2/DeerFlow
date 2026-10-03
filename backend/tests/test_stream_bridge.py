"""Tests for StreamBridge implementations."""

import asyncio
import logging
import os
import re
import uuid
from collections import defaultdict

import anyio
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import ResponseError

from deerflow.config.stream_bridge_config import StreamBridgeConfig, set_stream_bridge_config
from deerflow.runtime import END_SENTINEL, HEARTBEAT_SENTINEL, MemoryStreamBridge, make_stream_bridge
from deerflow.runtime.stream_bridge.redis import RedisStreamBridge, StreamReplayGapError


def _stream_id_gt(left: str, right: str) -> bool:
    left_ms, left_seq = left.split("-", 1)
    right_ms, right_seq = right.split("-", 1)
    return (int(left_ms), int(left_seq)) > (int(right_ms), int(right_seq))


class _FakeRedis:
    def __init__(self) -> None:
        self.streams = defaultdict(list)
        self.conditions = defaultdict(asyncio.Condition)
        self.counters = defaultdict(int)
        self.deleted = []
        self.expirations = []
        self.closed = False

    async def xadd(self, name, fields, maxlen=None, approximate=True):
        self.counters[name] += 1
        event_id = f"{self.counters[name]}-0"
        async with self.conditions[name]:
            self.streams[name].append((event_id, dict(fields)))
            if maxlen is not None and len(self.streams[name]) > maxlen:
                del self.streams[name][: len(self.streams[name]) - maxlen]
            self.conditions[name].notify_all()
        return event_id

    async def xread(self, streams, count=None, block=None):
        [(name, last_id)] = list(streams.items())
        timeout = None if block is None else block / 1000
        while True:
            async with self.conditions[name]:
                entries = [(event_id, fields) for event_id, fields in self.streams.get(name, []) if _stream_id_gt(event_id, last_id)]
                if entries:
                    return [(name, entries[:count] if count is not None else entries)]
                if timeout is None:
                    return []
                try:
                    await asyncio.wait_for(self.conditions[name].wait(), timeout=timeout)
                except TimeoutError:
                    return []

    async def xrevrange(self, name, max="+", min="-", count=None):
        entries = list(reversed(self.streams.get(name, [])))
        return entries[:count] if count is not None else entries

    async def xrange(self, name, min="-", max="+", count=None):
        entries = self.streams.get(name, [])
        return entries[:count] if count is not None else entries

    async def delete(self, name):
        self.deleted.append(name)
        self.streams.pop(name, None)
        return 1

    async def exists(self, name):
        return 1 if name in self.streams else 0

    async def expire(self, name, seconds):
        self.expirations.append((name, seconds))
        return True

    def pipeline(self, *, transaction=True):
        return _FakeRedisPipeline(self)

    async def aclose(self):
        self.closed = True


class _FakeRedisPipeline:
    def __init__(self, redis: _FakeRedis) -> None:
        self.redis = redis
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def xadd(self, name, fields, maxlen=None, approximate=True):
        self.ops.append(("xadd", name, fields, maxlen, approximate))
        return self

    def expire(self, name, seconds):
        self.ops.append(("expire", name, seconds))
        return self

    async def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "xadd":
                _, name, fields, maxlen, approximate = op
                results.append(await self.redis.xadd(name, fields, maxlen=maxlen, approximate=approximate))
            elif op[0] == "expire":
                _, name, seconds = op
                results.append(await self.redis.expire(name, seconds))
        return results


class _ResettingRedis(_FakeRedis):
    def __init__(self, *, after_commit: bool, probe_failures: int = 0) -> None:
        super().__init__()
        self.after_commit = after_commit
        self.probe_failures = probe_failures
        self.execute_count = 0

    def pipeline(self, *, transaction=True):
        return _ResettingRedisPipeline(self)

    async def xrevrange(self, name, max="+", min="-", count=None):
        if self.probe_failures > 0:
            self.probe_failures -= 1
            raise RedisConnectionError("read-back unavailable")
        return await super().xrevrange(name, max=max, min=min, count=count)


class _ResettingRedisPipeline(_FakeRedisPipeline):
    async def execute(self):
        self.redis.execute_count += 1
        if self.redis.execute_count == 1:
            if self.redis.after_commit:
                await super().execute()
            raise RedisConnectionError("connection reset while reading transaction response")
        return await super().execute()


class _ScriptRedis(_FakeRedis):
    def __init__(self) -> None:
        super().__init__()
        self.script_calls = 0
        self.remaining_ttl = {}

    async def expire(self, name, seconds):
        self.remaining_ttl[name] = seconds
        return await super().expire(name, seconds)

    def register_script(self, script):
        assert "redis.call('XADD'" in script

        async def execute(*, keys, args):
            self.script_calls += 1
            [name] = keys
            maxlen, ttl, slack, terminal = map(int, args[:4])
            fields = dict(zip(args[4::2], args[5::2], strict=True))
            event_id = await _FakeRedis.xadd(self, name, fields, maxlen=maxlen, approximate=False)
            if terminal:
                await self.expire(name, ttl)
            elif (remaining := self.remaining_ttl.get(name, -1)) <= ttl or remaining > ttl + slack:
                await self.expire(name, ttl + slack)
            return event_id

        return execute


class _ResettingScriptRedis(_ScriptRedis):
    def __init__(self, *, after_commit: bool) -> None:
        super().__init__()
        self.after_commit = after_commit
        self.attempts = 0

    def register_script(self, script):
        execute = super().register_script(script)

        async def resetting_execute(*, keys, args):
            self.attempts += 1
            if self.attempts == 1:
                if self.after_commit:
                    await execute(keys=keys, args=args)
                raise RedisConnectionError("connection reset while reading script response")
            return await execute(keys=keys, args=args)

        return resetting_execute


class _BatchScriptRedis(_ScriptRedis):
    def __init__(self, *, reset_on_first: bool = False, commit_before_reset: bool = False, delayed_commit: bool = False, partial: bool = False) -> None:
        super().__init__()
        self.reset_on_first = reset_on_first
        self.commit_before_reset = commit_before_reset
        self.delayed_commit = delayed_commit
        self.partial = partial
        self.batch_attempts = 0
        self.pending_commit = None
        self.active = 0
        self.peak = 0

    def register_script(self, script):
        if "batch_index" not in script:
            return super().register_script(script)

        async def commit(*, keys, args, only_first=False):
            [name] = keys
            maxlen, ttl, batch_id, count, expected_predecessor = args[:5]
            latest_id, latest = self.streams[name][-1] if self.streams[name] else ("0-0", {})
            if latest.get("batch_id") == batch_id:
                if int(latest["batch_index"]) == count:
                    return "already_committed"
                raise ResponseError("partial stream bridge batch; refusing replay")
            if latest_id != expected_predecessor:
                raise ResponseError("stream advanced before batch commit; refusing stale write")
            for index in range(1, (1 if only_first else count) + 1):
                offset = 5 + (index - 1) * 3
                event, data, publish_id = args[offset : offset + 3]
                await self.xadd(
                    name,
                    {"kind": "event", "event": event, "data": data, "publish_id": publish_id, "batch_id": batch_id, "batch_index": str(index)},
                    maxlen=maxlen,
                    approximate=False,
                )
            if ttl:
                await self.expire(name, ttl)
            return "committed"

        async def execute(*, keys, args):
            self.batch_attempts += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                if self.batch_attempts == 1 and self.reset_on_first:
                    if self.commit_before_reset or self.partial:
                        await commit(keys=keys, args=args, only_first=self.partial)
                    if self.delayed_commit:
                        self.pending_commit = lambda: commit(keys=keys, args=args)
                    raise RedisConnectionError("batch reply lost")
                await asyncio.sleep(0.01)
                return await commit(keys=keys, args=args)
            finally:
                self.active -= 1

        return execute


# ---------------------------------------------------------------------------
# Unit tests for MemoryStreamBridge
# ---------------------------------------------------------------------------


@pytest.fixture
def bridge() -> MemoryStreamBridge:
    return MemoryStreamBridge(queue_maxsize=256)


@pytest.mark.anyio
async def test_publish_subscribe(bridge: MemoryStreamBridge):
    """Three events followed by end should be received in order."""
    run_id = "run-1"

    await bridge.publish(run_id, "metadata", {"run_id": run_id})
    await bridge.publish(run_id, "values", {"messages": []})
    await bridge.publish(run_id, "updates", {"step": 1})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(received) == 4
    assert received[0].event == "metadata"
    assert received[1].event == "values"
    assert received[2].event == "updates"
    assert received[3] is END_SENTINEL


@pytest.mark.anyio
async def test_heartbeat(bridge: MemoryStreamBridge):
    """When no events arrive within the heartbeat interval, yield a heartbeat."""
    run_id = "run-heartbeat"
    bridge._get_or_create_stream(run_id)  # ensure stream exists

    received = []

    async def consumer():
        async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
            received.append(entry)
            if entry is HEARTBEAT_SENTINEL:
                break

    await asyncio.wait_for(consumer(), timeout=2.0)
    assert len(received) == 1
    assert received[0] is HEARTBEAT_SENTINEL


@pytest.mark.anyio
async def test_cleanup(bridge: MemoryStreamBridge):
    """After cleanup, the run's stream/event log is removed."""
    run_id = "run-cleanup"
    await bridge.publish(run_id, "test", {})
    assert run_id in bridge._streams

    await bridge.cleanup(run_id)
    assert run_id not in bridge._streams
    assert run_id not in bridge._counters


@pytest.mark.anyio
async def test_stream_exists_reports_cleanup(bridge: MemoryStreamBridge):
    """Callers can detect when the in-process event log has been cleaned up.

    Before cleanup a completed run's retained history still exists; after
    cleanup ``stream_exists`` reports False so a reconnecting subscriber does
    not hang waiting on a stream whose data is already gone.
    """
    run_id = "run-post-cleanup"
    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish_end(run_id)

    assert await bridge.stream_exists(run_id) is True
    await bridge.cleanup(run_id)
    assert await bridge.stream_exists(run_id) is False


@pytest.mark.anyio
async def test_history_is_bounded():
    """Retained history should be bounded by queue_maxsize."""
    bridge = MemoryStreamBridge(queue_maxsize=1)
    run_id = "run-bp"

    await bridge.publish(run_id, "first", {})
    await bridge.publish(run_id, "second", {})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(received) == 2
    assert received[0].event == "second"
    assert received[1] is END_SENTINEL


@pytest.mark.anyio
async def test_multiple_runs(bridge: MemoryStreamBridge):
    """Two different run_ids should not interfere with each other."""
    await bridge.publish("run-a", "event-a", {"a": 1})
    await bridge.publish("run-b", "event-b", {"b": 2})
    await bridge.publish_end("run-a")
    await bridge.publish_end("run-b")

    events_a = []
    async for entry in bridge.subscribe("run-a", heartbeat_interval=1.0):
        events_a.append(entry)
        if entry is END_SENTINEL:
            break

    events_b = []
    async for entry in bridge.subscribe("run-b", heartbeat_interval=1.0):
        events_b.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(events_a) == 2
    assert events_a[0].event == "event-a"
    assert events_a[0].data == {"a": 1}

    assert len(events_b) == 2
    assert events_b[0].event == "event-b"
    assert events_b[0].data == {"b": 2}


@pytest.mark.anyio
async def test_event_id_format(bridge: MemoryStreamBridge):
    """Event IDs should use timestamp-sequence format."""
    run_id = "run-id-format"
    await bridge.publish(run_id, "test", {"key": "value"})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    event = received[0]
    assert re.match(r"^\d+-\d+$", event.id), f"Expected timestamp-seq format, got {event.id}"


@pytest.mark.anyio
async def test_subscribe_replays_after_last_event_id(bridge: MemoryStreamBridge):
    """Reconnect should replay buffered events after the provided Last-Event-ID."""
    run_id = "run-replay"
    await bridge.publish(run_id, "metadata", {"run_id": run_id})
    await bridge.publish(run_id, "values", {"step": 1})
    await bridge.publish(run_id, "updates", {"step": 2})
    await bridge.publish_end(run_id)

    first_pass = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=1.0):
        first_pass.append(entry)
        if entry is END_SENTINEL:
            break

    received = []
    async for entry in bridge.subscribe(
        run_id,
        last_event_id=first_pass[0].id,
        heartbeat_interval=1.0,
    ):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["values", "updates"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_slow_subscriber_does_not_skip_after_buffer_trim():
    """A slow subscriber should continue from the correct absolute offset."""
    bridge = MemoryStreamBridge(queue_maxsize=2)
    run_id = "run-slow-subscriber"
    await bridge.publish(run_id, "e1", {"step": 1})
    await bridge.publish(run_id, "e2", {"step": 2})

    stream = bridge._streams[run_id]
    e1_id = stream.events[0].id
    assert stream.start_offset == 0

    await bridge.publish(run_id, "e3", {"step": 3})  # trims e1
    assert stream.start_offset == 1
    assert [entry.event for entry in stream.events] == ["e2", "e3"]

    resumed_after_e1 = []
    async for entry in bridge.subscribe(
        run_id,
        last_event_id=e1_id,
        heartbeat_interval=1.0,
    ):
        resumed_after_e1.append(entry)
        if len(resumed_after_e1) == 2:
            break

    assert [entry.event for entry in resumed_after_e1] == ["e2", "e3"]
    e2_id = resumed_after_e1[0].id

    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(
        run_id,
        last_event_id=e2_id,
        heartbeat_interval=1.0,
    ):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["e3"]
    assert received[-1] is END_SENTINEL


# ---------------------------------------------------------------------------
# Stream termination tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_end_terminates_even_when_history_is_full():
    """publish_end() should terminate subscribers without mutating retained history."""
    bridge = MemoryStreamBridge(queue_maxsize=2)
    run_id = "run-end-history-full"

    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish(run_id, "event-2", {"n": 2})
    stream = bridge._streams[run_id]
    assert [entry.event for entry in stream.events] == ["event-1", "event-2"]

    await bridge.publish_end(run_id)
    assert [entry.event for entry in stream.events] == ["event-1", "event-2"]

    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in events[:-1]] == ["event-1", "event-2"]
    assert events[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_publish_end_without_history_yields_end_immediately():
    """Subscribers should still receive END when a run completes without events."""
    bridge = MemoryStreamBridge(queue_maxsize=2)
    run_id = "run-end-empty"
    await bridge.publish_end(run_id)

    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    assert len(events) == 1
    assert events[0] is END_SENTINEL


@pytest.mark.anyio
async def test_publish_end_preserves_history_when_space_available():
    """When history has spare capacity, publish_end should preserve prior events."""
    bridge = MemoryStreamBridge(queue_maxsize=10)
    run_id = "run-no-evict"

    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish(run_id, "event-2", {"n": 2})
    await bridge.publish_end(run_id)

    events = []
    async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
        events.append(entry)
        if entry is END_SENTINEL:
            break

    # All events plus END should be present
    assert len(events) == 3
    assert events[0].event == "event-1"
    assert events[1].event == "event-2"
    assert events[2] is END_SENTINEL


@pytest.mark.anyio
async def test_concurrent_tasks_end_sentinel():
    """Multiple concurrent producer/consumer pairs should all terminate properly.

    Simulates the production scenario where multiple runs share a single
    bridge instance — each must receive its own END sentinel.
    """
    bridge = MemoryStreamBridge(queue_maxsize=4)
    num_runs = 4

    async def producer(run_id: str):
        for i in range(10):  # More events than queue capacity
            await bridge.publish(run_id, f"event-{i}", {"i": i})
        await bridge.publish_end(run_id)

    async def consumer(run_id: str) -> list:
        events = []
        async for entry in bridge.subscribe(run_id, heartbeat_interval=0.1):
            events.append(entry)
            if entry is END_SENTINEL:
                return events
        return events  # pragma: no cover

    run_ids = [f"concurrent-{i}" for i in range(num_runs)]
    results: dict[str, list] = {}

    async def consume_into(run_id: str) -> None:
        results[run_id] = await consumer(run_id)

    with anyio.fail_after(10):
        async with anyio.create_task_group() as task_group:
            for run_id in run_ids:
                task_group.start_soon(consume_into, run_id)
            await anyio.sleep(0)
            for run_id in run_ids:
                task_group.start_soon(producer, run_id)

    for run_id in run_ids:
        events = results[run_id]
        assert events[-1] is END_SENTINEL, f"Run {run_id} did not receive END sentinel"


# ---------------------------------------------------------------------------
# Unit tests for RedisStreamBridge
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_bridge() -> RedisStreamBridge:
    return RedisStreamBridge(redis_url="redis://fake", queue_maxsize=2, client=_FakeRedis())


def test_redis_bridge_without_script_disables_message_buffering(redis_bridge: RedisStreamBridge):
    assert redis_bridge.supports_batched_publish is False


def test_redis_bridge_default_write_limit_is_32(redis_bridge: RedisStreamBridge):
    assert redis_bridge._write_slots._value == 32


@pytest.mark.anyio
async def test_redis_publish_subscribe(redis_bridge: RedisStreamBridge):
    """Redis bridge should deliver events in order and terminate on end."""
    run_id = "redis-run-1"

    await redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
    await redis_bridge.publish(run_id, "values", {"messages": []})
    await redis_bridge.publish_end(run_id)

    received = []
    async for entry in redis_bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["metadata", "values"]
    assert received[0].data == {"run_id": run_id}
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_publish_many_preserves_each_event_id_and_replay():
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=_BatchScriptRedis())
    assert bridge.supports_batched_publish is True
    run_id = "redis-batch-order"

    await bridge.publish(run_id, "metadata", {"run_id": run_id})
    await bridge.publish_many(run_id, [("messages-tuple", {"delta": part}) for part in "abc"])
    await bridge.publish(run_id, "updates", {"step": 1})
    await bridge.publish_end(run_id)

    received = [entry async for entry in bridge.subscribe(run_id, heartbeat_interval=1)]
    assert [entry.event for entry in received[:-1]] == ["metadata", "messages-tuple", "messages-tuple", "messages-tuple", "updates"]
    assert [entry.data for entry in received[1:4]] == [{"delta": part} for part in "abc"]
    assert len({entry.id for entry in received[:-1]}) == 5
    replayed = [entry async for entry in bridge.subscribe(run_id, last_event_id=received[2].id, heartbeat_interval=1)]
    assert [entry.data for entry in replayed[:-2]] == [{"delta": "c"}]
    assert replayed[-2].event == "updates"
    assert replayed[-1] is END_SENTINEL


@pytest.mark.anyio
@pytest.mark.parametrize("after_commit", [True, False])
async def test_redis_publish_many_recovers_ambiguous_response_without_duplicate(after_commit: bool):
    fake = _BatchScriptRedis(reset_on_first=True, commit_before_reset=after_commit)
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=fake)
    run_id = "redis-batch-reset"

    await bridge.publish_many(run_id, [("messages-tuple", {"delta": part}) for part in "abc"])

    assert fake.batch_attempts == 2
    entries = fake.streams[bridge._stream_key(run_id)]
    assert [entry[1]["event"] for entry in entries] == ["messages-tuple"] * 3
    assert len({entry[1]["publish_id"] for entry in entries}) == 3


@pytest.mark.anyio
async def test_redis_publish_many_late_first_attempt_cannot_duplicate_retry():
    fake = _BatchScriptRedis(reset_on_first=True, delayed_commit=True)
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=fake)
    run_id = "redis-batch-late-commit"

    await bridge.publish_many(run_id, [("messages-tuple", {"delta": part}) for part in "abc"])
    assert fake.pending_commit is not None
    assert await fake.pending_commit() == "already_committed"
    assert len(fake.streams[bridge._stream_key(run_id)]) == 3


@pytest.mark.anyio
async def test_redis_publish_many_late_first_attempt_after_following_event_fails_closed():
    fake = _BatchScriptRedis(reset_on_first=True, delayed_commit=True)
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=fake)
    run_id = "redis-batch-late-after-update"
    await bridge.publish_many(run_id, [("messages-tuple", {"delta": part}) for part in "abc"])
    await bridge.publish(run_id, "updates", {"step": 1})
    assert fake.pending_commit is not None
    with pytest.raises(ResponseError, match="stream advanced"):
        await fake.pending_commit()
    assert [entry[1]["event"] for entry in fake.streams[bridge._stream_key(run_id)]] == ["messages-tuple"] * 3 + ["updates"]


@pytest.mark.anyio
async def test_redis_publish_many_repeated_commit_is_server_side_idempotent():
    fake = _BatchScriptRedis(reset_on_first=True, commit_before_reset=True)
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=fake)
    await bridge.publish_many("redis-batch-unchecked", [("messages-tuple", {"delta": part}) for part in "abc"])
    assert fake.batch_attempts == 2
    assert len(fake.streams[bridge._stream_key("redis-batch-unchecked")]) == 3


@pytest.mark.anyio
async def test_redis_publish_many_fails_closed_on_partial_observation():
    fake = _BatchScriptRedis(reset_on_first=True, partial=True)
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=fake)

    with pytest.raises(ResponseError, match="partial stream bridge batch"):
        await bridge.publish_many("redis-batch-partial", [("messages-tuple", {"delta": part}) for part in "abc"])

    assert len(fake.streams[bridge._stream_key("redis-batch-partial")]) == 1


@pytest.mark.anyio
async def test_redis_publish_many_renews_ttl_once_per_batch():
    fake = _BatchScriptRedis()
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, stream_ttl_seconds=42, client=fake)

    await bridge.publish_many("redis-batch-ttl", [("messages-tuple", {"delta": part}) for part in "abc"])

    assert fake.expirations == [(bridge._stream_key("redis-batch-ttl"), 63)]


@pytest.mark.anyio
async def test_redis_publish_many_bounds_parallel_writes_across_runs():
    fake = _BatchScriptRedis()
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=fake)
    bridge._write_slots = asyncio.Semaphore(2)
    events = [("messages-tuple", {"delta": "a"}), ("messages-tuple", {"delta": "b"})]

    await asyncio.gather(*(bridge.publish_many(f"parallel-{index}", events) for index in range(6)))

    assert fake.peak == 2
    assert all(len(fake.streams[bridge._stream_key(f"parallel-{index}")]) == 2 for index in range(6))


@pytest.mark.anyio
async def test_redis_write_timing_separates_slot_wait_from_client_await(monkeypatch, caplog):
    from deerflow.runtime.stream_bridge import redis as redis_module

    monkeypatch.setattr(redis_module, "_WRITE_TIMING_LOG_EVERY", 1)
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=8, client=_BatchScriptRedis())
    bridge._write_slots = asyncio.Semaphore(0)
    events = [("messages-tuple", {"secret": "must-not-appear"})] * 2

    with caplog.at_level(logging.INFO, logger=redis_module.__name__):
        pending = asyncio.create_task(bridge.publish_many("timed-run", events))
        await asyncio.sleep(0.02)
        bridge._write_slots.release()
        await pending

    records = [record.getMessage() for record in caplog.records if "REDIS_STREAM_WRITE_TIMING" in record.getMessage()]
    assert any("operation=batch_predecessor" in record and re.search(r"slot_wait_total_ms=[1-9]", record) for record in records)
    assert any("operation=batch_write" in record and re.search(r"redis_await_total_ms=[1-9]", record) for record in records)
    assert all("must-not-appear" not in record and "timed-run" not in record for record in records)


@pytest.mark.anyio
async def test_redis_replays_after_last_event_id(redis_bridge: RedisStreamBridge):
    """Redis XREAD should resume after Last-Event-ID."""
    run_id = "redis-run-replay"

    await redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
    await redis_bridge.publish(run_id, "values", {"step": 1})
    await redis_bridge.publish_end(run_id)

    first_pass = []
    async for entry in redis_bridge.subscribe(run_id, heartbeat_interval=1.0):
        first_pass.append(entry)
        if entry is END_SENTINEL:
            break

    received = []
    async for entry in redis_bridge.subscribe(run_id, last_event_id=first_pass[0].id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["values"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_reconnect_reports_trimmed_replay_window():
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=2, client=_FakeRedis())
    run_id = "redis-run-replay-gap"
    await bridge.publish(run_id, "metadata", {})
    stale_id = bridge._redis.streams[bridge._stream_key(run_id)][0][0]
    for index in range(3):
        await bridge.publish(run_id, "messages-tuple", {"index": index})

    with pytest.raises(StreamReplayGapError, match="older than the retained history"):
        async for _ in bridge.subscribe(run_id, last_event_id=stale_id):
            pass


@pytest.mark.anyio
@pytest.mark.parametrize("last_event_id", ["-1", "9" * 5000])
async def test_redis_invalid_last_event_id_tails_live_events(redis_bridge: RedisStreamBridge, last_event_id: str):
    """Malformed reconnect ids should not replay retained Redis events."""
    run_id = "redis-run-invalid-last-event-id"

    await redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
    received = []

    async def publish_later() -> None:
        await anyio.sleep(0.05)
        await redis_bridge.publish(run_id, "values", {"step": 1})
        await redis_bridge.publish_end(run_id)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(publish_later)
            async for entry in redis_bridge.subscribe(run_id, last_event_id=last_event_id, heartbeat_interval=0.01):
                if entry is HEARTBEAT_SENTINEL:
                    continue
                received.append(entry)
                if entry is END_SENTINEL:
                    break

    assert [entry.event for entry in received[:-1]] == ["values"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_invalid_last_event_id_tails_empty_stream(redis_bridge: RedisStreamBridge):
    """Malformed reconnect ids should still wait for the first Redis event."""
    run_id = "redis-run-invalid-empty"
    received = []

    async def publish_later() -> None:
        await anyio.sleep(0.05)
        await redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
        await redis_bridge.publish_end(run_id)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(publish_later)
            async for entry in redis_bridge.subscribe(run_id, last_event_id="-1", heartbeat_interval=0.01):
                if entry is HEARTBEAT_SENTINEL:
                    continue
                received.append(entry)
                if entry is END_SENTINEL:
                    break

    assert [entry.event for entry in received[:-1]] == ["metadata"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_invalid_last_event_id_on_terminal_run_replays_end(redis_bridge: RedisStreamBridge):
    """Malformed reconnect ids on terminal streams should drain END instead of hanging."""
    run_id = "redis-run-invalid-terminal"

    await redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
    await redis_bridge.publish_end(run_id)

    received = []
    async for entry in redis_bridge.subscribe(run_id, last_event_id="-1", heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["metadata"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_heartbeat(redis_bridge: RedisStreamBridge):
    """Redis bridge should yield heartbeats when XREAD times out on an existing stream."""
    run_id = "redis-run-heartbeat"
    await redis_bridge.publish(run_id, "init", {})

    received = []
    async for entry in redis_bridge.subscribe(run_id, heartbeat_interval=0.01):
        received.append(entry)
        if entry is HEARTBEAT_SENTINEL:
            break

    assert len(received) == 2
    assert received[0].event == "init"
    assert received[1] is HEARTBEAT_SENTINEL


@pytest.mark.anyio
async def test_redis_publish_end_preserves_data_history_capacity(redis_bridge: RedisStreamBridge):
    """The internal end marker should not evict the configured data history."""
    run_id = "redis-run-end-capacity"

    await redis_bridge.publish(run_id, "event-1", {"n": 1})
    await redis_bridge.publish(run_id, "event-2", {"n": 2})
    await redis_bridge.publish_end(run_id)

    received = []
    async for entry in redis_bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["event-1", "event-2"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_cleanup_deletes_stream(redis_bridge: RedisStreamBridge):
    fake = redis_bridge._redis
    run_id = "redis-run-cleanup"

    await redis_bridge.publish(run_id, "event", {})
    await redis_bridge.cleanup(run_id)

    assert fake.deleted == ["deerflow:stream_bridge:redis-run-cleanup"]


@pytest.mark.anyio
async def test_redis_publish_refreshes_stream_ttl():
    """Pipeline-only test doubles retain rolling TTL on every publication."""
    fake = _FakeRedis()
    bridge = RedisStreamBridge(
        redis_url="redis://fake",
        queue_maxsize=2,
        stream_ttl_seconds=42,
        client=fake,
    )
    run_id = "redis-run-ttl"
    key = "deerflow:stream_bridge:redis-run-ttl"

    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish(run_id, "event-2", {"n": 2})
    await bridge.publish_end(run_id)

    assert fake.expirations == [(key, 42), (key, 42), (key, 42)]


@pytest.mark.anyio
async def test_redis_script_publish_refreshes_ttl_only_when_needed():
    fake = _ScriptRedis()
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=2, stream_ttl_seconds=42, client=fake)
    run_id = "redis-run-script-ttl"
    key = bridge._stream_key(run_id)

    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish(run_id, "event-2", {"n": 2})
    assert fake.expirations == [(key, 63)]

    fake.remaining_ttl[key] = 42
    await bridge.publish(run_id, "event-3", {"n": 3})
    await bridge.publish_end(run_id)

    assert fake.script_calls == 4
    assert fake.expirations == [(key, 63), (key, 63), (key, 42)]
    assert [entry[1]["event"] for entry in fake.streams[key][:-1]] == ["event-2", "event-3"]
    assert fake.streams[key][-1][1]["kind"] == "end"


@pytest.mark.anyio
async def test_redis_script_publish_restores_expiry_after_key_recreation():
    fake = _ScriptRedis()
    bridge = RedisStreamBridge(redis_url="redis://fake", stream_ttl_seconds=42, client=fake)
    run_id = "redis-run-script-recreated"
    key = bridge._stream_key(run_id)

    await bridge.publish(run_id, "event-1", {})
    fake.streams.pop(key)
    fake.remaining_ttl.pop(key)
    await bridge.publish(run_id, "event-2", {})

    assert fake.expirations == [(key, 63), (key, 63)]
    assert len(fake.streams[key]) == 1


@pytest.mark.anyio
async def test_redis_script_publish_bounds_preexisting_long_expiry():
    fake = _ScriptRedis()
    bridge = RedisStreamBridge(redis_url="redis://fake", stream_ttl_seconds=42, client=fake)
    run_id = "redis-run-script-long-expiry"
    key = bridge._stream_key(run_id)
    fake.remaining_ttl[key] = 600

    await bridge.publish(run_id, "event", {})

    assert fake.expirations == [(key, 63)]


@pytest.mark.anyio
@pytest.mark.parametrize("after_commit,expected_attempts", [(True, 1), (False, 2)])
async def test_redis_script_publish_recovers_ambiguous_reset_without_duplicate(after_commit: bool, expected_attempts: int):
    fake = _ResettingScriptRedis(after_commit=after_commit)
    bridge = RedisStreamBridge(redis_url="redis://fake", client=fake)
    run_id = "redis-run-script-reset"
    key = bridge._stream_key(run_id)

    await bridge.publish(run_id, "messages", {"chunk": "a"})

    assert fake.attempts == expected_attempts
    assert len(fake.streams[key]) == 1
    assert fake.streams[key][0][1]["event"] == "messages"


@pytest.mark.anyio
async def test_redis_publish_recovers_reset_after_commit_without_duplicate():
    fake = _ResettingRedis(after_commit=True)
    bridge = RedisStreamBridge(redis_url="redis://fake", client=fake)
    run_id = "redis-run-reset-after-commit"
    key = bridge._stream_key(run_id)

    await bridge.publish(run_id, "messages", {"chunk": "a"})

    assert fake.execute_count == 1
    assert len(fake.streams[key]) == 1
    assert fake.streams[key][0][1]["event"] == "messages"


@pytest.mark.anyio
async def test_redis_publish_retries_reset_before_commit():
    fake = _ResettingRedis(after_commit=False)
    bridge = RedisStreamBridge(redis_url="redis://fake", client=fake)
    run_id = "redis-run-reset-before-commit"
    key = bridge._stream_key(run_id)

    await bridge.publish(run_id, "messages", {"chunk": "a"})

    assert fake.execute_count == 2
    assert len(fake.streams[key]) == 1
    assert fake.streams[key][0][1]["event"] == "messages"


@pytest.mark.anyio
async def test_redis_publish_does_not_retry_when_commit_cannot_be_checked():
    fake = _ResettingRedis(after_commit=True, probe_failures=5)
    bridge = RedisStreamBridge(redis_url="redis://fake", client=fake)
    run_id = "redis-run-unchecked-commit"
    key = bridge._stream_key(run_id)

    with pytest.raises(RedisConnectionError, match="read-back unavailable"):
        await bridge.publish(run_id, "messages", {"chunk": "a"})

    assert fake.execute_count == 1
    assert len(fake.streams[key]) == 1


@pytest.mark.anyio
async def test_redis_publish_waits_for_readback_before_retrying_ambiguous_commit():
    fake = _ResettingRedis(after_commit=True, probe_failures=2)
    bridge = RedisStreamBridge(redis_url="redis://fake", client=fake)
    run_id = "redis-run-delayed-readback"
    key = bridge._stream_key(run_id)

    await bridge.publish(run_id, "messages", {"chunk": "a"})

    assert fake.execute_count == 1
    assert len(fake.streams[key]) == 1


@pytest.mark.anyio
async def test_redis_publish_end_recovers_reset_after_commit_without_duplicate():
    fake = _ResettingRedis(after_commit=True)
    bridge = RedisStreamBridge(redis_url="redis://fake", client=fake)
    run_id = "redis-run-end-reset-after-commit"
    key = bridge._stream_key(run_id)

    await bridge.publish_end(run_id)

    assert fake.execute_count == 1
    assert len(fake.streams[key]) == 1
    assert fake.streams[key][0][1]["kind"] == "end"


@pytest.mark.anyio
async def test_redis_stream_ttl_can_be_disabled():
    """A zero TTL disables the Redis leak safety net for installations that need it."""
    fake = _FakeRedis()
    bridge = RedisStreamBridge(
        redis_url="redis://fake",
        queue_maxsize=2,
        stream_ttl_seconds=0,
        client=fake,
    )

    await bridge.publish("redis-run-no-ttl", "event", {})
    await bridge.publish_end("redis-run-no-ttl")

    assert fake.expirations == []


@pytest.mark.anyio
async def test_redis_subscribe_waits_for_first_publish(redis_bridge: RedisStreamBridge):
    """A subscriber that starts before the first XADD must not receive END."""
    run_id = "redis-run-first-publish"
    received = []

    async def publish_later() -> None:
        await anyio.sleep(0.05)
        await redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
        await redis_bridge.publish_end(run_id)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(publish_later)
            async for entry in redis_bridge.subscribe(run_id, heartbeat_interval=0.01):
                if entry is HEARTBEAT_SENTINEL:
                    continue
                received.append(entry)
                if entry is END_SENTINEL:
                    break

    assert [entry.event for entry in received[:-1]] == ["metadata"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_stream_exists_reports_cleanup(redis_bridge: RedisStreamBridge):
    """Callers can detect when retained Redis stream data has been cleaned up."""
    run_id = "redis-run-post-cleanup"
    await redis_bridge.publish(run_id, "event-1", {"n": 1})
    await redis_bridge.publish_end(run_id)

    assert await redis_bridge.stream_exists(run_id) is True
    await redis_bridge.cleanup(run_id)
    assert await redis_bridge.stream_exists(run_id) is False


@pytest.mark.anyio
async def test_redis_transient_error_retries():
    """Transient RedisError during XREAD should be retried, not propagated."""
    from redis.exceptions import RedisError

    fake = _FakeRedis()
    call_count = 0
    original_xread = fake.xread

    async def flaky_xread(streams, count=None, block=None):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RedisError("Transient connection error")
        return await original_xread(streams, count=count, block=block)

    fake.xread = flaky_xread
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=2, client=fake)

    run_id = "redis-run-retry"
    await bridge.publish(run_id, "event-1", {"n": 1})
    await bridge.publish_end(run_id)

    received = []
    with anyio.fail_after(5):
        async for entry in bridge.subscribe(run_id, heartbeat_interval=0.01):
            received.append(entry)
            if entry is END_SENTINEL:
                break

    assert call_count > 2
    assert [e.event for e in received[:-1]] == ["event-1"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_redis_transient_error_gives_up_after_max_retries():
    """After exceeding max consecutive errors, RedisError should propagate."""
    from redis.exceptions import RedisError

    fake = _FakeRedis()

    async def always_fail_xread(streams, count=None, block=None):
        raise RedisError("Persistent connection error")

    fake.xread = always_fail_xread
    bridge = RedisStreamBridge(redis_url="redis://fake", queue_maxsize=2, client=fake)

    with pytest.raises(RedisError, match="Persistent connection error"):
        async for _ in bridge.subscribe("redis-run-fail", heartbeat_interval=0.01):
            pass


# ---------------------------------------------------------------------------
# Factory tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_make_stream_bridge_defaults():
    """make_stream_bridge() with no config yields a MemoryStreamBridge."""
    async with make_stream_bridge() as bridge:
        assert isinstance(bridge, MemoryStreamBridge)


# ---------------------------------------------------------------------------
# _resolve_start_offset: O(1) seq-indexed resolution (parity with linear scan)
# ---------------------------------------------------------------------------


def _linear_resolve(stream, last_event_id):
    """The original linear-scan resolver, kept as a parity reference."""
    if last_event_id is None:
        return stream.start_offset
    for index, entry in enumerate(stream.events):
        if entry.id == last_event_id:
            return stream.start_offset + index + 1
    return stream.start_offset


@pytest.mark.parametrize(
    "event_id,expected",
    [
        ("1718000000000-0", 0),
        ("1718000000000-42", 42),
        ("garbage", None),  # no separator
        ("1718000000000-x", None),  # non-integer seq
        ("", None),
    ],
)
def test_parse_event_seq(event_id, expected):
    assert MemoryStreamBridge._parse_event_seq(event_id) == expected


@pytest.mark.anyio
async def test_resolve_start_offset_matches_linear_scan():
    """The seq-indexed resolver must return exactly what the linear scan returned,
    across retained, evicted, foreign (same seq / wrong ts), malformed, and None ids."""
    bridge = MemoryStreamBridge(queue_maxsize=4)
    run_id = "run-parity"
    ids = []
    for i in range(10):
        await bridge.publish(run_id, f"e{i}", {"i": i})
        ids.append(bridge._streams[run_id].events[-1].id)  # includes ids that later evict
    stream = bridge._streams[run_id]
    assert stream.start_offset == 6  # 10 published, buffer of 4 retains seq 6..9

    # A foreign id: a retained event's seq but a different timestamp -> must NOT match.
    ts, _, seq_text = stream.events[0].id.rpartition("-")
    foreign_id = f"{int(ts) + 1}-{seq_text}"

    candidates = [None, "garbage", "1718000000000-x", "999999-999999", foreign_id, *ids]
    for eid in candidates:
        assert bridge._resolve_start_offset(stream, eid) == _linear_resolve(stream, eid), eid


@pytest.mark.anyio
async def test_subscribe_with_unknown_last_event_id_replays_from_earliest():
    """A foreign/garbage Last-Event-ID falls back to replaying retained events."""
    bridge = MemoryStreamBridge(queue_maxsize=10)
    run_id = "run-unknown-id"
    await bridge.publish(run_id, "first", {})
    await bridge.publish(run_id, "second", {})
    await bridge.publish_end(run_id)

    received = []
    async for entry in bridge.subscribe(run_id, last_event_id="not-a-real-id", heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [entry.event for entry in received[:-1]] == ["first", "second"]
    assert received[-1] is END_SENTINEL


@pytest.mark.anyio
async def test_make_stream_bridge_uses_docker_redis_env(monkeypatch):
    """Docker can enable Redis bridge without editing config.yaml."""
    set_stream_bridge_config(None)
    monkeypatch.setenv("DEER_FLOW_STREAM_BRIDGE_REDIS_URL", "redis://redis:6379/0")
    try:
        async with make_stream_bridge() as bridge:
            assert isinstance(bridge, RedisStreamBridge)
            assert bridge._redis_url == "redis://redis:6379/0"
    finally:
        set_stream_bridge_config(None)


@pytest.mark.anyio
async def test_make_stream_bridge_passes_redis_options(monkeypatch):
    """Redis options from config should be forwarded to Redis bridge setup."""
    import deerflow.runtime.stream_bridge.redis as redis_module

    captured: dict = {}

    def fake_from_url(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeRedis()

    monkeypatch.setattr(redis_module.Redis, "from_url", staticmethod(fake_from_url))
    set_stream_bridge_config(
        StreamBridgeConfig(
            type="redis",
            redis_url="redis://fake:6379/0",
            max_connections=50,
            stream_ttl_seconds=42,
        )
    )
    try:
        async with make_stream_bridge() as bridge:
            assert isinstance(bridge, RedisStreamBridge)
            assert bridge._stream_ttl_seconds == 42
        assert captured["max_connections"] == 50
        assert captured["decode_responses"] is True
    finally:
        set_stream_bridge_config(None)


# ---------------------------------------------------------------------------
# Integration tests against a real Redis server
# ---------------------------------------------------------------------------
#
# Opt-in and self-skipping: when no Redis is reachable these are skipped so
# `make test` stays green without Redis. Point at a server with
# DEER_FLOW_TEST_REDIS_URL (defaults to redis://localhost:6379/15 — DB 15 to
# avoid clobbering real data) and select with `pytest -m integration`. They
# cover what _FakeRedis only approximates: real XADD/XREAD semantics, live-tail
# reconnects for malformed Last-Event-ID values, the server <ms>-<seq> ID
# format, and MAXLEN trimming.

REDIS_TEST_URL = os.environ.get("DEER_FLOW_TEST_REDIS_URL", "redis://localhost:6379/15")


def _redis_available() -> bool:
    try:
        import redis  # sync client, used only for the connectivity probe
    except ImportError:
        return False
    try:
        client = redis.Redis.from_url(REDIS_TEST_URL, socket_connect_timeout=0.5)
        try:
            client.ping()
        finally:
            client.close()
        return True
    except Exception:
        return False


requires_redis = pytest.mark.skipif(not _redis_available(), reason=f"Redis not reachable at {REDIS_TEST_URL}")


@pytest.fixture
async def real_redis_bridge():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    key_prefix = f"deerflow:test:{uuid.uuid4().hex}"
    bridge = RedisStreamBridge(redis_url=REDIS_TEST_URL, queue_maxsize=2, key_prefix=key_prefix, client=client)
    try:
        yield bridge
    finally:
        async for key in client.scan_iter(f"{key_prefix}:*"):
            await client.delete(key)
        await client.aclose()


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_publish_subscribe_and_id_format(real_redis_bridge):
    run_id = "integ-basic"
    await real_redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
    await real_redis_bridge.publish(run_id, "values", {"step": 1})
    await real_redis_bridge.publish_end(run_id)

    received = []
    async for entry in real_redis_bridge.subscribe(run_id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [e.event for e in received[:-1]] == ["metadata", "values"]
    assert received[0].data == {"run_id": run_id}
    assert received[-1] is END_SENTINEL
    # Real Redis stream IDs use the <ms>-<seq> format the fake only approximates.
    assert re.match(r"^\d+-\d+$", received[0].id), received[0].id


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_replays_after_last_event_id(real_redis_bridge):
    run_id = "integ-replay"
    await real_redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
    await real_redis_bridge.publish(run_id, "values", {"step": 1})
    await real_redis_bridge.publish_end(run_id)

    first_pass = []
    async for entry in real_redis_bridge.subscribe(run_id, heartbeat_interval=1.0):
        first_pass.append(entry)
        if entry is END_SENTINEL:
            break

    received = []
    async for entry in real_redis_bridge.subscribe(run_id, last_event_id=first_pass[0].id, heartbeat_interval=1.0):
        received.append(entry)
        if entry is END_SENTINEL:
            break

    assert [e.event for e in received[:-1]] == ["values"]
    assert received[-1] is END_SENTINEL


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_batch_script_writes_ordered_entries(real_redis_bridge):
    run_id = "integ-idempotent-batch"
    events = [("messages-tuple", {"part": part}) for part in "ab"]
    await real_redis_bridge.publish_many(run_id, events)
    key = real_redis_bridge._stream_key(run_id)
    entries = await real_redis_bridge._redis.xrange(key)
    assert [entry[1]["event"] for entry in entries] == ["messages-tuple", "messages-tuple"]
    assert len({entry[1]["batch_id"] for entry in entries}) == 1
    assert [entry[1]["batch_index"] for entry in entries] == ["1", "2"]
    # A lost reply causes the client to issue the exact same script call.
    retry_key = real_redis_bridge._stream_key("integ-idempotent-retry")
    args = [2, 0, uuid.uuid4().hex, 2, "0-0", "messages-tuple", '{"part":"a"}', uuid.uuid4().hex, "messages-tuple", '{"part":"b"}', uuid.uuid4().hex]
    assert await real_redis_bridge._xadd_batch_script(keys=[retry_key], args=args) == "committed"
    assert await real_redis_bridge._xadd_batch_script(keys=[retry_key], args=args) == "already_committed"
    assert await real_redis_bridge._redis.xlen(retry_key) == 2


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_reconnect_rejects_trimmed_cursor(real_redis_bridge):
    run_id = "integ-trimmed-cursor"
    await real_redis_bridge.publish(run_id, "first", {})
    key = real_redis_bridge._stream_key(run_id)
    stale_id = (await real_redis_bridge._redis.xrange(key, count=1))[0][0]
    for index in range(3):
        await real_redis_bridge.publish(run_id, "messages-tuple", {"index": index})
    with pytest.raises(StreamReplayGapError):
        async for _ in real_redis_bridge.subscribe(run_id, last_event_id=stale_id):
            pass


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_invalid_last_event_id_tails_live_events(real_redis_bridge):
    """A malformed Last-Event-ID should wait at the live tail."""
    run_id = "integ-bad-leid"
    await real_redis_bridge.publish(run_id, "metadata", {"run_id": run_id})
    received = []

    async def publish_later() -> None:
        await anyio.sleep(0.05)
        await real_redis_bridge.publish(run_id, "values", {"step": 1})
        await real_redis_bridge.publish_end(run_id)

    with anyio.fail_after(2):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(publish_later)
            async for entry in real_redis_bridge.subscribe(run_id, last_event_id="not-a-valid-id", heartbeat_interval=0.01):
                if entry is HEARTBEAT_SENTINEL:
                    continue
                received.append(entry)
                if entry is END_SENTINEL:
                    break

    assert [e.event for e in received[:-1]] == ["values"]
    assert received[-1] is END_SENTINEL


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_maxlen_trims_history(real_redis_bridge):
    """queue_maxsize should bound the retained stream via XADD MAXLEN (exact)."""
    run_id = "integ-maxlen"
    # Fixture sets queue_maxsize=2; publish more data events than that.
    for i in range(6):
        await real_redis_bridge.publish(run_id, f"event-{i}", {"i": i})

    key = real_redis_bridge._stream_key(run_id)
    length = await real_redis_bridge._redis.xlen(key)
    assert length == 2


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_script_refreshes_ttl_and_terminal_expiry():
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    key_prefix = f"deerflow:test:{uuid.uuid4().hex}"
    bridge = RedisStreamBridge(
        redis_url=REDIS_TEST_URL,
        key_prefix=key_prefix,
        stream_ttl_seconds=4,
        client=client,
    )
    run_id = "integ-refresh-ttl"
    key = bridge._stream_key(run_id)
    try:
        await bridge.publish(run_id, "first", {})
        assert 4 <= await client.ttl(key) <= 6
        await anyio.sleep(2.1)
        await bridge.publish(run_id, "second", {})
        assert 4 <= await client.ttl(key) <= 6
        await bridge.publish_end(run_id)
        assert 0 < await client.ttl(key) <= 4
    finally:
        async for existing_key in client.scan_iter(f"{key_prefix}:*"):
            await client.delete(existing_key)
        await client.aclose()


@pytest.mark.integration
@requires_redis
@pytest.mark.anyio
async def test_redis_integration_stream_ttl_reclaims_key():
    """Redis should reclaim retained stream data when cleanup never runs."""
    from redis.asyncio import Redis

    client = Redis.from_url(REDIS_TEST_URL, decode_responses=True)
    key_prefix = f"deerflow:test:{uuid.uuid4().hex}"
    bridge = RedisStreamBridge(
        redis_url=REDIS_TEST_URL,
        queue_maxsize=2,
        key_prefix=key_prefix,
        stream_ttl_seconds=1,
        client=client,
    )
    run_id = "integ-ttl"
    key = bridge._stream_key(run_id)
    try:
        await bridge.publish(run_id, "metadata", {"run_id": run_id})
        assert await client.exists(key) == 1
        assert await client.ttl(key) >= 0
        await anyio.sleep(2.0)

        assert await client.exists(key) == 0
    finally:
        async for existing_key in client.scan_iter(f"{key_prefix}:*"):
            await client.delete(existing_key)
        await client.aclose()
