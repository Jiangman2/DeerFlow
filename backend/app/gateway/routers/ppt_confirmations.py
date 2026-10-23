"""Owner-authenticated stable confirmation state and explicit page recovery.

GET reads durable state only. Opening/recovering a native page is an owner-only,
CSRF-protected POST which first disables automation for the current task. No
browser request supplies a port, Sandbox address, project, session or choice.
"""

from __future__ import annotations

import asyncio
import os
import time
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.gateway.authz import get_auth_context, require_permission
from app.gateway.deps import get_run_store
from deerflow.persistence import get_session_factory
from deerflow.persistence.ppt_confirmation.sql import ConfirmationRepository, Scope
from deerflow.ppt_confirmation import lifecycle as core
from deerflow.ppt_confirmation.monitor import public_status

router = APIRouter(prefix="/threads/{thread_id}/ppt-confirmations", tags=["ppt-confirmations"])


async def owned_state(request, thread_id, task_id):
    from app.gateway.routers.artifacts import _resolve_artifact_owner_user_id

    try:
        core.identifier(thread_id)
        core.identifier(task_id)
    except core.ConsentConflict:
        raise HTTPException(404, "PPT confirmation unavailable") from None
    owner = await _resolve_artifact_owner_user_id(request, thread_id)
    if not owner:
        raise HTTPException(404, "PPT confirmation unavailable")
    row = await get_run_store(request).get(task_id, user_id=owner)
    if not isinstance(row, dict) or any(row.get(key) != value for key, value in (("run_id", task_id), ("thread_id", thread_id), ("user_id", owner), ("assistant_id", "ppt-master-runner"))):
        raise HTTPException(404, "PPT confirmation unavailable")
    factory = get_session_factory()
    if factory is None:
        raise HTTPException(503, "PPT confirmation store unavailable")
    scope = Scope(owner, thread_id, task_id)
    repository = ConfirmationRepository(factory)
    snapshot = await repository.get(scope)
    return scope, repository, snapshot, row


def safe_state(scope, snapshot, row, *, viewer_id):
    active = row.get("status") in {"pending", "running"} and not row.get("cancel_action")
    return {
        "protocol": "ppt-confirmation-v1",
        "thread_id": scope.thread_id,
        "task_id": scope.task_id,
        "admitted": snapshot is not None,
        "revision": snapshot["revision"] if snapshot else None,
        "auto_enabled": snapshot["policy"]["auto_enabled"] if snapshot else False,
        "cancelled": snapshot["cancelled"] if snapshot else False,
        "run_active": active,
        "can_open": viewer_id == scope.owner_id and active and snapshot is not None and not snapshot["cancelled"],
        "gate": public_status(snapshot) if snapshot and not snapshot["cancelled"] else None,
        "server_time": int(time.time()),
    }


@router.get("/{task_id}/state")
@require_permission("threads", "read", owner_check=True, require_existing=True)
async def confirmation_state(thread_id: str, task_id: str, request: Request):
    scope, _repository, snapshot, row = await owned_state(request, thread_id, task_id)
    user = get_auth_context(request).require_user()
    return JSONResponse(safe_state(scope, snapshot, row, viewer_id=user.id), headers={"Cache-Control": "private, no-store"})


async def open_native_page(request, scope):
    # Match the existing preview ownership boundary: only Runner may discover
    # its Sandbox. Gateway forwards the existing browser credentials instead.
    from app.gateway.routers.artifacts import _forward_live_preview_to_runner

    if os.environ.get("DEER_FLOW_RUNTIME_PROFILE") != "ppt-master-runner":
        # The bounded validator already consumed the original ASGI stream.
        # Forward only the accepted empty body, keeping the authenticated scope
        # and original browser/CSRF headers. Never reread a consumed stream.
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        return await _forward_live_preview_to_runner(Request(request.scope, receive), scope.thread_id)

    import httpx

    from deerflow.config.app_config import get_app_config
    from deerflow.sandbox.sandbox_provider import get_sandbox_provider

    provider = get_sandbox_provider()
    sandbox = await asyncio.to_thread(provider.get_for_thread, scope.thread_id, user_id=scope.owner_id)
    if sandbox is None:
        raise HTTPException(409, "PPT Sandbox recovery is required; no Agent was started")
    base = urlsplit(getattr(sandbox, "base_url", ""))
    if base.scheme != "http" or not base.hostname or base.username or base.password:
        raise HTTPException(409, "PPT Host unavailable")
    host = urlunsplit(("http", f"{base.hostname}:8788", f"/v1/tasks/{scope.task_id}/confirmation", "", ""))
    key = get_app_config().sandbox.sandbox_api_key
    if not key:
        raise HTTPException(503, "PPT Host authentication unavailable")
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=20, headers={"Authorization": "Bearer " + key}) as client:
            result = await client.get(host)
            result.raise_for_status()
            value = result.json()
            if isinstance(value, dict) and value.get("state") == "owner_unreachable":
                result = await client.post(host + "/recover", json={})
                result.raise_for_status()
                value = result.json()
        if not isinstance(value, dict) or value.get("state") not in {"waiting_user", "preparing", "confirmed"}:
            raise ValueError("page_unavailable")
        port = value.get("port")
        if type(port) is not int or not 5050 <= port <= 5099:
            raise ValueError("invalid_port")
        return JSONResponse({"url": f"/api/threads/{scope.thread_id}/live-preview/{port}/", "auto_enabled": False, "confirmation_submitted": False, "agent_started": False}, headers={"Cache-Control": "private, no-store"})
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(409, "PPT confirmation page could not be recovered; no confirmation was submitted") from None


@router.post("/{task_id}/open")
@require_permission("threads", "write", owner_check=True, require_existing=True)
async def confirmation_open(thread_id: str, task_id: str, request: Request):
    # Strictly empty body. No user-controlled target, consent, version or path.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 2:
            raise HTTPException(400, "PPT confirmation open accepts no parameters")
    if bytes(body) not in {b"", b"{}"}:
        raise HTTPException(400, "PPT confirmation open accepts no parameters")
    scope, repository, snapshot, row = await owned_state(request, thread_id, task_id)
    if snapshot is None or snapshot["cancelled"] or row.get("status") not in {"pending", "running"} or row.get("cancel_action"):
        raise HTTPException(409, "PPT task is not active; history remains readable")
    gate = snapshot["gate"]
    if gate is None or gate["state"] in {"submission_pending", "submission_unknown", "continuation_pending", "continuation_unknown"}:
        raise HTTPException(409, "PPT confirmation effect is not yet reconciled")
    try:
        if snapshot["policy"]["auto_enabled"]:
            await repository.disable_auto(scope, expected_revision=snapshot["revision"])
    except core.ConsentConflict:
        raise HTTPException(409, "PPT confirmation state changed; refresh before opening") from None
    return await open_native_page(request, scope)
