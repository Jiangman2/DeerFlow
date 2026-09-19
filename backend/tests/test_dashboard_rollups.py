from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth.models import User
from app.gateway.dashboard_cache import dashboard_response_cache
from app.gateway.dashboard_rollups import (
    DashboardContribution,
    business_day_bounds,
    business_hour_floor,
    fully_covered_business_hours,
    load_external_platform_rollup_summary,
    load_hybrid_contribution,
    refresh_changed_rollup_days,
    refresh_changed_rollup_hours,
    refresh_rollup_day,
    refresh_rollup_hour,
)
from app.gateway.routers import dashboard
from deerflow.persistence.base import Base
from deerflow.persistence.dashboard_rollup.model import (
    DashboardDailyRollupRow,
    DashboardHourlyRollupRow,
    DashboardRollupStateRow,
)
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.observability.model import AuditLogRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow
from deerflow.persistence.user.model import UserRow

pytestmark = pytest.mark.asyncio

ADMIN_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = "99999999-8888-7777-6666-555555555555"


def _admin_user() -> User:
    return User(
        id=UUID(ADMIN_ID),
        email="admin@example.com",
        password_hash="x",
        system_role="admin",
    )


async def _database():
    import deerflow.persistence.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _client(sf, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = make_authed_test_app(user_factory=_admin_user)
    app.include_router(dashboard.router)
    monkeypatch.setattr(dashboard, "get_session_factory", lambda: sf)
    return TestClient(app)


async def test_refresh_day_is_atomic_and_idempotent() -> None:
    sf, engine = await _database()
    day = date(2026, 1, 3)
    start, _ = business_day_bounds(day)
    try:
        async with sf() as session:
            session.add_all(
                [
                    ThreadCreationRow(thread_id="thread-1", user_id=USER_ID, created_at=start + timedelta(hours=1)),
                    ThreadMetaRow(
                        thread_id="thread-1",
                        user_id=USER_ID,
                        assistant_id="geology",
                        display_name="地质研究专家",
                        status="idle",
                        created_at=start + timedelta(hours=1),
                    ),
                    RunRow(
                        run_id="run-1",
                        thread_id="thread-1",
                        user_id=USER_ID,
                        assistant_id="geology",
                        model_name="model-a",
                        status="success",
                        total_tokens=120,
                        llm_call_count=2,
                        created_at=start + timedelta(hours=2),
                        updated_at=start + timedelta(hours=2, seconds=10),
                    ),
                    FeedbackRow(
                        feedback_id="feedback-1",
                        run_id="run-1",
                        thread_id="thread-1",
                        user_id=USER_ID,
                        rating=1,
                        created_at=start + timedelta(hours=3),
                    ),
                ]
            )
            await session.commit()

        first = await refresh_rollup_day(sf, day)
        second = await refresh_rollup_day(sf, day)
        assert first.status == "refreshed"
        assert second.status == "refreshed"
        async with sf() as session:
            assert await session.scalar(select(func.count()).select_from(DashboardDailyRollupRow)) == 1
            row = await session.get(DashboardDailyRollupRow, day)
            assert row is not None
            assert row.source_run_count == 1
            assert row.payload_json["feedback_positive"] == 1
            assert row.payload_json["thread_creations_by_user"] == [{"user_id": USER_ID, "threads": 1}]
    finally:
        await engine.dispose()


async def test_large_uncovered_window_fails_fast_without_raw_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _database()
    await dashboard_response_cache.clear()
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.casefold())

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with sf() as session:
            session.add(UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"))
            await session.commit()
        statements.clear()
        start, _ = business_day_bounds(date(2026, 1, 1))
        _, end = business_day_bounds(date(2026, 1, 20))
        monkeypatch.setenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", "true")
        with _client(sf, monkeypatch) as client:
            response = client.get(
                "/api/admin/dashboard/summary",
                params={
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "include_knowledge": "false",
                    "include_business_details": "false",
                },
            )

        assert response.status_code == 503
        assert response.headers["Retry-After"] == "60"
        assert "未执行大范围明细扫描" in response.json()["detail"]
        assert not any("from runs" in statement for statement in statements)
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()


async def test_compatibility_mode_keeps_uncovered_history_queryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _database()
    await dashboard_response_cache.clear()
    monkeypatch.delenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", raising=False)
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.casefold())

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        start, _ = business_day_bounds(date(2026, 1, 1))
        _, end = business_day_bounds(date(2026, 1, 20))
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    RunRow(
                        run_id="compatibility-run",
                        thread_id="compatibility-thread",
                        user_id=USER_ID,
                        status="success",
                        total_tokens=12,
                        created_at=start + timedelta(days=1),
                        updated_at=start + timedelta(days=1, seconds=1),
                    ),
                    AuditLogRow(
                        created_at=start + timedelta(days=1, hours=1),
                        user_id=USER_ID,
                        user_email="user@example.com",
                        action="external_agent.remote_click",
                        resource_type="external_agent",
                        resource_id="自动数采专家",
                        method="POST",
                        path="/api/agent_click",
                        status_code=200,
                        result="success",
                        duration_ms=1,
                        details_json={"source": "海能平台"},
                    ),
                ]
            )
            await session.commit()
        statements.clear()

        with _client(sf, monkeypatch) as client:
            response = client.get(
                "/api/admin/dashboard/summary",
                params={
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "include_knowledge": "false",
                    "include_business_details": "false",
                },
            )

        assert response.status_code == 200
        assert response.headers["X-Dashboard-Data-Source"] == "raw"
        assert response.json()["overview"]["runs"] == 1
        external_agent = next(item for item in response.json()["external_platform_agent_clicks"]["agents"] if item["name"] == "自动数采专家")
        assert response.json()["external_platform_agent_clicks"]["available"] is True
        assert external_agent["total_clicks"] == 1
        assert any("from runs" in statement for statement in statements)
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()


