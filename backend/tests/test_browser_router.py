import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.gateway.auth.models import User
from app.gateway.routers import browser as browser_router
from app.gateway.routers.browser import (
    _negotiate_browser_frame_format,
    _send_browser_frame,
    _should_apply_browser_seed,
    _ws_origin_allowed,
)


class _FakeWebSocket:
    """Minimal stand-in exposing only the headers ``_ws_origin_allowed`` reads."""

    def __init__(self, headers: dict[str, str]):
        self.headers = {k.lower(): v for k, v in headers.items()}


def _user(user_id: str = "browser-user") -> SimpleNamespace:
    return SimpleNamespace(id=user_id)


def _browser_ws_app(thread_store=...):
    app = FastAPI()
    app.include_router(browser_router.router)
    if thread_store is not ...:
        app.state.thread_store = thread_store
    return app


def _expect_ws_close(app: FastAPI, code: int, *, headers: dict[str, str] | None = None) -> None:
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/threads/thread-1/browser/stream", headers=headers or {}):
                pass
    assert exc_info.value.code == code


def test_browser_stream_closes_4401_when_unauthenticated():
    app = _browser_ws_app()
    with patch.object(browser_router, "_authenticate_ws", AsyncMock(return_value=None)):
        _expect_ws_close(app, 4401)


def test_browser_stream_closes_4403_for_cross_origin_upgrade():
    app = _browser_ws_app()
    with patch.object(browser_router, "_authenticate_ws", AsyncMock(return_value=_user())):
        _expect_ws_close(app, 4403, headers={"origin": "https://evil.example.com"})


def test_browser_stream_closes_4404_when_thread_store_missing():
    app = _browser_ws_app()
    with patch.object(browser_router, "_authenticate_ws", AsyncMock(return_value=_user())):
        _expect_ws_close(app, 4404)


def test_browser_stream_rejects_legacy_null_owner_thread():
    store = MagicMock()
    store.check_access = AsyncMock(return_value=True)
    store.get = AsyncMock(return_value={"thread_id": "thread-1", "user_id": None})
    app = _browser_ws_app(store)
    with (
        patch.object(browser_router, "_authenticate_ws", AsyncMock(return_value=_user())),
        patch.object(browser_router, "_browser_tools_enabled", return_value=False),
    ):
        _expect_ws_close(app, 4404)
    store.get.assert_awaited_once_with("thread-1", user_id="browser-user")


def test_browser_stream_closes_4404_when_tools_disabled_for_owned_thread():
    store = MagicMock()
    store.check_access = AsyncMock(return_value=True)
    store.get = AsyncMock(return_value={"thread_id": "thread-1", "user_id": "browser-user"})
    app = _browser_ws_app(store)
    with (
        patch.object(browser_router, "_authenticate_ws", AsyncMock(return_value=_user())),
        patch.object(browser_router, "_browser_tools_enabled", return_value=False),
    ):
        _expect_ws_close(app, 4404)


def test_browser_stream_closes_4501_when_browser_runtime_unavailable():
    store = MagicMock()
    store.check_access = AsyncMock(return_value=True)
    store.get = AsyncMock(return_value={"thread_id": "thread-1", "user_id": "browser-user"})
    app = _browser_ws_app(store)
    real_import = __import__

    def fail_browser_runtime_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "deerflow.community.browser_automation" and "get_browser_session_manager" in fromlist:
            raise ImportError("browser runtime unavailable")
        return real_import(name, globals, locals, fromlist, level)

    with (
        patch.object(browser_router, "_authenticate_ws", AsyncMock(return_value=_user())),
        patch.object(browser_router, "_browser_tools_enabled", return_value=True),
        patch("builtins.__import__", side_effect=fail_browser_runtime_import),
    ):
        _expect_ws_close(app, 4501)


def test_browser_navigate_rejects_legacy_null_owner_thread():
    user = User(email="browser@example.com", password_hash="x", system_role="user")
    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(browser_router.router)
    app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "user_id": None})

    with (
        patch.object(browser_router, "_browser_tools_enabled", return_value=True),
        patch("deerflow.community.browser_automation.navigate_and_capture", new=AsyncMock()),
    ):
        response = TestClient(app).post(
            "/api/threads/thread-1/browser/navigate",
            json={"url": "https://example.com"},
        )

    assert response.status_code == 404
    app.state.thread_store.get.assert_awaited_once_with("thread-1", user_id=str(user.id))


def test_browser_tools_disabled_when_cdp_risk_not_explicitly_accepted():
    tool_cfg = SimpleNamespace(name="browser_navigate", model_extra={"cdp_url": "http://127.0.0.1:9222"})
    app_config = SimpleNamespace(tools=[tool_cfg])

    with (
        patch("deerflow.config.get_app_config", return_value=app_config),
        patch("app.gateway.browser_capability.browser_multi_worker_error", return_value=None),
        patch("app.gateway.browser_capability.importlib.util.find_spec", return_value=object()),
    ):
        assert browser_router._browser_tools_enabled() is False


