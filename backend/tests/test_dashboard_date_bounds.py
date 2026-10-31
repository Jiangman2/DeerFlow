"""Calendar bounds use retained dates without loading diagnostic payloads."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.dashboard_cache import dashboard_response_cache
from app.gateway.routers import dashboard
from deerflow.persistence.base import Base
from deerflow.persistence.dashboard_rollup.model import DashboardDailyRollupRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow


@pytest.mark.asyncio
async def test_bounds_empty_first_partial_day_and_retained_rollups(monkeypatch):
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dashboard, "_session_factory", lambda: sf)
    monkeypatch.setattr(dashboard, "dashboard_rollups_enabled", lambda: False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    statements = []
    event.listen(engine.sync_engine, "before_cursor_execute", lambda _c, _u, statement, _p, _x, _e: statements.append(statement))
    try:
        empty = await dashboard._build_dashboard_date_bounds()
        assert empty.earliest_at is None and empty.selectable_start_at is None
        async with sf() as session:
            session.add_all(
                [
                    ThreadCreationRow(thread_id="first", user_id="user", created_at=datetime(2026, 9, 30, 17, 10, 25, tzinfo=UTC)),
                    ThreadCreationRow(thread_id="future", user_id="user", created_at=datetime.now(UTC) + timedelta(days=2)),
                ]
            )
            await session.commit()
        bounds = await dashboard._build_dashboard_date_bounds()
        assert bounds.earliest_at == datetime(2026, 9, 30, 17, 10, 25, tzinfo=UTC)
        assert bounds.selectable_start_at == datetime(2026, 9, 30, 16, tzinfo=UTC)
        async with sf() as session:
            await session.execute(delete(ThreadCreationRow).where(ThreadCreationRow.thread_id == "first"))
            session.add_all(
                [
                    DashboardDailyRollupRow(day=date(2026, 9, 1), schema_version=dashboard.ROLLUP_SCHEMA_VERSION, source_run_count=1),
                    DashboardDailyRollupRow(day=date(2020, 1, 1), schema_version=dashboard.ROLLUP_SCHEMA_VERSION),
                ]
            )
            await session.commit()
        monkeypatch.setattr(dashboard, "dashboard_rollups_enabled", lambda: True)
        retained = await dashboard._build_dashboard_date_bounds()
        assert retained.selectable_start_at == datetime(2026, 8, 31, 16, tzinfo=UTC)
        assert not any("SELECT " in s and any(field in s for field in ("payload_json", "content", "details_json")) for s in statements)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bounds_endpoint_is_admin_only_cached_and_failure_is_recoverable(monkeypatch):
    async def admin(_request, **_kwargs):
        return None

    monkeypatch.setattr(dashboard, "require_admin_user", admin)
    producer = AsyncMock(return_value=dashboard.DashboardDateBounds(earliest_at=None, selectable_start_at=None))
    monkeypatch.setattr(dashboard, "_build_dashboard_date_bounds", producer)
    app = FastAPI()
    app.include_router(dashboard.router)
    await dashboard_response_cache.clear()
    try:
        with TestClient(app) as client:
            for _ in range(2):
                response = client.get("/api/admin/dashboard/date-bounds")
                assert response.status_code == 200
                assert response.json()["selectable_start_at"] is None
                assert response.headers["Cache-Control"] == "private, no-store"
            assert producer.await_count == 1
        await dashboard_response_cache.clear()
        producer.side_effect = TimeoutError("private database details")
        with TestClient(app) as client:
            response = client.get("/api/admin/dashboard/date-bounds")
            assert response.status_code == 503
            assert "private" not in response.text
    finally:
        await dashboard_response_cache.clear()


@pytest.mark.parametrize("authenticated,expected", [(False, 401), (True, 403)])
def test_date_bounds_never_queries_database_for_non_admin(monkeypatch, authenticated, expected):
    producer = AsyncMock()
    monkeypatch.setattr(dashboard, "_build_dashboard_date_bounds", producer)
    app = make_authed_test_app(user_factory=lambda: User(id=UUID(int=1), email="user@example.com", password_hash="x", system_role="user")) if authenticated else FastAPI()
    app.include_router(dashboard.router)
    with TestClient(app) as client:
        assert client.get("/api/admin/dashboard/date-bounds").status_code == expected
    producer.assert_not_awaited()
