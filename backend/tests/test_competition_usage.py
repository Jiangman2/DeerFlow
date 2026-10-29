"""Machine feed: authentication, identical SQL counts, and Beijing midnight."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_employee_square_analytics import EMPTY, EXTERNAL, PUBLIC, USER
from test_employee_square_analytics import setup as analytics_setup  # noqa: F401

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.routers import competition_usage as feed
from deerflow.persistence.observability.model import AuditLogRow

KEY = "test-only-competition-key-not-a-production-secret"
URL = "/api/competition/agent-usage"
HEADERS = {"Authorization": "Bearer " + KEY}
NOW = datetime(2026, 10, 28, 0, 0, 1, tzinfo=feed.BEIJING)


@pytest.fixture()
def configured(request, monkeypatch):
    client, sf = request.getfixturevalue("analytics_setup")
    monkeypatch.setenv("COMPETITION_USAGE_API_KEY", KEY)
    monkeypatch.setenv("COMPETITION_USAGE_AGENTS_JSON", json.dumps({"制度助手": PUBLIC.selection_key, "外部员工": EXTERNAL.selection_key, "空库": EMPTY.selection_key}))
    monkeypatch.setattr(feed, "_now", lambda: NOW)
    feed._cache.clear()
    monkeypatch.setattr(feed, "_lock", asyncio.Lock())
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(feed.router)
    return TestClient(app), client, sf


def test_key_is_required_even_with_session_or_internal_identity(configured):
    client, _, _ = configured
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Cookie": "session=fake"}, {"X-Internal-Token": KEY}, {"Authorization": "Basic " + KEY}):
        assert client.get(URL, params={"name": "制度助手"}, headers=headers).status_code == 401
    assert client.get(URL, params={"name": "制度助手", "api_key": KEY}).status_code == 401
    assert client.get(URL + "/private").status_code == 401
    assert client.get(URL, params={"name": "空库"}, headers=HEADERS).json()["total_clicks"] == 0


def test_beijing_boundary_legacy_history_dashboard_parity_and_no_writes(configured):
    client, dashboard, sf = configured
    start = NOW.replace(second=0)

    async def seed():
        async with sf() as session:
            for when in (start - timedelta(microseconds=1), start, NOW - timedelta(microseconds=1)):
                session.add(
                    AuditLogRow(
                        user_id=USER, action=feed.analytics.CLICK_ACTION, resource_type="employee_square", resource_id=PUBLIC.selection_key, method="POST", path="/click", status_code=204, result="success", created_at=when.astimezone(UTC)
                    )
                )
            await session.commit()

    async def count():
        async with sf() as session:
            return await session.scalar(select(func.count()).select_from(AuditLogRow))

    asyncio.run(seed())
    before = asyncio.run(count())
    result = client.get(URL, params={"name": "制度助手"}, headers=HEADERS)
    assert result.status_code == 200 and result.headers["Cache-Control"] == "no-store"
    payload = result.json()
    assert payload["today_clicks"] == 2 and payload["updated_at"].endswith("+08:00")
    page = dashboard.get("/api/admin/dashboard/employee-clicks", params={"start_at": start.isoformat(), "end_at": NOW.isoformat()}, headers={"x-test-role": "admin"}).json()
    row = next(item for item in page["items"] if item["identity"] == PUBLIC.selection_key)
    assert (payload["today_clicks"], payload["total_clicks"]) == (row["clicks"], row["total_clicks"])
    assert client.get(URL, params={"name": "外部员工"}, headers=HEADERS).json()["total_clicks"] == 1
    assert asyncio.run(count()) == before
    assert set(payload) == {"agent_name", "total_clicks", "today_clicks", "updated_at"}


def test_unknown_withdrawn_database_failure_and_disabled_are_not_zero(configured, monkeypatch):
    client, _, _ = configured
    assert client.get(URL, headers=HEADERS).status_code == 422
    assert client.get(URL, params={"name": "未约定"}, headers=HEADERS).status_code == 404
    monkeypatch.setattr(feed.analytics, "_published_catalog", lambda: [])
    assert client.get(URL, params={"name": "制度助手"}, headers=HEADERS).status_code == 409
    monkeypatch.setattr(feed.analytics, "_published_catalog", lambda: [PUBLIC])

    def failed():
        raise RuntimeError("private database message")

    monkeypatch.setattr(feed.analytics, "_session_factory", failed)
    response = client.get(URL, params={"name": "制度助手"}, headers=HEADERS)
    assert response.status_code == 503 and "private database" not in response.text
    monkeypatch.delenv("COMPETITION_USAGE_API_KEY")
    assert client.get(URL, params={"name": "制度助手"}, headers=HEADERS).status_code == 503


def test_cache_expires_at_midnight_and_revocation_is_immediate(configured, monkeypatch):
    client, _, _ = configured
    first = client.get(URL, params={"name": "制度助手"}, headers=HEADERS).json()
    monkeypatch.setattr(feed, "_now", lambda: NOW + timedelta(days=1))
    second = client.get(URL, params={"name": "制度助手"}, headers=HEADERS).json()
    assert second["updated_at"] != first["updated_at"]
    monkeypatch.setenv("COMPETITION_USAGE_API_KEY", "replacement-test-key-that-has-at-least-thirty-two-characters")
    assert client.get(URL, params={"name": "制度助手"}, headers=HEADERS).status_code == 401
    monkeypatch.setenv("COMPETITION_USAGE_API_KEY", KEY)
    monkeypatch.setenv("COMPETITION_USAGE_AGENTS_JSON", "{}")
    assert client.get(URL, params={"name": "制度助手"}, headers=HEADERS).status_code == 503
