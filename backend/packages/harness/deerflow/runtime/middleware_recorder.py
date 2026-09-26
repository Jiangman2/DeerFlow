"""Narrow, fenced child audit bridge back to a RunJournal's owning loop."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from deerflow.runtime.events.catalog import MIDDLEWARE_LOOP_DETECTION_TAG, MIDDLEWARE_TOOL_PROGRESS_TAG, MIDDLEWARE_TOOL_PROMOTION_TAG

logger = logging.getLogger(__name__)
_TAGS = {MIDDLEWARE_LOOP_DETECTION_TAG, MIDDLEWARE_TOOL_PROMOTION_TAG, MIDDLEWARE_TOOL_PROGRESS_TAG}


def _append(journal: Any, kwargs: dict[str, Any]) -> None:
    try:
        journal.record_middleware(**kwargs)
    except Exception:
        logger.warning("Failed to append child middleware audit event")


class ParentLoopMiddlewareRecorder:
    def __init__(self, journal: Any, loop: asyncio.AbstractEventLoop) -> None:
        owner = getattr(journal, "_owner_loop", None)
        if isinstance(owner, asyncio.AbstractEventLoop) and owner is not loop:
            raise ValueError("Middleware recorder must use the journal owner loop")
        self._journal = journal
        self._loop = loop
        self._lock = threading.Lock()
        self._closed = False
        self._claimed_tool_promotions: set[str] = set()

    def claim_tool_promotions(self, tool_names: Iterable[str]) -> list[str]:
        """One child execution owns its claims, independently of the parent."""
        with self._lock:
            if self._closed or self._loop.is_closed():
                return []
            names = sorted(set(tool_names) - self._claimed_tool_promotions)
            self._claimed_tool_promotions.update(names)
            return names

    def record_middleware(self, **kwargs: Any) -> None:
        if kwargs.get("tag") not in _TAGS:
            raise ValueError("Unsupported child middleware audit category")
        snapshot = deepcopy(kwargs)
        with self._lock:
            if self._closed or self._loop.is_closed():
                return
            try:
                self._loop.call_soon_threadsafe(_append, self._journal, snapshot)
            except RuntimeError:
                # Loop shutdown raced with scheduling. Never call a SQL-backed
                # journal directly on the child loop as a fallback.
                logger.debug("Dropping child audit after owner loop shutdown")

    @property
    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    async def aclose(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("Close middleware recorder on its owner loop")
        with self._lock:
            self._closed = True
        # Accepted callbacks were scheduled under the same lock, so they run
        # before this continuation. RunJournal retains its own flush lifecycle.
        await asyncio.sleep(0)
