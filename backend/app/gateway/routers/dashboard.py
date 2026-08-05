"""Admin business dashboard endpoints."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import case, desc, func, or_, select

from app.gateway.deps import require_admin_user
from app.gateway.external_agent_analytics import (
    EXTERNAL_PLATFORM_CLICK_ACTION,
    summarize_external_platform_clicks,
)
from app.gateway.weknora_analytics import get_weknora_dashboard_analytics
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.model import AuditLogRow, OperationDispositionRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from deerflow.persistence.scheduled_tasks.model import ScheduledTaskRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow
from deerflow.persistence.user.model import UserRow

router = APIRouter(prefix="/api/admin/dashboard", tags=["dashboard"])

_ADMIN_REQUIRED = "Admin privileges are required to view the dashboard"
_FAILED_STATUSES = {"error", "timeout", "interrupted"}
_TERMINAL_STATUSES = {"success", *_FAILED_STATUSES}
_DASHBOARD_EVENT_TYPES = {
    "llm.ai.response",
    "llm.tool.result",
    "tool",
    "tool.start",
    "tool.end",
    "tool_start",
    "tool_end",
}
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
_RAG_PATTERN = re.compile(r"(^|[^a-z0-9])rag([^a-z0-9]|$)")
_ARTIFACT_PATH_PATTERN = re.compile(
    r"(?:/mnt/user-data/outputs|outputs)/[^\r\n\\\"'`<>]+?"
    r"\.(?:pptx?|pdf|docx?|xlsx?|csv|md|txt|html?|png|jpe?g|webp|gif|svg|zip|skill|mp3|wav|mp4|log)"
    r"(?=$|[\s\"'`<>),;，。；])",
    re.IGNORECASE,
)
_VALUE_PROFILES: dict[str, dict[str, int]] = {
    "conservative": {
        "知识库检索": 5,
        "文件处理": 10,
        "产物生成": 20,
        "PPT生成": 45,
        "图片生成": 15,
    },
    "standard": {
        "知识库检索": 8,
        "文件处理": 15,
        "产物生成": 30,
        "PPT生成": 60,
        "图片生成": 20,
    },
}


class DashboardWindow(BaseModel):
    start_at: datetime
    end_at: datetime
    hours: int


class DashboardOverview(BaseModel):
    total_users: int
    new_users: int
    active_users: int
    threads: int
    all_time_threads: int
    existing_threads: int
    runs: int
    terminal_runs: int
    successful_runs: int
    failed_runs: int
    running_runs: int
    success_rate: float
    avg_run_duration_ms: int
    total_tokens: int
    llm_calls: int
    artifact_runs: int
    knowledge_runs: int
    feedback_total: int
    satisfaction_rate: float
    estimated_minutes_saved: int


class UserActivityPoint(BaseModel):
    date: str
    end_date: str
    active_users: int
    threads: int
    runs: int


class UserUsageItem(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    organization_name: str | None
    runs: int
    threads: int
    total_tokens: int
    success_rate: float


class ExternalAgentClickItem(BaseModel):
    name: str
    clicks: int
    users: int


class ExternalPlatformSourceMetric(BaseModel):
    source: str
    today_users: int
    total_users: int
    today_clicks: int
    total_clicks: int


class ExternalPlatformAgentMetric(BaseModel):
    name: str
    today_users: int
    total_users: int
    today_clicks: int
    total_clicks: int
    by_source: list[ExternalPlatformSourceMetric]


class ExternalPlatformTrend(BaseModel):
    dates: list[str]
    series: dict[str, list[int]]


class ExternalPlatformClickSummary(BaseModel):
    today: str
    days: int
    agents: list[ExternalPlatformAgentMetric]
    trend: ExternalPlatformTrend


class FeatureUsageItem(BaseModel):
    name: str
    count: int


class AgentUsageItem(BaseModel):
    assistant_id: str | None
    name: str
    runs: int
    users: int
    success_rate: float
    avg_duration_ms: int


class ModelUsageItem(BaseModel):
    model_name: str
    runs: int
    users: int
    success_rate: float
    avg_duration_ms: int
    total_tokens: int
    llm_calls: int


class BusinessValueContribution(BaseModel):
    name: str
    count: int
    minutes_per_event: int
    estimated_minutes: int


class BusinessValueSummary(BaseModel):
    artifact_runs: int
    knowledge_runs: int
    file_processing_runs: int
    presentation_runs: int
    image_runs: int
    estimated_minutes_saved: int
    profile: str
    contributions: list[BusinessValueContribution]


class ArtifactTypeItem(BaseModel):
    type: str
    count: int


class RecentArtifactItem(BaseModel):
    run_id: str
    thread_id: str
    path: str
    type: str
    created_at: datetime


class ArtifactSummary(BaseModel):
    total: int
    by_type: list[ArtifactTypeItem]
    previewable: int
    recent: list[RecentArtifactItem]


class FeedbackFeatureItem(BaseModel):
    name: str
    positive: int
    negative: int
    satisfaction_rate: float


class FeedbackSummary(BaseModel):
    total: int
    positive: int
    negative: int
    satisfaction_rate: float
    negative_rate: float
    recent_comments: list[dict[str, Any]]
    recent_negative: list[dict[str, Any]]
    by_feature: list[FeedbackFeatureItem]


class KnowledgeFileFormatUsageItem(BaseModel):
    format: str
    current_count: int
    uploaded_count: int


class KnowledgeBaseUsageItem(BaseModel):
    knowledge_base_id: str
    name: str
    creator_email: str | None
    creator_name: str | None
    current_documents: int
    uploaded_documents: int
    current_conversation_sessions: int
    conversation_sessions: int
    current_question_turns: int
    question_turns: int
    file_formats: list[KnowledgeFileFormatUsageItem] = Field(default_factory=list)


class KnowledgeUserUsageItem(BaseModel):
    weknora_user_id: str
    email: str | None
    name: str | None
    current_knowledge_bases: int
    created_knowledge_bases: int
    current_documents: int
    uploaded_documents: int
    conversation_sessions: int
    question_turns: int


class SharedSpaceUsageItem(BaseModel):
    shared_space_id: str
    name: str
    description: str | None
    created_at: datetime
    owner_email: str | None
    owner_name: str | None
    current_members: int
    joined_members: int
    admins: int
    editors: int
    viewers: int
    current_knowledge_bases: int
    shared_knowledge_bases: int
    current_documents: int
    uploaded_documents: int
    pending_requests: int
    join_requests: int


class KnowledgeUsageSummary(BaseModel):
    available: bool = False
    error: str | None = None
    current_users: int = 0
    registered_users: int = 0
    current_knowledge_bases: int = 0
    created_knowledge_bases: int = 0
    current_documents: int = 0
    uploaded_documents: int = 0
    current_conversation_sessions: int = 0
    conversation_sessions: int = 0
    current_question_turns: int = 0
    question_turns: int = 0
    current_shared_spaces: int = 0
    created_shared_spaces: int = 0
    current_shared_space_members: int = 0
    joined_shared_space_members: int = 0
    current_shared_knowledge_bases: int = 0
    shared_knowledge_bases: int = 0
    current_shared_documents: int = 0
    uploaded_shared_documents: int = 0
    pending_shared_space_requests: int = 0
    shared_space_join_requests: int = 0
    file_formats: list[KnowledgeFileFormatUsageItem] = Field(default_factory=list)
    knowledge_bases: list[KnowledgeBaseUsageItem] = Field(default_factory=list)
    users: list[KnowledgeUserUsageItem] = Field(default_factory=list)
    shared_spaces: list[SharedSpaceUsageItem] = Field(default_factory=list)


class ScheduledTaskUserUsageItem(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    organization_name: str | None
    current_tasks: int
    created_tasks: int
    enabled_tasks: int
    paused_tasks: int
    runs: int
    successful_runs: int
    failed_runs: int
    skipped_runs: int


class ScheduledTaskUsageSummary(BaseModel):
    current_tasks: int
    created_tasks: int
    enabled_tasks: int
    paused_tasks: int
    runs: int
    successful_runs: int
    failed_runs: int
    skipped_runs: int
    users: list[ScheduledTaskUserUsageItem] = Field(default_factory=list)


class RecentFailureItem(BaseModel):
    run_id: str
    thread_id: str
    user_id: str | None
    email: str | None
    status: str
    error: str | None
    assistant_id: str | None
    model_name: str | None
    started_at: datetime
    updated_at: datetime
    duration_ms: int
    disposition_status: str | None
    diagnostics_url: str
    can_open: bool
    chat_url: str | None


class FailedRunList(BaseModel):
    items: list[RecentFailureItem] = Field(default_factory=list)
    total: int


class RecentBusinessEventItem(BaseModel):
    run_id: str
    thread_id: str
    created_at: datetime
    event_type: str
    features: list[str]
    snippet: str


class DashboardSummary(BaseModel):
    window: DashboardWindow
    overview: DashboardOverview
    activity_bucket: str
    user_activity: list[UserActivityPoint]
    user_usage: list[UserUsageItem]
    external_agent_clicks: list[ExternalAgentClickItem]
    external_platform_agent_clicks: ExternalPlatformClickSummary
    feature_usage: list[FeatureUsageItem]
    recent_business_events: list[RecentBusinessEventItem]
    agent_usage: list[AgentUsageItem]
    model_usage: list[ModelUsageItem]
    business_value: BusinessValueSummary
    artifact_summary: ArtifactSummary
    feedback: FeedbackSummary
    knowledge_usage: KnowledgeUsageSummary
    scheduled_task_usage: ScheduledTaskUsageSummary
    recent_failures: list[RecentFailureItem]


class RegisteredUserSessionSummary(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    organization_name: str | None
    system_role: str
    is_disabled: bool
    registered_at: datetime
    existing_threads: int
    runs: int
    terminal_runs: int
    successful_runs: int
    failed_runs: int
    running_runs: int
    last_active_at: datetime | None


class RegisteredUserSessionList(BaseModel):
    items: list[RegisteredUserSessionSummary]
    total: int


class ExistingSessionItem(BaseModel):
    thread_id: str
    title: str | None
    assistant_id: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    runs: int
    terminal_runs: int
    successful_runs: int
    failed_runs: int
    running_runs: int
    last_run_at: datetime | None
    last_run_status: str | None
    can_open: bool
    chat_url: str | None


class ExistingSessionList(BaseModel):
    user_id: str
    items: list[ExistingSessionItem]
    total: int


@dataclass
class _AgentBucket:
    name: str
    runs: int = 0
    terminal_runs: int = 0
    successes: int = 0
    users: set[str] = field(default_factory=set)
    durations: list[int] = field(default_factory=list)


@dataclass
class _ModelBucket:
    runs: int = 0
    terminal_runs: int = 0
    successes: int = 0
    users: set[str] = field(default_factory=set)
    durations: list[int] = field(default_factory=list)
    total_tokens: int = 0
    llm_calls: int = 0


@dataclass(frozen=True)
class _RunStatusSummary:
    successful: int
    failed: int
    running: int
    terminal: int
    success_rate: float
    duration_values: list[int]


def _session_factory():
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Dashboard database is not available")
    return sf


def _coerce_aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _window_bounds(hours: int, start_at: datetime | None, end_at: datetime | None) -> tuple[datetime, datetime, int]:
    end = _coerce_aware(end_at) or datetime.now(UTC)
    start = _coerce_aware(start_at) or (end - timedelta(hours=max(1, hours)))
    if start >= end:
        start = end - timedelta(hours=1)
    window_hours = max(1, int((end - start).total_seconds() // 3600) or 1)
    return start, end, window_hours


def _summarize_scheduled_task_usage(
    tasks: list[ScheduledTaskRow],
    runs: list[ScheduledTaskRunRow],
    creation_audits: list[AuditLogRow],
    users_by_id: dict[str, UserRow],
    *,
    start: datetime,
    end: datetime,
    limit: int,
) -> ScheduledTaskUsageSummary:
    """Combine current task inventory with append-only creation and run history."""

    task_owner: dict[str, str] = {str(task.id): str(task.user_id) for task in tasks if task.id and task.user_id}
    audit_email_by_user: dict[str, str] = {}
    for audit in creation_audits:
        if audit.resource_id and audit.user_id:
            task_owner.setdefault(str(audit.resource_id), str(audit.user_id))
        if audit.user_id and audit.user_email:
            audit_email_by_user.setdefault(str(audit.user_id), audit.user_email)

    created_task_ids: set[str] = {str(task.id) for task in tasks if start <= (_coerce_aware(task.created_at) or start) <= end}
    created_task_ids.update(str(audit.resource_id) for audit in creation_audits if audit.resource_id and start <= (_coerce_aware(audit.created_at) or start) <= end)

    user_buckets: dict[str, dict[str, int]] = defaultdict(
        lambda: {
            "current_tasks": 0,
            "created_tasks": 0,
            "enabled_tasks": 0,
            "paused_tasks": 0,
            "runs": 0,
            "successful_runs": 0,
            "failed_runs": 0,
            "skipped_runs": 0,
        }
    )
    for task in tasks:
        user_id = str(task.user_id)
        bucket = user_buckets[user_id]
        bucket["current_tasks"] += 1
        bucket["enabled_tasks"] += int(task.status == "enabled")
        bucket["paused_tasks"] += int(task.status == "paused")

    for task_id in created_task_ids:
        user_id = task_owner.get(task_id)
        if user_id:
            user_buckets[user_id]["created_tasks"] += 1

    for run in runs:
        user_id = task_owner.get(str(run.task_id))
        if not user_id:
            continue
        bucket = user_buckets[user_id]
        bucket["runs"] += 1
        bucket["successful_runs"] += int(run.status == "success")
        bucket["failed_runs"] += int(run.status in {"failed", "interrupted"})
        bucket["skipped_runs"] += int(run.status == "skipped")

    users = [
        ScheduledTaskUserUsageItem(
            user_id=user_id,
            email=(users_by_id[user_id].email if user_id in users_by_id else audit_email_by_user.get(user_id)),
            display_name=(users_by_id[user_id].display_name if user_id in users_by_id else None),
            organization_name=(users_by_id[user_id].organization_name if user_id in users_by_id else None),
            **bucket,
        )
        for user_id, bucket in sorted(
            user_buckets.items(),
            key=lambda item: (
                item[1]["runs"],
                item[1]["created_tasks"],
                item[1]["current_tasks"],
            ),
            reverse=True,
        )[:limit]
    ]

    return ScheduledTaskUsageSummary(
        current_tasks=len(tasks),
        created_tasks=len(created_task_ids),
        enabled_tasks=sum(task.status == "enabled" for task in tasks),
        paused_tasks=sum(task.status == "paused" for task in tasks),
        runs=len(runs),
        successful_runs=sum(run.status == "success" for run in runs),
        failed_runs=sum(run.status in {"failed", "interrupted"} for run in runs),
        skipped_runs=sum(run.status == "skipped" for run in runs),
        users=users,
    )


def _duration_ms(created_at: datetime | None, updated_at: datetime | None) -> int:
    start = _coerce_aware(created_at)
    end = _coerce_aware(updated_at)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _day_key(value: datetime | None) -> str:
    aware = (_coerce_aware(value) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE)
    return aware.date().isoformat()


def _activity_bucket_days(start: datetime, end: datetime) -> int:
    start_day = (_coerce_aware(start) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    end_day = (_coerce_aware(end) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    span_days = (end_day - start_day).days + 1
    if span_days <= 31:
        return 1
    if span_days <= 366:
        return 7
    if span_days <= 365 * 5:
        return 30
    return 365


def _activity_bucket_name(bucket_days: int) -> str:
    return {1: "day", 7: "week", 30: "month", 365: "year"}[bucket_days]


def _iter_activity_periods(start: datetime, end: datetime, bucket_days: int) -> list[tuple[str, str]]:
    current = (_coerce_aware(start) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    final = (_coerce_aware(end) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    periods: list[tuple[str, str]] = []
    while current <= final:
        period_end = min(final, current + timedelta(days=bucket_days - 1))
        periods.append((current.isoformat(), period_end.isoformat()))
        current = period_end + timedelta(days=1)
    return periods


def _activity_bucket_key(value: datetime | None, start: datetime, bucket_days: int) -> str:
    start_day = (_coerce_aware(start) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    value_day = (_coerce_aware(value) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    offset = max(0, (value_day - start_day).days)
    return (start_day + timedelta(days=(offset // bucket_days) * bucket_days)).isoformat()


def _metadata_feature_labels(metadata: dict[str, Any] | None) -> set[str]:
    """Read explicit business feature labels before falling back to text heuristics."""

    if not metadata:
        return set()

    aliases = {
        "presentation": "PPT生成",
        "ppt": "PPT生成",
        "ppt_generation": "PPT生成",
        "knowledge": "知识库检索",
        "knowledge_retrieval": "知识库检索",
        "rag": "知识库检索",
        "image": "图片生成",
        "image_generation": "图片生成",
        "file": "文件处理",
        "file_processing": "文件处理",
        "artifact": "产物生成",
        "artifact_generation": "产物生成",
        "sandbox": "沙箱执行",
        "sandbox_execution": "沙箱执行",
    }
    known = set(aliases.values())
    raw_values: list[Any] = []
    for key in (
        "dashboard_features",
        "business_features",
        "business_events",
        "features",
        "feature",
        "event",
        "tool_category",
    ):
        value = metadata.get(key)
        if isinstance(value, list | tuple | set):
            raw_values.extend(value)
        elif value is not None:
            raw_values.append(value)

    labels: set[str] = set()
    for value in raw_values:
        token = str(value).strip()
        if not token:
            continue
        if token in known:
            labels.add(token)
            continue
        normalized = token.lower().replace("-", "_").replace(" ", "_")
        if normalized in aliases:
            labels.add(aliases[normalized])
    return labels


def _metadata_values(metadata: dict[str, Any] | None, *keys: str) -> list[Any]:
    if not metadata:
        return []
    values: list[Any] = []
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, list | tuple | set):
            values.extend(value)
        elif value is not None:
            values.append(value)
    return values


def _coerce_artifact_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip().strip("\"'`.,;，。；：！？")
    if not stripped:
        return None
    stripped = stripped.split("\\n", 1)[0].split("\\r", 1)[0]
    if "/mnt/user-data/outputs/" in stripped:
        stripped = stripped[stripped.index("/mnt/user-data/outputs/") :]
    elif stripped.startswith("outputs/"):
        stripped = f"/mnt/user-data/{stripped}"
    elif stripped.startswith("/outputs/"):
        stripped = f"/mnt/user-data{stripped}"
    elif not (stripped.startswith("/mnt/") and "/outputs/" in stripped):
        return None
    normalized = stripped.rstrip("\"'`.,;，。；：！？")
    path = PurePosixPath(normalized)
    if path.name in {"", ".", ".."}:
        return None
    return str(path)


def _artifact_type(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower().lstrip(".")
    if suffix in {"ppt", "pptx"}:
        return "演示材料"
    if suffix in {"doc", "docx", "pdf", "md", "txt"}:
        return "文档"
    if suffix in {"xls", "xlsx", "csv"}:
        return "表格"
    if suffix in {"png", "jpg", "jpeg", "webp", "gif", "svg"}:
        return "图片"
    if suffix in {"html", "htm"}:
        return "网页"
    return suffix.upper() if suffix else "其他"


def _is_previewable_artifact(path: str) -> bool:
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in {".ppt", ".pptx", ".pdf", ".html", ".htm", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".txt", ".md"}


_ARTIFACT_METADATA_KEYS = {
    "artifact",
    "artifacts",
    "artifact_path",
    "artifact_paths",
    "path",
    "paths",
    "filepath",
    "filepaths",
    "files",
    "outputs",
}


def _json_mapping(content: str) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _artifact_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        collected: list[Any] = []
        for key, item in value.items():
            if key in _ARTIFACT_METADATA_KEYS:
                collected.extend(_artifact_values(item))
        return collected
    if isinstance(value, list | tuple | set):
        collected = []
        for item in value:
            collected.extend(_artifact_values(item))
        return collected
    return [value]


def _tool_calls(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_calls = payload.get("tool_calls")
    if not isinstance(raw_calls, list):
        return []
    return [call for call in raw_calls if isinstance(call, dict)]


def _extract_artifact_paths(event: RunEventRow) -> list[str]:
    raw_values = _artifact_values(event.event_metadata or {})
    payload = _json_mapping(event.content or "")
    if event.event_type == "llm.ai.response":
        for call in _tool_calls(payload):
            if str(call.get("name") or "").strip().lower() == "present_files":
                raw_values.extend(_artifact_values(call.get("args") or {}))
    elif event.event_type == "llm.tool.result":
        if str(payload.get("name") or "").strip().lower() == "present_files":
            raw_values.extend(_artifact_values(payload.get("artifact")))

    paths: list[str] = []
    for value in raw_values:
        path = _coerce_artifact_path(value)
        if path:
            paths.append(path)
    if event.event_type in {"tool", "tool.start", "tool.end", "tool_start", "tool_end"}:
        for match in _ARTIFACT_PATH_PATTERN.findall(event.content or ""):
            path = _coerce_artifact_path(match)
            if path:
                paths.append(path)
    return list(dict.fromkeys(paths))


def _tool_features(name: str, args: Any) -> set[str]:
    normalized_name = name.strip().lower().replace("-", "_")
    args_text = json.dumps(args, ensure_ascii=False, default=str).lower()
    features: set[str] = set()
    if normalized_name.startswith("weknora_") or normalized_name in {"knowledge_search", "knowledge_retrieval", "rag_search"}:
        features.add("知识库检索")
        return features
    if normalized_name == "present_files":
        features.add("产物生成")
        return features
    if normalized_name in {"read_file", "write_file", "str_replace", "upload", "extract_text"}:
        features.add("文件处理")
    is_execution_tool = normalized_name in {"bash", "ls", "execute_shell", "python"}
    if is_execution_tool:
        features.add("沙箱执行")
    if "ppt" in normalized_name or (is_execution_tool and any(token in args_text for token in ("ppt-master", "svg_to_pptx", ".pptx", "presentation"))):
        features.add("PPT生成")
    if normalized_name in {"image_generation", "generate_image", "imagegen", "image_gen", "image_gen__imagegen"} or (is_execution_tool and any(token in args_text for token in ("image_gen.py", "image_backend", "qwen-image", "生成图片"))):
        features.add("图片生成")
    if any(token in args_text for token in ("/uploads/", ".pdf", ".docx", ".xlsx", ".xls", ".csv")):
        features.add("文件处理")
    return features


def classify_dashboard_features(
    content: str,
    metadata: dict[str, Any] | None = None,
    *,
    event_type: str | None = None,
) -> set[str]:
    """Classify a run event into high-level business usage buckets."""

    features = _metadata_feature_labels(metadata)
    if event_type in {"run.start", "run.end", "llm.human.input"}:
        return features

    if event_type in {"llm.ai.response", "llm.tool.result"}:
        payload = _json_mapping(content)
        if event_type == "llm.ai.response":
            for call in _tool_calls(payload):
                features.update(_tool_features(str(call.get("name") or ""), call.get("args")))
        else:
            features.update(_tool_features(str(payload.get("name") or ""), {}))
        return features

    if event_type is not None and event_type not in {"tool", "tool.start", "tool.end", "tool_start", "tool_end"}:
        return features

    # Legacy tool events predate the structured journal payload. Their content
    # is still safe to classify because the row represents an actual tool call.
    haystack = f"{content}\n{metadata or {}}".lower()
    if any(token in haystack for token in ("ppt-master", "svg_to_pptx", ".pptx", "presentation")):
        features.add("PPT生成")
    if any(token in haystack for token in ("weknora", "knowledge", "知识库", "retrieval")) or _RAG_PATTERN.search(haystack):
        features.add("知识库检索")
    if any(token in haystack for token in ("image_gen.py", "image_backend", "qwen-image", "minimax", "生成图片")):
        features.add("图片生成")
    if any(token in haystack for token in ("read_file", "write_file", "upload", "uploads", "extract_text", "pdf", ".docx")):
        features.add("文件处理")
    if any(token in haystack for token in ("present_files", "artifact", "/mnt/user-data/outputs", "outputs/")):
        features.add("产物生成")
    if any(token in haystack for token in ("sandbox", "bash", "python", "execute_shell", "exec")):
        features.add("沙箱执行")
    return features


def _metadata_name(row: ThreadMetaRow | None) -> str | None:
    if row is None:
        return None
    metadata = row.metadata_json or {}
    for key in ("agent_name", "assistant_name", "name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return row.display_name


def _success_rate(successes: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(successes / total, 4)


def _run_status_summary(runs: list[RunRow]) -> _RunStatusSummary:
    status_counts = Counter(row.status for row in runs)
    successful = status_counts.get("success", 0)
    failed = sum(status_counts.get(status, 0) for status in _FAILED_STATUSES)
    running = status_counts.get("running", 0) + status_counts.get("pending", 0)
    terminal = successful + failed
    duration_values = [_duration_ms(row.created_at, row.updated_at) for row in runs if row.status in _TERMINAL_STATUSES and row.updated_at]
    return _RunStatusSummary(
        successful=successful,
        failed=failed,
        running=running,
        terminal=terminal,
        success_rate=_success_rate(successful, terminal),
        duration_values=duration_values,
    )


def _avg(values: list[int]) -> int:
    if not values:
        return 0
    return int(sum(values) / len(values))


def _value_weights(profile: str) -> tuple[str, dict[str, int]]:
    normalized = profile if profile in _VALUE_PROFILES else "conservative"
    return normalized, _VALUE_PROFILES[normalized]


def _business_value_contributions(
    feature_counts: Counter[str],
    *,
    profile: str,
) -> tuple[str, list[BusinessValueContribution], int]:
    resolved_profile, weights = _value_weights(profile)
    contributions = [
        BusinessValueContribution(
            name=name,
            count=feature_counts.get(name, 0),
            minutes_per_event=minutes,
            estimated_minutes=feature_counts.get(name, 0) * minutes,
        )
        for name, minutes in weights.items()
    ]
    return resolved_profile, contributions, sum(item.estimated_minutes for item in contributions)


def _assistant_key(run: RunRow, thread_meta: dict[str, ThreadMetaRow]) -> str:
    meta = thread_meta.get(run.thread_id)
    return run.assistant_id or (meta.assistant_id if meta else None) or "default"


def _summarize_artifact_events(
    artifact_events: list[tuple[RunEventRow, str]],
    *,
    limit: int,
) -> ArtifactSummary:
    unique_instances: dict[tuple[str, str], RunEventRow] = {}
    ordered_events = sorted(
        artifact_events,
        key=lambda item: _coerce_aware(item[0].created_at) or datetime.min.replace(tzinfo=UTC),
        reverse=True,
    )
    for event, path in ordered_events:
        unique_instances.setdefault((event.thread_id, path), event)

    artifact_counts = Counter(_artifact_type(path) for _, path in unique_instances)
    recent_artifacts = [
        RecentArtifactItem(
            run_id=event.run_id,
            thread_id=thread_id,
            path=path,
            type=_artifact_type(path),
            created_at=event.created_at,
        )
        for (thread_id, path), event in unique_instances.items()
    ]
    return ArtifactSummary(
        total=len(unique_instances),
        by_type=[ArtifactTypeItem(type=name, count=count) for name, count in artifact_counts.most_common(limit)],
        previewable=sum(1 for _, path in unique_instances if _is_previewable_artifact(path)),
        recent=recent_artifacts[: min(limit, 8)],
    )


@router.get("/users", response_model=RegisteredUserSessionList)
async def get_registered_user_sessions(
    request: Request,
    q: str | None = Query(default=None, max_length=320),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> RegisteredUserSessionList:
    """List every registered user with runs belonging to non-deleted threads."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    sf = _session_factory()
    async with sf() as session:
        user_stmt = select(UserRow)
        count_stmt = select(func.count()).select_from(UserRow)
        if q and q.strip():
            pattern = f"%{q.strip()}%"
            identity_filter = or_(
                UserRow.email.ilike(pattern),
                UserRow.display_name.ilike(pattern),
                UserRow.organization_name.ilike(pattern),
            )
            user_stmt = user_stmt.where(identity_filter)
            count_stmt = count_stmt.where(identity_filter)
        users = list((await session.execute(user_stmt.order_by(UserRow.created_at.desc(), UserRow.email.asc()).limit(limit).offset(offset))).scalars().all())
        total = int(await session.scalar(count_stmt) or 0)
        user_ids = [row.id for row in users]
        stats: dict[str, Any] = {}
        if user_ids:
            stats_stmt = (
                select(
                    ThreadMetaRow.user_id,
                    func.count(func.distinct(ThreadMetaRow.thread_id)),
                    func.count(RunRow.run_id),
                    func.sum(case((RunRow.status == "success", 1), else_=0)),
                    func.sum(case((RunRow.status.in_(_FAILED_STATUSES), 1), else_=0)),
                    func.sum(case((RunRow.status.in_({"pending", "running"}), 1), else_=0)),
                    func.max(RunRow.updated_at),
                    func.max(ThreadMetaRow.updated_at),
                )
                .outerjoin(RunRow, RunRow.thread_id == ThreadMetaRow.thread_id)
                .where(ThreadMetaRow.user_id.in_(user_ids))
                .group_by(ThreadMetaRow.user_id)
            )
            stats = {str(row[0]): row for row in (await session.execute(stats_stmt)).all() if row[0]}

    items: list[RegisteredUserSessionSummary] = []
    for user in users:
        row = stats.get(user.id)
        successful = int(row[3] or 0) if row else 0
        failed = int(row[4] or 0) if row else 0
        running = int(row[5] or 0) if row else 0
        last_run_at = _coerce_aware(row[6]) if row else None
        last_thread_at = _coerce_aware(row[7]) if row else None
        last_active_at = max((value for value in (last_run_at, last_thread_at) if value is not None), default=None)
        items.append(
            RegisteredUserSessionSummary(
                user_id=user.id,
                email=user.email,
                display_name=user.display_name,
                organization_name=user.organization_name,
                system_role=user.system_role,
                is_disabled=user.is_disabled,
                registered_at=user.created_at,
                existing_threads=int(row[1] or 0) if row else 0,
                runs=int(row[2] or 0) if row else 0,
                terminal_runs=successful + failed,
                successful_runs=successful,
                failed_runs=failed,
                running_runs=running,
                last_active_at=last_active_at,
            )
        )
    return RegisteredUserSessionList(items=items, total=total)


