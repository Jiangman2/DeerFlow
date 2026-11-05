from datetime import date

import pytest
from sqlalchemy.dialects import postgresql

from app.gateway.account_activity import activity_statement, summarize_days


def test_streaks_use_shanghai_calendar_and_yesterday_grace():
    rows = [("2026-01-01", 5, 4, 1, 1, 10), ("2026-01-02", 7, 5, 2, 2, 20), ("2026-01-04", 3, 2, 1, 1, 30)]
    result = summarize_days(rows, today=date(2026, 1, 5))
    assert result["peak_daily_tokens"] == 7
    assert result["longest_streak_days"] == 2
    assert result["current_streak_days"] == 1
    assert result["longest_task_seconds"] == 30
    assert result["total_tokens"] == 15
    assert result["days"][0]["date"] == "2025-02-01"
    assert result["days"][-1]["date"] == "2026-01-05"
    assert result["days"][-1]["tokens"] == 0


def test_empty_and_broken_streak():
    assert summarize_days([], today=date(2026, 1, 5))["current_streak_days"] == 0
    assert summarize_days([("2026-01-01", 0, 0, 0, 1, 0)], today=date(2026, 1, 5))["current_streak_days"] == 0


def test_query_is_owner_scoped_scalar_and_bounded():
    sql = str(activity_statement("owner", "postgresql").compile(dialect=postgresql.dialect()))
    assert "runs.user_id =" in sql and "GROUP BY" in sql and "LIMIT" in sql
    assert "first_human_message" not in sql and "metadata_json" not in sql and "JOIN" not in sql
    assert "Asia/Shanghai" in str(activity_statement("owner", "postgresql").compile().params)


def test_missing_identity_is_rejected():
    with pytest.raises(ValueError):
        activity_statement("", "postgresql")


def test_lifetime_streak_and_peak_are_not_limited_to_heatmap_year():
    result = summarize_days([("2020-01-01", 1000, 500, 500, 1, 90), ("2020-01-02", 10, 5, 5, 1, 2)], today=date(2026, 1, 5))
    assert result["peak_daily_tokens"] == 1000 and result["longest_streak_days"] == 2
    assert sum(day["tokens"] for day in result["days"]) == 0


@pytest.mark.asyncio
async def test_real_scalar_query_owner_boundary_timezone_and_running_duration():
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.run.model import RunRow

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(RunRow.__table__.create)
    sessions = async_sessionmaker(engine)
    async with sessions() as session:
        at = datetime(2026, 1, 1, 16, 1, tzinfo=UTC)  # Jan 2 in Shanghai
        session.add_all(
            [
                RunRow(run_id="a", thread_id="a", user_id="owner", status="success", created_at=at, updated_at=at + timedelta(seconds=90), total_tokens=100, total_input_tokens=80, total_output_tokens=20),
                RunRow(run_id="b", thread_id="b", user_id="other", status="success", created_at=at, updated_at=at + timedelta(hours=5), total_tokens=9999),
                RunRow(run_id="c", thread_id="c", user_id="owner", status="running", created_at=at, updated_at=at + timedelta(hours=5), total_tokens=5),
                RunRow(run_id="d", thread_id="d", user_id="owner", status="error", created_at=at, updated_at=at, total_tokens=-4),
            ]
        )
        await session.commit()
        rows = (await session.execute(activity_statement("owner", "sqlite"))).all()
    await engine.dispose()
    result = summarize_days(rows, today=date(2026, 1, 2))
    assert result["total_tokens"] == 105 and result["run_count"] == 3
    assert result["days"][-1]["tokens"] == 105
    assert result["longest_task_seconds"] == pytest.approx(90, abs=0.01)


@pytest.mark.asyncio
async def test_missing_user_never_opens_database():
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    from app.gateway.account_activity import get_account_activity

    factory = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await get_account_activity("", factory)
    assert exc.value.status_code == 401
    factory.assert_not_called()
