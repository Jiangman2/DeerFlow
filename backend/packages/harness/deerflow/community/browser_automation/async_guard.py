"""Bound synchronous SSRF/DNS work without blocking either browser event loop.

Timeout/cancellation cannot stop a running system resolver. Its capacity slot
therefore belongs to the concurrent Future, not the async waiter. Saturation
fails closed rather than creating an unbounded executor queue. No DNS cache or
policy bypass is introduced; redirects and subresources are checked separately.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor


class BoundedURLGuard:
    """Process-local pool shared safely by the Gateway and browser loops."""

    def __init__(self, *, workers: int = 8, capacity: int = 64, timeout_seconds: float = 5.0) -> None:
        if capacity < workers or timeout_seconds <= 0:
            raise ValueError("URL guard capacity must cover workers and timeout must be positive")
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="browser-url-guard")
        # Absorb a bounded page-resource burst; the deadline includes queueing.
        self._slots = threading.BoundedSemaphore(capacity)
        self._timeout_seconds = timeout_seconds

    async def check(self, guard: Callable[[str], str | None], url: str) -> str | None:
        if not self._slots.acquire(blocking=False):
            return "Error: URL safety validation busy; request blocked, retry later"
        context = contextvars.copy_context()
        abandoned = threading.Event()

        def evaluate() -> str | None:
            # A timed-out queued request must not initiate fresh DNS work.
            if abandoned.is_set():
                return "Error: URL safety validation expired; request blocked"
            return context.run(guard, url)

        try:
            future = self._executor.submit(evaluate)
        except Exception:
            self._slots.release()
            return "Error: URL safety validation unavailable; request blocked"
        future.add_done_callback(lambda _future: self._slots.release())
        pending = asyncio.wrap_future(future)
        # Observe a late exception even after the async caller has left.
        pending.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            # Do not cancel queued Futures on caller timeout: ThreadPoolExecutor
            # leaves cancelled work items in its queue until a worker dequeues
            # them. Releasing their slots early would permit unbounded buildup.
            result = await asyncio.wait_for(asyncio.shield(pending), timeout=self._timeout_seconds)
        except TimeoutError:
            return "Error: URL safety validation timed out; request blocked"
        except Exception:
            # Do not disclose resolver internals or credential-bearing URLs.
            return "Error: URL safety validation failed; request blocked"
        finally:
            abandoned.set()
        # CancelledError propagates. A still-running resolver retains its slot.
        if result is None or isinstance(result, str):
            return result
        return "Error: Invalid URL safety validation result; request blocked"


_url_guard_pool = BoundedURLGuard()


async def run_url_guard(guard: Callable[[str], str | None], url: str) -> str | None:
    return await _url_guard_pool.check(guard, url)
