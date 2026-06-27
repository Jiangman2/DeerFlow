"""Workspace site navigation endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from app.gateway.deps import require_admin_user
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.observability.sql import ObservabilityRepository
from deerflow.persistence.site_navigation import SiteNavigationRepository

router = APIRouter(tags=["site-navigation"])

_ADMIN_REQUIRED = "Admin privileges are required to manage site navigation"


def _repo() -> SiteNavigationRepository:
    return SiteNavigationRepository(get_session_factory())


def _audit_repo() -> ObservabilityRepository:
    return ObservabilityRepository(get_session_factory())


def _user_fields(request: Request) -> dict[str, str | None]:
    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    return {
        "user_id": str(user_id) if user_id is not None else None,
        "user_email": getattr(user, "email", None),
    }


def _normalize_aliases(value: list[str] | str | None) -> list[str]:
    if value is None:
        return []
    raw = value.split(",") if isinstance(value, str) else value
    aliases: list[str] = []
    seen: set[str] = set()
    for item in raw:
        alias = str(item).strip()
        key = alias.casefold()
        if alias and key not in seen:
            aliases.append(alias)
            seen.add(key)
    return aliases


def _validate_http_url(value: str) -> str:
    cleaned = value.strip()
    if not cleaned.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    return cleaned


class SiteNavigationWriteRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=2048)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    description: str = Field(default="", max_length=1000)
    category: str = Field(default="", max_length=80)
    sort_order: int = Field(default=0, ge=-100000, le=100000)
    enabled: bool = True

    @field_validator("name", "description", "category")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_http_url(value)

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_aliases(cls, value: list[str] | str | None) -> list[str]:
        return _normalize_aliases(value)


class SiteNavigationPatchRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    aliases: list[str] | None = Field(default=None, max_length=50)
    description: str | None = Field(default=None, max_length=1000)
    category: str | None = Field(default=None, max_length=80)
    sort_order: int | None = Field(default=None, ge=-100000, le=100000)
    enabled: bool | None = None

    @field_validator("name", "description", "category")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator("url")
    @classmethod
    def validate_optional_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value) if value is not None else value

    @field_validator("aliases", mode="before")
    @classmethod
    def normalize_optional_aliases(cls, value: list[str] | str | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_aliases(value)


async def _record_audit(
    request: Request,
    *,
    action: str,
    site_id: str | None,
    result: str = "success",
    error: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    user_fields = _user_fields(request)
    await _audit_repo().record_audit(
        {
            "created_at": datetime.now(UTC),
            **user_fields,
            "user_role": getattr(getattr(request.state, "user", None), "system_role", None),
            "action": f"site_navigation.{action}",
            "resource_type": "site_navigation",
            "resource_id": site_id,
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


def _duplicate_name_error() -> HTTPException:
    return HTTPException(status_code=409, detail="A site with this name already exists")


@router.get("/api/site-navigation")
async def list_public_site_navigation(limit: int = Query(default=200, ge=1, le=500)):
    return {"items": await _repo().list_sites(include_disabled=False, limit=limit)}


@router.get("/api/admin/site-navigation")
async def list_admin_site_navigation(
    request: Request,
    include_disabled: bool = Query(default=True),
    limit: int = Query(default=500, ge=1, le=1000),
):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    return {"items": await _repo().list_sites(include_disabled=include_disabled, limit=limit)}


@router.post("/api/admin/site-navigation")
async def create_site_navigation(request: Request, body: SiteNavigationWriteRequest):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    user_fields = _user_fields(request)
    try:
        item = await _repo().create(
            {
                **body.model_dump(),
                "created_by_user_id": user_fields["user_id"],
                "created_by_email": user_fields["user_email"],
                "updated_by_user_id": user_fields["user_id"],
                "updated_by_email": user_fields["user_email"],
            }
        )
    except IntegrityError as exc:
        await _record_audit(request, action="create", site_id=None, result="failure", error=str(exc), details={"name": body.name})
        raise _duplicate_name_error() from exc
    await _record_audit(request, action="create", site_id=item["id"], details={"name": item["name"]})
    return {"item": item}


@router.patch("/api/admin/site-navigation/{site_id}")
async def update_site_navigation(request: Request, site_id: str, body: SiteNavigationPatchRequest):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    if await _repo().get(site_id) is None:
        raise HTTPException(status_code=404, detail="Site navigation item not found")
    user_fields = _user_fields(request)
    updates = body.model_dump(exclude_unset=True)
    updates.update(
        {
            "updated_by_user_id": user_fields["user_id"],
            "updated_by_email": user_fields["user_email"],
        }
    )
    try:
        item = await _repo().update(site_id, updates)
    except IntegrityError as exc:
        await _record_audit(request, action="update", site_id=site_id, result="failure", error=str(exc))
        raise _duplicate_name_error() from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Site navigation item not found")
    await _record_audit(request, action="update", site_id=site_id, details={"name": item["name"]})
    return {"item": item}


@router.delete("/api/admin/site-navigation/{site_id}")
async def delete_site_navigation(request: Request, site_id: str):
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    deleted = await _repo().delete(site_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Site navigation item not found")
    await _record_audit(request, action="delete", site_id=site_id)
    return {"success": True}
