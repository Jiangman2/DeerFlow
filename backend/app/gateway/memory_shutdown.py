"""Cancellation-safe ownership of the Gateway memory shutdown drain."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from deerflow.utils.file_io import await_drained

logger = logging.getLogger(__name__)


def _load_app_config() -> Any:
    """Resolve configuration lazily so importing Gateway stays lightweight."""
    from deerflow.config.app_config import get_app_config

    return get_app_config()


def _load_memory_manager() -> Any:
    """Resolve the configured backend lazily and off the event-loop thread."""
    from deerflow.agents.memory import get_memory_manager

    return get_memory_manager()


async def _shutdown_memory_backend() -> None:
    """Resolve and flush memory as one best-effort shutdown operation."""
    try:
        app_cfg = await asyncio.to_thread(_load_app_config)
        if not app_cfg.memory.enabled:
            return

        manager = await asyncio.to_thread(_load_memory_manager)
        flush_timeout = app_cfg.memory.shutdown_flush_timeout_seconds
        completed = await asyncio.to_thread(manager.shutdown_flush, flush_timeout)
        if completed:
            logger.info("Memory queue flush completed within %.1fs", flush_timeout)
        else:
            logger.warning(
                "Memory queue flush did not finish within %.1fs; remaining updates may be lost",
                flush_timeout,
            )
    except Exception:
        logger.exception("Failed to flush memory queue on shutdown")


async def shutdown_memory_backend() -> None:
    """Finish all owned memory workers before propagating caller cancellation.

    ``asyncio.to_thread`` cancellation detaches only the awaiter; its worker
    continues running. Draining the complete resolve-and-flush coroutine keeps
    Gateway lifespan teardown from returning while one of those workers still
    owns memory state.
    """
    await await_drained(_shutdown_memory_backend())
