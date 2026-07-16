"""Workspace announcement endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from app.gateway.deps import require_admin_user
from deerflow.persistence.announcement import AnnouncementRepository
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.observability.sql import ObservabilityRepository

router = APIRouter(tags=["announcements"])

AnnouncementSeverity = Literal["info", "success", "warning", "critical"]
AnnouncementStatus = Literal["draft", "published", "archived"]

_ADMIN_REQUIRED = "Admin privileges are required to manage announcements"


def _repo() -> AnnouncementRepository:
    return AnnouncementRepository(get_session_factory())


def _audit_repo() -> ObservabilityRepository:
    return ObservabilityRepository(get_session_factory())


def _user_fields(request: Request) -> dict[str, str | None]:
    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    return {
        "user_id": str(user_id) if user_id is not None else None,
        "user_email": getattr(user, "email", None),
    }


def _normalize_dt(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class AnnouncementWriteRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=4000)
    severity: AnnouncementSeverity = "info"
    status: AnnouncementStatus = "draft"
    dismissible: bool = True
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> AnnouncementWriteRequest:
        self.starts_at = _normalize_dt(self.starts_at)
        self.ends_at = _normalize_dt(self.ends_at)
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class AnnouncementPatchRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    severity: AnnouncementSeverity | None = None
    status: AnnouncementStatus | None = None
    dismissible: bool | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> AnnouncementPatchRequest:
        self.starts_at = _normalize_dt(self.starts_at)
        self.ends_at = _normalize_dt(self.ends_at)
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class StatusRequest(BaseModel):
    status: AnnouncementStatus


async def _record_audit(
    request: Request,
    *,
    action: str,
    announcement_id: str | None,
    result: str = "success",
    error: str | None = None,
    details: dict | None = None,
) -> None:
    user_fields = _user_fields(request)
    await _audit_repo().record_audit(
        {
            "created_at": datetime.now(UTC),
            **user_fields,
            "user_role": getattr(getattr(request.state, "user", None), "system_role", None),
            "action": f"announcement.{action}",
            "resource_type": "announcement",
            "resource_id": announcement_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": 200 if result == "success" else 409,
            "result": result,
            "duration_ms": 0,
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "error": error,
            "details_json": details or {},
        }
    )


async def _ensure_publishable(repo: AnnouncementRepository, *, starts_at: datetime | None, ends_at: datetime | None, exclude_id: str | None = None) -> None:
    overlapping = await repo.find_overlapping_published(starts_at=starts_at, ends_at=ends_at, exclude_id=exclude_id)
    if overlapping:
        raise HTTPException(status_code=409, detail="Another published announcement overlaps this time window")


@router.get("/api/announcements/active")
async def get_active_announcement():
    return {"announcement": await _repo().get_active()}


@router.get("/api/admin/announcements")
async def list_announcements(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    include_archived: bool = Query(default=True),
):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    return {"items": await _repo().list_announcements(limit=limit, include_archived=include_archived)}


@router.post("/api/admin/announcements")
async def create_announcement(request: Request, body: AnnouncementWriteRequest):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    repo = _repo()
    if body.status == "published":
        await _ensure_publishable(repo, starts_at=body.starts_at, ends_at=body.ends_at)
    user_fields = _user_fields(request)
    item = await repo.create(
        {
            **body.model_dump(),
            "created_by_user_id": user_fields["user_id"],
            "created_by_email": user_fields["user_email"],
            "updated_by_user_id": user_fields["user_id"],
            "updated_by_email": user_fields["user_email"],
        }
    )
    await _record_audit(request, action="create", announcement_id=item["id"], details={"status": item["status"]})
    return {"item": item}


@router.patch("/api/admin/announcements/{announcement_id}")
async def update_announcement(request: Request, announcement_id: str, body: AnnouncementPatchRequest):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    repo = _repo()
    existing = await repo.get(announcement_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    updates = body.model_dump(exclude_unset=True)
    starts_at = updates.get("starts_at", existing["starts_at"])
    ends_at = updates.get("ends_at", existing["ends_at"])
    starts_dt = _normalize_dt(datetime.fromisoformat(starts_at)) if isinstance(starts_at, str) else starts_at
    ends_dt = _normalize_dt(datetime.fromisoformat(ends_at)) if isinstance(ends_at, str) else ends_at
    if starts_dt and ends_dt and ends_dt <= starts_dt:
        raise HTTPException(status_code=422, detail="ends_at must be later than starts_at")
    next_status = updates.get("status", existing["status"])
    if next_status == "published":
        await _ensure_publishable(repo, starts_at=starts_dt, ends_at=ends_dt, exclude_id=announcement_id)
    user_fields = _user_fields(request)
    updates.update(
        {
            "updated_by_user_id": user_fields["user_id"],
            "updated_by_email": user_fields["user_email"],
        }
    )
    item = await repo.update(announcement_id, updates)
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    await _record_audit(request, action="update", announcement_id=announcement_id, details={"status": item["status"]})
    return {"item": item}


@router.post("/api/admin/announcements/{announcement_id}/status")
async def set_announcement_status(request: Request, announcement_id: str, body: StatusRequest):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    repo = _repo()
    existing = await repo.get(announcement_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    if body.status == "published":
        starts_at = _normalize_dt(datetime.fromisoformat(existing["starts_at"])) if existing["starts_at"] else None
        ends_at = _normalize_dt(datetime.fromisoformat(existing["ends_at"])) if existing["ends_at"] else None
        await _ensure_publishable(repo, starts_at=starts_at, ends_at=ends_at, exclude_id=announcement_id)
    user_fields = _user_fields(request)
    item = await repo.set_status(
        announcement_id,
        status=body.status,
        updated_by_user_id=user_fields["user_id"],
        updated_by_email=user_fields["user_email"],
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Announcement not found")
    await _record_audit(request, action=f"status.{body.status}", announcement_id=announcement_id)
    return {"item": item}