def test_browser_tools_enabled_when_cdp_risk_explicitly_accepted():
    tool_cfg = SimpleNamespace(
        name="browser_navigate",
        model_extra={"cdp_url": "http://127.0.0.1:9222", "allow_unguarded_cdp": True},
    )
    app_config = SimpleNamespace(tools=[tool_cfg])

    with (
        patch("deerflow.config.get_app_config", return_value=app_config),
        patch("app.gateway.browser_capability.browser_multi_worker_error", return_value=None),
        patch("app.gateway.browser_capability.importlib.util.find_spec", return_value=object()),
    ):
        assert browser_router._browser_tools_enabled() is True


def test_browser_navigate_redacts_failure_url_from_logs_and_response(caplog):
    user = User(email="browser@example.com", password_hash="x", system_role="user")
    app = make_authed_test_app(user_factory=lambda: user)
    app.include_router(browser_router.router)
    app.state.thread_store.get = AsyncMock(return_value={"thread_id": "thread-1", "user_id": str(user.id)})
    failing_url = "https://example.com/callback?code=secret#fragment"

    caplog.set_level(logging.ERROR, logger="app.gateway.routers.browser")
    with (
        patch.object(browser_router, "_browser_tools_enabled", return_value=True),
        patch(
            "deerflow.community.browser_automation.navigate_and_capture",
            new=AsyncMock(side_effect=RuntimeError(f"timed out opening {failing_url}")),
        ),
    ):
        response = TestClient(app).post(
            "/api/threads/thread-1/browser/navigate",
            json={"url": failing_url},
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "Browser navigation failed"}
    assert "https://example.com/callback" in caplog.text
    assert "code=secret" not in caplog.text
    assert "fragment" not in caplog.text
    assert "secret" not in response.text


def test_browser_stream_seed_applies_to_blank_page():
    assert _should_apply_browser_seed("about:blank", "https://github.com/bytedance/deer-flow")


def test_browser_stream_seed_applies_when_current_url_differs():
    assert _should_apply_browser_seed(
        "https://docs.byteplus.com/en/docs/InfoQuest/What_is_Info_Quest",
        "https://github.com/bytedance/deer-flow",
    )


def test_browser_stream_seed_ignores_hash_and_trailing_slash_for_same_page():
    assert not _should_apply_browser_seed(
        "https://github.com/bytedance/deer-flow/#readme",
        "https://github.com/bytedance/deer-flow/",
    )


def test_ws_origin_allowed_without_origin_header():
    # Native ws clients / tests do not send Origin — allow them.
    assert _ws_origin_allowed(_FakeWebSocket({"host": "app.example.com"})) is True


def test_ws_origin_allowed_same_origin_host():
    # Browser page scheme (https) differs from the ws scheme, so same-origin
    # compares host[:port] against the upgrade target Host.
    ws = _FakeWebSocket({"origin": "https://app.example.com", "host": "app.example.com"})
    assert _ws_origin_allowed(ws) is True


@pytest.mark.asyncio
async def test_send_browser_frame_uses_binary_websocket_message_when_requested():
    websocket = MagicMock()
    websocket.send_bytes = AsyncMock()
    websocket.send_text = AsyncMock()

    await _send_browser_frame(websocket, b"\xff\xd8jpeg", binary=True)

    websocket.send_bytes.assert_awaited_once_with(b"\xff\xd8jpeg")
    websocket.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_browser_frame_keeps_legacy_base64_json_protocol():
    websocket = MagicMock()
    websocket.send_bytes = AsyncMock()
    websocket.send_text = AsyncMock()

    await _send_browser_frame(websocket, b"\xff\xd8jpeg", binary=False)

    websocket.send_bytes.assert_not_awaited()
    payload = json.loads(websocket.send_text.await_args.args[0])
    assert payload == {"type": "frame", "data": "/9hq cGVn".replace(" ", "")}


@pytest.mark.asyncio
async def test_browser_frame_format_rejects_unknown_capability():
    websocket = MagicMock()
    websocket.query_params = {"frame_format": "avif"}
    websocket.accept = AsyncMock()
    websocket.send_text = AsyncMock()
    websocket.close = AsyncMock()

    result = await _negotiate_browser_frame_format(websocket)

    assert result is None
    websocket.accept.assert_awaited_once()
    payload = json.loads(websocket.send_text.await_args.args[0])
    assert payload["type"] == "error"
    assert "frame_format" in payload["message"]
    websocket.close.assert_awaited_once_with(code=1008)


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "expected"), [(None, False), ("binary", True)])
async def test_browser_frame_format_accepts_legacy_and_binary(value, expected):
    websocket = MagicMock()
    websocket.query_params = {} if value is None else {"frame_format": value}
    websocket.accept = AsyncMock()
    websocket.send_text = AsyncMock()
    websocket.close = AsyncMock()

    assert await _negotiate_browser_frame_format(websocket) is expected
    websocket.accept.assert_awaited_once()
    websocket.send_text.assert_not_awaited()
    websocket.close.assert_not_awaited()


