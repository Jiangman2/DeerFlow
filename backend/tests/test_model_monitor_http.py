"""Exercise the browser's actual query-string boundary without SQL or Redis."""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.gateway.dashboard_cache import DashboardResponseCache
from app.gateway.routers import model_monitor as router


@pytest.fixture
def monitor_http(monkeypatch):
    auth = AsyncMock(return_value=object())
    read = AsyncMock(return_value={"status": "observed", "series": [], "trend": [], "reporting_processes": 70})
    monkeypatch.setattr(router, "require_admin_user", auth)
    monkeypatch.setattr(router.model_monitor, "read", read)
    monkeypatch.setattr(router, "_cache", DashboardResponseCache(max_entries=3))
    app = FastAPI()
    app.include_router(router.router)
    with TestClient(app) as client:
        yield client, read, auth


@pytest.mark.parametrize(("query", "window"), [("", 15), ("?minutes=15", 15), ("?minutes=60", 60)])
def test_browser_query_string_reaches_bounded_monitor(monitor_http, query, window):
    client, read, auth = monitor_http
    response = client.get("/api/admin/model-monitor" + query)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["reporting_processes"] == 70
    read.assert_awaited_once_with(window)
    auth.assert_awaited_once()


@pytest.mark.parametrize("value", ["", "0", "16", "59", "61", "1440", "15.0", "abc", "-15"])
def test_unsupported_windows_never_read_metrics(monitor_http, value):
    client, read, _ = monitor_http
    assert client.get("/api/admin/model-monitor", params={"minutes": value}).status_code == 422
    read.assert_not_awaited()


def test_both_cards_share_cache_but_windows_remain_separate(monitor_http):
    client, read, auth = monitor_http
    for window in (15, 15, 60, 60):
        assert client.get("/api/admin/model-monitor", params={"minutes": window}).status_code == 200
    assert [call.args for call in read.await_args_list] == [(15,), (60,)]
    assert auth.await_count == 4


@pytest.mark.parametrize("code", [401, 403])
def test_auth_denial_still_precedes_monitor_and_cache(monitor_http, code):
    client, read, auth = monitor_http
    auth.side_effect = HTTPException(code, "denied")
    assert client.get("/api/admin/model-monitor?minutes=15").status_code == code
    read.assert_not_awaited()