async def test_large_ready_window_reads_rollups_without_runs_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _database()
    await dashboard_response_cache.clear()
    first_day = date(2026, 1, 1)
    last_day = first_day + timedelta(days=13)
    run_start, _ = business_day_bounds(first_day + timedelta(days=4))
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    UserRow(id=USER_ID, email="user@example.com", display_name="勘探用户", system_role="user"),
                    ThreadCreationRow(thread_id="thread-1", user_id=USER_ID, created_at=run_start),
                    ThreadMetaRow(
                        thread_id="thread-1",
                        user_id=USER_ID,
                        assistant_id="geology",
                        display_name="地质研究专家",
                        status="idle",
                        created_at=run_start,
                    ),
                    RunRow(
                        run_id="run-1",
                        thread_id="thread-1",
                        user_id=USER_ID,
                        assistant_id="geology",
                        model_name="model-a",
                        status="success",
                        total_tokens=99,
                        llm_call_count=3,
                        created_at=run_start,
                        updated_at=run_start + timedelta(seconds=5),
                    ),
                ]
            )
            await session.commit()
        for offset in range(14):
            result = await refresh_rollup_day(sf, first_day + timedelta(days=offset))
            assert result.status == "refreshed"

        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.casefold())

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        start, _ = business_day_bounds(first_day)
        _, end = business_day_bounds(last_day)
        monkeypatch.setenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", "true")
        with _client(sf, monkeypatch) as client:
            response = client.get(
                "/api/admin/dashboard/summary",
                params={
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "include_knowledge": "false",
                    "include_business_details": "false",
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert response.headers["X-Dashboard-Data-Source"] == "rollup"
        assert payload["aggregation"] == {
            "source": "rollup",
            "schema_version": 1,
            "rollup_days": 14,
            "rollup_hours": 0,
            "raw_segments": 0,
        }
        assert payload["overview"]["runs"] == 1
        assert payload["overview"]["threads"] == 1
        assert payload["overview"]["total_tokens"] == 99
        assert payload["user_usage"][0]["threads"] == 1
        assert not any("from runs" in statement for statement in statements)

        statements.clear()
        with _client(sf, monkeypatch) as client:
            rankings = client.get(
                "/api/admin/dashboard/run-rankings",
                params={"start_at": start.isoformat(), "end_at": end.isoformat()},
            )
        assert rankings.status_code == 200
        assert rankings.json()["slow_runs"][0]["run_id"] == "run-1"
        assert rankings.json()["high_token_runs"][0]["run_id"] == "run-1"
        assert not any("from runs" in statement for statement in statements)
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()


async def test_hybrid_loader_merges_only_edge_raw_segments() -> None:
    sf, engine = await _database()
    day = date(2026, 1, 8)
    start, end = business_day_bounds(day)
    try:
        contribution = DashboardContribution()
        async with sf() as session:
            session.add(
                DashboardDailyRollupRow(
                    day=day,
                    schema_version=1,
                    payload_json=contribution.to_daily_payload(day),
                    refreshed_at=datetime.now(UTC),
                )
            )
            await session.commit()
        selection = await load_hybrid_contribution(
            sf,
            start=start - timedelta(hours=1),
            end=end + timedelta(hours=1),
            minimum_rollup_days=1,
        )
        assert selection.source == "rollup"
        assert selection.rollup_days == 1
        assert selection.raw_segments == (
            (start - timedelta(hours=1), start),
            (end, end + timedelta(hours=1)),
        )
    finally:
        await engine.dispose()


async def test_run_rankings_use_database_top_n_without_event_materialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _database()
    await dashboard_response_cache.clear()
    started = datetime(2026, 2, 1, tzinfo=UTC)
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.casefold())

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    RunRow(
                        run_id="slow",
                        thread_id="slow-thread",
                        status="success",
                        total_tokens=1,
                        created_at=started,
                        updated_at=started + timedelta(minutes=5),
                    ),
                    RunRow(
                        run_id="tokens",
                        thread_id="token-thread",
                        status="success",
                        total_tokens=900,
                        created_at=started + timedelta(hours=1),
                        updated_at=started + timedelta(hours=1, seconds=1),
                    ),
                ]
            )
            await session.commit()
        statements.clear()
        with _client(sf, monkeypatch) as client:
            response = client.get(
                "/api/admin/dashboard/run-rankings",
                params={
                    "start_at": started.isoformat(),
                    "end_at": (started + timedelta(days=1)).isoformat(),
                    "limit": 1,
                },
            )
        assert response.status_code == 200
        assert response.json()["slow_runs"][0]["run_id"] == "slow"
        assert response.json()["high_token_runs"][0]["run_id"] == "tokens"
        assert sum("from runs" in statement for statement in statements) == 2
        assert not any("run_events" in statement for statement in statements)
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()


