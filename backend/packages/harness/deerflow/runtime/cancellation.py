"""Cancellation-safe helpers for resources owned by async runtime contexts."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
from contextlib import AbstractAsyncContextManager, asynccontextmanager


async def _drain_context_exit(awaitable: Awaitable[bool | None]) -> bool | None:
    """Wait for backend teardown despite caller cancellation, preserving exit errors.

    This follows upstream DeerFlow f6269fb: the connection/pool remains owned
    until its __aexit__ completes. A teardown error takes precedence over the
    cancellation; otherwise cancellation is propagated after cleanup.
    """
    task = asyncio.ensure_future(awaitable)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.wait({task})
            except asyncio.CancelledError:
                continue
        task.result()
        raise


@asynccontextmanager
async def drained_async_context[T](manager: AbstractAsyncContextManager[T]) -> AsyncIterator[T]:
    """Keep an entered backend context owned until its exit fully settles."""
    value = await manager.__aenter__()
    try:
        yield value
    except BaseException as exc:
        suppressed = await _drain_context_exit(manager.__aexit__(type(exc), exc, exc.__traceback__))
        if not suppressed:
            raise
    else:
        await _drain_context_exit(manager.__aexit__(None, None, None))
