"""Independent admin monitoring endpoint; no business database reads."""

import json
from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from app.gateway.dashboard_cache import DashboardResponseCache
from app.gateway.deps import require_admin_user
from app.gateway.model_monitor import model_monitor

router = APIRouter(prefix="/api/admin/model-monitor", tags=["model-monitor"])
_cache = DashboardResponseCache(max_entries=3)


@router.get("")
async def get_model_monitor(request: Request, response: Response, minutes: Literal["15", "60"] = Query(default="15")):
    await require_admin_user(request, detail="Administrator access required")
    response.headers["Cache-Control"] = "no-store"
    # Query parameters arrive as strings; numeric Literals reject even ?minutes=15.
    window = int(minutes)
    result = await _cache.get_or_create(f"model-monitor-v1:{model_monitor.prefix}:{window}", ttl_seconds=10, producer=lambda: model_monitor.read(window), encode=json.dumps, decode=json.loads)
    return result.value
