"""Operational observability repository.

The dashboard intentionally reuses DeerFlow's existing persistence tables for
users, threads, runs, and run events. Audit events are the only new table.
"""

from __future__ import annotations

import json
import time
from collections import Counter, deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.model import AuditLogRow, OperationDispositionRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow
from deerflow.utils.time import coerce_iso

_MEMORY_AUDIT_LOGS: deque[dict[str, Any]] = deque(maxlen=1000)
_TERMINAL_ERROR_STATUSES = {"error", "timeout", "interrupted"}
_TERMINAL_STATUSES = {"success", *_TERMINAL_ERROR_STATUSES}
_ACTIVE_STATUSES = {"pending", "running"}
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
_MANAGEMENT_ACTIONS = {
    "announcement.create",
    "announcement.update",
    "announcement.status.published",
    "announcement.status.draft",
    "announcement.status.archived",
    "agent.post",
    "agent.put",
    "agent.patch",
    "agent.delete",
    "skill.post",
    "skill.put",
    "skill.patch",
    "skill.delete",
    "channel.post",
    "channel.put",
    "channel.patch",
    "channel.delete",
    "mcp.post",
    "mcp.put",
    "mcp.patch",
    "mcp.delete",
    "user.post",
    "user.put",
    "user.patch",
    "user.delete",
}
_MANAGEMENT_RESOURCES = {
    "agent",
    "announcement",
    "channel",
    "config",
    "knowledge",
    "mcp",
    "skill",
    "user",
    "users",
}
_CAUSE_LABELS = {
    "model_timeout": "模型超时",
    "tool_failure": "工具失败",
    "permission": "权限问题",
    "database": "数据库问题",
    "file_missing": "文件缺失",
    "config": "配置错误",
    "unknown": "未知原因",
}


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return coerce_iso(value)
    if value is None:
        return ""
    return str(value)


def _hours_ago(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=max(1, hours))