async def test_external_platform_totals_use_daily_facts_not_historical_audit_scan() -> None:
    sf, engine = await _database()
    day = date(2026, 1, 2)
    start, _ = business_day_bounds(day)
    try:
        async with sf() as session:
            session.add(
                AuditLogRow(
                    created_at=start + timedelta(hours=1),
                    user_id="external-user",
                    action="external_agent.remote_click",
                    resource_type="external_agent",
                    resource_id="自动数采专家",
                    method="POST",
                    path="/api/agent_click",
                    status_code=200,
                    result="success",
                    duration_ms=1,
                    details_json={"source": "海能平台"},
                )
            )
            await session.commit()
        assert (await refresh_rollup_day(sf, day)).status == "refreshed"

        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.casefold())

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        result = await load_external_platform_rollup_summary(
            sf,
            now=datetime(2026, 1, 3, 4, tzinfo=UTC),
        )
        assert result is not None
        auto_collection = next(item for item in result["agents"] if item["name"] == "自动数采专家")
        assert auto_collection["total_clicks"] == 1
        assert auto_collection["total_users"] == 1
        assert not any("audit_logs.details_json" in statement and "created_at >=" not in statement for statement in statements)
    finally:
        await engine.dispose()


async def test_late_change_backlog_processes_only_the_configured_days() -> None:
    sf, engine = await _database()
    day_one = date(2026, 1, 6)
    day_two = date(2026, 1, 7)
    try:
        async with sf() as session:
            session.add(
                DashboardRollupStateRow(
                    key="daily-dashboard-v1",
                    schema_version=1,
                    last_change_scan_at=datetime.now(UTC),
                    pending_days_json=[day_one.isoformat(), day_two.isoformat()],
                    updated_at=datetime.now(UTC),
                )
            )
            await session.commit()

        first = await refresh_changed_rollup_days(sf, max_days=1)
        assert [result.day for result in first] == [day_one]
        async with sf() as session:
            state = await session.get(DashboardRollupStateRow, "daily-dashboard-v1")
            assert state is not None
            assert state.pending_days_json == [day_two.isoformat()]

        second = await refresh_changed_rollup_days(sf, max_days=1)
        assert [result.day for result in second] == [day_two]
        async with sf() as session:
            state = await session.get(DashboardRollupStateRow, "daily-dashboard-v1")
            assert state is not None
            assert state.pending_days_json == []
    finally:
        await engine.dispose()


