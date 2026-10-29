"""Read-only competition feed over the same published-employee click ledger."""

import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from app.gateway.competition_display_store import DisplayConfig, DisplayConflict, SelectedEmployee, read_display, save_display
from app.gateway.dashboard_rollups import DashboardQueryBusy, apply_dashboard_statement_limits, dashboard_query_slot
from app.gateway.deps import get_current_user_from_request, require_admin_user
from app.gateway.routers import employee_analytics as analytics

router = APIRouter(tags=["competition"])
BEIJING = timezone(timedelta(hours=8))
CACHE_SECONDS = 5
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
_lock = asyncio.Lock()
_display_cache: tuple[float, str, dict] | None = None


def _now() -> datetime:
    return datetime.now(BEIJING)


class UsageSummary(BaseModel):
    agent_name: str
    total_clicks: int
    today_clicks: int
    updated_at: datetime


def _authorize(request: Request) -> dict[str, str]:
    expected = os.getenv("COMPETITION_USAGE_API_KEY", "")
    if len(expected) < 32:
        raise HTTPException(503, "competition_feed_not_configured")
    values = request.headers.getlist("authorization")
    header = values[0] if len(values) == 1 else ""
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied or not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise HTTPException(401, "invalid_competition_credential", headers={"WWW-Authenticate": "Bearer"})
    raw = os.getenv("COMPETITION_USAGE_AGENTS_JSON", "{}")
    try:
        if len(raw.encode()) > 32768:
            raise ValueError
        aliases = json.loads(raw)
        if not isinstance(aliases, dict) or not 0 < len(aliases) <= 100:
            raise ValueError
        if any(not isinstance(k, str) or not k.strip() or len(k) > 128 or not isinstance(v, str) or not re.fullmatch(r"[a-f0-9]{64}", v) for k, v in aliases.items()):
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(503, "competition_roster_not_configured") from None
    return aliases


@router.get("/api/competition/agent-usage", response_model=UsageSummary)
async def agent_usage(request: Request, response: Response, name: str = Query(..., min_length=1, max_length=128)) -> UsageSummary:
    aliases = _authorize(request)
    identity = aliases.get(name)
    if identity is None:
        raise HTTPException(404, "competition_agent_not_configured")
    # A name is an operator-agreed alias for one stable publisher identity.
    # Never guess among same-name employees or return withdrawn/private entries.
    try:
        async with asyncio.timeout(10):
            roster = await asyncio.to_thread(analytics._published_catalog)
    except Exception:
        raise HTTPException(503, "competition_statistics_unavailable") from None

    entries = [entry for entry in roster if entry.selection_key == identity]
    if len(entries) != 1:
        raise HTTPException(409, "competition_agent_no_longer_available")
    now = _now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    fingerprint = hashlib.sha256(json.dumps(aliases, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = (fingerprint, name)
    response.headers["Cache-Control"] = "no-store"
    try:
        # A per-worker bounded cache coalesces polling; no identities or tokens
        # are cached. Revalidate the configured key and publication on every GET.
        async with asyncio.timeout(10), _lock:
            cached = _cache.get(key)
            if cached and time.monotonic() < cached[0] and cached[1]["updated_at"].date() == now.date():
                return UsageSummary(**cached[1])
            async with dashboard_query_slot():
                async with analytics._session_factory()() as session:
                    await apply_dashboard_statement_limits(session, maintenance=False)
                    metrics = await analytics.aggregate_clicks(session, entries, start, now)
            today, _, total = metrics.get(identity, (0, 0, 0))
            result = UsageSummary(agent_name=name, total_clicks=total, today_clicks=today, updated_at=now)
            if len(_cache) >= 100:
                _cache.clear()
            _cache[key] = (time.monotonic() + CACHE_SECONDS, result.model_dump())
            return result
    except DashboardQueryBusy:
        raise HTTPException(503, "competition_statistics_busy") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "competition_statistics_unavailable") from None


@router.get("/api/admin/competition/display")
async def display_settings(request: Request, response: Response):
    await require_admin_user(request, detail="Only administrators can configure the competition display")
    response.headers["Cache-Control"] = "no-store"
    try:
        return await asyncio.to_thread(read_display)
    except Exception:
        raise HTTPException(503, "competition_display_unavailable") from None


@router.get("/api/admin/competition/employees")
async def display_choices(request: Request, response: Response, query: str = Query("", max_length=128), offset: int = Query(0, ge=0, le=10000), limit: int = Query(50, ge=1, le=100)):
    await require_admin_user(request, detail="Only administrators can configure the competition display")
    response.headers["Cache-Control"] = "no-store"
    entries = await asyncio.to_thread(analytics._published_catalog)
    if len(entries) > 10000:
        raise HTTPException(503, "competition_catalog_too_large")
    rows = [
        {"identity": e.selection_key, "publication": e.token, "name": analytics._label(e), "source": e.source}
        for e in entries
        if not query.strip() or query.strip().casefold() in analytics._label(e).casefold() or query.strip().casefold() in e.name.casefold()
    ]
    rows.sort(key=lambda e: (e["name"], e["identity"]))
    return {"items": rows[offset : offset + limit], "total": len(rows)}


@router.put("/api/admin/competition/display")
async def update_display(request: Request, body: DisplayConfig):
    await require_admin_user(request, detail="Only administrators can configure the competition display")
    user = await get_current_user_from_request(request)
    if body.enabled and not body.employees:
        raise HTTPException(422, "select_at_least_one_employee")
    if len({e.identity for e in body.employees}) != len(body.employees):
        raise HTTPException(422, "duplicate_employee_selection")
    try:
        entries = await asyncio.to_thread(analytics._published_catalog)
        catalog = {e.selection_key: e for e in entries}
        selected = []
        for item in body.employees:
            entry = catalog.get(item.identity)
            if entry is None or entry.token != item.publication:
                if not body.enabled:
                    continue
                raise HTTPException(409, "employee_publication_changed_refresh_selection")
            # Ignore browser-provided names and actor/time metadata.
            selected.append(SelectedEmployee(identity=entry.selection_key, publication=entry.token, name=analytics._label(entry)))
        canonical = body.model_copy(update={"employees": selected})
        return await asyncio.to_thread(save_display, canonical, str(user.id))
    except DisplayConflict:
        raise HTTPException(409, "display_changed_by_another_administrator") from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "competition_display_save_unavailable") from None


