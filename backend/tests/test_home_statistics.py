from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.dashboard_cache import DashboardResponseCache
from app.gateway.routers import dashboard, home_statistics
from deerflow.persistence.base import Base
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow


def _client(monkeypatch, authenticated=True):
    app = FastAPI()
    if authenticated:

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.user = User(id=uuid4(), email="user@example.com", password_hash="x", system_role="user")
            request.state.auth_source = "session"
            return await call_next(request)

    app.include_router(home_statistics.router)
    app.include_router(dashboard.router)
    monkeypatch.setattr(home_statistics, "dashboard_response_cache", DashboardResponseCache())
    return TestClient(app)


def test_home_statistics_requires_login_before_querying(monkeypatch):
    def forbidden():
        pytest.fail("Anonymous request must not query the database")

    monkeypatch.setattr(dashboard, "_session_factory", forbidden)
    assert _client(monkeypatch, False).get("/api/home/statistics").status_code == 401


def test_today_window_uses_shanghai_midnight_and_dashboard_minute_boundary():
    start, end = home_statistics._today_window(datetime(2026, 10, 9, 16, 5, 42, tzinfo=UTC))
    assert start == datetime(2026, 10, 9, 16, tzinfo=UTC)
    assert end == datetime(2026, 10, 9, 16, 5, tzinfo=UTC)


@pytest.mark.asyncio
async def test_today_counts_match_dashboard_include_failed_running_deleted_history(monkeypatch):
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 10, 9, 16, tzinfo=UTC)
    end = start + timedelta(hours=8)
    monkeypatch.setattr(dashboard, "_session_factory", lambda: sf)
    monkeypatch.setattr(home_statistics, "_today_window", lambda: (start, end))
    monkeypatch.setattr(dashboard, "dashboard_rollups_enabled", lambda: False)
    try:
        async with sf() as session:
            session.add_all(
                [
                    RunRow(run_id="old", thread_id="gone-old", user_id="old", created_at=start - timedelta(seconds=1)),
                    RunRow(run_id="a", thread_id="gone-a", user_id="a", status="success", created_at=start),
                    RunRow(run_id="b", thread_id="gone-b", user_id="a", status="error", created_at=start),
                    RunRow(run_id="c", thread_id="gone-c", user_id="b", status="running", created_at=end),
                    RunRow(run_id="anonymous", thread_id="gone-anon", user_id=None, created_at=start),
                    RunRow(run_id="future", thread_id="gone-future", user_id="future", created_at=end + timedelta(seconds=1)),
                    ThreadCreationRow(thread_id="new", user_id="c", created_at=start),
                    ThreadCreationRow(thread_id="duplicate", user_id="a", created_at=start),
                ]
            )
            await session.commit()
        client = _client(monkeypatch)
        response = client.get("/api/home/statistics")
        assert response.status_code == 200
        assert response.json() == {"todayUsers": 3, "todayTasks": 4}
        assert response.headers["cache-control"] == "private, no-store"
        assert client.get("/api/admin/dashboard/summary").status_code == 403
        full = await dashboard._build_dashboard_summary(
            start=start, end=end, window_hours=8, limit=10, status=None, model=None, agent=None, feature=None, value_profile="conservative", include_knowledge=False, include_business_details=False
        )
        assert response.json() == {"todayUsers": full.overview.active_users, "todayTasks": full.overview.runs}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_rollups_use_same_deduplicated_contribution_as_dashboard(monkeypatch):
    from app.gateway.dashboard_rollups import DashboardContribution, RunDimensionMetric

    contribution = DashboardContribution()
    day = datetime(2026, 10, 10).date()
    contribution.dimensions[(day, "a", "default", "model", "success")] = RunDimensionMetric(runs=2)
    contribution.dimensions[(day, "a", "default", "model", "error")] = RunDimensionMetric(runs=1)
    contribution.thread_creation_users[day].update({"a", "b"})
    monkeypatch.setattr(dashboard, "dashboard_rollups_enabled", lambda: True)
    monkeypatch.setattr(dashboard, "_session_factory", lambda: None)

    async def load(*args, **kwargs):
        return SimpleNamespace(contribution=contribution)

    monkeypatch.setattr(home_statistics, "load_hybrid_contribution", load)
    result = _client(monkeypatch).get("/api/home/statistics")
    assert result.status_code == 200
    assert result.json() == {"todayUsers": 2, "todayTasks": 3}