async def test_24_hour_window_reads_hourly_facts_without_full_window_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _database()
    await dashboard_response_cache.clear()
    end = business_hour_floor(datetime.now(UTC))
    start = end - timedelta(hours=24)
    run_at = start + timedelta(hours=5, minutes=10)
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    RunRow(
                        run_id="hourly-run",
                        thread_id="hourly-thread",
                        user_id=USER_ID,
                        status="success",
                        total_tokens=77,
                        created_at=run_at,
                        updated_at=run_at + timedelta(seconds=3),
                    ),
                ]
            )
            await session.commit()
        covered = fully_covered_business_hours(start, end)
        assert len(covered) == 24
        for bucket_start in covered:
            result = await refresh_rollup_hour(sf, bucket_start, as_of=end)
            assert result.status == "refreshed"

        statements: list[str] = []

        def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.casefold())

        event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
        monkeypatch.setenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", "true")
        monkeypatch.setenv("DEER_FLOW_DASHBOARD_REQUIRE_HOURLY_ROLLUPS", "true")
        with _client(sf, monkeypatch) as client:
            response = client.get(
                "/api/admin/dashboard/summary",
                params={
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "include_knowledge": "false",
                    "include_business_details": "false",
                },
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["aggregation"] == {
            "source": "hourly_rollup",
            "schema_version": 1,
            "rollup_days": 0,
            "rollup_hours": 24,
            "raw_segments": 0,
        }
        assert payload["overview"]["runs"] == 1
        assert payload["overview"]["total_tokens"] == 77
        assert not any("from runs" in statement for statement in statements)
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()


async def test_required_hourly_window_fails_fast_when_fact_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sf, engine = await _database()
    await dashboard_response_cache.clear()
    end = business_hour_floor(datetime.now(UTC))
    start = end - timedelta(hours=24)
    statements: list[str] = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.casefold())

    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        async with sf() as session:
            session.add(UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"))
            await session.commit()
        statements.clear()
        monkeypatch.setenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", "true")
        monkeypatch.setenv("DEER_FLOW_DASHBOARD_REQUIRE_HOURLY_ROLLUPS", "true")
        with _client(sf, monkeypatch) as client:
            response = client.get(
                "/api/admin/dashboard/summary",
                params={
                    "start_at": start.isoformat(),
                    "end_at": end.isoformat(),
                    "include_knowledge": "false",
                    "include_business_details": "false",
                },
            )
        assert response.status_code == 503
        assert "小时" in response.json()["detail"]
        assert not any("from runs" in statement for statement in statements)
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()


async def test_hourly_late_change_backlog_is_bounded() -> None:
    sf, engine = await _database()
    as_of = business_hour_floor(datetime.now(UTC))
    hour_one = as_of - timedelta(hours=2)
    hour_two = as_of - timedelta(hours=1)
    try:
        async with sf() as session:
            session.add(
                DashboardRollupStateRow(
                    key="hourly-dashboard-v1",
                    schema_version=1,
                    last_change_scan_at=as_of,
                    pending_days_json=[hour_one.isoformat(), hour_two.isoformat()],
                    updated_at=as_of,
                )
            )
            await session.commit()
        first = await refresh_changed_rollup_hours(sf, max_hours=1, as_of=as_of)
        assert [result.bucket_start for result in first] == [hour_two]
        async with sf() as session:
            assert await session.scalar(select(func.count()).select_from(DashboardHourlyRollupRow)) == 1
            state = await session.get(DashboardRollupStateRow, "hourly-dashboard-v1")
            assert state is not None
            assert state.pending_days_json == [hour_one.isoformat()]
    finally:
        await engine.dispose()


async def test_rollup_maintenance_reads_replica_and_only_upserts_primary() -> None:
    source_sf, source_engine = await _database()
    target_sf, target_engine = await _database()
    as_of = business_hour_floor(datetime.now(UTC))
    bucket_start = as_of - timedelta(hours=1)
    source_statements: list[str] = []
    target_statements: list[str] = []

    def record_source(_conn, _cursor, statement, _parameters, _context, _executemany):
        source_statements.append(statement.casefold())

    def record_target(_conn, _cursor, statement, _parameters, _context, _executemany):
        target_statements.append(statement.casefold())

    event.listen(source_engine.sync_engine, "before_cursor_execute", record_source)
    event.listen(target_engine.sync_engine, "before_cursor_execute", record_target)
    try:
        async with source_sf() as session:
            session.add(
                RunRow(
                    run_id="replica-source-run",
                    thread_id="replica-source-thread",
                    status="success",
                    total_tokens=12,
                    created_at=bucket_start + timedelta(minutes=5),
                    updated_at=bucket_start + timedelta(minutes=6),
                )
            )
            await session.commit()
        source_statements.clear()
        target_statements.clear()
        result = await refresh_rollup_hour(
            target_sf,
            bucket_start,
            as_of=as_of,
            source_session_factory=source_sf,
        )
        assert result.status == "refreshed"
        assert any("from runs" in statement for statement in source_statements)
        assert not any("dashboard_hourly_rollups" in statement and "insert" in statement for statement in source_statements)
        assert any("insert into dashboard_hourly_rollups" in statement for statement in target_statements)
        assert not any("from runs" in statement for statement in target_statements)
    finally:
        await source_engine.dispose()
        await target_engine.dispose()
