from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.persistence.base import Base
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.sql import ObservabilityRepository
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

pytestmark = pytest.mark.asyncio


async def _repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return ObservabilityRepository(sf), sf, engine


async def test_record_and_list_audit_logs() -> None:
    repo, _sf, engine = await _repo()
    try:
        await repo.record_audit(
            {
                "user_id": "u1",
                "user_email": "admin@example.com",
                "user_role": "admin",
                "action": "thread.delete",
                "resource_type": "thread",
                "resource_id": "t1",
                "method": "DELETE",
                "path": "/api/threads/t1",
                "status_code": 204,
                "result": "success",
                "duration_ms": 12,
                "client_ip": "127.0.0.1",
                "user_agent": "pytest",
                "error": None,
                "details_json": {"query": ""},
            }
        )

        rows = await repo.list_audit_logs()

        assert len(rows) == 1
        assert rows[0]["action"] == "thread.delete"
        assert rows[0]["details"] == {"query": ""}
    finally:
        await engine.dispose()


async def test_snapshot_aggregates_metrics_errors_and_traces() -> None:
    repo, sf, engine = await _repo()
    now = datetime.now(UTC)
    try:
        async with sf() as session:
            session.add(UserRow(id="u1", email="admin@example.com", system_role="admin"))
            session.add(ThreadMetaRow(thread_id="t1", user_id="u1", status="idle"))
            session.add(
                RunRow(
                    run_id="r1",
                    thread_id="t1",
                    user_id="u1",
                    status="success",
                    model_name="gpt-test",
                    total_tokens=42,
                    llm_call_count=2,
                    created_at=now - timedelta(minutes=3),
                    updated_at=now - timedelta(minutes=2),
                )
            )
            session.add(
                RunRow(
                    run_id="r2",
                    thread_id="t1",
                    user_id="u1",
                    status="error",
                    model_name="gpt-test",
                    total_tokens=8,
                    llm_call_count=1,
                    error="boom",
                    created_at=now - timedelta(minutes=1),
                    updated_at=now,
                )
            )
            session.add(
                RunEventRow(
                    thread_id="t1",
                    run_id="r2",
                    user_id="u1",
                    event_type="error",
                    category="trace",
                    content="boom",
                    event_metadata={},
                    seq=1,
                    created_at=now,
                )
            )
            await session.commit()

        snapshot = await repo.snapshot(hours=24)

        assert snapshot["metrics"]["users_total"] == 1
        assert snapshot["metrics"]["threads_total"] == 1
        assert snapshot["metrics"]["runs_window"] == 2
        assert snapshot["metrics"]["tokens_window"] == 50
        assert snapshot["health"]["error_rate"] == 0.5
        assert {item["name"] for item in snapshot["component_health"]} >= {
            "PostgreSQL",
            "LangGraph 检查点",
            "运行事件写入",
            "模型供应商",
            "Sandbox",
            "文件/制品存储",
            "工作负载",
        }
        assert all("last_checked_at" in item for item in snapshot["component_health"])
        assert snapshot["recent_errors"][0]["run_id"] == "r2"
        assert snapshot["recent_traces"][0]["run_id"] == "r2"
        assert snapshot["recent_traces"][0]["event_count"] == 1

        traces = await repo.list_traces(hours=24, status="error", error_type="unknown")
        assert [trace["run_id"] for trace in traces] == ["r2"]

        diagnostics = await repo.run_diagnostics("r2")
        assert diagnostics is not None
        assert diagnostics["run"]["run_id"] == "r2"
        assert diagnostics["possible_causes"][0]["type"] == "unknown"

        analytics = await repo.analytics(hours=24)
        assert analytics["current"]["runs"] == 2
        assert analytics["comparison"]["runs"]["current"] == 2
        assert analytics["slow_runs"][0]["run_id"] in {"r1", "r2"}

        ranged = await repo.snapshot(start_at=now - timedelta(minutes=2), end_at=now + timedelta(minutes=1))
        assert ranged["window_start"]
        assert ranged["window_end"]
        assert ranged["metrics"]["runs_window"] == 1
    finally:
        await engine.dispose()


async def test_disposition_records_are_returned_with_diagnostics() -> None:
    repo, sf, engine = await _repo()
    now = datetime.now(UTC)
    try:
        async with sf() as session:
            session.add(UserRow(id="u1", email="admin@example.com", system_role="admin"))
            session.add(ThreadMetaRow(thread_id="t1", user_id="u1", status="idle"))
            session.add(
                RunRow(
                    run_id="r1",
                    thread_id="t1",
                    user_id="u1",
                    status="error",
                    model_name="gpt-test",
                    error="boom",
                    created_at=now - timedelta(minutes=2),
                    updated_at=now,
                )
            )
            await session.commit()

        await repo.record_disposition(
            {
                "run_id": "r1",
                "thread_id": "t1",
                "action": "mark_handled",
                "status": "handled",
                "note": "checked",
                "user_id": "u1",
                "user_email": "admin@example.com",
                "result": "success",
                "details_json": {},
            }
        )

        diagnostics = await repo.run_diagnostics("r1")

        assert diagnostics is not None
        assert diagnostics["disposition_status"] == "handled"
        assert diagnostics["dispositions"][0]["note"] == "checked"
    finally:
        await engine.dispose()


async def test_trace_events_are_ordered_by_sequence() -> None:
    repo, sf, engine = await _repo()
    try:
        async with sf() as session:
            session.add_all(
                [
                    RunEventRow(
                        thread_id="t1",
                        run_id="r1",
                        event_type="end",
                        category="trace",
                        content="second",
                        event_metadata={},
                        seq=2,
                    ),
                    RunEventRow(
                        thread_id="t1",
                        run_id="r1",
                        event_type="start",
                        category="trace",
                        content="first",
                        event_metadata={},
                        seq=1,
                    ),
                ]
            )
            await session.commit()

        events = await repo.trace_events("r1")

        assert [event["seq"] for event in events] == [1, 2]
        assert events[0]["content"] == "first"
    finally:
        await engine.dispose()