def _coerce_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _window_bounds(
    *,
    hours: int | None = 24,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> tuple[datetime, datetime, int]:
    end = _coerce_aware(end_at) or datetime.now(UTC)
    start = _coerce_aware(start_at) or (end - timedelta(hours=max(1, hours or 24)))
    if start >= end:
        start = end - timedelta(hours=1)
    window_hours = max(1, int((end - start).total_seconds() // 3600) or 1)
    return start, end, window_hours


def _duration_ms(created_at: datetime | None, updated_at: datetime | None) -> int:
    if not created_at or not updated_at:
        return 0
    try:
        return max(0, int((updated_at - created_at).total_seconds() * 1000))
    except Exception:
        return 0


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _is_management_action(data: dict[str, Any]) -> bool:
    method = str(data.get("method") or "").upper()
    action = str(data.get("action") or "")
    resource_type = data.get("resource_type")
    path = str(data.get("path") or "")
    if action in _MANAGEMENT_ACTIONS:
        return True
    if resource_type in _MANAGEMENT_RESOURCES and method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if path.startswith("/api/admin/") or "/config" in path:
        return True
    if resource_type == "thread" and method == "DELETE":
        return True
    if "export" in path and method in {"GET", "POST"}:
        return True
    return False


def _row_dict(row: AuditLogRow) -> dict[str, Any]:
    data = row.to_dict()
    data["created_at"] = _iso(data.get("created_at"))
    data["details"] = data.pop("details_json", {}) or {}
    data["operation_type"] = "management" if _is_management_action(data) else "api"
    data["is_management"] = data["operation_type"] == "management"
    return data


def _component(
    name: str,
    status: str,
    detail: str,
    *,
    metric: str | None = None,
    latency_ms: int | None = None,
    checked_at: datetime | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "metric": metric,
        "latency_ms": latency_ms,
        "last_checked_at": _iso(checked_at or datetime.now(UTC)),
        "error": error,
    }


def _json_content(row: RunEventRow) -> dict[str, Any] | None:
    try:
        value = json.loads(row.content or "{}")
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def _event_dict(row: RunEventRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "thread_id": row.thread_id,
        "run_id": row.run_id,
        "seq": row.seq,
        "event_type": row.event_type,
        "category": row.category,
        "content": row.content,
        "metadata": row.event_metadata or {},
        "created_at": _iso(row.created_at),
    }


def _disposition_dict(row: OperationDispositionRow) -> dict[str, Any]:
    data = row.to_dict()
    data["created_at"] = _iso(data.get("created_at"))
    data["details"] = data.pop("details_json", {}) or {}
    return data


def _run_summary(row: RunRow, user_email: dict[str, str | None], event_counts: dict[str, int]) -> dict[str, Any]:
    return {
        "trace_id": row.run_id,
        "run_id": row.run_id,
        "thread_id": row.thread_id,
        "user_id": row.user_id,
        "email": user_email.get(row.user_id or ""),
        "status": row.status,
        "model": row.model_name,
        "agent": row.assistant_id,
        "duration_ms": _duration_ms(row.created_at, row.updated_at),
        "tokens": row.total_tokens,
        "input_tokens": row.total_input_tokens,
        "output_tokens": row.total_output_tokens,
        "llm_calls": row.llm_call_count,
        "event_count": event_counts.get(row.run_id, 0),
        "started_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "error": row.error,
        "error_type": _classify_error(row, []),
        "user_question": row.first_human_message,
    }


def _caller(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("caller") or metadata.get("agent_name") or metadata.get("node")
    return str(value) if value is not None else None


def _extract_tool_calls(events: list[RunEventRow]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    pending: dict[str, dict[str, Any]] = {}
    for event in events:
        payload = _json_content(event) or {}
        if event.event_type == "llm.ai.response":
            for tool_call in payload.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                call_id = str(tool_call.get("id") or f"seq-{event.seq}")
                entry = {
                    "id": call_id,
                    "name": tool_call.get("name") or tool_call.get("function", {}).get("name"),
                    "args": tool_call.get("args") or tool_call.get("function", {}).get("arguments"),
                    "status": "pending",
                    "started_at": _iso(event.created_at),
                    "finished_at": None,
                    "duration_ms": None,
                    "result_preview": None,
                    "error": None,
                }
                pending[call_id] = entry
                calls.append(entry)
        elif event.event_type == "llm.tool.result":
            call_id = str(payload.get("tool_call_id") or f"seq-{event.seq}")
            entry = pending.get(call_id)
            if entry is None:
                entry = {
                    "id": call_id,
                    "name": payload.get("name"),
                    "args": None,
                    "status": "pending",
                    "started_at": None,
                    "finished_at": None,
                    "duration_ms": None,
                    "result_preview": None,
                    "error": None,
                }
                calls.append(entry)
            status = payload.get("status") or ("error" if payload.get("is_error") else "success")
            entry["status"] = status
            entry["name"] = entry.get("name") or payload.get("name")
            entry["finished_at"] = _iso(event.created_at)
            content = payload.get("content")
            entry["result_preview"] = str(content or "")[:500]
            if status not in {"success", "ok"}:
                entry["error"] = str(content or status)[:1000]
    return calls


def _tool_duration_ms(events: list[RunEventRow]) -> int:
    started_by_id: dict[str, datetime] = {}
    total = 0
    for event in events:
        payload = _json_content(event) or {}
        if event.event_type == "llm.ai.response":
            for tool_call in payload.get("tool_calls") or []:
                if not isinstance(tool_call, dict):
                    continue
                call_id = str(tool_call.get("id") or "")
                if call_id and event.created_at:
                    started_by_id[call_id] = event.created_at
        elif event.event_type == "llm.tool.result":
            call_id = str(payload.get("tool_call_id") or "")
            started_at = started_by_id.get(call_id)
            if started_at and event.created_at:
                total += _duration_ms(started_at, event.created_at)
    return total


def _bucket_key(value: datetime | None, bucket: str) -> str:
    if value is None:
        return "unknown"
    value = (_coerce_aware(value) or value).astimezone(_BUSINESS_TIMEZONE)
    if bucket == "day":
        return value.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    return value.replace(minute=0, second=0, microsecond=0).isoformat()


def _empty_metric_bucket(period_start: str) -> dict[str, Any]:
    return {
        "period_start": period_start,
        "runs": 0,
        "terminal_runs": 0,
        "error_runs": 0,
        "error_rate": 0,
        "tokens": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "lead_agent_tokens": 0,
        "subagent_tokens": 0,
        "middleware_tokens": 0,
        "avg_duration_ms": 0,
        "tool_duration_ms": 0,
    }


def _summarize_runs(rows: list[RunRow]) -> dict[str, Any]:
    runs = len(rows)
    terminal_rows = [row for row in rows if row.status in _TERMINAL_STATUSES]
    error_runs = sum(1 for row in rows if row.status in _TERMINAL_ERROR_STATUSES)
    durations = [_duration_ms(row.created_at, row.updated_at) for row in terminal_rows]
    durations = [value for value in durations if value]
    return {
        "runs": runs,
        "error_runs": error_runs,
        "error_rate": round(error_runs / len(terminal_rows), 4) if terminal_rows else 0,
        "tokens": sum(row.total_tokens or 0 for row in rows),
        "input_tokens": sum(row.total_input_tokens or 0 for row in rows),
        "output_tokens": sum(row.total_output_tokens or 0 for row in rows),
        "lead_agent_tokens": sum(row.lead_agent_tokens or 0 for row in rows),
        "subagent_tokens": sum(row.subagent_tokens or 0 for row in rows),
        "middleware_tokens": sum(row.middleware_tokens or 0 for row in rows),
        "avg_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
    }


def _comparison(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("runs", "error_rate", "tokens", "avg_duration_ms"):
        current_value = current.get(key, 0) or 0
        previous_value = previous.get(key, 0) or 0
        delta = current_value - previous_value
        if previous_value:
            change = round(delta / previous_value, 4)
        else:
            change = 1 if current_value else 0
        result[key] = {
            "current": current_value,
            "previous": previous_value,
            "delta": delta,
            "change_rate": change,
        }
    return result


def _classify_error(row: RunRow, events: list[RunEventRow]) -> str:
    if row.status not in _TERMINAL_ERROR_STATUSES and not row.error:
        return ""
    text_blob = " ".join(
        [
            row.status or "",
            row.error or "",
            *(event.event_type or "" for event in events),
            *(event.content or "" for event in events[-20:]),
        ]
    ).lower()
    if "timeout" in text_blob or "timed out" in text_blob:
        return "model_timeout"
    if "permission" in text_blob or "unauthorized" in text_blob or "forbidden" in text_blob or "403" in text_blob:
        return "permission"
    if "postgres" in text_blob or "database" in text_blob or "sqlalchemy" in text_blob or "connection refused" in text_blob:
        return "database"
    if "no such file" in text_blob or "file not found" in text_blob or "filenotfound" in text_blob:
        return "file_missing"
    if "config" in text_blob or "environment variable" in text_blob or "api key" in text_blob or "missing key" in text_blob:
        return "config"
    if any(event.event_type == "llm.tool.result" and '"status": "error"' in (event.content or "") for event in events):
        return "tool_failure"
    if "tool" in text_blob and ("error" in text_blob or "failed" in text_blob):
        return "tool_failure"
    if row.status in _TERMINAL_ERROR_STATUSES or row.error:
        return "unknown"
    return ""


def _error_cause(row: RunRow, events: list[RunEventRow]) -> dict[str, Any] | None:
    cause = _classify_error(row, events)
    if not cause:
        return None
    return {
        "type": cause,
        "label": _CAUSE_LABELS.get(cause, cause),
        "confidence": "high" if cause != "unknown" else "low",
    }


def _find_nodes(events: list[RunEventRow]) -> tuple[str | None, str | None]:
    last_success: str | None = None
    failed: str | None = None
    for event in events:
        metadata = event.event_metadata or {}
        caller = _caller(metadata)
        if event.event_type in {"llm.ai.response", "llm.tool.result"} and caller:
            last_success = caller
        status = str(metadata.get("status") or "").lower()
        if event.event_type.endswith("error") or status in {"error", "failure", "failed"}:
            failed = caller or event.event_type
    return last_success, failed


def _probe_file_writable(label: str) -> tuple[str, int, str | None]:
    started = time.perf_counter()
    try:
        root = Path.cwd() / ".deer-flow" / "observability-probes"
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f"{label}.tmp"
        probe.write_text("ok", encoding="utf-8")
        if probe.read_text(encoding="utf-8") != "ok":
            raise RuntimeError("probe readback mismatch")
        probe.unlink(missing_ok=True)
        return "healthy", int((time.perf_counter() - started) * 1000), None
    except Exception as exc:
        return "degraded", int((time.perf_counter() - started) * 1000), str(exc)[:500]


class ObservabilityRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None) -> None:
        self._sf = session_factory

    async def record_audit(self, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("created_at", datetime.now(UTC))
        payload.setdefault("details_json", {})
        payload.setdefault("result", "success")
        if self._sf is None:
            memory_payload = dict(payload)
            memory_payload["created_at"] = _iso(memory_payload.get("created_at"))
            memory_payload["details"] = memory_payload.pop("details_json", {}) or {}
            memory_payload["id"] = len(_MEMORY_AUDIT_LOGS) + 1
            _MEMORY_AUDIT_LOGS.appendleft(memory_payload)
            return
        async with self._sf() as session:
            session.add(AuditLogRow(**payload))
            await session.commit()

    async def record_disposition(self, event: dict[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        payload.setdefault("created_at", datetime.now(UTC))
        payload.setdefault("result", "success")
        payload.setdefault("details_json", {})
        if self._sf is None:
            return {
                **payload,
                "id": 0,
                "created_at": _iso(payload.get("created_at")),
                "details": payload.get("details_json", {}),
            }
        async with self._sf() as session:
            row = OperationDispositionRow(**payload)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _disposition_dict(row)

    async def list_dispositions(self, run_id: str) -> list[dict[str, Any]]:
        if self._sf is None:
            return []
        async with self._sf() as session:
            rows = (await session.execute(select(OperationDispositionRow).where(OperationDispositionRow.run_id == run_id).order_by(desc(OperationDispositionRow.created_at)))).scalars().all()
        return [_disposition_dict(row) for row in rows]

    async def latest_disposition_status(self, run_id: str) -> str | None:
        rows = await self.list_dispositions(run_id)
        for row in rows:
            if row.get("status"):
                return str(row["status"])
        return None

    async def list_audit_logs(
        self,
        *,
        limit: int = 100,
        hours: int | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        user_id: str | None = None,
        result: str | None = None,
        operation_type: str | None = None,
        q: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded_limit = min(max(limit, 1), 500)
        if self._sf is None:
            rows = list(_MEMORY_AUDIT_LOGS)
            if user_id:
                rows = [row for row in rows if row.get("user_id") == user_id]
            if result:
                rows = [row for row in rows if row.get("result") == result]
            rows = [{**row, "operation_type": "management" if _is_management_action(row) else "api"} for row in rows]
            if operation_type and operation_type != "all":
                rows = [row for row in rows if row.get("operation_type") == operation_type]
            if q:
                needle = q.lower()
                rows = [row for row in rows if needle in json.dumps(row, ensure_ascii=False).lower()]
            return rows[:bounded_limit]
        query_limit = min(max(bounded_limit * 5, bounded_limit), 2500) if operation_type and operation_type != "all" else bounded_limit
        stmt = select(AuditLogRow).order_by(desc(AuditLogRow.created_at)).limit(query_limit)
        if hours or start_at or end_at:
            since, until, _window_hours = _window_bounds(hours=hours, start_at=start_at, end_at=end_at)
            stmt = stmt.where(AuditLogRow.created_at >= since, AuditLogRow.created_at <= until)
        if user_id:
            stmt = stmt.where(AuditLogRow.user_id == user_id)
        if result:
            stmt = stmt.where(AuditLogRow.result == result)
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    AuditLogRow.user_email.ilike(pattern),
                    AuditLogRow.user_id.ilike(pattern),
                    AuditLogRow.action.ilike(pattern),
                    AuditLogRow.resource_type.ilike(pattern),
                    AuditLogRow.resource_id.ilike(pattern),
                    AuditLogRow.path.ilike(pattern),
                    AuditLogRow.client_ip.ilike(pattern),
                    AuditLogRow.error.ilike(pattern),
                )
            )
        async with self._sf() as session:
            result = await session.execute(stmt)
            rows = [_row_dict(row) for row in result.scalars()]
        if operation_type and operation_type != "all":
            rows = [row for row in rows if row.get("operation_type") == operation_type]
        return rows[:bounded_limit]

    async def list_traces(
        self,
        *,
        hours: int = 24,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 100,
        q: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        model: str | None = None,
        agent: str | None = None,
        thread_id: str | None = None,
        run_id: str | None = None,
        error_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._sf is None:
            return []
        bounded_limit = min(max(limit, 1), 500)
        since, until, _window_hours = _window_bounds(hours=hours, start_at=start_at, end_at=end_at)
        stmt = select(RunRow).where(RunRow.created_at >= since, RunRow.created_at <= until).order_by(desc(RunRow.created_at)).limit(1000)
        if user_id:
            stmt = stmt.where(RunRow.user_id == user_id)
        if status and status != "all":
            stmt = stmt.where(RunRow.status == status)
        if model and model != "all":
            stmt = stmt.where(RunRow.model_name == model)
        if agent and agent != "all":
            stmt = stmt.where(RunRow.assistant_id == agent)
        if thread_id:
            stmt = stmt.where(RunRow.thread_id.ilike(f"%{thread_id}%"))
        if run_id:
            stmt = stmt.where(RunRow.run_id.ilike(f"%{run_id}%"))
        if q:
            pattern = f"%{q}%"
            stmt = stmt.where(
                or_(
                    RunRow.run_id.ilike(pattern),
                    RunRow.thread_id.ilike(pattern),
                    RunRow.user_id.ilike(pattern),
                    RunRow.model_name.ilike(pattern),
                    RunRow.assistant_id.ilike(pattern),
                    RunRow.first_human_message.ilike(pattern),
                    RunRow.error.ilike(pattern),
                )
            )
        async with self._sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
            user_rows = await session.execute(select(UserRow.id, UserRow.email))
            user_email = {uid: email for uid, email in user_rows.all()}
            event_counts_rows = await session.execute(select(RunEventRow.run_id, func.count()).where(RunEventRow.run_id.in_([row.run_id for row in rows] or [""])).group_by(RunEventRow.run_id))
            event_counts = {rid: count for rid, count in event_counts_rows.all()}
            if error_type and error_type != "all":
                event_rows = (await session.execute(select(RunEventRow).where(RunEventRow.run_id.in_([row.run_id for row in rows] or [""])))).scalars().all()
                events_by_run: dict[str, list[RunEventRow]] = {}
                for event in event_rows:
                    events_by_run.setdefault(event.run_id, []).append(event)
                rows = [row for row in rows if _classify_error(row, events_by_run.get(row.run_id, [])) == error_type]

        summaries = [_run_summary(row, user_email, event_counts) for row in rows]
        if q:
            needle = q.lower()
            summaries = [item for item in summaries if needle in json.dumps(item, ensure_ascii=False).lower()]
        return summaries[:bounded_limit]

    async def analytics(
        self,
        *,
        hours: int = 24,
        bucket: str = "hour",
        limit: int = 10,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, Any]:
        current_since, current_until, window_hours = _window_bounds(hours=hours, start_at=start_at, end_at=end_at)
        span = current_until - current_since
        previous_since = current_since - span
        if self._sf is None:
            return {
                "generated_at": _iso(datetime.now(UTC)),
                "window_hours": window_hours,
                "window_start": _iso(current_since),
                "window_end": _iso(current_until),
                "bucket": bucket,
                "trend": [],
                "current": _summarize_runs([]),
                "previous": _summarize_runs([]),
                "comparison": _comparison(_summarize_runs([]), _summarize_runs([])),
                "slow_runs": [],
                "high_token_runs": [],
                "by_user": [],
                "by_model": [],
                "by_agent": [],
            }
        bounded_limit = min(max(limit, 1), 100)
        now = datetime.now(UTC)
        async with self._sf() as session:
            rows = (await session.execute(select(RunRow).where(RunRow.created_at >= previous_since, RunRow.created_at <= current_until).order_by(desc(RunRow.created_at)))).scalars().all()
            user_rows = await session.execute(select(UserRow.id, UserRow.email))
            user_email = {uid: email for uid, email in user_rows.all()}
            current_run_ids = [row.run_id for row in rows if _aware(row.created_at) and current_since <= _aware(row.created_at) <= current_until]
            event_rows = (await session.execute(select(RunEventRow).where(RunEventRow.run_id.in_(current_run_ids or [""])))).scalars().all()

        events_by_run: dict[str, list[RunEventRow]] = {}
        for event in event_rows:
            events_by_run.setdefault(event.run_id, []).append(event)
        tool_duration_by_run = {run_id: _tool_duration_ms(events) for run_id, events in events_by_run.items()}
        event_counts = {run_id: len(events) for run_id, events in events_by_run.items()}

        current_rows = [row for row in rows if _aware(row.created_at) and current_since <= _aware(row.created_at) <= current_until]
        previous_rows = [row for row in rows if _aware(row.created_at) and previous_since <= _aware(row.created_at) < current_since]
        trend_map: dict[str, dict[str, Any]] = {}
        trend_durations: dict[str, list[int]] = {}
        for row in current_rows:
            key = _bucket_key(row.created_at, bucket)
            item = trend_map.setdefault(key, _empty_metric_bucket(key))
            item["runs"] += 1
            item["terminal_runs"] += 1 if row.status in _TERMINAL_STATUSES else 0
            item["error_runs"] += 1 if row.status in _TERMINAL_ERROR_STATUSES else 0
            item["tokens"] += row.total_tokens or 0
            item["input_tokens"] += row.total_input_tokens or 0
            item["output_tokens"] += row.total_output_tokens or 0
            item["lead_agent_tokens"] += row.lead_agent_tokens or 0
            item["subagent_tokens"] += row.subagent_tokens or 0
            item["middleware_tokens"] += row.middleware_tokens or 0
            item["tool_duration_ms"] += tool_duration_by_run.get(row.run_id, 0)
            duration = _duration_ms(row.created_at, row.updated_at) if row.status in _TERMINAL_STATUSES else 0
            if duration:
                trend_durations.setdefault(key, []).append(duration)
        for key, item in trend_map.items():
            durations = trend_durations.get(key, [])
            item["avg_duration_ms"] = int(sum(durations) / len(durations)) if durations else 0
            item["error_rate"] = round(item["error_runs"] / item["terminal_runs"], 4) if item["terminal_runs"] else 0

        def breakdown(key_fn) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
            groups: dict[str, dict[str, Any]] = {}
            durations: dict[str, list[int]] = {}
            for row in current_rows:
                key = key_fn(row) or "unknown"
                item = groups.setdefault(
                    key,
                    {
                        "key": key,
                        "email": user_email.get(key) if key == row.user_id else None,
                        "runs": 0,
                        "terminal_runs": 0,
                        "error_runs": 0,
                        "tokens": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "lead_agent_tokens": 0,
                        "subagent_tokens": 0,
                        "middleware_tokens": 0,
                        "tool_duration_ms": 0,
                        "avg_duration_ms": 0,
                    },
                )
                item["runs"] += 1
                item["terminal_runs"] += 1 if row.status in _TERMINAL_STATUSES else 0
                item["error_runs"] += 1 if row.status in _TERMINAL_ERROR_STATUSES else 0
                item["tokens"] += row.total_tokens or 0
                item["input_tokens"] += row.total_input_tokens or 0
                item["output_tokens"] += row.total_output_tokens or 0
                item["lead_agent_tokens"] += row.lead_agent_tokens or 0
                item["subagent_tokens"] += row.subagent_tokens or 0
                item["middleware_tokens"] += row.middleware_tokens or 0
                item["tool_duration_ms"] += tool_duration_by_run.get(row.run_id, 0)
                duration = _duration_ms(row.created_at, row.updated_at) if row.status in _TERMINAL_STATUSES else 0
                if duration:
                    durations.setdefault(key, []).append(duration)
            for key, item in groups.items():
                values = durations.get(key, [])
                item["avg_duration_ms"] = int(sum(values) / len(values)) if values else 0
                item["error_rate"] = round(item["error_runs"] / item["terminal_runs"], 4) if item["terminal_runs"] else 0
            return sorted(groups.values(), key=lambda item: (item["tokens"], item["runs"]), reverse=True)[:bounded_limit]

        summaries = [_run_summary(row, user_email, event_counts) for row in current_rows if row.status in _TERMINAL_STATUSES]
        for item in summaries:
            item["tool_duration_ms"] = tool_duration_by_run.get(item["run_id"], 0)
        return {
            "generated_at": _iso(now),
            "window_hours": window_hours,
            "window_start": _iso(current_since),
            "window_end": _iso(current_until),
            "bucket": bucket,
            "trend": [trend_map[key] for key in sorted(trend_map)],
            "current": _summarize_runs(current_rows),
            "previous": _summarize_runs(previous_rows),
            "comparison": _comparison(_summarize_runs(current_rows), _summarize_runs(previous_rows)),
            "slow_runs": sorted(summaries, key=lambda item: item["duration_ms"], reverse=True)[:bounded_limit],
            "high_token_runs": sorted(summaries, key=lambda item: item["tokens"], reverse=True)[:bounded_limit],
            "by_user": breakdown(lambda row: row.user_id),
            "by_model": breakdown(lambda row: row.model_name),
            "by_agent": breakdown(lambda row: row.assistant_id),
        }

    async def user_activity(
        self,
        *,
        hours: int = 24,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        since, until, window_hours = _window_bounds(hours=hours, start_at=start_at, end_at=end_at)
        generated_at = datetime.now(UTC)
        empty_summary = {
            "total_users": 0,
            "active_users": 0,
            "activation_rate": 0,
            "total_runs": 0,
            "success_runs": 0,
            "error_runs": 0,
            "error_rate": 0,
            "total_tokens": 0,
            "avg_runs_per_active_user": 0,
            "avg_tokens_per_active_user": 0,
        }
        if self._sf is None:
            return {
                "generated_at": _iso(generated_at),
                "window_hours": window_hours,
                "window_start": _iso(since),
                "window_end": _iso(until),
                "bucket": "day" if window_hours > 72 else "hour",
                "summary": empty_summary,
                "trend": [],
                "users": [],
            }

        bounded_limit = min(max(limit, 1), 500)
        bucket = "day" if window_hours > 72 else "hour"
        async with self._sf() as session:
            rows = (await session.execute(select(RunRow).where(RunRow.created_at >= since, RunRow.created_at <= until).order_by(desc(RunRow.created_at)).limit(20_000))).scalars().all()
            users_total = await session.scalar(select(func.count()).select_from(UserRow).where(UserRow.is_disabled.is_(False))) or 0
            user_rows = await session.execute(select(UserRow.id, UserRow.email, UserRow.is_disabled))
            user_email = {uid: email for uid, email, _disabled in user_rows.all()}
            audit_rows = (
                await session.execute(
                    select(
                        AuditLogRow.user_id,
                        AuditLogRow.path,
                        AuditLogRow.method,
                        AuditLogRow.created_at,
                    )
                    .where(
                        AuditLogRow.created_at >= since,
                        AuditLogRow.created_at <= until,
                        AuditLogRow.user_id.is_not(None),
                        or_(
                            AuditLogRow.path.ilike("%upload%"),
                            AuditLogRow.path.ilike("%artifact%"),
                            AuditLogRow.path.ilike("%download%"),
                            AuditLogRow.path.ilike("%/files%"),
                        ),
                    )
                    .order_by(desc(AuditLogRow.created_at))
                    .limit(20_000)
                )
            ).all()

        users: dict[str, dict[str, Any]] = {}
        durations: dict[str, list[int]] = {}
        thread_ids: dict[str, set[str]] = {}
        agent_counts: dict[str, Counter[str]] = {}
        model_counts: dict[str, Counter[str]] = {}
        trend_map: dict[str, dict[str, Any]] = {}
        trend_users: dict[str, set[str]] = {}
        active_user_ids: set[str] = set()

        def ensure_user(user_id: str | None) -> dict[str, Any]:
            key = user_id or "anonymous"
            return users.setdefault(
                key,
                {
                    "user_id": key,
                    "email": user_email.get(key),
                    "runs": 0,
                    "success_runs": 0,
                    "error_runs": 0,
                    "error_rate": 0,
                    "tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "llm_calls": 0,
                    "threads": 0,
                    "avg_duration_ms": 0,
                    "uploads": 0,
                    "downloads": 0,
                    "last_active_at": None,
                    "agents": [],
                    "models": [],
                },
            )

        for row in rows:
            user_id = row.user_id or "anonymous"
            if row.user_id:
                active_user_ids.add(row.user_id)
            item = ensure_user(user_id)
            item["runs"] += 1
            item["success_runs"] += 1 if row.status == "success" else 0
            item["error_runs"] += 1 if row.status in _TERMINAL_ERROR_STATUSES else 0
            item["tokens"] += row.total_tokens or 0
            item["input_tokens"] += row.total_input_tokens or 0
            item["output_tokens"] += row.total_output_tokens or 0
            item["llm_calls"] += row.llm_call_count or 0
            if row.thread_id:
                thread_ids.setdefault(user_id, set()).add(row.thread_id)
            duration = _duration_ms(row.created_at, row.updated_at) if row.status in _TERMINAL_STATUSES else 0
            if duration:
                durations.setdefault(user_id, []).append(duration)
            if row.assistant_id:
                agent_counts.setdefault(user_id, Counter())[row.assistant_id] += 1
            if row.model_name:
                model_counts.setdefault(user_id, Counter())[row.model_name] += 1
            created_at = _aware(row.created_at)
            if created_at and (not item["last_active_at"] or created_at > item["last_active_at"]):
                item["last_active_at"] = created_at
            period = _bucket_key(row.created_at, bucket)
            bucket_item = trend_map.setdefault(
                period,
                {"period_start": period, "active_users": 0, "runs": 0, "tokens": 0},
            )
            bucket_item["runs"] += 1
            bucket_item["tokens"] += row.total_tokens or 0
            if row.user_id:
                trend_users.setdefault(period, set()).add(row.user_id)

        for user_id, path, method, created_at in audit_rows:
            item = ensure_user(user_id)
            if user_id:
                active_user_ids.add(user_id)
            lower_path = (path or "").lower()
            upper_method = (method or "").upper()
            if upper_method in {"POST", "PUT", "PATCH"} and ("upload" in lower_path or "/files" in lower_path):
                item["uploads"] += 1
            if upper_method == "GET" and ("artifact" in lower_path or "download" in lower_path):
                item["downloads"] += 1
            audit_created_at = _aware(created_at)
            if audit_created_at and (not item["last_active_at"] or audit_created_at > item["last_active_at"]):
                item["last_active_at"] = audit_created_at

        for user_id, item in users.items():
            item["threads"] = len(thread_ids.get(user_id, set()))
            item_durations = durations.get(user_id, [])
            item["avg_duration_ms"] = int(sum(item_durations) / len(item_durations)) if item_durations else 0
            terminal_runs = item["success_runs"] + item["error_runs"]
            item["error_rate"] = round(item["error_runs"] / terminal_runs, 4) if terminal_runs else 0
            item["agents"] = [key for key, _count in agent_counts.get(user_id, Counter()).most_common(3)]
            item["models"] = [key for key, _count in model_counts.get(user_id, Counter()).most_common(3)]
            item["last_active_at"] = _iso(item["last_active_at"])

        for period, item in trend_map.items():
            item["active_users"] = len(trend_users.get(period, set()))

        total_runs = len(rows)
        success_runs = sum(1 for row in rows if row.status == "success")
        error_runs = sum(1 for row in rows if row.status in _TERMINAL_ERROR_STATUSES)
        terminal_runs = success_runs + error_runs
        total_tokens = sum(row.total_tokens or 0 for row in rows)
        active_users = len(active_user_ids)
        summary = {
            "total_users": users_total,
            "active_users": active_users,
            "activation_rate": round(active_users / users_total, 4) if users_total else 0,
            "total_runs": total_runs,
            "success_runs": success_runs,
            "error_runs": error_runs,
            "error_rate": round(error_runs / terminal_runs, 4) if terminal_runs else 0,
            "total_tokens": total_tokens,
            "avg_runs_per_active_user": round(total_runs / active_users, 2) if active_users else 0,
            "avg_tokens_per_active_user": int(total_tokens / active_users) if active_users else 0,
        }
        return {
            "generated_at": _iso(generated_at),
            "window_hours": window_hours,
            "window_start": _iso(since),
            "window_end": _iso(until),
            "bucket": bucket,
            "summary": summary,
            "trend": [trend_map[key] for key in sorted(trend_map)],
            "users": sorted(users.values(), key=lambda item: (item["runs"], item["tokens"]), reverse=True)[:bounded_limit],
        }

    async def snapshot(
        self,
        *,
        hours: int = 24,
        limit: int = 50,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> dict[str, Any]:
        since, until, window_hours = _window_bounds(hours=hours, start_at=start_at, end_at=end_at)
        checked_at = datetime.now(UTC)
        if self._sf is None:
            return {
                "generated_at": _iso(checked_at),
                "window_hours": window_hours,
                "window_start": _iso(since),
                "window_end": _iso(until),
                "health": {"status": "degraded", "database": "memory", "active_runs": 0, "error_rate": 0},
                "component_health": [
                    _component("网关服务", "healthy", "内存模式运行时可响应管理监控请求", checked_at=checked_at),
                    _component("PostgreSQL", "degraded", "当前使用内存持久化，未连接 PostgreSQL", checked_at=checked_at),
                    _component("LangGraph 检查点", "unknown", "内存模式下没有可探测的持久化检查点", checked_at=checked_at),
                    _component("运行事件写入", "unknown", "内存模式下没有持久化 run_events 写入探针", checked_at=checked_at),
                    _component("模型供应商", "unknown", "没有近期模型运行可用于判断供应商状态", checked_at=checked_at),
                    _component("Sandbox", "unknown", "内存模式下未执行 Sandbox 探针", checked_at=checked_at),
                    _component("文件/制品存储", "healthy", "内存模式下审计事件保存在进程内环形缓冲区", checked_at=checked_at),
                ],
                "metrics": {
                    "users_total": 0,
                    "active_users": 0,
                    "threads_total": 0,
                    "runs_total": 0,
                    "runs_window": 0,
                    "tokens_window": 0,
                    "llm_calls_window": 0,
                    "avg_run_duration_ms": 0,
                },
                "runs_by_status": {},
                "runs_by_hour": [],
                "top_users": [],
                "recent_errors": [],
                "recent_traces": [],
                "audit_logs": await self.list_audit_logs(limit=limit),
            }

        async with self._sf() as session:
            db_started = time.perf_counter()
            db_error: str | None = None
            try:
                await session.execute(text("select 1"))
                db_latency_ms = int((time.perf_counter() - db_started) * 1000)
                database_status = "healthy"
            except Exception as exc:
                db_latency_ms = int((time.perf_counter() - db_started) * 1000)
                database_status = "degraded"
                db_error = str(exc)[:500]

            users_total = await session.scalar(select(func.count()).select_from(UserRow)) or 0
            threads_total = await session.scalar(select(func.count()).select_from(ThreadMetaRow)) or 0
            runs_total = await session.scalar(select(func.count()).select_from(RunRow)) or 0

            window_rows = (await session.execute(select(RunRow).where(RunRow.created_at >= since, RunRow.created_at <= until).order_by(desc(RunRow.created_at)))).scalars().all()
            active_runs = sum(1 for row in window_rows if row.status in _ACTIVE_STATUSES)
            error_runs = [row for row in window_rows if row.status in _TERMINAL_ERROR_STATUSES]
            terminal_runs = [row for row in window_rows if row.status in _TERMINAL_STATUSES]
            error_rate = round(len(error_runs) / len(terminal_runs), 4) if terminal_runs else 0

            status_rows = await session.execute(select(RunRow.status, func.count()).group_by(RunRow.status))
            runs_by_status = {status or "unknown": count for status, count in status_rows.all()}

            user_rows = await session.execute(select(UserRow.id, UserRow.email))
            user_email = {uid: email for uid, email in user_rows.all()}

            event_counts_rows = await session.execute(select(RunEventRow.run_id, func.count()).where(RunEventRow.created_at >= since, RunEventRow.created_at <= until).group_by(RunEventRow.run_id))
            event_counts = {run_id: count for run_id, count in event_counts_rows.all()}

            recent_audit_rows = (await session.execute(select(AuditLogRow).order_by(desc(AuditLogRow.created_at)).limit(limit))).scalars().all()
            run_event_total = await session.scalar(select(func.count()).select_from(RunEventRow)) or 0
            audit_total = await session.scalar(select(func.count()).select_from(AuditLogRow)) or 0
            latest_event_at = await session.scalar(select(func.max(RunEventRow.created_at)).select_from(RunEventRow))
            latest_audit_at = await session.scalar(select(func.max(AuditLogRow.created_at)).select_from(AuditLogRow))

        buckets: dict[str, int] = {}
        user_usage: dict[str, dict[str, Any]] = {}
        durations: list[int] = []
        for row in window_rows:
            hour_key = _bucket_key(row.created_at, "hour")
            buckets[hour_key] = buckets.get(hour_key, 0) + 1
            if row.user_id:
                entry = user_usage.setdefault(
                    row.user_id,
                    {"user_id": row.user_id, "email": user_email.get(row.user_id), "runs": 0, "tokens": 0},
                )
                entry["runs"] += 1
                entry["tokens"] += row.total_tokens or 0
            duration = _duration_ms(row.created_at, row.updated_at) if row.status in _TERMINAL_STATUSES else 0
            if duration:
                durations.append(duration)

        recent_errors = [
            {
                "run_id": row.run_id,
                "thread_id": row.thread_id,
                "user_id": row.user_id,
                "email": user_email.get(row.user_id or ""),
                "status": row.status,
                "error": row.error,
                "created_at": _iso(row.created_at),
                "updated_at": _iso(row.updated_at),
            }
            for row in error_runs[:10]
        ]

        recent_traces = [_run_summary(row, user_email, event_counts) for row in window_rows[:20]]

        event_status = "healthy" if run_event_total > 0 else "unknown"
        workload_status = "degraded" if error_rate >= 0.2 else "healthy"
        userdata_status, userdata_latency_ms, userdata_error = _probe_file_writable("userdata")
        storage_status, storage_latency_ms, storage_error = _probe_file_writable("artifact")
        model_names = sorted({row.model_name for row in window_rows if row.model_name})
        model_status = "healthy" if model_names else "unknown"
        checkpointer_status = "healthy" if database_status == "healthy" else "degraded"
        overall_status = (
            "degraded"
            if any(
                status == "degraded"
                for status in (
                    database_status,
                    checkpointer_status,
                    userdata_status,
                    storage_status,
                    workload_status,
                )
            )
            else "healthy"
        )
        component_health = [
            _component("网关服务", "healthy", "管理监控接口可正常响应", latency_ms=0, checked_at=checked_at),
            _component("PostgreSQL", database_status, "应用数据库连接可用，用户、运行、审计等表可查询", metric=f"{users_total} 个用户", latency_ms=db_latency_ms, checked_at=checked_at, error=db_error),
            _component("LangGraph 检查点", checkpointer_status, "检查点使用当前配置的持久化数据库后端", metric="已配置", latency_ms=db_latency_ms, checked_at=checked_at, error=db_error),
            _component(
                "运行事件写入",
                event_status,
                "链路追踪事件表可查询，且能看到运行事件写入",
                metric=f"{run_event_total} 条事件 · 最新 {_iso(latest_event_at) or '-'}",
                latency_ms=db_latency_ms,
                checked_at=checked_at,
            ),
            _component("模型供应商", model_status, "根据当前窗口内的模型运行记录判断供应商是否可用", metric=", ".join(model_names[:3]) or "当前窗口无模型运行", checked_at=checked_at),
            _component(
                "Sandbox 执行环境",
                "unknown",
                "业务监控不主动创建 sandbox；K8s 模式需结合 provisioner 与 sandbox Pod 监控判断",
                checked_at=checked_at,
            ),
            _component(
                "用户数据存储",
                userdata_status,
                "Gateway 挂载的数据目录可写入和读回；K8s 模式下应对应 userdata PVC",
                latency_ms=userdata_latency_ms,
                checked_at=checked_at,
                error=userdata_error,
            ),
            _component(
                "文件/制品存储",
                storage_status,
                "文件与制品存储路径可写入和读回",
                metric=f"{audit_total} 条审计 · 最新 {_iso(latest_audit_at) or '-'}",
                latency_ms=storage_latency_ms,
                checked_at=checked_at,
                error=storage_error,
            ),
            _component("工作负载", workload_status, "当前窗口运行错误率阈值为 20%", metric=f"{error_rate:.1%} 错误率", checked_at=checked_at),
        ]

        return {
            "generated_at": _iso(checked_at),
            "window_hours": window_hours,
            "window_start": _iso(since),
            "window_end": _iso(until),
            "health": {
                "status": overall_status,
                "database": "connected" if database_status == "healthy" else database_status,
                "active_runs": active_runs,
                "error_rate": error_rate,
            },
            "component_health": component_health,
            "metrics": {
                "users_total": users_total,
                "active_users": len(user_usage),
                "threads_total": threads_total,
                "runs_total": runs_total,
                "runs_window": len(window_rows),
                "tokens_window": sum(row.total_tokens or 0 for row in window_rows),
                "llm_calls_window": sum(row.llm_call_count or 0 for row in window_rows),
                "avg_run_duration_ms": int(sum(durations) / len(durations)) if durations else 0,
            },
            "runs_by_status": runs_by_status,
            "runs_by_hour": [{"hour": hour, "runs": count} for hour, count in sorted(buckets.items())],
            "top_users": sorted(user_usage.values(), key=lambda item: (item["runs"], item["tokens"]), reverse=True)[:10],
            "recent_errors": recent_errors,
            "recent_traces": recent_traces,
            "audit_logs": [_row_dict(row) for row in recent_audit_rows],
        }

    async def run_diagnostics(self, run_id: str) -> dict[str, Any] | None:
        if self._sf is None:
            return None
        async with self._sf() as session:
            row = await session.get(RunRow, run_id)
            if row is None:
                return None
            events = (await session.execute(select(RunEventRow).where(RunEventRow.run_id == run_id).order_by(RunEventRow.seq.asc()))).scalars().all()
            user_email = {}
            if row.user_id:
                user = await session.get(UserRow, row.user_id)
                if user is not None:
                    user_email[row.user_id] = user.email

        start_event = next((event for event in events if event.event_type == "run.start"), None)
        start_meta = start_event.event_metadata if start_event is not None else {}
        human_event = next((event for event in events if event.event_type == "llm.human.input"), None)
        human_payload = _json_content(human_event) if human_event is not None else None
        user_question = row.first_human_message
        if not user_question and human_payload:
            content = human_payload.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    user_question = first.get("text")
            elif isinstance(content, str):
                user_question = content
        tool_calls = _extract_tool_calls(events)
        last_success, failed_node = _find_nodes(events)
        cause = _error_cause(row, events)
        error_stack = row.error
        for event in reversed(events):
            blob = event.content or ""
            if "Traceback" in blob or "Exception" in blob or "Error" in blob:
                error_stack = error_stack or blob[:4000]
                break

        event_counts = {run_id: len(events)}
        summary = _run_summary(row, user_email, event_counts)
        summary["agent"] = summary.get("agent") or start_meta.get("agent_name") or start_meta.get("caller")
        return {
            "run": summary,
            "user_question": user_question,
            "agent": summary.get("agent"),
            "model": row.model_name or start_meta.get("model_name"),
            "status": row.status,
            "duration_ms": _duration_ms(row.created_at, row.updated_at),
            "tokens": {
                "input": row.total_input_tokens,
                "output": row.total_output_tokens,
                "total": row.total_tokens,
                "llm_calls": row.llm_call_count,
                "by_model": row.token_usage_by_model or {},
            },
            "tool_calls": tool_calls,
            "error_stack": error_stack,
            "last_successful_node": last_success,
            "failed_node": failed_node or (row.status if row.status in _TERMINAL_ERROR_STATUSES else None),
            "possible_causes": [cause] if cause else [],
            "events": [_event_dict(event) for event in events],
            "dispositions": await self.list_dispositions(run_id),
            "disposition_status": await self.latest_disposition_status(run_id),
        }

    async def trace_events(self, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        if self._sf is None:
            return []
        bounded_limit = min(max(limit, 1), 500)
        async with self._sf() as session:
            rows = (await session.execute(select(RunEventRow).where(RunEventRow.run_id == run_id).order_by(RunEventRow.seq.asc()).limit(bounded_limit))).scalars().all()
        return [
            {
                "id": row.id,
                "thread_id": row.thread_id,
                "run_id": row.run_id,
                "seq": row.seq,
                "event_type": row.event_type,
                "category": row.category,
                "content": row.content,
                "metadata": row.event_metadata or {},
                "created_at": _iso(row.created_at),
            }
            for row in rows
        ]
