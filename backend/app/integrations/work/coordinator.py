"""Process-owned reconciliation survives stopped receivers and SQL outages."""

import asyncio
import logging
import random

logger = logging.getLogger(__name__)


class WorkChannelCoordinator:
    def __init__(self, service, *, interval=5.0, max_delay=60.0):
        self.service = service
        self.interval, self.max_delay = interval, max_delay
        self.task = None
        self._wake = asyncio.Event()
        self._closed = False
        self._failures = 0

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run())

    def wake(self):
        self._wake.set()

    def retry_delay(self, success):
        self._failures = 0 if success else min(self._failures + 1, 6)
        ceiling = min(self.max_delay, self.interval * 2**self._failures)
        return random.uniform(ceiling * 0.8, ceiling)

    async def _run(self):
        while not self._closed and self.service._running:
            self._wake.clear()
            try:
                success = await self.service._reconcile_work_channel()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Config/SQL errors may embed credentials: fixed diagnostics only.
                logger.warning("work_coordinator stage=reconcile_unavailable")
                success = False
            delay = self.retry_delay(success)
            if self._closed or not self.service._running:
                return
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def close(self):
        self._closed = True
        self.wake()
        if self.task:
            # Never cancel halfway through socket stop/thread join. Service.stop
            # clears _running first; in-flight reconciliation cannot start again.
            await asyncio.shield(self.task)