@router.get("/users/{user_id}/sessions", response_model=ExistingSessionList)
async def get_user_existing_sessions(
    user_id: str,
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ExistingSessionList:
    """Return the user's currently existing threads and all runs in them."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    sf = _session_factory()
    async with sf() as session:
        if await session.get(UserRow, user_id) is None:
            raise HTTPException(status_code=404, detail="User not found")
        total = int(await session.scalar(select(func.count()).select_from(ThreadMetaRow).where(ThreadMetaRow.user_id == user_id)) or 0)
        threads = list((await session.execute(select(ThreadMetaRow).where(ThreadMetaRow.user_id == user_id).order_by(desc(ThreadMetaRow.updated_at), desc(ThreadMetaRow.created_at)).limit(limit).offset(offset))).scalars().all())
        thread_ids = [row.thread_id for row in threads]
        runs = list((await session.execute(select(RunRow).where(RunRow.thread_id.in_(thread_ids)).order_by(desc(RunRow.created_at)))).scalars().all()) if thread_ids else []

    runs_by_thread: dict[str, list[RunRow]] = defaultdict(list)
    for run in runs:
        runs_by_thread[run.thread_id].append(run)
    items: list[ExistingSessionItem] = []
    for thread in threads:
        thread_runs = runs_by_thread.get(thread.thread_id, [])
        status_summary = _run_status_summary(thread_runs)
        last_run = thread_runs[0] if thread_runs else None
        items.append(
            ExistingSessionItem(
                thread_id=thread.thread_id,
                title=thread.display_name,
                assistant_id=thread.assistant_id,
                status=thread.status,
                created_at=thread.created_at,
                updated_at=thread.updated_at,
                runs=len(thread_runs),
                terminal_runs=status_summary.terminal,
                successful_runs=status_summary.successful,
                failed_runs=status_summary.failed,
                running_runs=status_summary.running,
                last_run_at=last_run.created_at if last_run else None,
                last_run_status=last_run.status if last_run else None,
                can_open=True,
                chat_url=f"/workspace/chats/{thread.thread_id}?admin_review=1",
            )
        )
    return ExistingSessionList(user_id=user_id, items=items, total=total)


@router.get("/failures", response_model=FailedRunList)
async def get_failed_runs(
    request: Request,
    limit: int = Query(default=5, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> FailedRunList:
    """Return every failed run in reverse chronological, paged order."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    failed_filter = or_(RunRow.status.in_(_FAILED_STATUSES), RunRow.error.is_not(None))
    sf = _session_factory()
    async with sf() as session:
        total = int(await session.scalar(select(func.count()).select_from(RunRow).where(failed_filter)) or 0)
        runs = list((await session.execute(select(RunRow).where(failed_filter).order_by(desc(RunRow.updated_at), desc(RunRow.created_at), desc(RunRow.run_id)).limit(limit).offset(offset))).scalars().all())
        user_ids = {run.user_id for run in runs if run.user_id}
        users_by_id = {row.id: row for row in (await session.execute(select(UserRow).where(UserRow.id.in_(user_ids)))).scalars()} if user_ids else {}
        thread_ids = {run.thread_id for run in runs}
        existing_thread_ids = set((await session.execute(select(ThreadMetaRow.thread_id).where(ThreadMetaRow.thread_id.in_(thread_ids)))).scalars()) if thread_ids else set()
        message_thread_ids = (
            set(
                (
                    await session.execute(
                        select(RunEventRow.thread_id)
                        .where(
                            RunEventRow.thread_id.in_(thread_ids),
                            RunEventRow.category == "message",
                        )
                        .distinct()
                    )
                ).scalars()
            )
            if thread_ids
            else set()
        )
        disposition_rows = (
            list((await session.execute(select(OperationDispositionRow).where(OperationDispositionRow.run_id.in_({run.run_id for run in runs})).order_by(desc(OperationDispositionRow.created_at)))).scalars().all()) if runs else []
        )

    latest_disposition_by_run: dict[str, OperationDispositionRow] = {}
    for row in disposition_rows:
        latest_disposition_by_run.setdefault(row.run_id, row)

    items: list[RecentFailureItem] = []
    for run in runs:
        can_open = run.thread_id in existing_thread_ids and run.thread_id in message_thread_ids
        disposition = latest_disposition_by_run.get(run.run_id)
        items.append(
            RecentFailureItem(
                run_id=run.run_id,
                thread_id=run.thread_id,
                user_id=run.user_id,
                email=users_by_id.get(run.user_id).email if run.user_id and run.user_id in users_by_id else None,
                status=run.status,
                error=run.error,
                assistant_id=run.assistant_id,
                model_name=run.model_name,
                started_at=run.created_at,
                updated_at=run.updated_at,
                duration_ms=_duration_ms(run.created_at, run.updated_at),
                disposition_status=(disposition.status or disposition.action) if disposition else None,
                diagnostics_url=f"/workspace/admin/observability?run_id={run.run_id}",
                can_open=can_open,
                chat_url=f"/workspace/chats/{run.thread_id}?admin_review=1" if can_open else None,
            )
        )
    return FailedRunList(items=items, total=total)


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    request: Request,
    hours: int = Query(default=168, ge=1),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    status: str | None = Query(default=None),
    model: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    feature: str | None = Query(default=None),
    value_profile: str = Query(default="conservative"),
) -> DashboardSummary:
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    start, end, window_hours = _window_bounds(hours, start_at, end_at)

    sf = _session_factory()
    async with sf() as session:
        run_stmt = select(RunRow).where(RunRow.created_at >= start, RunRow.created_at <= end).order_by(desc(RunRow.created_at))
        runs = list((await session.execute(run_stmt)).scalars().all())

        thread_creation_stmt = select(ThreadCreationRow).where(
            ThreadCreationRow.created_at >= start,
            ThreadCreationRow.created_at <= end,
        )
        thread_creations = list((await session.execute(thread_creation_stmt)).scalars().all())
        total_thread_creations = await session.scalar(select(func.count()).select_from(ThreadCreationRow)) or 0
        existing_threads = await session.scalar(select(func.count()).select_from(ThreadMetaRow)) or 0

        thread_ids = {row.thread_id for row in runs} | {row.thread_id for row in thread_creations}
        thread_meta: dict[str, ThreadMetaRow] = {}
        if thread_ids:
            meta_rows = (await session.execute(select(ThreadMetaRow).where(ThreadMetaRow.thread_id.in_(thread_ids)))).scalars()
            thread_meta = {row.thread_id: row for row in meta_rows}

        scheduled_task_rows = list((await session.execute(select(ScheduledTaskRow).order_by(desc(ScheduledTaskRow.created_at)))).scalars().all())
        scheduled_task_run_rows = list(
            (
                await session.execute(
                    select(ScheduledTaskRunRow)
                    .where(
                        ScheduledTaskRunRow.created_at >= start,
                        ScheduledTaskRunRow.created_at <= end,
                    )
                    .order_by(desc(ScheduledTaskRunRow.created_at))
                )
            )
            .scalars()
            .all()
        )
        scheduled_creation_audits = list((await session.execute(select(AuditLogRow).where(AuditLogRow.action == "scheduled_task.create"))).scalars().all())

        user_ids = (
            {row.user_id for row in runs if row.user_id}
            | {row.user_id for row in thread_creations if row.user_id}
            | {row.user_id for row in scheduled_task_rows if row.user_id}
            | {row.user_id for row in scheduled_creation_audits if row.user_id}
        )
        users_by_id: dict[str, UserRow] = {}
        if user_ids:
            user_rows = (await session.execute(select(UserRow).where(UserRow.id.in_(user_ids)))).scalars()
            users_by_id = {row.id: row for row in user_rows}

        total_users = await session.scalar(select(func.count()).select_from(UserRow)) or 0
        new_users = await session.scalar(select(func.count()).select_from(UserRow).where(UserRow.created_at >= start, UserRow.created_at <= end)) or 0

        event_stmt = (
            select(RunEventRow)
            .where(
                RunEventRow.created_at >= start,
                RunEventRow.created_at <= end,
                RunEventRow.event_type.in_(_DASHBOARD_EVENT_TYPES),
            )
            .order_by(desc(RunEventRow.created_at))
        )
        events = list((await session.execute(event_stmt)).scalars().all())

        feedback_stmt = select(FeedbackRow).where(FeedbackRow.created_at >= start, FeedbackRow.created_at <= end).order_by(desc(FeedbackRow.created_at))
        feedback_rows = list((await session.execute(feedback_stmt)).scalars().all())

        disposition_stmt = select(OperationDispositionRow).where(OperationDispositionRow.created_at >= start, OperationDispositionRow.created_at <= end).order_by(desc(OperationDispositionRow.created_at))
        disposition_rows = list((await session.execute(disposition_stmt)).scalars().all())

        click_stmt = select(AuditLogRow).where(
            AuditLogRow.created_at >= start,
            AuditLogRow.created_at <= end,
            AuditLogRow.action == "external_agent.click",
        )
        external_click_rows = list((await session.execute(click_stmt)).scalars().all())
        external_platform_click_rows = list((await session.execute(select(AuditLogRow).where(AuditLogRow.action == EXTERNAL_PLATFORM_CLICK_ACTION).order_by(AuditLogRow.created_at))).scalars().all())

    weknora_usage = await get_weknora_dashboard_analytics(
        start=start,
        end=end,
        limit=limit,
    )
    scheduled_task_usage = _summarize_scheduled_task_usage(
        scheduled_task_rows,
        scheduled_task_run_rows,
        scheduled_creation_audits,
        users_by_id,
        start=start,
        end=end,
        limit=limit,
    )

    all_events = events
    all_feedback_rows = feedback_rows
    run_features: dict[str, set[str]] = defaultdict(set)
    business_events: list[tuple[RunEventRow, set[str]]] = []
    artifact_events: list[tuple[RunEventRow, str]] = []
    for event in all_events:
        features = classify_dashboard_features(event.content, event.event_metadata, event_type=event.event_type)
        if features:
            run_features[event.run_id].update(features)
            if event.event_type != "llm.tool.result":
                business_events.append((event, features))
        for artifact_path in _extract_artifact_paths(event):
            artifact_events.append((event, artifact_path))

    normalized_status = status if status and status != "all" else None
    normalized_model = model if model and model != "all" else None
    normalized_agent = agent if agent and agent != "all" else None
    normalized_feature = feature if feature and feature != "all" else None

    def _matches_filters(run: RunRow) -> bool:
        if normalized_status and run.status != normalized_status:
            return False
        if normalized_model and (run.model_name or "未记录模型") != normalized_model:
            return False
        if normalized_agent and _assistant_key(run, thread_meta) != normalized_agent:
            return False
        if normalized_feature and normalized_feature not in run_features.get(run.run_id, set()):
            return False
        return True

    runs = [run for run in runs if _matches_filters(run)]
    run_ids = {run.run_id for run in runs}
    thread_ids_for_runs = {run.thread_id for run in runs}
    filters_active = any((normalized_status, normalized_model, normalized_agent, normalized_feature))
    if filters_active:
        thread_creations = [row for row in thread_creations if row.thread_id in thread_ids_for_runs]
    events = [event for event in all_events if event.run_id in run_ids]
    feedback_rows = [row for row in all_feedback_rows if row.run_id in run_ids]
    run_features = defaultdict(set, {run_id: features for run_id, features in run_features.items() if run_id in run_ids})
    business_events = [(event, features) for event, features in business_events if event.run_id in run_ids]
    artifact_events = [(event, path) for event, path in artifact_events if event.run_id in run_ids]

    latest_disposition_by_run: dict[str, OperationDispositionRow] = {}
    for row in disposition_rows:
        if row.run_id in run_ids and row.run_id not in latest_disposition_by_run:
            latest_disposition_by_run[row.run_id] = row

    status_summary = _run_status_summary(runs)
    successful_runs = status_summary.successful
    failed_runs = status_summary.failed
    running_runs = status_summary.running
    durations = status_summary.duration_values

    feature_counts = Counter()
    for features in run_features.values():
        for feature in features:
            feature_counts[feature] += 1
    ordinary_runs = max(0, len(runs) - len(run_features))
    if ordinary_runs:
        feature_counts["普通对话"] += ordinary_runs

    feature_usage = [FeatureUsageItem(name=name, count=count) for name, count in feature_counts.most_common(limit)]

    recent_business_events = [
        RecentBusinessEventItem(
            run_id=event.run_id,
            thread_id=event.thread_id,
            created_at=event.created_at,
            event_type=event.event_type,
            features=sorted(features),
            snippet=(event.content or "")[:180],
        )
        for event, features in business_events[: min(limit, 12)]
    ]

    activity_bucket_days = _activity_bucket_days(start, end)
    activity_bucket = _activity_bucket_name(activity_bucket_days)
    buckets = {
        period_start: {
            "end_date": period_end,
            "users": set(),
            "threads": 0,
            "runs": 0,
        }
        for period_start, period_end in _iter_activity_periods(start, end, activity_bucket_days)
    }
    for thread in thread_creations:
        key = _activity_bucket_key(thread.created_at, start, activity_bucket_days)
        buckets.setdefault(key, {"end_date": key, "users": set(), "threads": 0, "runs": 0})
        buckets[key]["threads"] += 1
        if thread.user_id:
            buckets[key]["users"].add(thread.user_id)
    for run in runs:
        key = _activity_bucket_key(run.created_at, start, activity_bucket_days)
        buckets.setdefault(key, {"end_date": key, "users": set(), "threads": 0, "runs": 0})
        buckets[key]["runs"] += 1
        if run.user_id:
            buckets[key]["users"].add(run.user_id)

    user_activity = [
        UserActivityPoint(
            date=key,
            end_date=str(value["end_date"]),
            active_users=len(value["users"]),
            threads=int(value["threads"]),
            runs=int(value["runs"]),
        )
        for key, value in sorted(buckets.items())
    ]

    user_buckets: dict[str, dict[str, Any]] = {}
    for run in runs:
        if not run.user_id:
            continue
        bucket = user_buckets.setdefault(
            run.user_id,
            {"runs": 0, "terminal_runs": 0, "successes": 0, "threads": set(), "tokens": 0},
        )
        bucket["runs"] += 1
        bucket["terminal_runs"] += int(run.status in _TERMINAL_STATUSES)
        bucket["successes"] += int(run.status == "success")
        bucket["threads"].add(run.thread_id)
        bucket["tokens"] += run.total_tokens or 0
    user_usage = [
        UserUsageItem(
            user_id=user_id,
            email=users_by_id.get(user_id).email if user_id in users_by_id else None,
            display_name=users_by_id.get(user_id).display_name if user_id in users_by_id else None,
            organization_name=users_by_id.get(user_id).organization_name if user_id in users_by_id else None,
            runs=bucket["runs"],
            threads=len(bucket["threads"]),
            total_tokens=bucket["tokens"],
            success_rate=_success_rate(bucket["successes"], bucket["terminal_runs"]),
        )
        for user_id, bucket in sorted(user_buckets.items(), key=lambda item: (item[1]["runs"], item[1]["tokens"]), reverse=True)[:limit]
    ]

    click_buckets: dict[str, dict[str, Any]] = {}
    for row in external_click_rows:
        if not row.resource_id:
            continue
        bucket = click_buckets.setdefault(row.resource_id, {"clicks": 0, "users": set()})
        bucket["clicks"] += 1
        if row.user_id:
            bucket["users"].add(row.user_id)
    external_agent_clicks = [ExternalAgentClickItem(name=name, clicks=bucket["clicks"], users=len(bucket["users"])) for name, bucket in sorted(click_buckets.items(), key=lambda item: item[1]["clicks"], reverse=True)]
    external_platform_agent_clicks = ExternalPlatformClickSummary(**summarize_external_platform_clicks(external_platform_click_rows))

    agent_buckets: dict[str, _AgentBucket] = {}
    for run in runs:
        bucket_key = _assistant_key(run, thread_meta)
        assistant_id = None if bucket_key == "default" else bucket_key
        meta = thread_meta.get(run.thread_id)
        name = _metadata_name(meta) or assistant_id or "默认智能体"
        bucket = agent_buckets.setdefault(bucket_key, _AgentBucket(name=name))
        bucket.runs += 1
        if run.status == "success":
            bucket.successes += 1
        if run.status in _TERMINAL_STATUSES:
            bucket.terminal_runs += 1
            bucket.durations.append(_duration_ms(run.created_at, run.updated_at))
        if run.user_id:
            bucket.users.add(run.user_id)

    agent_usage = [
        AgentUsageItem(
            assistant_id=None if key == "default" else key,
            name=bucket.name,
            runs=bucket.runs,
            users=len(bucket.users),
            success_rate=_success_rate(bucket.successes, bucket.terminal_runs),
            avg_duration_ms=_avg(bucket.durations),
        )
        for key, bucket in sorted(agent_buckets.items(), key=lambda item: item[1].runs, reverse=True)[:limit]
    ]

    model_buckets: dict[str, _ModelBucket] = {}
    for run in runs:
        model_name = run.model_name or "未记录模型"
        bucket = model_buckets.setdefault(model_name, _ModelBucket())
        bucket.runs += 1
        if run.status == "success":
            bucket.successes += 1
        if run.status in _TERMINAL_STATUSES:
            bucket.terminal_runs += 1
            bucket.durations.append(_duration_ms(run.created_at, run.updated_at))
        if run.user_id:
            bucket.users.add(run.user_id)
        bucket.total_tokens += run.total_tokens or 0
        bucket.llm_calls += run.llm_call_count or 0

    model_usage = [
        ModelUsageItem(
            model_name=model_name,
            runs=bucket.runs,
            users=len(bucket.users),
            success_rate=_success_rate(bucket.successes, bucket.terminal_runs),
            avg_duration_ms=_avg(bucket.durations),
            total_tokens=bucket.total_tokens,
            llm_calls=bucket.llm_calls,
        )
        for model_name, bucket in sorted(
            model_buckets.items(),
            key=lambda item: (item[1].runs, item[1].total_tokens),
            reverse=True,
        )[:limit]
    ]

    recent_failures = [
        RecentFailureItem(
            run_id=run.run_id,
            thread_id=run.thread_id,
            user_id=run.user_id,
            email=users_by_id.get(run.user_id).email if run.user_id and run.user_id in users_by_id else None,
            status=run.status,
            error=run.error,
            assistant_id=run.assistant_id,
            model_name=run.model_name,
            started_at=run.created_at,
            updated_at=run.updated_at,
            duration_ms=_duration_ms(run.created_at, run.updated_at),
            disposition_status=((latest_disposition_by_run[run.run_id].status or latest_disposition_by_run[run.run_id].action) if run.run_id in latest_disposition_by_run else None),
            diagnostics_url=f"/workspace/admin/observability?run_id={run.run_id}",
            can_open=True,
            chat_url=f"/workspace/chats/{run.thread_id}?admin_review=1",
        )
        for run in runs
        if run.status in _FAILED_STATUSES or run.error
    ][:limit]

    active_users = {row.user_id for row in runs if row.user_id} | {row.user_id for row in thread_creations if row.user_id}
    artifact_runs = feature_counts.get("产物生成", 0)
    knowledge_runs = feature_counts.get("知识库检索", 0)
    file_processing_runs = feature_counts.get("文件处理", 0)
    presentation_runs = feature_counts.get("PPT生成", 0)
    image_runs = feature_counts.get("图片生成", 0)
    resolved_value_profile, value_contributions, estimated_minutes_saved = _business_value_contributions(
        feature_counts,
        profile=value_profile,
    )

    artifact_summary = _summarize_artifact_events(artifact_events, limit=limit)

    positive_feedback = sum(1 for row in feedback_rows if row.rating > 0)
    negative_feedback = sum(1 for row in feedback_rows if row.rating < 0)
    feedback_total = positive_feedback + negative_feedback
    feedback_feature_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in feedback_rows:
        labels = run_features.get(row.run_id) or {"普通对话"}
        for label in labels:
            feedback_feature_counts[label]["positive" if row.rating > 0 else "negative"] += 1
    feedback_summary = FeedbackSummary(
        total=feedback_total,
        positive=positive_feedback,
        negative=negative_feedback,
        satisfaction_rate=_success_rate(positive_feedback, feedback_total),
        negative_rate=_success_rate(negative_feedback, feedback_total),
        recent_comments=[
            {
                "run_id": row.run_id,
                "thread_id": row.thread_id,
                "rating": row.rating,
                "comment": row.comment,
                "created_at": row.created_at.isoformat(),
            }
            for row in feedback_rows
            if row.comment
        ][: min(limit, 8)],
        recent_negative=[
            {
                "run_id": row.run_id,
                "thread_id": row.thread_id,
                "rating": row.rating,
                "comment": row.comment,
                "created_at": row.created_at.isoformat(),
                "diagnostics_url": f"/workspace/admin/observability?run_id={row.run_id}",
            }
            for row in feedback_rows
            if row.rating < 0
        ][: min(limit, 8)],
        by_feature=[
            FeedbackFeatureItem(
                name=name,
                positive=counts["positive"],
                negative=counts["negative"],
                satisfaction_rate=_success_rate(counts["positive"], counts["positive"] + counts["negative"]),
            )
            for name, counts in sorted(
                feedback_feature_counts.items(),
                key=lambda item: (item[1]["negative"], item[1]["positive"]),
                reverse=True,
            )[:limit]
        ],
    )

    return DashboardSummary(
        window=DashboardWindow(start_at=start, end_at=end, hours=window_hours),
        overview=DashboardOverview(
            total_users=int(total_users),
            new_users=int(new_users),
            active_users=len(active_users),
            threads=len(thread_creations),
            all_time_threads=int(total_thread_creations),
            existing_threads=int(existing_threads),
            runs=len(runs),
            terminal_runs=status_summary.terminal,
            successful_runs=successful_runs,
            failed_runs=failed_runs,
            running_runs=running_runs,
            success_rate=status_summary.success_rate,
            avg_run_duration_ms=_avg(durations),
            total_tokens=sum(row.total_tokens or 0 for row in runs),
            llm_calls=sum(row.llm_call_count or 0 for row in runs),
            artifact_runs=artifact_runs,
            knowledge_runs=knowledge_runs,
            feedback_total=feedback_total,
            satisfaction_rate=feedback_summary.satisfaction_rate,
            estimated_minutes_saved=estimated_minutes_saved,
        ),
        activity_bucket=activity_bucket,
        user_activity=user_activity,
        user_usage=user_usage,
        external_agent_clicks=external_agent_clicks,
        external_platform_agent_clicks=external_platform_agent_clicks,
        feature_usage=feature_usage,
        recent_business_events=recent_business_events,
        agent_usage=agent_usage,
        model_usage=model_usage,
        business_value=BusinessValueSummary(
            artifact_runs=artifact_runs,
            knowledge_runs=knowledge_runs,
            file_processing_runs=file_processing_runs,
            presentation_runs=presentation_runs,
            image_runs=image_runs,
            estimated_minutes_saved=estimated_minutes_saved,
            profile=resolved_value_profile,
            contributions=value_contributions,
        ),
        artifact_summary=artifact_summary,
        feedback=feedback_summary,
        knowledge_usage=KnowledgeUsageSummary(**weknora_usage),
        scheduled_task_usage=scheduled_task_usage,
        recent_failures=recent_failures,
    )
