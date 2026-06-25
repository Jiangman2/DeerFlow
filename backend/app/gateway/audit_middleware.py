"""Audit logging middleware for Gateway API requests."""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.observability.sql import ObservabilityRepository

logger = logging.getLogger(__name__)

_RESOURCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^/api/threads/(?P<id>[^/]+)"), "thread"),
    (re.compile(r"^/api/runs/(?P<id>[^/]+)"), "run"),
    (re.compile(r"^/api/agents/(?P<id>[^/]+)"), "agent"),
    (re.compile(r"^/api/skills/(?P<id>[^/]+)"), "skill"),
    (re.compile(r"^/api/channels/(?P<id>[^/]+)"), "channel"),
    (re.compile(r"^/api/mcp(?:/config)?"), "mcp"),
    (re.compile(r"^/api/memory"), "memory"),
    (re.compile(r"^/api/v1/auth/(?P<id>[^/]+)"), "auth"),
)

_SKIP_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/admin/observability",
)


def _should_audit(path: str) -> bool:
    if any(path.startswith(prefix) for prefix in _SKIP_PREFIXES):
        return False
    return path.startswith("/api/")


def _resource_for_path(path: str) -> tuple[str | None, str | None]:
    for pattern, resource_type in _RESOURCE_PATTERNS:
        match = pattern.search(path)
        if match:
            return resource_type, match.groupdict().get("id")
    if path.startswith("/api/"):
        parts = [part for part in path.split("/") if part]
        return (parts[1] if len(parts) > 1 else "api"), None
    return None, None


def _action_for(method: str, path: str, resource_type: str | None) -> str:
    verb = method.lower()
    if resource_type == "auth":
        tail = path.rstrip("/").rsplit("/", 1)[-1]
        return f"auth.{tail}"
    if resource_type == "thread" and "/runs" in path:
        return "thread.run.create" if verb == "post" else f"thread.run.{verb}"
    if resource_type:
        return f"{resource_type}.{verb}"
    return f"api.{verb}"


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else None


def _user_fields(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    return {
        "user_id": str(user_id) if user_id is not None else None,
        "user_email": getattr(user, "email", None),
        "user_role": getattr(user, "system_role", None),
    }


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Persist a compact audit record for Gateway API requests."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        path = request.url.path
        if not _should_audit(path):
            return await call_next(request)

        started = time.perf_counter()
        status_code = 500
        error: str | None = None
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            error = str(exc)[:2000]
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            resource_type, resource_id = _resource_for_path(path)
            event = {
                "created_at": datetime.now(UTC),
                **_user_fields(request),
                "action": _action_for(request.method, path, resource_type),
                "resource_type": resource_type,
                "resource_id": resource_id,
                "method": request.method.upper(),
                "path": path,
                "status_code": status_code,
                "result": "success" if status_code < 400 else "failure",
                "duration_ms": duration_ms,
                "client_ip": _client_ip(request),
                "user_agent": request.headers.get("user-agent"),
                "error": error,
                "details_json": {
                    "query": str(request.url.query)[:1000],
                    "auth_source": getattr(request.state, "auth_source", None),
                },
            }
            try:
                await ObservabilityRepository(get_session_factory()).record_audit(event)
            except Exception:
                logger.warning("Failed to persist audit log for %s %s", request.method, path, exc_info=True)
