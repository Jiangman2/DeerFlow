"""Chromium request-stage guard, including HTTP redirect hops.

Playwright context routes don't see every HTTP redirect. Attach to owned
Chromium targets *before* they run and pause at Fetch's Request stage instead.
This leaves response streaming, cookies and redirect semantics with Chromium;
it never fetches, buffers, retries or rewrites a response itself. This is not a
replacement for deployment egress restrictions (notably DNS rebinding).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from typing import Any

from .async_guard import run_url_guard

logger = logging.getLogger(__name__)
_FILTER = [{"type": kind, "exclude": False} for kind in ("page", "iframe", "worker", "shared_worker")] + [{"exclude": True}]


class ChromiumRequestGuard:
    def __init__(self, browser: Any, guard: Callable[[str], str | None]) -> None:
        self.browser = browser
        self.guard = guard
        self.cdp = None
        self.closed = False
        self.failed = False
        self.serial = 0
        self.pending: dict[tuple[tuple[str, ...], int], asyncio.Future] = {}
        self.tasks: set[asyncio.Task] = set()
        self.dns_slots = asyncio.Semaphore(16)
        self.ready: dict[str, asyncio.Event] = {}
        self.page_targets: dict[Any, str] = {}
        self.detached: set[tuple[str, ...]] = set()

    async def start(self) -> None:
        async with asyncio.timeout(10):
            self.cdp = await self.browser.new_browser_cdp_session()
            self.cdp.on("Target.attachedToTarget", lambda event: self._attached((), event))
            self.cdp.on("Target.receivedMessageFromTarget", lambda event: self._received((), event))
            self.cdp.on("Target.detachedFromTarget", lambda event: self._detached((), event))
            options = self._attach_options()
            options["flatten"] = True
            await self.cdp.send("Target.setAutoAttach", options)

    async def wait_page(self, context: Any, page: Any) -> None:
        async with asyncio.timeout(10):
            if page not in self.page_targets:
                session = await context.new_cdp_session(page)
                try:
                    result = await session.send("Target.getTargetInfo")
                    self.page_targets[page] = result["targetInfo"]["targetId"]
                finally:
                    await session.detach()
            await self.ready.setdefault(self.page_targets[page], asyncio.Event()).wait()
            if self.failed or self.closed:
                raise RuntimeError("Browser request guard unavailable")

    @staticmethod
    def _attach_options() -> dict:
        return {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": False, "filter": _FILTER}

    def _spawn(self, coroutine) -> None:
        if self.closed or self.failed:
            coroutine.close()
            return
        if len(self.tasks) >= 256:
            coroutine.close()
            self._fatal()
            return
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if task.cancelled():
            return
        if task.exception() is not None and not self.closed:
            # Do not log CDP error strings: they can include URLs or form data.
            self._fatal()

    def _fatal(self) -> None:
        if self.closed or self.failed:
            return
        self.failed = True
        logger.error("Browser request guard failed; closing the owned browser")
        task = asyncio.create_task(self.browser.close())
        self.tasks.add(task)
        task.add_done_callback(self._done)

    def _attached(self, parent: tuple[str, ...], event: dict) -> None:
        if not parent:
            if event.get("waitingForDebugger"):
                self._spawn(self._adopt(event))
        else:
            self._spawn(self._initialize(parent + (event["sessionId"],)))

    async def _adopt(self, event: dict) -> None:
        # Browser-level auto-attach requires flat mode. Keep that target paused
        # while creating our addressable session, then release the pause only
        # after Fetch interception is installed.
        attached = await self.cdp.send(
            "Target.attachToTarget",
            {
                "targetId": event["targetInfo"]["targetId"],
                "flatten": False,
            },
        )
        await self._initialize((attached["sessionId"],), resume=False)
        await self.cdp.send("Target.detachFromTarget", {"sessionId": event["sessionId"]})
        await self._send((attached["sessionId"],), "Runtime.runIfWaitingForDebugger", {})
        self.ready.setdefault(event["targetInfo"]["targetId"], asyncio.Event()).set()

    def _detached(self, parent: tuple[str, ...], event: dict) -> None:
        chain = parent + (event["sessionId"],)
        self.detached.add(chain)
        for (pending_chain, _), future in self.pending.items():
            if pending_chain[: len(chain)] == chain and not future.done():
                future.cancel()

    def _received(self, parent: tuple[str, ...], event: dict) -> None:
        try:
            chain = parent + (event["sessionId"],)
            message = json.loads(event["message"])
            if "id" in message:
                future = self.pending.get((chain, message["id"]))
                if future is not None and not future.done():
                    if "error" in message:
                        future.set_exception(RuntimeError("Browser request guard command rejected"))
                    else:
                        future.set_result(message.get("result", {}))
            elif message.get("method") == "Target.attachedToTarget":
                self._attached(chain, message["params"])
            elif message.get("method") == "Target.receivedMessageFromTarget":
                self._received(chain, message["params"])
            elif message.get("method") == "Target.detachedFromTarget":
                self._detached(chain, message["params"])
            elif message.get("method") == "Fetch.requestPaused":
                self._spawn(self._request(chain, message["params"]))
        except Exception:
            self._fatal()

    async def _send(self, chain: tuple[str, ...], method: str, params: dict) -> dict:
        if self.closed or self.failed:
            raise RuntimeError("Browser request guard unavailable")
        if any(chain[: len(item)] == item for item in self.detached):
            raise asyncio.CancelledError
        if not chain:
            async with asyncio.timeout(10):
                return await self.cdp.send(method, params)
        self.serial += 1
        key = (chain, self.serial)
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        try:
            async with asyncio.timeout(10):
                await self._send(
                    chain[:-1],
                    "Target.sendMessageToTarget",
                    {
                        "sessionId": chain[-1],
                        "message": json.dumps({"id": key[1], "method": method, "params": params}),
                    },
                )
                return await future
        finally:
            self.pending.pop(key, None)
            if not future.done():
                future.cancel()

    async def _initialize(self, chain: tuple[str, ...], *, resume: bool = True) -> None:
        await self._send(
            chain,
            "Fetch.enable",
            {
                "patterns": [
                    {"urlPattern": "http://*", "requestStage": "Request"},
                    {"urlPattern": "https://*", "requestStage": "Request"},
                ]
            },
        )
        await self._send(chain, "Target.setAutoAttach", self._attach_options())
        if resume:
            await self._send(chain, "Runtime.runIfWaitingForDebugger", {})

    async def _request(self, chain: tuple[str, ...], event: dict) -> None:
        url = event.get("request", {}).get("url", "")
        allowed = False
        try:
            async with asyncio.timeout(5):
                async with self.dns_slots:
                    allowed = bool(url) and await run_url_guard(self.guard, url) is None
        except Exception:
            pass
        method = "Fetch.continueRequest" if allowed else "Fetch.failRequest"
        params = {"requestId": event["requestId"]}
        if not allowed:
            params["errorReason"] = "BlockedByClient"
        await self._send(chain, method, params)

    async def close(self) -> None:
        # Called when disposing this owned browser, never while pages keep running.
        self.closed = True
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.cdp is not None:
            with contextlib.suppress(Exception):
                await self.cdp.detach()
