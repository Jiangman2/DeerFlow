"""Current-user lifetime usage, including active/failed runs and legacy totals."""

from unittest.mock import AsyncMock

from app.gateway.routers import console

pytest_plugins = ["test_console_router"]


def test_lifetime_summary_is_always_scoped_to_current_user(client, monkeypatch):
    monkeypatch.setattr(console, "get_current_user", AsyncMock(return_value="user-a"))
    response = client.get("/api/console/token-summary?user_id=user-b")
    assert response.status_code == 200
    data = response.json()
    assert data["total_tokens"] == 2549
    assert data["input_tokens"] == 1050
    assert data["output_tokens"] == 500
    assert data["unclassified_tokens"] == 999
    assert data["run_count"] == 4
    assert data["scope"] == "current_user_recorded_platform_runs"
    assert "user-b" not in response.text


def test_missing_identity_never_falls_back_to_all_users(client, monkeypatch):
    monkeypatch.setattr(console, "get_current_user", AsyncMock(return_value=None))
    assert client.get("/api/console/token-summary").status_code == 401


def test_other_user_only_receives_their_own_usage(client, monkeypatch):
    monkeypatch.setattr(console, "get_current_user", AsyncMock(return_value="user-b"))
    data = client.get("/api/console/token-summary").json()
    assert data["total_tokens"] == 70
    assert data["input_tokens"] == 40
    assert data["output_tokens"] == 30
    assert data["unclassified_tokens"] == 0


def test_no_usage_is_zero_but_unavailable_storage_is_not(client, monkeypatch):
    monkeypatch.setattr(console, "get_current_user", AsyncMock(return_value="new-user"))
    data = client.get("/api/console/token-summary").json()
    assert data["total_tokens"] == 0 and data["run_count"] == 0
    assert data["recorded_since"] is None
    monkeypatch.setattr(console, "get_session_factory", lambda: None)
    assert client.get("/api/console/token-summary").status_code == 503
