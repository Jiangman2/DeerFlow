"""Admin observability endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.deps import get_config, get_run_manager, require_admin_user
from app.gateway.routers.thread_runs import RunCreateRequest
from app.gateway.services import start_run
from deerflow.config.app_config import AppConfig
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.observability.sql import ObservabilityRepository

router = APIRouter(prefix="/api/admin/observability", tags=["observability"])


def _repo() -> ObservabilityRepository:
    return ObservabilityRepository(get_session_factory())


class DispositionRequest(BaseModel):
    action: Literal[
        "copy_run_id",
        "copy_thread_id",
        "open_thread",
        "retry_run",
        "cancel_run",
        "mark_handled",
        "add_note",
    ]
    note: str | None = Field(default=None, max_length=4000)
    status: Literal["open", "handled", "ignored"] | None = None


def _user_fields(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    return {
        "user_id": str(user_id) if user_id is not None else None,
        "user_email": getattr(user, "email", None),
    }


async def _record_action_audit(
    request: Request,
    *,
    run_id: str,
    thread_id: str,
    action: str,
    result: str = "success",
    error: str | None = None,
    details: dict | None = None,
) -> None:
    user_fields = _user_fields(request)
    await _repo().record_audit(
        {
            "created_at": datetime.now(UTC),
            **user_fields,
            "user_role": getattr(getattr(request.state, "user", None), "system_role", None),
            "action": f"observability.{action}",
            "resource_type": "run",
            "resource_id": run_id,
            "method": "POST",
            "path": f"/api/admin/observability/runs/{run_id}/dispositions",
            "status_code": 200 if result == "success" else 500,
            "result": result,
            "duration_ms": 0,
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "error": error,
            "details_json": {"thread_id": thread_id, **(details or {})},
        }
    )


@router.get("/snapshot")
async def get_observability_snapshot(
    request: Request,
    hours: int = Query(default=24, ge=1, le=24 * 90),
    limit: int = Query(default=50, ge=1, le=500),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
):
    await require_admin_user(request, detail="Admin privileges are required to view observability data")
    return await _repo().snapshot(hours=hours, limit=limit, start_at=start_at, end_at=end_at)


@router.get("/analytics")
async def get_observability_analytics(
    request: Request,
    hours: int = Query(default=24, ge=1),
    bucket: Literal["hour", "day"] = Query(default="hour"),
    limit: int = Query(default=10, ge=1, le=100),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
):
    await require_admin_user(request, detail="Admin privileges are required to view observability analytics")
    return await _repo().analytics(hours=hours, bucket=bucket, limit=limit, start_at=start_at, end_at=end_at)


@router.get("/user-activity")
async def get_user_activity(
    request: Request,
    hours: int = Query(default=24, ge=1, le=24 * 90),
    limit: int = Query(default=100, ge=1, le=500),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
):
    await require_admin_user(request, detail="Admin privileges are required to view user activity analytics")
    return await _repo().user_activity(hours=hours, start_at=start_at, end_at=end_at, limit=limit)


@router.get("/audit-logs")
async def list_audit_logs(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    hours: int | None = Query(default=None, ge=1, le=24 * 30),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    user_id: str | None = Query(default=None),
    result: str | None = Query(default=None),
    operation_type: str | None = Query(default=None),
    q: str | None = Query(default=None),
    actions: list[str] | None = Query(default=None),
    resource_type: str | None = Query(default=None),
):
    await require_admin_user(request, detail="Admin privileges are required to view audit logs")
    return {
        "items": await _repo().list_audit_logs(
            limit=limit,
            hours=hours,
            start_at=start_at,
            end_at=end_at,
            user_id=user_id,
            result=result,
            operation_type=operation_type,
            q=q,
            actions=actions,
            resource_type=resource_type,
        )
    }


@router.get("/traces")
async def list_traces(
    request: Request,
    hours: int = Query(default=24, ge=1, le=24 * 30),
    limit: int = Query(default=100, ge=1, le=500),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    q: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    model: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    thread_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    error_type: str | None = Query(default=None),
):
    await require_admin_user(request, detail="Admin privileges are required to view trace data")
    return {
        "items": await _repo().list_traces(
            hours=hours,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
            q=q,
            user_id=user_id,
            status=status,
            model=model,
            agent=agent,
            thread_id=thread_id,
            run_id=run_id,
            error_type=error_type,
        )
    }


@router.get("/runs/{run_id}/diagnostics")
async def get_run_diagnostics(request: Request, run_id: str):
    await require_admin_user(request, detail="Admin privileges are required to view run diagnostics")
    diagnostics = await _repo().run_diagnostics(run_id)
    if diagnostics is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return diagnostics


@router.post("/runs/{run_id}/dispositions")
async def record_run_disposition(request: Request, run_id: str, body: DispositionRequest):
    await require_admin_user(request, detail="Admin privileges are required to operate on run diagnostics")
    repo = _repo()
    diagnostics = await repo.run_diagnostics(run_id)
    if diagnostics is None:
        raise HTTPException(status_code=404, detail="Run not found")
    run = diagnostics["run"]
    thread_id = run["thread_id"]
    result = "success"
    error: str | None = None
    details: dict = {}

    if body.action == "cancel_run":
        run_mgr = get_run_manager(request)
        cancelled = await run_mgr.cancel(run_id, action="interrupt")
        if not cancelled:
            result = "failure"
            error = "Run is not active on this worker or is not cancellable"
    elif body.action == "retry_run":
        question = diagnostics.get("user_question")
        if not question:
            result = "failure"
            error = "Run has no recorded user question to retry"
        else:
            create_body = RunCreateRequest(
                assistant_id=diagnostics.get("agent"),
                input={"messages": [{"type": "human", "content": [{"type": "text", "text": question}]}]},
                metadata={"retry_of_run_id": run_id, "source": "observability"},
                context={"model_name": diagnostics.get("model")} if diagnostics.get("model") else None,
                multitask_strategy="enqueue",
                on_disconnect="continue",
            )
            try:
                record = await start_run(create_body, thread_id, request)
                details["new_run_id"] = record.run_id
            except Exception as exc:
                result = "failure"
                error = str(exc)[:1000]

    user_fields = _user_fields(request)
    disposition = await repo.record_disposition(
        {
            "run_id": run_id,
            "thread_id": thread_id,
            "action": body.action,
            "status": body.status or ("handled" if body.action == "mark_handled" else None),
            "note": body.note,
            **user_fields,
            "result": result,
            "details_json": details,
        }
    )
    await _record_action_audit(
        request,
        run_id=run_id,
        thread_id=thread_id,
        action=body.action,
        result=result,
        error=error,
        details=details,
    )
    if result != "success":
        raise HTTPException(status_code=409, detail=error or "Disposition action failed")
    return {"item": disposition, "details": details}


@router.get("/traces/{run_id}/events")
async def list_trace_events(
    request: Request,
    run_id: str,
    limit: int = Query(default=200, ge=1, le=500),
):
    await require_admin_user(request, detail="Admin privileges are required to view trace events")
    return {"items": await _repo().trace_events(run_id, limit=limit)}


# ---------------------------------------------------------------------------
# Channel (IM bot) audit views
# ---------------------------------------------------------------------------
#
# IM activity is written by the channel layer into the same ``audit_logs`` table
# (``resource_type="channel"``), so every endpoint here is a filtered view over
# data that already exists. Aggregation lives in the repository
# (``audit_summary``) rather than here: the same digest has to be reproducible
# from the UI and from the scheduled pusher.


class ChannelAuditFilters(BaseModel):
    """Shared filter surface for the channel audit views."""

    hours: int | None = Field(default=24, ge=1, le=24 * 30)
    start_at: datetime | None = None
    end_at: datetime | None = None
    user_id: str | None = None
    result: str | None = None
    q: str | None = None
    actions: list[str] | None = None
    limit: int = Field(default=5000, ge=1, le=20000)
    error_samples: int = Field(default=20, ge=0, le=200)
    bucket_minutes: int = Field(default=60, ge=1, le=1440)

    def summary_kwargs(self) -> dict:
        return {
            "hours": self.hours,
            "start_at": self.start_at,
            "end_at": self.end_at,
            "user_id": self.user_id,
            "result": self.result,
            "q": self.q,
            "actions": self.actions,
            "resource_type": "channel",
            "limit": self.limit,
            "error_samples": self.error_samples,
            "bucket_minutes": self.bucket_minutes,
        }


class ChannelAuditSendRequest(ChannelAuditFilters):
    chat_id: str | None = Field(default=None, description="Target IM chat; defaults to the configured notify chat")
    title: str | None = Field(default=None, max_length=120)


class ChannelAuditAnalyzeRequest(ChannelAuditFilters):
    question: str | None = Field(default=None, max_length=2000, description="Optional extra context from the operator")
    model_name: str | None = Field(default=None, description="Optional model override for the analysis call")
    send_to_channel: bool = Field(default=False, description="Also push the analysis into the IM chat")
    chat_id: str | None = None
    title: str | None = Field(default=None, max_length=120)


async def _record_channel_audit_action(
    request: Request,
    *,
    action: str,
    result: str = "success",
    error: str | None = None,
    details: dict | None = None,
) -> None:
    """Record that an operator pushed/analysed channel logs.

    Uses ``resource_type="channel_audit"`` on purpose: these rows describe the
    *reporting* activity, and tagging them as ``channel`` would make them show up
    inside the very digest that produced them.
    """
    user_fields = _user_fields(request)
    await _repo().record_audit(
        {
            "created_at": datetime.now(UTC),
            **user_fields,
            "user_role": getattr(getattr(request.state, "user", None), "system_role", None),
            "action": action,
            "resource_type": "channel_audit",
            "resource_id": (details or {}).get("chat_id"),
            "method": "POST",
            "path": request.url.path,
            "status_code": 200 if result == "success" else 500,
            "result": result,
            "duration_ms": 0,
            "client_ip": request.client.host if request.client else None,
            "user_agent": request.headers.get("user-agent"),
            "error": error,
            "details_json": details or {},
        }
    )


@router.get("/channel-audit")
async def get_channel_audit(
    request: Request,
    hours: int | None = Query(default=24, ge=1, le=24 * 30),
    start_at: datetime | None = Query(default=None),
    end_at: datetime | None = Query(default=None),
    user_id: str | None = Query(default=None),
    result: str | None = Query(default=None),
    q: str | None = Query(default=None),
    actions: list[str] | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=20000),
    error_samples: int = Query(default=20, ge=0, le=200),
    bucket_minutes: int = Query(default=60, ge=1, le=1440),
):
    """Aggregate IM bot activity, plus the rendered digest text.

    The ``digest`` string is exactly what ``/channel-audit/send`` would push, so
    the UI can show a faithful preview before anything leaves the building.
    """
    await require_admin_user(request, detail="Admin privileges are required to view channel audit logs")

    from app.channels.audit_report import render_digest

    summary = await _repo().audit_summary(
        hours=hours,
        start_at=start_at,
        end_at=end_at,
        user_id=user_id,
        result=result,
        q=q,
        actions=actions,
        resource_type="channel",
        limit=limit,
        error_samples=error_samples,
        bucket_minutes=bucket_minutes,
    )
    return {"summary": summary, "digest": render_digest(summary)}


@router.post("/channel-audit/send")
async def send_channel_audit(request: Request, body: ChannelAuditSendRequest):
    """Render the digest for the given filters and push it into the IM chat."""
    await require_admin_user(request, detail="Admin privileges are required to send channel audit reports")

    from app.channels.audit_report import render_digest, send_digest

    summary = await _repo().audit_summary(**body.summary_kwargs())
    digest = render_digest(summary, title=body.title)
    delivery = await send_digest(digest, chat_id=body.chat_id)
    await _record_channel_audit_action(
        request,
        action="channel_audit.send",
        result="success" if delivery.get("sent") else "failure",
        error=None if delivery.get("sent") else str(delivery.get("reason") or ""),
        details={
            "chat_id": delivery.get("chat_id"),
            "events": (summary.get("totals") or {}).get("events"),
            "failure": (summary.get("totals") or {}).get("failure"),
            "window": summary.get("window"),
        },
    )
    return {"summary": summary, "digest": digest, "delivery": delivery}


@router.post("/channel-audit/analyze")
async def analyze_channel_audit(
    request: Request,
    body: ChannelAuditAnalyzeRequest,
    config: AppConfig = Depends(get_config),
):
    """Ask a model why these failures happened, optionally pushing the answer to IM."""
    await require_admin_user(request, detail="Admin privileges are required to analyse channel audit logs")

    from app.channels.audit_analysis import analyze_digest, render_analysis_message
    from app.channels.audit_report import send_digest

    summary = await _repo().audit_summary(**body.summary_kwargs())
    if not (summary.get("totals") or {}).get("events"):
        raise HTTPException(status_code=404, detail="No channel audit events match these filters")

    try:
        analysis = await analyze_digest(
            summary,
            app_config=config,
            model_name=body.model_name,
            question=body.question,
        )
    except Exception as exc:
        await _record_channel_audit_action(
            request,
            action="channel_audit.analyze",
            result="failure",
            error=str(exc)[:1000],
            details={"chat_id": body.chat_id, "window": summary.get("window")},
        )
        raise HTTPException(status_code=503, detail="Failed to analyse the selected audit window") from exc

    message = render_analysis_message(summary, analysis, title=body.title)
    delivery: dict = {"sent": False, "reason": "not requested", "chat_id": None}
    if body.send_to_channel:
        delivery = await send_digest(message, chat_id=body.chat_id)

    await _record_channel_audit_action(
        request,
        action="channel_audit.analyze",
        result="success",
        details={
            "chat_id": delivery.get("chat_id"),
            "events": (summary.get("totals") or {}).get("events"),
            "failure": (summary.get("totals") or {}).get("failure"),
            "model": body.model_name,
            "sent": bool(delivery.get("sent")),
            "window": summary.get("window"),
        },
    )
    return {"summary": summary, "analysis": analysis, "message": message, "delivery": delivery}
