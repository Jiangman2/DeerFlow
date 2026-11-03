"""Bounded, ordered batching of model-message stream publications."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeVar

_T = TypeVar("_T")
logger = logging.getLogger(__name__)

_COALESCIBLE_MESSAGE_TYPES = frozenset({"ai", "AIMessageChunk"})
_MESSAGE_DELTA_FIELDS = frozenset({"id", "type", "content", "additional_kwargs", "response_metadata", "usage_metadata"})


def _cumulative_usage_pair(previous: Any, current: Any) -> bool:
    """Recognize only a complete, monotonically increasing usage snapshot.

    Per-chunk usage deltas and unknown value types stay as separate events.
    Nested token-detail counters must also be monotone with the same shape.
    """
    if not isinstance(previous, dict) or not isinstance(current, dict) or previous.keys() != current.keys():
        return False
    if not {"input_tokens", "output_tokens", "total_tokens"}.issubset(previous):
        return False
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if type(previous[key]) is not int or type(current[key]) is not int:
            return False
        if previous[key] < 0 or current[key] < 0:
            return False
    if previous["input_tokens"] != current["input_tokens"] or previous["input_tokens"] == 0:
        return False
    if current["output_tokens"] <= previous["output_tokens"]:
        return False
    if previous["total_tokens"] != previous["input_tokens"] + previous["output_tokens"]:
        return False
    if current["total_tokens"] != current["input_tokens"] + current["output_tokens"]:
        return False

    def monotone(before: dict[str, Any], after: dict[str, Any]) -> bool:
        if before.keys() != after.keys():
            return False
        for key, old_value in before.items():
            new_value = after[key]
            if isinstance(old_value, dict) and isinstance(new_value, dict):
                if not monotone(old_value, new_value):
                    return False
            elif type(old_value) is int and type(new_value) is int:
                if old_value < 0 or new_value < old_value:
                    return False
            else:
                return False
        return True

    return monotone(previous, current)


def _coalescible_delta(data: Any) -> tuple[dict[str, Any], dict[str, Any], str, str] | None:
    """Accept only plain AI text or reasoning deltas with no other payload."""
    if not isinstance(data, list) or len(data) != 2:
        return None
    message, metadata = data
    if not isinstance(message, dict) or not isinstance(metadata, dict):
        return None
    message_type = message.get("type")
    if not isinstance(message_type, str) or message_type not in _COALESCIBLE_MESSAGE_TYPES:
        return None
    if not isinstance(message.get("id"), str) or not message["id"]:
        return None
    content = message.get("content", "")
    kwargs = message.get("additional_kwargs") or {}
    if not isinstance(content, str) or not isinstance(kwargs, dict):
        return None
    if set(kwargs) - {"reasoning_content"}:
        return None
    reasoning = kwargs.get("reasoning_content", "")
    if not isinstance(reasoning, str) or bool(content) == bool(reasoning):
        return None
    response_metadata = message.get("response_metadata", {})
    if not isinstance(response_metadata, dict):
        return None
    usage_metadata = message.get("usage_metadata")
    if usage_metadata is not None and not isinstance(usage_metadata, dict):
        return None
    if any(value not in (None, False, "", [], {}) for key, value in message.items() if key not in _MESSAGE_DELTA_FIELDS):
        return None
    return message, metadata, "content" if content else "reasoning_content", content or reasoning


def _merge_message_deltas(previous: Any, current: Any, *, max_chars: int) -> Any | None:
    before = _coalescible_delta(previous)
    after = _coalescible_delta(current)
    if before is None or after is None:
        return None
    old_message, old_metadata, old_kind, old_text = before
    new_message, new_metadata, new_kind, new_text = after
    if old_kind != new_kind or old_metadata != new_metadata or len(old_text) + len(new_text) > max_chars:
        return None
    if {key: value for key, value in old_message.items() if key not in {"content", "additional_kwargs", "usage_metadata"}} != {
        key: value for key, value in new_message.items() if key not in {"content", "additional_kwargs", "usage_metadata"}
    }:
        return None
    old_usage = old_message.get("usage_metadata")
    new_usage = new_message.get("usage_metadata")
    if old_usage is not None or new_usage is not None:
        if not _cumulative_usage_pair(old_usage, new_usage):
            return None
    merged = dict(old_message)
    if old_kind == "content":
        merged["content"] = old_text + new_text
    else:
        merged["additional_kwargs"] = {"reasoning_content": old_text + new_text}
    if new_usage is not None:
        merged["usage_metadata"] = new_usage
    return [merged, old_metadata]


class MessagePublishBatcher:
    """Batch consecutive messages without pausing the graph's next read.

    A pending ``anext`` continues to read the graph while the 500 ms deadline
    expires. Plain, adjacent AI deltas for the same message can share one SSE
    event; verified cumulative usage retains the latest snapshot. Tool calls,
    non-cumulative usage, metadata changes and other modes remain separate.
    No detached publish task exists: Redis failure reaches the Run immediately,
    and a terminal event cannot overtake buffered messages.
    """

    def __init__(
        self,
        *,
        publish_one: Callable[[str, Any, str], Awaitable[None]],
        publish_many: Callable[[Sequence[tuple[str, Any]]], Awaitable[None]],
        enabled: bool,
        max_events: int = 64,
        max_delay_seconds: float = 0.5,
        max_coalesced_chars: int = 4096,
    ) -> None:
        self._publish_one = publish_one
        self._publish_many = publish_many
        self._enabled = enabled
        self._max_events = max(1, max_events)
        self._max_delay_seconds = max_delay_seconds
        self._max_coalesced_chars = max(1, max_coalesced_chars)
        self._events: list[tuple[str, Any]] = []
        self._pending_source_events = 0
        self._first_at: float | None = None
        self._closed = False
        self.batch_count = 0
        self.batched_event_count = 0
        self.max_batch_size_observed = 0
        self.source_message_count = 0
        self.coalesced_message_count = 0

    async def publish(self, event: str, data: Any, mode: str) -> None:
        if self._closed:
            raise RuntimeError("message stream publisher already closed")
        if not self._enabled:
            await self._publish_one(event, data, mode)
            return
        if mode == "messages":
            self.source_message_count += 1
            if not self._events:
                self._first_at = time.monotonic()
            merged = None
            if self._events and self._events[-1][0] == event:
                merged = _merge_message_deltas(self._events[-1][1], data, max_chars=self._max_coalesced_chars)
            if merged is not None:
                self._events[-1] = (event, merged)
                self.coalesced_message_count += 1
            else:
                self._events.append((event, data))
            self._pending_source_events += 1
            if len(self._events) >= self._max_events or self._pending_source_events >= self._max_events:
                await self.flush()
            return
        await self.flush()
        await self._publish_one(event, data, mode)

    async def next_item(self, next_call: Callable[[], Awaitable[_T]]) -> _T:
        """Flush when due without cancelling an in-flight graph read."""
        if not self._enabled or not self._events:
            return await next_call()
        assert self._first_at is not None
        timeout = max(0.0, self._first_at + self._max_delay_seconds - time.monotonic())
        if timeout == 0.0:
            await self.flush()
            return await next_call()
        next_task = asyncio.create_task(next_call())
        try:
            done, _ = await asyncio.wait({next_task}, timeout=timeout)
            if not done:
                await self.flush()
            return await next_task
        except BaseException:
            if not next_task.done():
                next_task.cancel()
            await asyncio.gather(next_task, return_exceptions=True)
            raise

    async def flush(self) -> None:
        if not self._events:
            return
        events = tuple(self._events)
        self._events.clear()
        self._pending_source_events = 0
        self._first_at = None
        # Never retry here: the bridge owns idempotent server-side retry.
        self.batch_count += 1
        self.batched_event_count += len(events)
        self.max_batch_size_observed = max(self.max_batch_size_observed, len(events))
        await self._publish_many(events)

    async def finish(self, *, prior_error: BaseException | None = None) -> None:
        self._closed = True
        try:
            await self.flush()
        except Exception:
            if prior_error is None:
                raise
            logger.warning("Buffered stream publication failed while preserving an earlier Run exception", exc_info=True)
