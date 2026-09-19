from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.gateway import dashboard_database
from app.gateway.routers import dashboard


@pytest.mark.asyncio
async def test_strict_replica_mode_disables_only_dashboard_without_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEER_FLOW_DASHBOARD_DATABASE_URL", raising=False)
    monkeypatch.setenv("DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA", "true")
    await dashboard_database.init_dashboard_read_database()
    monkeypatch.setattr(dashboard, "get_session_factory", lambda: object())
    with pytest.raises(HTTPException) as raised:
        dashboard._session_factory()
    assert raised.value.status_code == 503
    assert "未回退主数据库" in str(raised.value.detail)
    await dashboard_database.close_dashboard_read_database()


@pytest.mark.asyncio
async def test_strict_replica_mode_rejects_non_postgres_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEER_FLOW_DASHBOARD_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA", "true")
    await dashboard_database.init_dashboard_read_database()
    assert dashboard_database.get_dashboard_read_session_factory() is None
    assert dashboard_database.get_dashboard_read_database_unavailable_reason() == ("strict dashboard isolation requires PostgreSQL")
    await dashboard_database.close_dashboard_read_database()