def test_ws_origin_allowed_rejects_cross_origin():
    ws = _FakeWebSocket({"origin": "https://evil.example.com", "host": "app.example.com"})
    assert _ws_origin_allowed(ws) is False


def test_ws_origin_allowed_rejects_malformed_origin():
    ws = _FakeWebSocket({"origin": "not-a-url", "host": "app.example.com"})
    assert _ws_origin_allowed(ws) is False


def test_ws_origin_allowed_honors_configured_cors_origin(monkeypatch):
    monkeypatch.setenv("GATEWAY_CORS_ORIGINS", "https://console.example.com")
    ws = _FakeWebSocket({"origin": "https://console.example.com", "host": "gateway.internal"})
    assert _ws_origin_allowed(ws) is True


def test_ws_origin_allowed_honors_forwarded_host():
    # Behind a proxy the real upgrade target is X-Forwarded-Host, not Host.
    ws = _FakeWebSocket(
        {
            "origin": "https://app.example.com",
            "host": "gateway.internal",
            "x-forwarded-host": "app.example.com",
        },
    )
    assert _ws_origin_allowed(ws) is True


def test_browser_frames_dirname_shared_between_tools_and_scanner():
    """The screenshots dir name must stay identical in the writer and the scanner.

    Both sides import the single ``BROWSER_FRAMES_DIRNAME`` constant; this locks
    that they resolve to the same value so the workspace-changes ignore cannot
    silently drift away from where the browser tools write frames.
    """
    from deerflow.community.browser_automation import tools as browser_tools
    from deerflow.constants import BROWSER_FRAMES_DIRNAME
    from deerflow.workspace_changes.scanner import EXCLUDED_DIR_NAMES

    assert browser_tools._BROWSER_FRAMES_DIRNAME == BROWSER_FRAMES_DIRNAME
    assert BROWSER_FRAMES_DIRNAME in EXCLUDED_DIR_NAMES


def test_validate_browser_url_rejects_private_and_non_http(monkeypatch):
    """WS seed / navigate events reuse the same SSRF policy as the agent tools.

    With no ``allow_private_addresses`` override the shared validator must reject
    loopback / metadata / non-http targets, so the live stream cannot be steered
    at internal infrastructure.
    """
    import ipaddress

    from deerflow.community.browser_automation import tools as browser_tools
    from deerflow.community.browser_automation import validate_browser_url

    # Isolate from any local config.yaml that may set allow_private_addresses.
    monkeypatch.setattr(browser_tools, "_get_tool_config", lambda _tool_name: {})
    monkeypatch.setattr(browser_tools, "_resolve_host_addresses", lambda _: [ipaddress.ip_address("8.8.8.8")])

    assert validate_browser_url("http://169.254.169.254/latest/meta-data/") is not None
    assert validate_browser_url("http://127.0.0.1:8001/") is not None
    assert validate_browser_url("file:///etc/passwd") is not None
    assert validate_browser_url("ftp://example.com") is not None
    # A normal public URL passes (returns None = allowed).
    assert validate_browser_url("https://github.com/bytedance/deer-flow") is None


def test_live_seed_and_navigation_await_async_validation(monkeypatch):
    import deerflow.community.browser_automation as browser_api
    import deerflow.config as config

    store = SimpleNamespace(
        check_access=AsyncMock(return_value=True),
        get=AsyncMock(return_value={"thread_id": "thread-1", "user_id": "browser-user"}),
    )
    session = SimpleNamespace(
        current_url=AsyncMock(return_value="about:blank"),
        tabs=AsyncMock(return_value=[]),
        start_screencast=AsyncMock(),
        stop_screencast=AsyncMock(),
        navigate=AsyncMock(),
        dispatch_input=AsyncMock(),
    )
    manager = MagicMock()
    manager.acquire_session.return_value.__enter__.return_value = session
    validator = AsyncMock(return_value="Error: URL safety validation timed out; request blocked")
    monkeypatch.setattr(browser_api, "get_browser_session_manager", lambda: manager)
    monkeypatch.setattr(browser_api, "validate_browser_url_async", validator)
    monkeypatch.setattr(config, "get_app_config", lambda: SimpleNamespace(get_tool_config=lambda _: None))
    with (
        patch.object(browser_router, "_authenticate_ws", AsyncMock(return_value=_user())),
        patch.object(browser_router, "_browser_tools_enabled", return_value=True),
        TestClient(_browser_ws_app(store)) as client,
        client.websocket_connect("/api/threads/thread-1/browser/stream?seed=https://example.test/seed") as ws,
    ):
        assert ws.receive_json()["type"] == "url"
        assert ws.receive_json()["type"] == "tabs"
        ws.send_json({"type": "navigate", "url": "https://example.test/input"})
        assert ws.receive_json()["type"] == "nav_rejected"
    assert [call.args[0] for call in validator.await_args_list] == ["https://example.test/seed", "https://example.test/input"]
    session.navigate.assert_not_awaited()
    session.dispatch_input.assert_not_awaited()
