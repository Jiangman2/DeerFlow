"""Process-wide admission and cancellation ownership for blocking workers."""

import asyncio
import contextvars
import functools
import threading
import time


class WorkCapacity:
    def __init__(self, active=1, waiting=2, admission_seconds=2.0):
        self.limit = active
        self.waiter_limit = waiting
        self.admission_seconds = admission_seconds
        self.active = 0
        self.waiting = 0
        self.lock = threading.Lock()

    async def acquire(self):
        with self.lock:
            if self.active < self.limit:
                self.active += 1
                return
            if self.waiting >= self.waiter_limit:
                raise TimeoutError("Worker capacity exhausted; retry later")
            self.waiting += 1
        deadline = time.monotonic() + self.admission_seconds
        try:
            while True:
                with self.lock:
                    if self.active < self.limit:
                        self.active += 1
                        return
                if time.monotonic() >= deadline:
                    raise TimeoutError("Worker admission timed out; retry later")
                await asyncio.sleep(0.02)
        finally:
            with self.lock:
                self.waiting -= 1

    def release(self):
        with self.lock:
            self.active -= 1
            if self.active < 0:
                raise RuntimeError("Worker ownership imbalance")


async def owned_call(function, *args, on_cancel=None, **kwargs):
    """Cancellation must not outlive a worker still touching its caller's files."""
    context = contextvars.copy_context()
    future = asyncio.get_running_loop().run_in_executor(None, context.run, functools.partial(function, *args, **kwargs))
    cancelled = False
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError:
            cancelled = True
            if on_cancel is not None:
                on_cancel()
        except Exception:
            break
    if cancelled:
        try:
            future.result()
        except BaseException:
            pass
        raise asyncio.CancelledError
    return future.result()
