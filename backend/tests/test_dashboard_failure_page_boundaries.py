"""Additional pagination boundaries; kept separate from the sealed 372-test Job."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import update
from test_dashboard_metrics import ADMIN_ID, USER_ID, UserRow, _client, _run, _session_factory

from deerflow.persistence.run.model import RunRow

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("total", [0, 1, 4, 5, 6, 204, 205, 206])
async def test_first_middle_last_and_beyond_pages_with_tied_timestamps(total, monkeypatch):
    sf, engine = await _session_factory()
    now = datetime(2026, 11, 1, tzinfo=UTC)
    try:
        async with sf() as session:
            session.add_all([UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"), UserRow(id=USER_ID, email="boundary@example.com", display_name="边界账户")])
            # All timestamps tie: run_id must supply a stable secondary order.
            for i in range(total):
                session.add(_run(f"boundary-{i:04}", "missing-thread", "error", now))
            session.add(_run("successful-not-a-failure", "other", "success", now))
            await session.commit()
        expected = [f"boundary-{i:04}" for i in reversed(range(total))]
        last_offset = max(0, ((total - 1) // 5) * 5)
        offsets = sorted({0, 5, (total // 10) * 5, last_offset, last_offset + 5, 195, 200})
        with _client(sf, monkeypatch) as client:
            for q in ("", "BOUNDARY@", "边界账户", "no-such-user"):
                matching = [] if q == "no-such-user" else expected
                for offset in offsets:
                    response = client.get("/api/admin/dashboard/failures", params={"limit": 5, "offset": offset, "q": q})
                    assert response.status_code == 200
                    result = response.json()
                    assert result["total"] == len(matching)
                    assert [r["run_id"] for r in result["items"]] == matching[offset : offset + 5]
                    assert len(response.content) < 15000
    finally:
        await engine.dispose()


async def test_new_failure_and_resolved_failure_change_counts_without_corrupting_pages(monkeypatch):
    sf, engine = await _session_factory()
    now = datetime(2026, 11, 1, tzinfo=UTC)
    try:
        async with sf() as session:
            session.add(UserRow(id=USER_ID, email="boundary@example.com"))
            session.add_all([_run(f"b-{i}", "thread", "error", now) for i in range(6)])
            await session.commit()
        with _client(sf, monkeypatch) as client:
            assert len(client.get("/api/admin/dashboard/failures?limit=5&offset=5").json()["items"]) == 1
        async with sf() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == "b-0").values(status="success", error=None))
            await session.commit()
        with _client(sf, monkeypatch) as client:
            result = client.get("/api/admin/dashboard/failures?limit=5&offset=5")
            assert result.status_code == 200 and result.json()["total"] == 5 and result.json()["items"] == []
        async with sf() as session:
            session.add(_run("b-6", "thread", "timeout", now))
            await session.commit()
        with _client(sf, monkeypatch) as client:
            result = client.get("/api/admin/dashboard/failures?limit=5&offset=5")
            assert result.status_code == 200 and result.json()["total"] == 6
            assert [r["run_id"] for r in result.json()["items"]] == ["b-1"]
    finally:
        await engine.dispose()


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}])
async def test_invalid_pagination_is_rejected_without_query(params, monkeypatch):
    sf, engine = await _session_factory()
    try:
        with _client(sf, monkeypatch) as client:
            response = client.get("/api/admin/dashboard/failures", params=params)
            assert response.status_code == 422
    finally:
        await engine.dispose()
