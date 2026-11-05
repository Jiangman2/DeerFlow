"""No live DNS/Playwright: exercise blocking resolvers, cancellation and SSRF."""

import asyncio
import contextvars
import ipaddress
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from deerflow.community.browser_automation import async_guard, tools
from deerflow.community.browser_automation.session import BrowserSession


@pytest.fixture
def pool():
    instance = async_guard.BoundedURLGuard(workers=1, capacity=1, timeout_seconds=0.05)
    yield instance
    instance._executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_slow_resolver_keeps_loop_responsive_and_slot_until_really_done(pool):
    started, release = threading.Event(), threading.Event()

    def slow(_url):
        started.set()
        release.wait(2)
        return None

    task = asyncio.create_task(pool.check(slow, "https://example.test"))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        # The resolver is still blocked but this heartbeat can run.
        assert not release.is_set()
        assert "timed out" in await task
        assert "busy" in await pool.check(lambda _: None, "https://example.test")
    finally:
        release.set()
        await task


@pytest.mark.asyncio
async def test_cancellation_does_not_release_running_resolver_capacity(pool):
    started, release = threading.Event(), threading.Event()

    def slow(_url):
        started.set()
        release.wait(2)
        return None

    task = asyncio.create_task(pool.check(slow, "https://example.test"))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert "busy" in await pool.check(lambda _: None, "https://example.test")
    finally:
        release.set()


@pytest.mark.asyncio
async def test_context_preserved_errors_redacted_capacity_reusable(pool):
    owner = contextvars.ContextVar("guard-owner", default=None)
    token = owner.set("owner-1")
    try:
        assert await pool.check(lambda _: None if owner.get() == "owner-1" else "wrong owner", "https://example.test") is None
    finally:
        owner.reset(token)

    def fail(_url):
        raise RuntimeError("secret.example/token")

    reason = await pool.check(fail, "https://example.test")
    assert "failed" in reason and "secret" not in reason
    assert await pool.check(lambda _: None, "https://example.test") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,addresses,blocked",
    [
        ("http://127.0.0.1/", [], True),
        ("http://169.254.169.254/latest/meta-data", [], True),
        ("file:///etc/passwd", [], True),
        ("https://example.test", ["8.8.8.8"], False),
        ("https://example.test", ["8.8.8.8", "10.1.2.3"], True),
        ("https://example.test", ["::1"], True),
        ("https://example.test", [], True),
    ],
)
async def test_async_validator_keeps_real_ssrf_policy(monkeypatch, url, addresses, blocked):
    monkeypatch.setattr(tools, "_get_tool_config", lambda _: {})
    monkeypatch.setattr(tools, "_resolve_host_addresses", lambda _: [ipaddress.ip_address(ip) for ip in addresses])
    assert (await tools.validate_browser_url_async(url) is not None) is blocked


@pytest.mark.asyncio
async def test_redirect_guard_fails_closed_on_timeout(monkeypatch, pool):
    release = threading.Event()
    monkeypatch.setattr(async_guard, "_url_guard_pool", pool)

    def slow(_url):
        release.wait(2)
        return None

    context = SimpleNamespace(route=AsyncMock(), route_web_socket=AsyncMock())
    session = BrowserSession(MagicMock(), headless=True, timeout_ms=1000, viewport={}, url_guard=slow)
    session._context = context
    await session._install_request_guard()
    context.route_web_socket.assert_awaited_once()
    handler = context.route.await_args.args[1]
    route = SimpleNamespace(request=SimpleNamespace(url="https://example.test/redirect"), abort=AsyncMock(), continue_=AsyncMock())
    try:
        await handler(route)
        route.abort.assert_awaited_once_with("blockedbyclient")
        route.continue_.assert_not_awaited()
    finally:
        release.set()


@pytest.mark.asyncio
async def test_navigation_never_opens_session_when_validation_times_out(monkeypatch, tmp_path):
    validator = AsyncMock(return_value="Error: URL safety validation timed out; request blocked")
    manager = MagicMock()
    monkeypatch.setattr(tools, "validate_browser_url_async", validator)
    monkeypatch.setattr(tools, "get_browser_session_manager", manager)
    with pytest.raises(ValueError, match="timed out"):
        await tools.navigate_and_capture(thread_id="thread-1", url="https://example.test", outputs_path=tmp_path)
    result = await tools.browser_navigate_tool.coroutine(runtime=SimpleNamespace(context={}, state={}), url="https://example.test", tool_call_id="tool-1")
    assert "timed out" in result.update["messages"][0].content
    manager.assert_not_called()


def test_pool_shared_between_two_event_loops_preserves_each_context():
    pool = async_guard.BoundedURLGuard(workers=2, capacity=2, timeout_seconds=2)
    owner = contextvars.ContextVar("loop-owner")
    barrier = threading.Barrier(2)

    def on_loop(name):
        owner.set(name)
        loop_thread = threading.get_ident()

        def guard(_url):
            barrier.wait(timeout=2)
            assert threading.get_ident() != loop_thread
            return None if owner.get() == name else "wrong context"

        return asyncio.run(pool.check(guard, "https://example.test"))

    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            futures = [callers.submit(on_loop, name) for name in ("gateway", "browser")]
            assert [future.result(timeout=5) for future in futures] == [None, None]
    finally:
        pool._executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_queue_stays_bounded_after_timeout_until_work_is_dequeued():
    pool = async_guard.BoundedURLGuard(workers=1, capacity=2, timeout_seconds=0.1)
    started, release = threading.Event(), threading.Event()
    queued_called = MagicMock()

    def slow(_url):
        started.set()
        release.wait(2)
        return None

    running = asyncio.create_task(pool.check(slow, "https://example.test"))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        queued = asyncio.create_task(pool.check(queued_called, "https://example.test"))
        await asyncio.sleep(0)
        assert "busy" in await pool.check(queued_called, "https://example.test")
        assert "timed out" in await queued
        assert "timed out" in await running
        queued_called.assert_not_called()
        # Timed-out queued Futures still occupy executor queue entries. New
        # requests cannot grow that queue while its single worker is blocked.
        for _ in range(10):
            assert "busy" in await pool.check(queued_called, "https://example.test")
    finally:
        release.set()
        await running
        pool._executor.shutdown(wait=True, cancel_futures=True)
    queued_called.assert_not_called()
