"""Internal checkpoint reservations never count as enterprise user tasks."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from test_dashboard_metrics import ADMIN_ID, USER_ID, _client, _session_factory

from app.gateway.dashboard_cache import dashboard_response_cache
from app.gateway.dashboard_rollups import _source_min_timestamp, build_raw_contribution, refresh_rollup_day, refresh_rollup_hour
from app.gateway.routers.admin_users import _load_counts
from deerflow.persistence.dashboard_rollup.model import DashboardDailyRollupRow, DashboardHourlyRollupRow
from deerflow.persistence.observability.sql import ObservabilityRepository
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

pytestmark = pytest.mark.asyncio


async def test_enterprise_metrics_exclude_internal_rows_without_losing_threads(monkeypatch):
    sf, engine = await _session_factory()
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)
    await dashboard_response_cache.clear()
    monkeypatch.setenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", "false")
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@test", system_role="admin"),
                    UserRow(id=USER_ID, email="owner@test", system_role="user"),
                    ThreadMetaRow(thread_id="chat", user_id=USER_ID),
                    ThreadMetaRow(thread_id="no-runs", user_id=USER_ID),
                    RunRow(run_id="real", thread_id="chat", user_id=USER_ID, status="success", total_tokens=7, created_at=now, updated_at=now + timedelta(seconds=3)),
                    RunRow(run_id="internal", thread_id="chat", user_id=USER_ID, status="error", error="not a user failure", operation_kind="checkpoint_write", total_tokens=999, created_at=now - timedelta(minutes=5), updated_at=now),
                    RunRow(run_id="internal-only", thread_id="no-runs", user_id=USER_ID, status="error", operation_kind="checkpoint_write", created_at=now, updated_at=now),
                ]
            )
            await session.commit()
        repo = ObservabilityRepository(sf)
        snapshot = await repo.snapshot(hours=24)
        assert snapshot["metrics"]["runs_total"] == snapshot["metrics"]["runs_window"] == 1
        assert snapshot["metrics"]["tokens_window"] == 7
        assert snapshot["recent_errors"] == []
        assert set(snapshot["runs_by_status"]) == {"success"}
        assert await repo.run_diagnostics("internal") is None
        assert [row["run_id"] for row in await repo.list_traces(hours=24)] == ["real"]
        assert (await repo.analytics(hours=24))["current"]["runs"] == 1
        async with sf() as session:
            thread_counts, run_counts = await _load_counts(session, [USER_ID])
            assert thread_counts == {USER_ID: 2} and run_counts == {USER_ID: 1}
            raw = await build_raw_contribution(session, start=now - timedelta(days=1), end=now + timedelta(days=1))
            assert raw.source_run_count == 1
            assert await session.get(RunRow, "internal") is not None  # retained recovery evidence
        with _client(sf, monkeypatch) as client:
            users = client.get("/api/admin/dashboard/users").json()["items"]
            owner = next(row for row in users if row["user_id"] == USER_ID)
            assert owner["runs"] == 1 and owner["existing_threads"] == 2
            assert owner["failed_runs"] == 0
            sessions = client.get(f"/api/admin/dashboard/users/{USER_ID}/sessions").json()["items"]
            assert {row["thread_id"]: row["runs"] for row in sessions} == {"chat": 1, "no-runs": 0}
            failures = client.get("/api/admin/dashboard/failures")
            assert failures.status_code == 200, failures.text
            assert failures.json()["total"] == 0
            rankings = client.get("/api/admin/dashboard/run-rankings", params={"hours": 24})
            assert rankings.status_code == 200, rankings.text
            assert "internal" not in rankings.text
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()


async def test_internal_only_storage_does_not_expand_rollup_coverage():
    sf, engine = await _session_factory()
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    try:
        async with sf() as session:
            session.add(RunRow(run_id="internal", thread_id="chat", operation_kind="checkpoint_write", status="error", created_at=now, updated_at=now))
            await session.commit()
            assert await _source_min_timestamp(session) is None
        await refresh_rollup_day(sf, now.date())
        await refresh_rollup_hour(sf, now)
        async with sf() as session:
            daily = (await session.scalars(select(DashboardDailyRollupRow))).all()
            hourly = (await session.scalars(select(DashboardHourlyRollupRow))).all()
            assert len(daily) == len(hourly) == 1
            assert daily[0].source_run_count == hourly[0].source_run_count == 0
    finally:
        await engine.dispose()
