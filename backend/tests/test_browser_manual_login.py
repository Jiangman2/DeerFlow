"""Owned browser login isolation; no real credentials or enterprise requests."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import test_browser_private_flow as browser_fixtures

from deerflow.community.browser_automation.login_policy import manual_login_origins
from deerflow.community.browser_automation.session import BrowserLoginRequired, BrowserSession, BrowserSessionChangedError, BrowserSessionManager

website = browser_fixtures.website
guarded_browser = browser_fixtures.guarded_browser


def test_operator_login_origins_are_exact_https_and_do_not_grant_network_access():
    assert manual_login_origins(["https://au.cnooc.com.cn:443/"]) == ("https://au.cnooc.com.cn",)
    for value in ("https://au.cnooc.com.cn", ["http://au.cnooc.com.cn"], ["https://x/?token=x"], ["https://user:secret@x/"], ["https://au.cnooc.com.cn\n"], ["https://*.cnooc.com.cn"], ["https://au.cnooc.com.cn:bad"]):
        with pytest.raises(ValueError):
            manual_login_origins(value)


@pytest.mark.asyncio
async def test_control_serializes_with_agent_and_blocks_all_agent_operations():
    s = BrowserSession(None, headless=True, timeout_ms=1000, viewport={})
    started, finish = asyncio.Event(), asyncio.Event()

    async def operation():
        started.set()
        await finish.wait()
        return "old non-sensitive result"

    task = asyncio.create_task(s._agent_operation(operation))
    await started.wait()
    control = asyncio.create_task(s._login_control("begin"))
    await asyncio.sleep(0)
    assert not control.done()
    finish.set()
    await task
    assert (await control)["manual_login"] is True
    probe = AsyncMock()
    with pytest.raises(BrowserLoginRequired):
        await s._agent_operation(probe)
    probe.assert_not_awaited()
    assert (await s._login_control("resume"))["manual_login"] is False


@pytest.mark.asyncio
async def test_owner_binding_denies_reuse_and_close_even_with_same_thread(monkeypatch):
    manager = BrowserSessionManager()
    monkeypatch.setattr(manager, "_ensure_loop", lambda: None)
    session = manager.get_session("thread", owner_id="alice")
    assert manager.get_session("thread", owner_id="alice") is session
    with pytest.raises(PermissionError):
        manager.get_session("thread", owner_id="bob")
    with pytest.raises(PermissionError):
        await manager.close_session("thread", owner_id="bob")
    assert manager._sessions["thread"] is session


def test_reconnect_refuses_other_worker_or_evicted_session_without_allocation(monkeypatch):
    manager = BrowserSessionManager(max_sessions=1)
    monkeypatch.setattr(manager, "_ensure_loop", lambda: None)
    session = manager.get_session("thread", owner_id="alice")
    assert manager.get_session("thread", owner_id="alice", expected_session_id=session.session_id, pin=True) is session
    manager.release_session("thread", session)
    with pytest.raises(BrowserSessionChangedError):
        manager.get_session("thread", owner_id="alice", expected_session_id="0" * 32)
    with pytest.raises(BrowserSessionChangedError):
        manager.get_session("other-thread", owner_id="alice", expected_session_id=session.session_id)
    with pytest.raises(PermissionError):
        manager.get_session("thread", owner_id="bob", expected_session_id=session.session_id)
    other_worker = BrowserSessionManager()
    with pytest.raises(BrowserSessionChangedError):
        other_worker.get_session("thread", owner_id="alice", expected_session_id=session.session_id)
    assert other_worker._loop is None
    assert other_worker._sessions == {}
    assert manager._sessions == {"thread": session}


@pytest.mark.asyncio
async def test_idp_tab_blocks_agent_even_without_password_field():
    s = BrowserSession(None, headless=True, timeout_ms=1000, viewport={}, manual_login_origins=("https://au.cnooc.com.cn",))
    page = SimpleNamespace(is_closed=lambda: False, frames=[SimpleNamespace(url="https://au.cnooc.com.cn:443/mfa?code=secret")])
    s._context = SimpleNamespace(pages=[page])
    with pytest.raises(BrowserLoginRequired) as exc:
        await s._agent_operation(AsyncMock())
    assert "secret" not in str(exc.value)
    with pytest.raises(BrowserLoginRequired):
        await s._login_control("resume")
    assert s._manual_login


@pytest.mark.asyncio
async def test_real_login_cookie_reuse_clear_and_user_isolation(website, monkeypatch):
    port, _ = website
    url = f"http://allowed.example.test:{port}"
    async with guarded_browser(monkeypatch, port) as (s, _):
        await s._login_control("begin")
        await s._user_input({"type": "navigate", "url": url + "/login"})
        await s._page.locator("input").evaluate("el => el.type='password'")
        await s._page.locator("input").fill("fixture-user")
        for op, args in ((s._snapshot, ()), (s._get_text, (8000,)), (s._screenshot_bytes, (False,)), (s._navigate, (url,))):
            with pytest.raises(BrowserLoginRequired):
                await s._agent_operation(op, *args)
        with pytest.raises(BrowserLoginRequired):
            await s._login_control("resume")
        # A user mouse click submits the fixture form; the Agent never sees credentials.
        box = await s._page.locator("button").bounding_box()
        await s._user_input({"type": "click", "nx": (box["x"] + 3) / 1000, "ny": (box["y"] + 3) / 700})
        await s._page.wait_for_url("**/welcome")
        await s._login_control("resume")
        assert "Fixture login complete" in await s._agent_operation(s._get_text, 1000)
        await s._agent_operation(s._navigate, url + "/welcome")
        async with guarded_browser(monkeypatch, port) as (other, _):
            await other._navigate(url + "/welcome")
            assert await other._get_text(1000) == "No fixture session"
        await s._login_control("clear")
        await s._agent_operation(s._navigate, url + "/welcome")
        assert await s._get_text(1000) == "No fixture session"


@pytest.mark.asyncio
async def test_real_password_redirect_protects_without_manual_click(website, monkeypatch):
    port, _ = website
    async with guarded_browser(monkeypatch, port) as (s, _):
        await s._navigate(f"http://allowed.example.test:{port}/login")
        await s._page.locator("input").evaluate("el => el.type='password'")
        with pytest.raises(BrowserLoginRequired):
            await s._agent_operation(s._snapshot)
        assert (await s._login_control("status"))["manual_login"] is True
