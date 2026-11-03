"""Optional per-process admission limit for long OpenAI-compatible streams.

This deliberately uses no Redis/DB dependency: for a fixed number of single-worker
Gateway Pods, ``replicas * per_process_limit`` is a conservative cluster upper
bound. It is a load-test control, not a dynamically shared cluster semaphore.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

_MODEL_ENV = "DEERFLOW_LLM_STREAM_LIMIT_MODEL"
_LIMIT_ENV = "DEERFLOW_LLM_STREAM_LIMIT_PER_PROCESS"

logger = logging.getLogger(__name__)


@dataclass
class _Limiter:
    semaphore: asyncio.Semaphore
    active: int = 0
    waiting: int = 0


_limiters: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[tuple[str, int], _Limiter]] = weakref.WeakKeyDictionary()
_limiters_guard = threading.Lock()


def validate_stream_admission_config() -> tuple[str, int] | None:
    """Validate the startup setting before accepting any requests."""

    raw_limit = os.environ.get(_LIMIT_ENV, "").strip()
    if not raw_limit or raw_limit == "0":
        return None
    target = os.environ.get(_MODEL_ENV, "").strip()
    if not target:
        raise ValueError(f"{_MODEL_ENV} is required when {_LIMIT_ENV} is enabled")
    try:
        limit = int(raw_limit)
    except ValueError as exc:
        raise ValueError(f"{_LIMIT_ENV} must be a positive integer") from exc
    if limit < 1:
        raise ValueError(f"{_LIMIT_ENV} must be a positive integer")
    return target, limit


def _configured_limit(model_name: str) -> int | None:
    configured = validate_stream_admission_config()
    if configured is None:
        return None
    target, limit = configured
    if model_name.casefold() != target.casefold():
        return None
    return limit


def _limiter_for(model_name: str, limit: int) -> _Limiter:
    loop = asyncio.get_running_loop()
    key = (model_name.casefold(), limit)
    with _limiters_guard:
        by_model = _limiters.setdefault(loop, {})
        limiter = by_model.get(key)
        if limiter is None:
            limiter = _Limiter(asyncio.Semaphore(limit))
            by_model[key] = limiter
        return limiter


@asynccontextmanager
async def admit_model_stream(model_name: str) -> AsyncIterator[None]:
    """Hold one slot from request creation until the stream is closed.

    Cancellation while queued leaves no occupied slot. The LLM request timing
    starts inside this context so queue delay does not inflate provider latency.
    """

    limit = _configured_limit(model_name)
    if limit is None:
        yield
        return

    limiter = _limiter_for(model_name, limit)
    queued_at = time.monotonic()
    limiter.waiting += 1
    if limiter.active >= limit:
        logger.info(
            "LLM_ADMISSION event=queued model=%s active=%d waiting=%d limit=%d",
            model_name,
            limiter.active,
            limiter.waiting,
            limit,
        )
    try:
        await limiter.semaphore.acquire()
    except asyncio.CancelledError:
        logger.info(
            "LLM_ADMISSION event=cancelled model=%s wait_ms=%d active=%d waiting=%d limit=%d",
            model_name,
            round((time.monotonic() - queued_at) * 1000),
            limiter.active,
            limiter.waiting - 1,
            limit,
        )
        raise
    finally:
        limiter.waiting -= 1

    limiter.active += 1
    logger.info(
        "LLM_ADMISSION event=acquired model=%s wait_ms=%d active=%d waiting=%d limit=%d",
        model_name,
        round((time.monotonic() - queued_at) * 1000),
        limiter.active,
        limiter.waiting,
        limit,
    )
    try:
        yield
    finally:
        limiter.active -= 1
        limiter.semaphore.release()
        logger.info(
            "LLM_ADMISSION event=released model=%s active=%d waiting=%d limit=%d",
            model_name,
            limiter.active,
            limiter.waiting,
            limit,
        )
