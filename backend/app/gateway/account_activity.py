"""Owner-indexed scalar analytics. Never load run payloads or replay events.

Five-minute cache; one short PostgreSQL analytics transaction across replicas.
Tokens are attributed to the run's Shanghai start day (including live snapshots),
not presented as exact billing-time/provider-day consumption.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, func, select, text

from app.gateway.dashboard_cache import DashboardResponseCache
from deerflow.persistence.run.model import RunRow

_cache = DashboardResponseCache(max_entries=128)
_limit = asyncio.Semaphore(1)
_MAX_DAYS = 20_000


def activity_statement(user_id: str, dialect: str):
    if not user_id:
        raise ValueError("An authenticated owner is required")
    if dialect == "postgresql":
        day = func.to_char(func.timezone("Asia/Shanghai", RunRow.created_at), "YYYY-MM-DD")
        duration = func.extract("epoch", RunRow.updated_at - RunRow.created_at)
    else:
        day = func.strftime("%Y-%m-%d", RunRow.created_at, "+8 hours")
        duration = (func.julianday(RunRow.updated_at) - func.julianday(RunRow.created_at)) * 86400

    def positive(column):
        return case((column > 0, column), else_=0)

    return (
        select(
            day.label("day"),
            func.sum(positive(RunRow.total_tokens)),
            func.sum(positive(RunRow.total_input_tokens)),
            func.sum(positive(RunRow.total_output_tokens)),
            func.count(),
            func.max(case((RunRow.status.in_(("success", "error", "timeout", "interrupted")), positive(duration)), else_=0)),
        )
        .where(RunRow.user_id == user_id, RunRow.created_at <= datetime.now(UTC))
        .group_by(day)
        .order_by(day)
        .limit(_MAX_DAYS + 1)
    )


def summarize_days(rows, *, today: date) -> dict:
    if len(rows) > _MAX_DAYS:
        raise ValueError("Activity history exceeds the bounded aggregate window")
    days = {str(row[0]): {"date": str(row[0]), "tokens": int(row[1] or 0), "runs": int(row[4] or 0)} for row in rows if row[0]}
    streak = longest = 0
    previous = None
    for day_text in sorted(days):
        day = date.fromisoformat(day_text)
        streak = streak + 1 if previous and day == previous + timedelta(days=1) else 1
        longest = max(longest, streak)
        previous = day
    current = streak if previous in (today, today - timedelta(days=1)) else 0
    # Twelve calendar months ending with this month, not a sliding date picker.
    month = today.year * 12 + today.month - 1 - 11
    start = date(month // 12, month % 12 + 1, 1)
    return {
        "scope": "current_user_recorded_platform_runs",
        "timezone": "Asia/Shanghai",
        "attribution": "run_start_day",
        "generated_at": datetime.now(UTC).isoformat(),
        "cache_seconds": 300,
        "total_tokens": sum(int(row[1] or 0) for row in rows),
        "input_tokens": sum(int(row[2] or 0) for row in rows),
        "output_tokens": sum(int(row[3] or 0) for row in rows),
        "run_count": sum(int(row[4] or 0) for row in rows),
        "peak_daily_tokens": max((value["tokens"] for value in days.values()), default=0),
        "longest_task_seconds": max((float(row[5] or 0) for row in rows), default=0),
        "longest_streak_days": longest,
        "current_streak_days": current,
        "recorded_since": min(days, default=None),
        "days": [days.get((start + timedelta(days=i)).isoformat(), {"date": (start + timedelta(days=i)).isoformat(), "tokens": 0, "runs": 0}) for i in range((today - start).days + 1)],
    }


async def get_account_activity(user_id: str, session_factory) -> dict:
    if not user_id:
        raise HTTPException(401, "Authenticated user required")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    key = "account-activity-v2:" + hashlib.sha256(user_id.encode()).hexdigest() + ":" + today.isoformat()

    async def produce():
        try:
            async with asyncio.timeout(4):
                async with _limit, session_factory() as session:
                    dialect = session.get_bind().dialect.name
                    if dialect == "postgresql":
                        await session.execute(text("SET TRANSACTION READ ONLY"))
                        await session.execute(text("SET LOCAL statement_timeout = '2000ms'"))
                        await session.execute(text("SET LOCAL lock_timeout = '250ms'"))
                        await session.execute(text("SET LOCAL work_mem = '4MB'"))
                        await session.execute(text("SET LOCAL max_parallel_workers_per_gather = 0"))
                        # Global admission bound even if Redis is absent/unavailable.
                        if not await session.scalar(text("SELECT pg_try_advisory_xact_lock(732492018)")):
                            raise HTTPException(503, "Usage analytics is busy; retry later", headers={"Retry-After": "30"})
                    rows = (await session.execute(activity_statement(user_id, dialect))).all()
                    return summarize_days(rows, today=today)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(503, "Usage analytics is temporarily unavailable", headers={"Retry-After": "30"}) from None

    def decode(payload):
        value = json.loads(payload)
        # Shared Redis entries must not gain another full local-cache lifetime.
        age = (datetime.now(UTC) - datetime.fromisoformat(value["generated_at"])).total_seconds()
        if not 0 <= age <= 300:
            raise ValueError("Usage snapshot expired")
        return value

    result = await _cache.get_or_create(key, ttl_seconds=300, producer=produce, encode=json.dumps, decode=decode)
    return result.value
