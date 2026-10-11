"""Authenticated homepage counters; never expose administrator report details."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func, select, union

from app.gateway.dashboard_cache import dashboard_response_cache
from app.gateway.dashboard_rollups import DashboardQueryBusy, DashboardRollupsNotReady, apply_dashboard_statement_limits, dashboard_query_slot, load_hybrid_contribution
from app.gateway.deps import get_current_user_from_request
from app.gateway.routers import dashboard
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow

router = APIRouter(prefix="/api/home", tags=["home"])


class HomeStatistics(BaseModel):
    todayUsers: int
    todayTasks: int


def _today_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    # Same minute-resolution upper boundary as the business dashboard.
    end = (now or datetime.now(UTC)).astimezone(UTC).replace(second=0, microsecond=0)
    start = end.astimezone(dashboard._BUSINESS_TIMEZONE).replace(hour=0, minute=0).astimezone(UTC)
    return start, end


async def _counts(start: datetime, end: datetime) -> HomeStatistics:
    sf = dashboard._session_factory()
    if dashboard.dashboard_rollups_enabled() and start < end:
        selection = await load_hybrid_contribution(sf, start=start, end=end, minimum_rollup_days=dashboard._dashboard_rollup_min_days(), minimum_hourly_rollup_hours=dashboard._dashboard_hourly_rollup_min_hours())
        contribution = selection.contribution
        users = {key[1] for key in contribution.dimensions if key[1]}
        for members in contribution.thread_creation_users.values():
            users.update(members)
        return HomeStatistics(todayUsers=len(users), todayTasks=sum(metric.runs for metric in contribution.dimensions.values()))

    # Match raw dashboard overview: all started runs (any status), plus distinct
    # users in the append-only creation ledger, including deleted conversations.
    async with sf() as session:
        await apply_dashboard_statement_limits(session, maintenance=False)
        active = union(
            select(RunRow.user_id).where(RunRow.created_at >= start, RunRow.created_at <= end, RunRow.user_id.is_not(None), RunRow.user_id != ""),
            select(ThreadCreationRow.user_id).where(ThreadCreationRow.created_at >= start, ThreadCreationRow.created_at <= end, ThreadCreationRow.user_id.is_not(None), ThreadCreationRow.user_id != ""),
        ).subquery()
        users = await session.scalar(select(func.count()).select_from(active))
        runs = await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.created_at >= start, RunRow.created_at <= end))
        return HomeStatistics(todayUsers=users or 0, todayTasks=runs or 0)


@router.get("/statistics", response_model=HomeStatistics)
async def get_home_statistics(request: Request, response: Response) -> HomeStatistics:
    await get_current_user_from_request(request)
    start, end = _today_window()

    async def produce():
        try:
            async with dashboard_query_slot():
                return await _counts(start, end)
        except DashboardQueryBusy as exc:
            raise HTTPException(status_code=429, detail="Homepage statistics are busy", headers={"Retry-After": "60"}) from exc
        except DashboardRollupsNotReady as exc:
            raise HTTPException(status_code=503, detail="Homepage statistics are preparing", headers={"Retry-After": "60"}) from exc

    cached = await dashboard_response_cache.get_or_create(
        dashboard._dashboard_cache_key("home-statistics", start=start.isoformat(), end=end.isoformat(), rollups=dashboard.dashboard_rollups_enabled()),
        ttl_seconds=60,
        producer=produce,
        encode=lambda value: value.model_dump_json(),
        decode=HomeStatistics.model_validate_json,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return cached.value
