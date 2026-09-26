"""Dedicated async offload helper for agent/tool assembly work."""

from __future__ import annotations

import asyncio
import atexit
import contextvars
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

logger = logging.getLogger(__name__)


def _default_assembly_workers() -> int:
    raw = os.getenv("DEER_FLOW_ASSEMBLY_WORKERS")
    if raw:
        try:
            workers = int(raw)
            if workers > 0:
                return workers
        except ValueError:
            pass
        logger.warning("Invalid DEER_FLOW_ASSEMBLY_WORKERS value; using default assembly worker count")
    return 8


_ASSEMBLY_WORKERS = _default_assembly_workers()
_ASSEMBLY_EXECUTOR = ThreadPoolExecutor(max_workers=_ASSEMBLY_WORKERS, thread_name_prefix="assembly")
_pending_assemblies = 0
_pending_lock = threading.Lock()
_last_starvation_log = 0.0
_STARVATION_LOG_INTERVAL_SECONDS = 30.0


def _shutdown_assembly_executor() -> None:
    _ASSEMBLY_EXECUTOR.shutdown(wait=False, cancel_futures=True)


atexit.register(_shutdown_assembly_executor)


async def run_assembly[**P, T](func: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Run blocking assembly on a bounded pool while preserving ContextVars."""
    global _pending_assemblies, _last_starvation_log

    with _pending_lock:
        _pending_assemblies += 1
        pending = _pending_assemblies
        warn = pending > _ASSEMBLY_WORKERS and (time.monotonic() - _last_starvation_log) > _STARVATION_LOG_INTERVAL_SECONDS
        if warn:
            _last_starvation_log = time.monotonic()
    if warn:
        logger.warning(
            "Assembly pool saturated: %d pending assemblies on %d workers; agent assembly is starved (likely a hung MCP server)",
            pending,
            _ASSEMBLY_WORKERS,
        )

    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()

    def _work() -> T:
        global _pending_assemblies
        try:
            return ctx.run(func, *args, **kwargs)
        finally:
            with _pending_lock:
                _pending_assemblies -= 1

    try:
        executor_future = _ASSEMBLY_EXECUTOR.submit(_work)
    except Exception:
        with _pending_lock:
            _pending_assemblies -= 1
        raise

    def _release_if_cancelled(future: Future[T]) -> None:
        global _pending_assemblies
        if future.cancelled():
            with _pending_lock:
                _pending_assemblies -= 1

    executor_future.add_done_callback(_release_if_cancelled)
    return await asyncio.wrap_future(executor_future, loop=loop)
