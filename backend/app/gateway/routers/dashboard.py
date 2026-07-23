"""Admin business dashboard endpoints."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app.gateway.deps import require_admin_user
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.model import AuditLogRow, OperationDispositionRow
from deerflow.persistence.run.model import RunRow
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
    runs: int
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
    active_users: int
    threads: int
    runs: int


class UserUsageItem(BaseModel):
    user_id: str
    email: str | None
    runs: int
    threads: int
    total_tokens: int
    success_rate: float


class ExternalAgentClickItem(BaseModel):
    name: str
    clicks: int
    users: int


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


class KnowledgeUsageSummary(BaseModel):
    runs: int
    tool_calls: int
    recent_tool_calls: list[dict[str, Any]]


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
    chat_url: str


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
    user_activity: list[UserActivityPoint]
    user_usage: list[UserUsageItem]
    external_agent_clicks: list[ExternalAgentClickItem]
    feature_usage: list[FeatureUsageItem]
    recent_business_events: list[RecentBusinessEventItem]
    agent_usage: list[AgentUsageItem]
    model_usage: list[ModelUsageItem]
    business_value: BusinessValueSummary
    artifact_summary: ArtifactSummary
    feedback: FeedbackSummary
    knowledge_usage: KnowledgeUsageSummary
    recent_failures: list[RecentFailureItem]


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


def _duration_ms(created_at: datetime | None, updated_at: datetime | None) -> int:
    start = _coerce_aware(created_at)
    end = _coerce_aware(updated_at)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _day_key(value: datetime | None) -> str:
    aware = (_coerce_aware(value) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE)
    return aware.date().isoformat()


def _iter_day_keys(start: datetime, end: datetime) -> list[str]:
    keys: list[str] = []
    current: date = (_coerce_aware(start) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    final: date = (_coerce_aware(end) or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE).date()
    while current <= final:
        keys.append(current.isoformat())
        current += timedelta(days=1)
    return keys


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


def _knowledge_tool_calls(event: RunEventRow) -> list[str]:
    if event.event_type == "llm.ai.response":
        payload = _json_mapping(event.content or "")
        return [str(call.get("name") or "") for call in _tool_calls(payload) if "知识库检索" in _tool_features(str(call.get("name") or ""), call.get("args"))]
    if event.event_type in {"tool", "tool.start", "tool.end", "tool_start", "tool_end"}:
        return ["legacy"] if "知识库检索" in classify_dashboard_features(event.content, event.event_metadata, event_type=event.event_type) else []
    return []


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


@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    request: Request,
    hours: int = Query(default=168, ge=1, le=24 * 90),
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

        thread_ids = {row.thread_id for row in runs} | {row.thread_id for row in thread_creations}
        thread_meta: dict[str, ThreadMetaRow] = {}
        if thread_ids:
            meta_rows = (await session.execute(select(ThreadMetaRow).where(ThreadMetaRow.thread_id.in_(thread_ids)))).scalars()
            thread_meta = {row.thread_id: row for row in meta_rows}

        user_ids = {row.user_id for row in runs if row.user_id} | {row.user_id for row in thread_creations if row.user_id}
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

    all_events = events
    all_feedback_rows = feedback_rows
    run_features: dict[str, set[str]] = defaultdict(set)
    business_events: list[tuple[RunEventRow, set[str]]] = []
    artifact_events: list[tuple[RunEventRow, str]] = []
    knowledge_events: list[tuple[RunEventRow, str]] = []
    for event in all_events:
        features = classify_dashboard_features(event.content, event.event_metadata, event_type=event.event_type)
        if features:
            run_features[event.run_id].update(features)
            if event.event_type != "llm.tool.result":
                business_events.append((event, features))
        knowledge_events.extend((event, tool_name) for tool_name in _knowledge_tool_calls(event))
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
    knowledge_events = [item for item in knowledge_events if item[0].run_id in run_ids]

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

    buckets = {key: {"users": set(), "threads": 0, "runs": 0} for key in _iter_day_keys(start, end)}
    for thread in thread_creations:
        key = _day_key(thread.created_at)
        buckets.setdefault(key, {"users": set(), "threads": 0, "runs": 0})
        buckets[key]["threads"] += 1
        if thread.user_id:
            buckets[key]["users"].add(thread.user_id)
    for run in runs:
        key = _day_key(run.created_at)
        buckets.setdefault(key, {"users": set(), "threads": 0, "runs": 0})
        buckets[key]["runs"] += 1
        if run.user_id:
            buckets[key]["users"].add(run.user_id)

    user_activity = [
        UserActivityPoint(
            date=key,
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
            chat_url=f"/workspace/chats/{run.thread_id}",
        )
        for run in runs
        if run.status in _FAILED_STATUSES or run.error
    ][:limit]

    recent_knowledge_calls = [
        {
            "run_id": event.run_id,
            "thread_id": event.thread_id,
            "created_at": event.created_at.isoformat(),
            "event_type": event.event_type,
            "tool_name": tool_name,
            "snippet": tool_name,
        }
        for event, tool_name in knowledge_events[: min(limit, 8)]
    ]

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
            threads=int(total_thread_creations),
            runs=len(runs),
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
        user_activity=user_activity,
        user_usage=user_usage,
        external_agent_clicks=external_agent_clicks,
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
        knowledge_usage=KnowledgeUsageSummary(
            runs=knowledge_runs,
            tool_calls=len(knowledge_events),
            recent_tool_calls=recent_knowledge_calls,
        ),
        recent_failures=recent_failures,
    )
