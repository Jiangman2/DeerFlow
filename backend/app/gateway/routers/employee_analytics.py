"""Authenticated square launches and administrator feedback attribution.

Uses existing audit/feedback tables. Private employees and fabricated actors
cannot enter the public launch stream; no message bodies are selected.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, case, func, or_, select

from app.gateway.dashboard_rollups import DashboardQueryBusy, apply_dashboard_statement_limits, dashboard_query_slot
from app.gateway.deps import get_current_user_from_request, require_admin_user
from app.gateway.employee_featured_store import EmployeeIdentity
from app.gateway.external_agent_analytics import EXTERNAL_PLATFORM_AGENT_NAMES
from app.gateway.routers.agents import _require_agents_api_enabled
from app.gateway.routers.dashboard import _coerce_aware, _session_factory, _window_bounds
from app.gateway.routers.employee_directory import EXTERNAL_EMPLOYEE_NAMES, SharedSource, _catalog, _sessions
from deerflow.config.agents_config import list_team_agents
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.observability.model import AuditLogRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

router = APIRouter(tags=["employee-analytics"])
CLICK_ACTION = "employee_square.launch"
SQUARE_SHADOW_NAMES = {
    "知识嵌入提升地震分辨率工具",
    "科研报告自动生产智能体",
    "科研报告自动生产智能体（PPT to Word）",
    "勘探开发图文智能检索工具",
    "源头采集自动化工具",
    "油气专业文档智能审查工具",
}


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_identity: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True)
class PublishedEmployee(EmployeeIdentity):
    display_name: str = ""


def _label(entry: EmployeeIdentity) -> str:
    return getattr(entry, "display_name", "").strip() or entry.name


def _published_catalog() -> list[EmployeeIdentity]:
    entries = _catalog("", include_personal=False)
    # The square hides legacy copies of the bundled external entries too.
    names = {a.name: a.display_name or a.name for a in list_team_agents()}
    return [
        PublishedEmployee(e.source, e.name, e.owner_id, e.publication_id, e.name if e.source == "external" else names.get(e.name, e.name))
        for e in entries
        if e.source == "external" or (e.name not in EXTERNAL_EMPLOYEE_NAMES and e.name not in SQUARE_SHADOW_NAMES)
    ]


@router.post("/api/employee-directory/{source}/{name}/click", status_code=204)
async def record_square_launch(source: SharedSource, name: str, body: LaunchRequest, request: Request) -> Response:
    user = await get_current_user_from_request(request)
    _require_agents_api_enabled()
    entries = await asyncio.to_thread(_published_catalog)
    identity = next((e for e in entries if e.source == source and e.name == name), None)
    if identity is None:
        raise HTTPException(404, "Published employee not found")
    if identity.token != body.expected_identity:
        raise HTTPException(409, "Employee publication changed; refresh the square")
    async with _sessions()() as session:
        session.add(
            AuditLogRow(
                user_id=str(user.id),
                action=CLICK_ACTION,
                resource_type="employee_square",
                resource_id=identity.selection_key,
                method="POST",
                path="/api/employee-directory/launch",
                status_code=204,
                result="success",
                duration_ms=0,
                details_json={"source": identity.source, "name": identity.name, "publication_identity": identity.token},
            )
        )
        await session.commit()
    return Response(status_code=204)


class EmployeeClickItem(BaseModel):
    identity: str
    source: str
    name: str
    clicks: int = 0
    users: int = 0
    total_clicks: int = 0


class EmployeeClickPage(BaseModel):
    items: list[EmployeeClickItem]
    total: int
    offset: int
    limit: int
    start_at: datetime
    end_at: datetime
    scope: str = "current_published_square_launches"


async def aggregate_clicks(session, catalog, start, end):
    """One shared counting implementation for dashboard and machine reports."""
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    metrics = {}
    # Bounded IN groups use the existing resource/action indexes.
    for offset_in_catalog in range(0, len(catalog), 250):
        batch = catalog[offset_in_catalog : offset_in_catalog + 250]
        tokens = [e.selection_key for e in batch]
        legacy = {e.name: e.selection_key for e in batch if e.source == "external" and e.name in EXTERNAL_PLATFORM_AGENT_NAMES}
        filters = and_(AuditLogRow.action == CLICK_ACTION, AuditLogRow.resource_type == "employee_square", AuditLogRow.resource_id.in_(tokens))
        identity = AuditLogRow.resource_id
        if legacy:
            filters = or_(filters, and_(AuditLogRow.action == "external_agent.click", AuditLogRow.resource_id.in_(legacy)))
            identity = case((AuditLogRow.action == CLICK_ACTION, AuditLogRow.resource_id), else_=case(legacy, value=AuditLogRow.resource_id))
        in_window = and_(AuditLogRow.created_at >= start, AuditLogRow.created_at < end)
        rows = await session.execute(
            select(identity, func.sum(case((in_window, 1), else_=0)), func.count(func.distinct(case((in_window, AuditLogRow.user_id)))), func.count()).where(filters, AuditLogRow.result == "success").group_by(identity)
        )
        metrics.update({key: (int(clicks), int(users), int(total)) for key, clicks, users, total in rows})
    return metrics


@router.get("/api/admin/dashboard/employee-clicks", response_model=EmployeeClickPage)
async def employee_clicks(
    request: Request,
    response: Response,
    hours: int = Query(168, ge=1, le=87840),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    query: str = Query("", max_length=100),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=100000),
) -> EmployeeClickPage:
    await require_admin_user(request, detail="Only administrators can view employee analytics")
    start, end, _ = _window_bounds(hours, start_at, end_at)
    catalog = await asyncio.to_thread(_published_catalog)
    if len(catalog) > 10000:
        raise HTTPException(503, "Employee catalog exceeds this report's bounded capacity")
    needle = query.strip().casefold()
    catalog = [e for e in catalog if not needle or needle in e.name.casefold() or needle in _label(e).casefold()]
    metrics: dict[str, tuple[int, int, int]] = {}
    try:
        async with dashboard_query_slot():
            async with _session_factory()() as session:
                await apply_dashboard_statement_limits(session, maintenance=False)
                metrics = await aggregate_clicks(session, catalog, start, end)
    except DashboardQueryBusy:
        raise HTTPException(503, "Analytics is busy; retry shortly") from None
    items = [
        EmployeeClickItem(
            identity=e.selection_key, source=e.source, name=_label(e), clicks=metrics.get(e.selection_key, (0, 0, 0))[0], users=metrics.get(e.selection_key, (0, 0, 0))[1], total_clicks=metrics.get(e.selection_key, (0, 0, 0))[2]
        )
        for e in catalog
    ]
    items.sort(key=lambda e: (-e.clicks, -e.total_clicks, e.name, e.identity))
    response.headers["Cache-Control"] = "no-store"
    return EmployeeClickPage(items=items[offset : offset + limit], total=len(items), offset=offset, limit=limit, start_at=start, end_at=end)


class FeedbackActorItem(BaseModel):
    feedback_id: str
    user_id: str | None
    display_name: str | None
    email: str | None
    organization_name: str | None
    rating: int
    created_at: datetime
    thread_id: str
    run_id: str
    thread_title: str | None


class FeedbackActorPage(BaseModel):
    items: list[FeedbackActorItem]
    total: int
    offset: int
    limit: int


@router.get("/api/admin/dashboard/feedback-actors", response_model=FeedbackActorPage)
async def feedback_actors(
    request: Request,
    response: Response,
    hours: int = Query(168, ge=1, le=87840),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    rating: int | None = Query(None, ge=-1, le=1),
    query: str = Query("", max_length=100),
    limit: int = Query(10, ge=1, le=50),
    offset: int = Query(0, ge=0, le=100000),
) -> FeedbackActorPage:
    await require_admin_user(request, detail="Only administrators can view feedback actors")
    if rating == 0:
        raise HTTPException(422, "Rating must be 1 or -1")
    start, end, _ = _window_bounds(hours, start_at, end_at)
    conditions = [FeedbackRow.created_at >= start, FeedbackRow.created_at < end]
    if rating is not None:
        conditions.append(FeedbackRow.rating == rating)
    if query.strip():
        # Escape LIKE metacharacters so a literal username remains a literal.
        term = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append(or_(UserRow.display_name.ilike(term, escape="\\"), UserRow.email.ilike(term, escape="\\"), UserRow.organization_name.ilike(term, escape="\\")))
    joined = (
        select(
            FeedbackRow.feedback_id,
            FeedbackRow.user_id,
            UserRow.display_name,
            UserRow.email,
            UserRow.organization_name,
            FeedbackRow.rating,
            FeedbackRow.created_at,
            FeedbackRow.thread_id,
            FeedbackRow.run_id,
            ThreadMetaRow.display_name.label("thread_title"),
        )
        .outerjoin(UserRow, FeedbackRow.user_id == UserRow.id)
        .outerjoin(ThreadMetaRow, FeedbackRow.thread_id == ThreadMetaRow.thread_id)
        .where(*conditions)
    )
    try:
        async with dashboard_query_slot():
            async with _session_factory()() as session:
                await apply_dashboard_statement_limits(session, maintenance=False)
                total = await session.scalar(select(func.count()).select_from(joined.subquery()))
                result = await session.execute(joined.order_by(FeedbackRow.created_at.desc(), FeedbackRow.feedback_id.desc()).offset(offset).limit(limit))
                items = [FeedbackActorItem(**{**dict(row), "created_at": _coerce_aware(row["created_at"])}) for row in result.mappings()]
    except DashboardQueryBusy:
        raise HTTPException(503, "Analytics is busy; retry shortly") from None
    response.headers["Cache-Control"] = "no-store"
    return FeedbackActorPage(items=items, total=int(total or 0), offset=offset, limit=limit)