@router.get("/api/competition/display")
async def public_display(response: Response):
    """Explicitly published aggregate-only board; no cookie or API key needed."""
    global _display_cache
    response.headers["Cache-Control"] = "no-store"
    try:
        async with asyncio.timeout(12):
            config = await asyncio.to_thread(read_display)
            if not config.enabled:
                raise HTTPException(404, "competition_display_not_published", headers={"Cache-Control": "no-store"})
            catalog = {e.selection_key: e for e in await asyncio.to_thread(analytics._published_catalog)}
            # A withdrawn or republished identity must be explicitly selected
            # again; it cannot silently appear as a new competition entrant.
            entries = [catalog[e.identity] for e in config.employees if e.identity in catalog and catalog[e.identity].token == e.publication]
            signature = json.dumps([config.model_dump(), [(e.selection_key, e.token, analytics._label(e)) for e in entries]], sort_keys=True)
            now = _now()
            async with _lock:
                cached = _display_cache
                if cached and cached[1] == signature and cached[0] > time.monotonic() and cached[2]["updated_at"].date() == now.date():
                    return cached[2]
                async with dashboard_query_slot():
                    async with analytics._session_factory()() as session:
                        await apply_dashboard_statement_limits(session, maintenance=False)
                        metrics = await analytics.aggregate_clicks(session, entries, now.replace(hour=0, minute=0, second=0, microsecond=0), now)
                items = [{"identity": e.selection_key, "name": analytics._label(e), "total_clicks": metrics.get(e.selection_key, (0, 0, 0))[2], "today_clicks": metrics.get(e.selection_key, (0, 0, 0))[0]} for e in entries]
                data = {"title": config.title, "items": items, "updated_at": now, "unavailable_count": len(config.employees) - len(entries), "refresh_seconds": 10}
                _display_cache = (time.monotonic() + CACHE_SECONDS, signature, data)
                return data
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(503, "competition_statistics_unavailable", headers={"Cache-Control": "no-store"}) from None
