"""Month-sized dashboard reads on a disposable PostgreSQL database only."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, insert, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.dashboard_cache import dashboard_response_cache
from app.gateway.routers import dashboard
from deerflow.persistence.base import Base
from deerflow.persistence.observability.model import AuditLogRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow
from deerflow.persistence.user.model import UserRow

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_month_with_more_than_driver_parameter_limit_preserves_totals(monkeypatch):
    url = os.environ.get("CHANNEL_RECOVERY_TEST_POSTGRES")
    if not url:
        pytest.skip("isolated PostgreSQL test database not requested")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost", "portal-incident-pg"}
    assert parsed.database == "incident_test", "never use an application database"
    schema = "dashboard_test_" + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}})
    sf = async_sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 10, 1, 3, 4, tzinfo=UTC)
    end = start + timedelta(days=30)
    count = 33_000
    parameter_counts = []
    monkeypatch.setattr(dashboard, "_session_factory", lambda: sf)
    monkeypatch.delenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", raising=False)
    await dashboard_response_cache.clear()
    try:
        import deerflow.persistence.models  # noqa: F401

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            for offset in range(0, count, 1000):
                await conn.execute(insert(ThreadCreationRow), [{"thread_id": f"thread-{n}", "user_id": f"owner-{n}", "created_at": start + timedelta(days=n % 30)} for n in range(offset, min(offset + 1000, count))])
                await conn.execute(
                    insert(ScheduledTaskRunRow),
                    [
                        {"id": f"task-run-{n}", "task_id": f"deleted-{n}", "thread_id": f"thread-{n}", "scheduled_for": start, "trigger": "scheduled", "status": "success", "created_at": start + timedelta(days=1)}
                        for n in range(offset, min(offset + 1000, count))
                    ],
                )
            await conn.execute(insert(UserRow), {"id": "owner", "email": "owner@example.test", "display_name": "Synthetic owner"})
            await conn.execute(
                insert(AuditLogRow),
                {
                    "user_id": "owner",
                    "user_email": "owner@example.test",
                    "action": "scheduled_task.create",
                    "resource_id": f"deleted-{count - 1}",
                    "resource_type": "scheduled_task",
                    "created_at": start - timedelta(days=10),
                    "method": "POST",
                    "path": "/api/scheduled-tasks",
                    "status_code": 200,
                    "result": "success",
                },
            )
            await conn.execute(insert(ThreadMetaRow), {"thread_id": f"thread-{count - 1}", "user_id": "owner", "assistant_id": "test-agent", "display_name": "Synthetic agent", "created_at": start})
            await conn.execute(
                insert(RunRow),
                {
                    "run_id": "run",
                    "thread_id": f"thread-{count - 1}",
                    "user_id": "owner",
                    "status": "success",
                    "model_name": "test-model",
                    "total_tokens": 17,
                    "llm_call_count": 1,
                    "created_at": start + timedelta(days=29),
                    "updated_at": end,
                },
            )
            # The range remains authoritative; outside history must not leak in.
            await conn.execute(insert(ThreadCreationRow), {"thread_id": "outside", "user_id": "owner", "created_at": start - timedelta(days=1)})

        def record(_conn, _cursor, _statement, parameters, _context, _executemany):
            parameter_counts.append(len(parameters))

        event.listen(engine.sync_engine, "before_cursor_execute", record)
        result = await dashboard._build_dashboard_summary(
            start=start, end=end, window_hours=720, limit=10, status=None, model=None, agent=None, feature=None, value_profile="conservative", include_knowledge=False, include_business_details=False
        )
        assert result.overview.threads == count
        assert result.overview.runs == 1
        assert result.overview.total_tokens == 17
        assert result.agent_usage[0].name == "Synthetic agent"
        assert result.user_usage[0].display_name == "Synthetic owner"
        assert result.scheduled_task_usage.runs == count
        assert result.scheduled_task_usage.users[0].runs == 1
        # Compact/rollup summaries must also retain the deleted task's owner,
        # without expanding every missing task id into bind parameters.
        compact = await dashboard._load_compact_supporting_metrics(start=start, end=end, limit=10, profile_user_ids=set())
        assert compact.scheduled_task_usage.runs == count
        assert compact.scheduled_task_usage.users[0].runs == 1
        assert max(parameter_counts) <= 1000
    finally:
        await dashboard_response_cache.clear()
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()
