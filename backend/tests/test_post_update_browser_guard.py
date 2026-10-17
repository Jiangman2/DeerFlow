"""Post-update merge regression: retain bounded DNS at every new guard surface.

Synthetic requests only; no browser, network, DNS or production configuration.
"""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from deerflow.community.browser_automation import request_guard, session
from deerflow.community.browser_automation.async_guard import BoundedURLGuard


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [None, "Error: DNS capacity exhausted"])
async def test_context_http_and_websocket_use_same_bounded_policy(monkeypatch, error):
    policy = lambda _: None
    bounded = AsyncMock(return_value=error)
    monkeypatch.setattr(session, "run_url_guard", bounded)
    browser = session.BrowserSession(None, headless=True, timeout_ms=1000, viewport={}, url_guard=policy)
    context = SimpleNamespace(route=AsyncMock(), route_web_socket=AsyncMock())
    browser._context = context
    await browser._install_request_guard()
    route = SimpleNamespace(request=SimpleNamespace(url="https://fixture.invalid/path"),
                            abort=AsyncMock(), continue_=AsyncMock())
    await context.route.await_args.args[1](route)
    # Playwright's async API intentionally exposes this method synchronously.
    websocket = SimpleNamespace(url="wss://fixture.invalid/socket", connect_to_server=Mock(), close=AsyncMock())
    await context.route_web_socket.await_args.args[1](websocket)
    assert bounded.await_args_list[0].args == (policy, "https://fixture.invalid/path")
    assert bounded.await_args_list[1].args == (policy, "https://fixture.invalid/socket")
    assert route.continue_.await_count == websocket.connect_to_server.call_count == int(error is None)
    assert route.abort.await_count == websocket.close.await_count == int(error is not None)


@pytest.mark.asyncio
async def test_chromium_redirect_guard_fails_closed_on_pool_saturation(monkeypatch):
    bounded = AsyncMock(return_value="Error: URL safety validation busy")
    monkeypatch.setattr(request_guard, "run_url_guard", bounded)
    guard = request_guard.ChromiumRequestGuard(None, lambda _: None)
    guard._send = AsyncMock()
    await guard._request(("target",), {"requestId": "request", "request": {"url": "https://fixture.invalid"}})
    bounded.assert_awaited_once()
    guard._send.assert_awaited_once_with(("target",), "Fetch.failRequest",
                                        {"requestId": "request", "errorReason": "BlockedByClient"})


@pytest.mark.asyncio
async def test_cancelled_dns_wait_retains_capacity_until_worker_finishes():
    pool = BoundedURLGuard(workers=1, capacity=1, timeout_seconds=2)
    entered, release = threading.Event(), threading.Event()

    def blocked(_):
        entered.set()
        release.wait(2)
        return None

    task = asyncio.create_task(pool.check(blocked, "https://fixture.invalid"))
    try:
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "busy" in await pool.check(lambda _: None, "https://second.invalid")
    finally:
        release.set()
        pool._executor.shutdown(wait=True)
