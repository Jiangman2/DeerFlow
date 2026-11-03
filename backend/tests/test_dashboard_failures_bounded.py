from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event
from test_dashboard_metrics import ADMIN_ID, USER_ID, UserRow, _client, _run, _session_factory

from app.gateway.routers import dashboard
from deerflow.persistence.observability.model import OperationDispositionRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

pytestmark = pytest.mark.asyncio


async def test_page_40_never_loads_full_run_user_or_disposition_payloads(monkeypatch):
    sf, engine = await _session_factory()
    now = datetime(2026, 11, 1, tzinfo=UTC)
    queries = []
    try:
        async with sf() as session:
            session.add_all([UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"), UserRow(id=USER_ID, email="user@example.com", display_name="测试")])
            for index in range(205):
                run = _run(f"failed-{index:03}", "deleted-thread", "error", now - timedelta(minutes=index))
                run.kwargs_json = {"secret_or_large_business_input": "x" * 50000}
                run.error = "错误" * 500000 if index == 198 else "synthetic"
                session.add(run)
            for index in range(50):
                session.add(
                    OperationDispositionRow(run_id="failed-198", thread_id="deleted-thread", action="inspect", status="latest" if index == 49 else "older", created_at=now + timedelta(seconds=index), details_json={"large": "x" * 100000})
                )
            await session.commit()
        event.listen(engine.sync_engine, "before_cursor_execute", lambda conn, cursor, stmt, params, context, many: queries.append(stmt))
        with _client(sf, monkeypatch) as client:
            for offset in (0, 195, 200, 205):
                response = client.get("/api/admin/dashboard/failures", params={"limit": 5, "offset": offset})
                assert response.status_code == 200
                data = response.json()
                assert data["total"] == 205
                assert [r["run_id"] for r in data["items"]] == [f"failed-{i:03}" for i in range(offset, min(offset + 5, 205))]
                assert len(response.content) < 25000
                if offset == 195:
                    large = data["items"][3]
                    assert len(large["error"]) == 4096
                    assert large["error_truncated"] is True
                    assert large["disposition_status"] == "latest"
                    assert large["can_open"] is False
        reads = "\n".join(queries).lower()
        for forbidden in (
            "runs.kwargs_json",
            "runs.metadata_json",
            "runs.first_human_message",
            "runs.last_ai_message",
            "users.password_hash",
            "users.cnooc_platform_token_hash",
            "run_events.content",
            "operation_dispositions.details_json",
            "operation_dispositions.note",
        ):
            assert forbidden not in reads
        assert "exists (select run_events.id" in reads
        assert "substr(runs.error" in reads
    finally:
        await engine.dispose()


async def test_unavailable_database_is_explicit_retryable_error_without_data_leak(monkeypatch, caplog):
    sf, engine = await _session_factory()
    try:
        with _client(sf, monkeypatch) as client:

            def unavailable():
                raise TimeoutError("private password business text")

            monkeypatch.setattr(dashboard, "_session_factory", unavailable)
            response = client.get("/api/admin/dashboard/failures", params={"q": "private-search"})
        assert response.status_code == 503
        assert response.headers["retry-after"] == "10"
        assert "private" not in response.text + caplog.text
        assert "total" not in response.json()
    finally:
        await engine.dispose()


async def test_filtered_count_preserves_owner_precedence_without_history_joins(monkeypatch):
    sf, engine = await _session_factory()
    now = datetime(2026, 11, 1, tzinfo=UTC)
    statements = []
    try:
        async with sf() as session:
            session.add_all(
                [
                    UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                    UserRow(id=USER_ID, email="legacy@example.com", display_name="旧账户"),
                    ThreadMetaRow(thread_id="legacy-thread", user_id=USER_ID),
                ]
            )
            for name, owner in (("fallback", None), ("explicit", ADMIN_ID)):
                run = _run(name, "legacy-thread", "error", now)
                run.user_id = owner
                session.add(run)
            run = _run("deleted-owner", "deleted-thread", "error", now)
            run.user_id = None
            session.add(run)
            await session.commit()
        event.listen(engine.sync_engine, "before_cursor_execute", lambda conn, cursor, stmt, params, context, many: statements.append(stmt))
        with _client(sf, monkeypatch) as client:
            for query, expected in (("LEGACY@", ["fallback"]), ("旧账户", ["fallback"]), ("ADMIN@", ["explicit"]), ("@", ["fallback", "explicit"])):
                response = client.get("/api/admin/dashboard/failures", params={"q": query})
                assert response.status_code == 200
                assert response.json()["total"] == len(expected)
                assert [r["run_id"] for r in response.json()["items"]] == expected
            assert client.get("/api/admin/dashboard/failures").json()["total"] == 3
        counts = [s.lower() for s in statements if "select count(" in s.lower()]
        assert counts and all(" join " not in s for s in counts)
        assert any("threads_meta.thread_id = runs.thread_id" in s for s in counts)
    finally:
        await engine.dispose()
