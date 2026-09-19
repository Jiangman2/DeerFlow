"""Admin business dashboard endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, desc, func, nullslast, or_, select
from sqlalchemy.orm import load_only

from app.gateway.dashboard_cache import dashboard_response_cache
from app.gateway.dashboard_database import (
    dashboard_read_replica_required,
    get_dashboard_read_database_unavailable_reason,
    get_dashboard_read_session_factory,
)
from app.gateway.dashboard_rollups import (
    ROLLUP_SCHEMA_VERSION,
    DashboardContribution,
    DashboardQueryBusy,
    DashboardRollupsNotReady,
    RollupStatus,
    apply_dashboard_statement_limits,
    dashboard_query_slot,
    dashboard_rollups_enabled,
    fully_covered_business_days,
    fully_covered_business_hours,
    get_rollup_status,
    load_external_platform_rollup_summary,
    load_hybrid_contribution,
)
from app.gateway.deps import require_admin_user
from app.gateway.external_agent_analytics import (
    EXTERNAL_PLATFORM_CLICK_ACTION,
    ExternalPlatformClickAccumulator,
)
from app.gateway.weknora_analytics import empty_weknora_dashboard_analytics, get_weknora_dashboard_analytics
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
logger = logging.getLogger(__name__)

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
    available: bool = True
    error: str | None = None
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
    display_name: str | None
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


class DashboardAggregationInfo(BaseModel):
    source: str = "raw"
    schema_version: int = ROLLUP_SCHEMA_VERSION
    rollup_days: int = 0
    rollup_hours: int = 0
    raw_segments: int = 1


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
    aggregation: DashboardAggregationInfo = Field(default_factory=DashboardAggregationInfo)


class DashboardRollupStatusResponse(BaseModel):
    schema_version: int
    source_first_day: str | None
    source_last_complete_day: str | None
    expected_days: int
    ready_days: int
    missing_days: int
    oldest_missing_day: str | None
    newest_ready_day: str | None
    last_change_scan_at: datetime | None
    pending_refresh_days: int
    hourly_retention_hours: int
    expected_hours: int
    ready_hours: int
    missing_hours: int
    oldest_missing_hour: str | None
    newest_ready_hour: str | None
    last_hourly_change_scan_at: datetime | None
    pending_refresh_hours: int


class DashboardRunRankingItem(BaseModel):
    run_id: str
    thread_id: str
    model: str | None
    agent: str | None
    duration_ms: int
    tokens: int


class DashboardRunRankings(BaseModel):
    slow_runs: list[DashboardRunRankingItem]
    high_token_runs: list[DashboardRunRankingItem]


class RegisteredUserSessionSummary(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    organization_name: str | None
    system_role: str
    is_disabled: bool
    registered_at: datetime
    last_login_at: datetime | None
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


@dataclass(frozen=True, slots=True)
class _DashboardEventRef:
    run_id: str
    thread_id: str
    created_at: datetime
    event_type: str


@dataclass(frozen=True, slots=True)
class _BusinessEventRef(_DashboardEventRef):
    features: frozenset[str]
    snippet: str


@dataclass(frozen=True, slots=True)
class _RunRef:
    run_id: str
    thread_id: str
    assistant_id: str | None
    user_id: str | None
    status: str
    model_name: str | None
    error: str | None
    total_tokens: int
    llm_call_count: int
    created_at: datetime
    updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class _FeedbackRef:
    run_id: str
    thread_id: str
    rating: int
    comment: str | None
    created_at: datetime


def _session_factory():
    sf = get_dashboard_read_session_factory()
    if sf is None and dashboard_read_replica_required():
        reason = get_dashboard_read_database_unavailable_reason() or "read replica is unavailable"
        raise HTTPException(
            status_code=503,
            detail=f"看板只读副本暂不可用（{reason}）；为保护正常服务未回退主数据库。",
        )
    sf = sf or get_session_factory()
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
    end = _coerce_aware(end_at)
    if end is None:
        # Rolling windows share one stable cache key for a minute instead of
        # forcing an expensive new computation for every slightly different
        # request timestamp.
        now = datetime.now(UTC)
        end = now.replace(second=0, microsecond=0)
    start = _coerce_aware(start_at) or (end - timedelta(hours=max(1, hours)))
    if start >= end:
        start = end - timedelta(hours=1)
    window_hours = max(1, int((end - start).total_seconds() // 3600) or 1)
    return start, end, window_hours


def _dashboard_cache_ttl(start: datetime, end: datetime) -> int:
    """Use short freshness for live data and longer reuse for closed history."""

    age_seconds = (datetime.now(UTC) - end).total_seconds()
    if age_seconds > 3600:
        return 1800
    if (end - start) >= timedelta(days=7):
        return 300
    return 60


def _dashboard_rollup_min_days() -> int:
    try:
        value = int(os.getenv("DEER_FLOW_DASHBOARD_ROLLUP_MIN_DAYS", "2"))
    except ValueError:
        value = 2
    return max(2, min(value, 366))


def _dashboard_hourly_rollup_min_hours() -> int:
    try:
        value = int(os.getenv("DEER_FLOW_DASHBOARD_HOURLY_ROLLUP_MIN_HOURS", "6"))
    except ValueError:
        value = 6
    return max(2, min(value, 48))


def _dashboard_hourly_rollups_required() -> bool:
    return os.getenv("DEER_FLOW_DASHBOARD_REQUIRE_HOURLY_ROLLUPS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _dashboard_cache_key(section: str, **values: Any) -> str:
    normalized = json.dumps(values, ensure_ascii=True, sort_keys=True, default=str, separators=(",", ":"))
    return f"v2:{section}:{hashlib.sha256(normalized.encode()).hexdigest()}"


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


@dataclass(frozen=True)
class _CompactSupportingMetrics:
    total_users: int
    new_users: int
    all_time_threads: int
    existing_threads: int
    users_by_id: dict[str, UserRow]
    scheduled_task_usage: ScheduledTaskUsageSummary


async def _load_compact_supporting_metrics(
    *,
    start: datetime,
    end: datetime,
    limit: int,
    profile_user_ids: set[str],
) -> _CompactSupportingMetrics:
    """Load bounded inventory facts without materializing scheduled-run history."""

    sf = _session_factory()
    async with sf() as session:
        await apply_dashboard_statement_limits(session, maintenance=False)
        total_users = int(await session.scalar(select(func.count()).select_from(UserRow)) or 0)
        new_users = int(
            await session.scalar(
                select(func.count())
                .select_from(UserRow)
                .where(
                    UserRow.created_at >= start,
                    UserRow.created_at < end,
                )
            )
            or 0
        )
        all_time_threads = int(await session.scalar(select(func.count()).select_from(ThreadCreationRow)) or 0)
        existing_threads = int(await session.scalar(select(func.count()).select_from(ThreadMetaRow)) or 0)

        tasks = list(
            (
                await session.execute(
                    select(ScheduledTaskRow).options(
                        load_only(
                            ScheduledTaskRow.id,
                            ScheduledTaskRow.user_id,
                            ScheduledTaskRow.status,
                            ScheduledTaskRow.created_at,
                        )
                    )
                )
            )
            .scalars()
            .all()
        )
        task_owner = {str(task.id): str(task.user_id) for task in tasks if task.id and task.user_id}
        run_groups = (
            await session.execute(
                select(
                    ScheduledTaskRunRow.task_id,
                    ScheduledTaskRunRow.status,
                    func.count().label("run_count"),
                )
                .where(
                    ScheduledTaskRunRow.created_at >= start,
                    ScheduledTaskRunRow.created_at < end,
                )
                .group_by(ScheduledTaskRunRow.task_id, ScheduledTaskRunRow.status)
            )
        ).all()
        run_task_ids = {str(row.task_id) for row in run_groups if row.task_id}
        audit_scope = and_(AuditLogRow.created_at >= start, AuditLogRow.created_at < end)
        missing_task_ids = run_task_ids - set(task_owner)
        audit_owner_filter = or_(audit_scope, AuditLogRow.resource_id.in_(missing_task_ids)) if missing_task_ids else audit_scope
        creation_audits = list(
            (
                await session.execute(
                    select(AuditLogRow)
                    .options(
                        load_only(
                            AuditLogRow.resource_id,
                            AuditLogRow.user_id,
                            AuditLogRow.user_email,
                            AuditLogRow.created_at,
                        )
                    )
                    .where(AuditLogRow.action == "scheduled_task.create", audit_owner_filter)
                )
            )
            .scalars()
            .all()
        )

        audit_email_by_user: dict[str, str] = {}
        for audit in creation_audits:
            if audit.resource_id and audit.user_id:
                task_owner.setdefault(str(audit.resource_id), str(audit.user_id))
            if audit.user_id and audit.user_email:
                audit_email_by_user.setdefault(str(audit.user_id), audit.user_email)

        created_task_ids = {str(task.id) for task in tasks if task.id and start <= (_coerce_aware(task.created_at) or start) < end}
        created_task_ids.update(str(audit.resource_id) for audit in creation_audits if audit.resource_id and start <= (_coerce_aware(audit.created_at) or start) < end)
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
            bucket = user_buckets[str(task.user_id)]
            bucket["current_tasks"] += 1
            bucket["enabled_tasks"] += int(task.status == "enabled")
            bucket["paused_tasks"] += int(task.status == "paused")
        for task_id in created_task_ids:
            owner = task_owner.get(task_id)
            if owner:
                user_buckets[owner]["created_tasks"] += 1
        run_totals = Counter()
        for row in run_groups:
            count = int(row.run_count or 0)
            status = str(row.status or "")
            run_totals[status] += count
            owner = task_owner.get(str(row.task_id))
            if not owner:
                continue
            bucket = user_buckets[owner]
            bucket["runs"] += count
            bucket["successful_runs"] += count if status == "success" else 0
            bucket["failed_runs"] += count if status in {"failed", "interrupted"} else 0
            bucket["skipped_runs"] += count if status == "skipped" else 0

        top_scheduled_users = [
            user_id
            for user_id, _ in sorted(
                user_buckets.items(),
                key=lambda item: (
                    item[1]["runs"],
                    item[1]["created_tasks"],
                    item[1]["current_tasks"],
                ),
                reverse=True,
            )[:limit]
        ]
        requested_profiles = profile_user_ids | set(top_scheduled_users)
        users_by_id: dict[str, UserRow] = {}
        if requested_profiles:
            users = (
                await session.execute(
                    select(UserRow)
                    .options(
                        load_only(
                            UserRow.id,
                            UserRow.email,
                            UserRow.display_name,
                            UserRow.organization_name,
                        )
                    )
                    .where(UserRow.id.in_(requested_profiles))
                )
            ).scalars()
            users_by_id = {str(user.id): user for user in users}

    scheduled_users = [
        ScheduledTaskUserUsageItem(
            user_id=user_id,
            email=(users_by_id[user_id].email if user_id in users_by_id else audit_email_by_user.get(user_id)),
            display_name=(users_by_id[user_id].display_name if user_id in users_by_id else None),
            organization_name=(users_by_id[user_id].organization_name if user_id in users_by_id else None),
            **user_buckets[user_id],
        )
        for user_id in top_scheduled_users
    ]
    scheduled = ScheduledTaskUsageSummary(
        current_tasks=len(tasks),
        created_tasks=len(created_task_ids),
        enabled_tasks=sum(task.status == "enabled" for task in tasks),
        paused_tasks=sum(task.status == "paused" for task in tasks),
        runs=sum(run_totals.values()),
        successful_runs=run_totals["success"],
        failed_runs=run_totals["failed"] + run_totals["interrupted"],
        skipped_runs=run_totals["skipped"],
        users=scheduled_users,
    )
    return _CompactSupportingMetrics(
        total_users=total_users,
        new_users=new_users,
        all_time_threads=all_time_threads,
        existing_threads=existing_threads,
        users_by_id=users_by_id,
        scheduled_task_usage=scheduled,
    )


def _duration_ms(created_at: datetime | None, updated_at: datetime | None) -> int:
    start = _coerce_aware(created_at)
    end = _coerce_aware(updated_at)
    if start is None or end is None:
        return 0
    return max(0, int((end - start).total_seconds() * 1000))


def _weknora_profile_emails(usage: dict[str, Any]) -> set[str]:
    """Collect normalized account emails from the read-only WeKnora report."""

    emails: set[str] = set()
    for collection, email_field in (
        ("knowledge_bases", "creator_email"),
        ("users", "email"),
        ("shared_spaces", "owner_email"),
    ):
        for item in usage.get(collection, []):
            if not isinstance(item, dict):
                continue
            value = item.get(email_field)
            if isinstance(value, str) and value.strip():
                emails.add(value.strip().casefold())
    return emails


def _apply_weknora_profile_names(
    usage: dict[str, Any],
    profiles_by_email: dict[str, UserRow],
) -> None:
    """Prefer DeerFlow's SSO display name over a WeKnora username fallback."""

    for collection, email_field, name_field in (
        ("knowledge_bases", "creator_email", "creator_name"),
        ("users", "email", "name"),
        ("shared_spaces", "owner_email", "owner_name"),
    ):
        for item in usage.get(collection, []):
            if not isinstance(item, dict):
                continue
            email = item.get(email_field)
            if not isinstance(email, str):
                continue
            profile = profiles_by_email.get(email.strip().casefold())
            if profile and profile.display_name and profile.display_name.strip():
                item[name_field] = profile.display_name.strip()


async def _load_dashboard_knowledge_usage(
    *,
    start: datetime,
    end: datetime,
    limit: int,
) -> dict[str, Any]:
    usage = await get_weknora_dashboard_analytics(start=start, end=end, limit=limit)
    profile_emails = _weknora_profile_emails(usage)
    if not profile_emails:
        return usage

    sf = _session_factory()
    async with sf() as session:
        profile_rows = (await session.execute(select(UserRow).options(load_only(UserRow.email, UserRow.display_name)).where(func.lower(UserRow.email).in_(profile_emails)))).scalars()
        _apply_weknora_profile_names(
            usage,
            {row.email.strip().casefold(): row for row in profile_rows},
        )
    return usage


async def _load_external_platform_click_summary() -> ExternalPlatformClickSummary:
    """Share external-platform totals across dashboard filters.

    Before rollups are explicitly enabled, retain the established source-table
    behavior but stream projected rows through the bounded dashboard pool.  Once
    rollups are enabled, missing facts remain a visible partial-data state rather
    than silently issuing the expensive compatibility scan.
    """

    business_day = datetime.now(UTC).astimezone(_BUSINESS_TIMEZONE).date().isoformat()
    rollups_enabled = dashboard_rollups_enabled()
    cache_key = _dashboard_cache_key(
        "external-platform-clicks",
        business_day=business_day,
        rollups_enabled=rollups_enabled,
    )

    async def _produce() -> ExternalPlatformClickSummary:
        if rollups_enabled:
            result = await load_external_platform_rollup_summary(_session_factory())
            if result is not None:
                return ExternalPlatformClickSummary(**result)
            empty = ExternalPlatformClickAccumulator().result()
            return ExternalPlatformClickSummary(
                **empty,
                available=False,
                error="历史点击汇总正在准备；为保护正常服务未扫描全量审计日志。",
            )

        accumulator = ExternalPlatformClickAccumulator()
        session_factory = _session_factory()
        async with session_factory() as session:
            await apply_dashboard_statement_limits(session, maintenance=False)
            statement = (
                select(
                    AuditLogRow.resource_id,
                    AuditLogRow.user_id,
                    AuditLogRow.user_email,
                    AuditLogRow.details_json,
                    AuditLogRow.created_at,
                )
                .where(AuditLogRow.action == EXTERNAL_PLATFORM_CLICK_ACTION)
                .execution_options(yield_per=1000)
            )
            rows = await session.stream(statement)
            async for row in rows:
                accumulator.add(row)
        return ExternalPlatformClickSummary(**accumulator.result())

    cached = await dashboard_response_cache.get_or_create(
        cache_key,
        ttl_seconds=60,
        producer=_produce,
        encode=lambda value: value.model_dump_json(),
        decode=ExternalPlatformClickSummary.model_validate_json,
    )
    return cached.value


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


def _run_status_summary(runs: list[RunRow | _RunRef]) -> _RunStatusSummary:
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


def _assistant_key(run: RunRow | _RunRef, thread_meta: dict[str, ThreadMetaRow]) -> str:
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
        users = list(
            (
                await session.execute(
                    user_stmt.order_by(
                        nullslast(desc(UserRow.last_login_at)),
                        desc(UserRow.created_at),
                        UserRow.email.asc(),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
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
                last_login_at=_coerce_aware(user.last_login_at),
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
    q: str = Query(default="", max_length=200),
) -> FailedRunList:
    """Return every failed run, optionally filtered by account or name."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    failed_filter = or_(RunRow.status.in_(_FAILED_STATUSES), RunRow.error.is_not(None))
    owner_user_id = func.coalesce(RunRow.user_id, ThreadMetaRow.user_id)
    rows_stmt = select(RunRow, owner_user_id.label("owner_user_id"), UserRow).outerjoin(ThreadMetaRow, ThreadMetaRow.thread_id == RunRow.thread_id).outerjoin(UserRow, UserRow.id == owner_user_id).where(failed_filter)
    count_stmt = select(func.count()).select_from(RunRow).outerjoin(ThreadMetaRow, ThreadMetaRow.thread_id == RunRow.thread_id).outerjoin(UserRow, UserRow.id == owner_user_id).where(failed_filter)
    normalized_query = q.strip().casefold()
    if normalized_query:
        pattern = f"%{normalized_query}%"
        identity_filter = or_(
            func.lower(UserRow.email).like(pattern),
            func.lower(func.coalesce(UserRow.display_name, "")).like(pattern),
        )
        rows_stmt = rows_stmt.where(identity_filter)
        count_stmt = count_stmt.where(identity_filter)

    sf = _session_factory()
    async with sf() as session:
        total = int(await session.scalar(count_stmt) or 0)
        result_rows = list(
            (
                await session.execute(
                    rows_stmt.order_by(
                        desc(RunRow.updated_at),
                        desc(RunRow.created_at),
                        desc(RunRow.run_id),
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).all()
        )
        runs = [row[0] for row in result_rows]
        owner_by_run = {row[0].run_id: (row[1], row[2]) for row in result_rows}
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
        owner_id, owner = owner_by_run.get(run.run_id, (run.user_id, None))
        items.append(
            RecentFailureItem(
                run_id=run.run_id,
                thread_id=run.thread_id,
                user_id=owner_id,
                email=owner.email if owner else None,
                display_name=owner.display_name if owner else None,
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
    response: Response,
    hours: int = Query(default=168, ge=1),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    status: str | None = Query(default=None),
    model: str | None = Query(default=None),
    agent: str | None = Query(default=None),
    feature: str | None = Query(default=None),
    value_profile: str = Query(default="conservative"),
    include_knowledge: bool = Query(default=True),
    include_business_details: bool = Query(default=True),
    refresh: bool = Query(default=False),
) -> DashboardSummary:
    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    start, end, window_hours = _window_bounds(hours, start_at, end_at)
    rollups_enabled = dashboard_rollups_enabled()

    cache_key = _dashboard_cache_key(
        "summary",
        start=start.isoformat(),
        end=end.isoformat(),
        limit=limit,
        status=status,
        model=model,
        agent=agent,
        feature=feature,
        value_profile=value_profile,
        include_knowledge=include_knowledge,
        include_business_details=include_business_details,
        rollups_enabled=rollups_enabled,
    )
    started = perf_counter()

    async def _produce() -> DashboardSummary:
        compact_eligible = rollups_enabled and not include_knowledge and not include_business_details and all(value in (None, "", "all") for value in (status, model, agent, feature))
        try:
            async with dashboard_query_slot():
                if compact_eligible:
                    return await _build_compact_dashboard_summary(
                        start=start,
                        end=end,
                        window_hours=window_hours,
                        limit=limit,
                        value_profile=value_profile,
                    )
                return await _build_dashboard_summary(
                    start=start,
                    end=end,
                    window_hours=window_hours,
                    limit=limit,
                    status=status,
                    model=model,
                    agent=agent,
                    feature=feature,
                    value_profile=value_profile,
                    include_knowledge=include_knowledge,
                    include_business_details=include_business_details,
                )
        except DashboardQueryBusy as exc:
            raise HTTPException(
                status_code=429,
                detail="看板统计正在生成，请稍后重试；正常对话和数字员工服务不受影响。",
                headers={"Retry-After": "2"},
            ) from exc
        except DashboardRollupsNotReady as exc:
            if exc.missing_hours:
                count = len(exc.missing_hours)
                unit = "小时"
                first = exc.missing_hours[0].isoformat()
            else:
                count = len(exc.missing_days)
                unit = "天"
                first = exc.missing_days[0].isoformat() if exc.missing_days else "unknown"
            raise HTTPException(
                status_code=503,
                detail=f"所选区间正在准备汇总数据（尚缺 {count} {unit}，最早 {first}），为保护正常服务未执行大范围明细扫描。",
                headers={"Retry-After": "60"},
            ) from exc

    cached = await dashboard_response_cache.get_or_create(
        cache_key,
        ttl_seconds=_dashboard_cache_ttl(start, end),
        producer=_produce,
        encode=lambda value: value.model_dump_json(),
        decode=DashboardSummary.model_validate_json,
        bypass=refresh,
    )
    duration_ms = int((perf_counter() - started) * 1000)
    response.headers["X-Dashboard-Cache"] = cached.source
    response.headers["X-Dashboard-Data-Source"] = cached.value.aggregation.source
    response.headers["Server-Timing"] = f"dashboard;dur={duration_ms}"
    response.headers["Cache-Control"] = "private, no-store"
    if duration_ms >= 2000:
        logger.warning(
            "Slow dashboard summary: duration_ms=%d window_hours=%d cache=%s include_knowledge=%s",
            duration_ms,
            window_hours,
            cached.source,
            include_knowledge,
        )
    return cached.value


@router.get("/rollups/status", response_model=DashboardRollupStatusResponse)
async def get_dashboard_rollup_status(request: Request) -> DashboardRollupStatusResponse:
    """Expose readiness only; maintenance is deliberately never run by HTTP."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    try:
        async with dashboard_query_slot():
            status: RollupStatus = await get_rollup_status(_session_factory())
    except DashboardQueryBusy as exc:
        raise HTTPException(status_code=429, detail="看板统计服务繁忙，请稍后重试。") from exc
    return DashboardRollupStatusResponse(
        schema_version=status.schema_version,
        source_first_day=status.source_first_day.isoformat() if status.source_first_day else None,
        source_last_complete_day=(status.source_last_complete_day.isoformat() if status.source_last_complete_day else None),
        expected_days=status.expected_days,
        ready_days=status.ready_days,
        missing_days=status.missing_days,
        oldest_missing_day=status.oldest_missing_day.isoformat() if status.oldest_missing_day else None,
        newest_ready_day=status.newest_ready_day.isoformat() if status.newest_ready_day else None,
        last_change_scan_at=status.last_change_scan_at,
        pending_refresh_days=status.pending_refresh_days,
        hourly_retention_hours=status.hourly_retention_hours,
        expected_hours=status.expected_hours,
        ready_hours=status.ready_hours,
        missing_hours=status.missing_hours,
        oldest_missing_hour=(status.oldest_missing_hour.isoformat() if status.oldest_missing_hour else None),
        newest_ready_hour=(status.newest_ready_hour.isoformat() if status.newest_ready_hour else None),
        last_hourly_change_scan_at=status.last_hourly_change_scan_at,
        pending_refresh_hours=status.pending_refresh_hours,
    )


@router.get("/run-rankings", response_model=DashboardRunRankings)
async def get_dashboard_run_rankings(
    request: Request,
    response: Response,
    hours: int = Query(default=168, ge=1),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=10, ge=1, le=50),
) -> DashboardRunRankings:
    """Return top-N runs using bounded SQL, never full-window ORM materialization."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    start, end, _ = _window_bounds(hours, start_at, end_at)
    rollups_enabled = dashboard_rollups_enabled()
    cache_key = _dashboard_cache_key(
        "run-rankings",
        start=start.isoformat(),
        end=end.isoformat(),
        limit=limit,
        rollups_enabled=rollups_enabled,
    )

    async def _produce() -> DashboardRunRankings:
        try:
            async with dashboard_query_slot():
                use_rollup = rollups_enabled and len(fully_covered_business_days(start, end)) >= _dashboard_rollup_min_days()
                use_rollup = use_rollup or (rollups_enabled and _dashboard_hourly_rollups_required() and len(fully_covered_business_hours(start, end)) >= _dashboard_hourly_rollup_min_hours())
                if use_rollup:
                    selection = await load_hybrid_contribution(
                        _session_factory(),
                        start=start,
                        end=end,
                        minimum_rollup_days=_dashboard_rollup_min_days(),
                        minimum_hourly_rollup_hours=_dashboard_hourly_rollup_min_hours(),
                    )

                    def _fact_items(facts) -> list[DashboardRunRankingItem]:  # type: ignore[no-untyped-def]
                        return [
                            DashboardRunRankingItem(
                                run_id=item.run_id,
                                thread_id=item.thread_id,
                                model=item.model_name,
                                agent=(None if item.assistant_id == "default" else item.assistant_id),
                                duration_ms=item.duration_ms,
                                tokens=item.total_tokens,
                            )
                            for item in facts[:limit]
                        ]

                    return DashboardRunRankings(
                        slow_runs=_fact_items(selection.contribution.slow_run_candidates),
                        high_token_runs=_fact_items(selection.contribution.high_token_candidates),
                    )
                sf = _session_factory()
                async with sf() as session:
                    await apply_dashboard_statement_limits(session, maintenance=False)
                    if session.get_bind().dialect.name == "postgresql":
                        duration_expression = func.extract("epoch", RunRow.updated_at - RunRow.created_at) * 1000
                    else:
                        duration_expression = (func.julianday(RunRow.updated_at) - func.julianday(RunRow.created_at)) * 86_400_000
                    columns = (
                        RunRow.run_id,
                        RunRow.thread_id,
                        RunRow.model_name,
                        RunRow.assistant_id,
                        RunRow.total_tokens,
                        duration_expression.label("duration_ms"),
                    )
                    filters = (
                        RunRow.created_at >= start,
                        RunRow.created_at < end,
                        RunRow.updated_at.is_not(None),
                        RunRow.status.in_(_TERMINAL_STATUSES),
                    )
                    slow_rows = (await session.execute(select(*columns).where(*filters).order_by(desc(duration_expression), desc(RunRow.created_at)).limit(limit))).all()
                    token_rows = (await session.execute(select(*columns).where(*filters).order_by(desc(RunRow.total_tokens), desc(RunRow.created_at)).limit(limit))).all()

                def _items(rows) -> list[DashboardRunRankingItem]:  # type: ignore[no-untyped-def]
                    return [
                        DashboardRunRankingItem(
                            run_id=row.run_id,
                            thread_id=row.thread_id,
                            model=row.model_name,
                            agent=row.assistant_id,
                            duration_ms=max(0, int(row.duration_ms or 0)),
                            tokens=max(0, int(row.total_tokens or 0)),
                        )
                        for row in rows
                    ]

                return DashboardRunRankings(slow_runs=_items(slow_rows), high_token_runs=_items(token_rows))
        except DashboardQueryBusy as exc:
            raise HTTPException(
                status_code=429,
                detail="运行排行正在生成，请稍后重试；正常服务不受影响。",
                headers={"Retry-After": "2"},
            ) from exc
        except DashboardRollupsNotReady as exc:
            count = len(exc.missing_hours) or len(exc.missing_days)
            unit = "小时" if exc.missing_hours else "天"
            raise HTTPException(
                status_code=503,
                detail=f"所选区间的运行排行正在准备（尚缺 {count} {unit}），未执行大范围明细扫描。",
                headers={"Retry-After": "60"},
            ) from exc

    cached = await dashboard_response_cache.get_or_create(
        cache_key,
        ttl_seconds=_dashboard_cache_ttl(start, end),
        producer=_produce,
        encode=lambda value: value.model_dump_json(),
        decode=DashboardRunRankings.model_validate_json,
    )
    response.headers["X-Dashboard-Cache"] = cached.source
    response.headers["Cache-Control"] = "private, no-store"
    return cached.value


@router.get("/knowledge", response_model=KnowledgeUsageSummary)
async def get_dashboard_knowledge(
    request: Request,
    response: Response,
    hours: int = Query(default=168, ge=1),
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    limit: int = Query(default=10, ge=1, le=50),
    refresh: bool = Query(default=False),
) -> KnowledgeUsageSummary:
    """Load the independent WeKnora panel without blocking headline metrics."""

    await require_admin_user(request, detail=_ADMIN_REQUIRED)
    start, end, _ = _window_bounds(hours, start_at, end_at)
    cache_key = _dashboard_cache_key(
        "knowledge",
        start=start.isoformat(),
        end=end.isoformat(),
        limit=limit,
    )

    async def _produce() -> KnowledgeUsageSummary:
        usage = await _load_dashboard_knowledge_usage(start=start, end=end, limit=limit)
        return KnowledgeUsageSummary(**usage)

    started = perf_counter()
    cached = await dashboard_response_cache.get_or_create(
        cache_key,
        ttl_seconds=max(300, _dashboard_cache_ttl(start, end)),
        producer=_produce,
        encode=lambda value: value.model_dump_json(),
        decode=KnowledgeUsageSummary.model_validate_json,
        bypass=refresh,
    )
    duration_ms = int((perf_counter() - started) * 1000)
    response.headers["X-Dashboard-Cache"] = cached.source
    response.headers["Server-Timing"] = f"dashboard-knowledge;dur={duration_ms}"
    response.headers["Cache-Control"] = "private, no-store"
    if duration_ms >= 2000:
        logger.warning(
            "Slow dashboard knowledge analytics: duration_ms=%d cache=%s",
            duration_ms,
            cached.source,
        )
    return cached.value


async def _build_dashboard_summary(
    *,
    start: datetime,
    end: datetime,
    window_hours: int,
    limit: int,
    status: str | None,
    model: str | None,
    agent: str | None,
    feature: str | None,
    value_profile: str,
    include_knowledge: bool,
    include_business_details: bool,
) -> DashboardSummary:

    sf = _session_factory()
    async with sf() as session:
        await apply_dashboard_statement_limits(session, maintenance=False)
        run_stmt = (
            select(
                RunRow.run_id,
                RunRow.thread_id,
                RunRow.assistant_id,
                RunRow.user_id,
                RunRow.status,
                RunRow.model_name,
                func.substr(RunRow.error, 1, 4000).label("error"),
                RunRow.total_tokens,
                RunRow.llm_call_count,
                RunRow.created_at,
                RunRow.updated_at,
            )
            .where(RunRow.created_at >= start, RunRow.created_at <= end)
            .order_by(desc(RunRow.created_at))
            .execution_options(yield_per=1000)
        )
        if status and status != "all":
            run_stmt = run_stmt.where(RunRow.status == status)
        if model and model != "all":
            run_stmt = run_stmt.where(func.coalesce(RunRow.model_name, "未记录模型") == model)
        run_stream = await session.stream(run_stmt)
        runs = [
            _RunRef(
                run_id=row.run_id,
                thread_id=row.thread_id,
                assistant_id=row.assistant_id,
                user_id=row.user_id,
                status=row.status,
                model_name=row.model_name,
                error=row.error,
                total_tokens=int(row.total_tokens or 0),
                llm_call_count=int(row.llm_call_count or 0),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            async for row in run_stream
        ]

        thread_creation_stmt = (
            select(ThreadCreationRow)
            .options(load_only(ThreadCreationRow.thread_id, ThreadCreationRow.user_id, ThreadCreationRow.created_at))
            .where(
                ThreadCreationRow.created_at >= start,
                ThreadCreationRow.created_at <= end,
            )
        )
        thread_creations = list((await session.execute(thread_creation_stmt)).scalars().all())
        total_thread_creations = await session.scalar(select(func.count()).select_from(ThreadCreationRow)) or 0
        existing_threads = await session.scalar(select(func.count()).select_from(ThreadMetaRow)) or 0

        thread_ids = {row.thread_id for row in runs} | {row.thread_id for row in thread_creations}
        thread_meta: dict[str, ThreadMetaRow] = {}
        if thread_ids:
            meta_rows = (
                await session.execute(
                    select(ThreadMetaRow)
                    .options(
                        load_only(
                            ThreadMetaRow.thread_id,
                            ThreadMetaRow.assistant_id,
                            ThreadMetaRow.user_id,
                            ThreadMetaRow.display_name,
                            ThreadMetaRow.metadata_json,
                        )
                    )
                    .where(ThreadMetaRow.thread_id.in_(thread_ids))
                )
            ).scalars()
            thread_meta = {row.thread_id: row for row in meta_rows}

        scheduled_task_rows = list(
            (await session.execute(select(ScheduledTaskRow).options(load_only(ScheduledTaskRow.id, ScheduledTaskRow.user_id, ScheduledTaskRow.status, ScheduledTaskRow.created_at)).order_by(desc(ScheduledTaskRow.created_at))))
            .scalars()
            .all()
        )
        scheduled_task_run_rows = list(
            (
                await session.execute(
                    select(ScheduledTaskRunRow)
                    .options(load_only(ScheduledTaskRunRow.task_id, ScheduledTaskRunRow.status, ScheduledTaskRunRow.created_at))
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
        scheduled_task_ids_for_runs = {str(row.task_id) for row in scheduled_task_run_rows if row.task_id}
        scheduled_audit_scope = [
            AuditLogRow.created_at >= start,
            AuditLogRow.created_at <= end,
        ]
        if scheduled_task_ids_for_runs:
            scheduled_audit_filter = or_(
                and_(*scheduled_audit_scope),
                AuditLogRow.resource_id.in_(scheduled_task_ids_for_runs),
            )
        else:
            scheduled_audit_filter = and_(*scheduled_audit_scope)
        scheduled_creation_audits = list(
            (
                await session.execute(
                    select(AuditLogRow)
                    .options(
                        load_only(
                            AuditLogRow.resource_id,
                            AuditLogRow.user_id,
                            AuditLogRow.user_email,
                            AuditLogRow.created_at,
                        )
                    )
                    .where(
                        AuditLogRow.action == "scheduled_task.create",
                        scheduled_audit_filter,
                    )
                )
            )
            .scalars()
            .all()
        )

        user_ids = (
            {row.user_id for row in runs if row.user_id}
            | {row.user_id for row in thread_creations if row.user_id}
            | {row.user_id for row in scheduled_task_rows if row.user_id}
            | {row.user_id for row in scheduled_creation_audits if row.user_id}
        )
        users_by_id: dict[str, UserRow] = {}
        if user_ids:
            user_rows = (await session.execute(select(UserRow).options(load_only(UserRow.id, UserRow.email, UserRow.display_name, UserRow.organization_name)).where(UserRow.id.in_(user_ids)))).scalars()
            users_by_id = {row.id: row for row in user_rows}

        total_users = await session.scalar(select(func.count()).select_from(UserRow)) or 0
        new_users = await session.scalar(select(func.count()).select_from(UserRow).where(UserRow.created_at >= start, UserRow.created_at <= end)) or 0

        run_features: dict[str, set[str]] = defaultdict(set)
        latest_business_event_by_run: dict[str, _BusinessEventRef] = {}
        artifact_events_by_run: dict[str, dict[tuple[str, str], _DashboardEventRef]] = defaultdict(dict)
        if include_business_details or (feature and feature != "all"):
            event_stmt = (
                select(RunEventRow)
                .join(RunRow, RunRow.run_id == RunEventRow.run_id)
                .options(
                    load_only(
                        RunEventRow.run_id,
                        RunEventRow.thread_id,
                        RunEventRow.event_type,
                        RunEventRow.content,
                        RunEventRow.event_metadata,
                        RunEventRow.created_at,
                    )
                )
                .where(
                    RunEventRow.created_at >= start,
                    RunEventRow.created_at <= end,
                    RunEventRow.event_type.in_(_DASHBOARD_EVENT_TYPES),
                    RunRow.created_at >= start,
                    RunRow.created_at <= end,
                )
                .order_by(desc(RunEventRow.created_at))
                .execution_options(yield_per=500)
            )
            if status and status != "all":
                event_stmt = event_stmt.where(RunRow.status == status)
            if model and model != "all":
                event_stmt = event_stmt.where(func.coalesce(RunRow.model_name, "未记录模型") == model)
            event_stream = await session.stream(event_stmt)
            async for event in event_stream.scalars():
                features = classify_dashboard_features(event.content, event.event_metadata, event_type=event.event_type)
                event_ref = _DashboardEventRef(
                    run_id=event.run_id,
                    thread_id=event.thread_id,
                    created_at=event.created_at,
                    event_type=event.event_type,
                )
                if features:
                    run_features[event.run_id].update(features)
                    if event.event_type != "llm.tool.result":
                        latest_business_event_by_run.setdefault(
                            event.run_id,
                            _BusinessEventRef(
                                run_id=event.run_id,
                                thread_id=event.thread_id,
                                created_at=event.created_at,
                                event_type=event.event_type,
                                features=frozenset(features),
                                snippet=(event.content or "")[:180],
                            ),
                        )
                for artifact_path in _extract_artifact_paths(event):
                    artifact_events_by_run[event.run_id].setdefault((event.thread_id, artifact_path), event_ref)

        feedback_stmt = (
            select(
                FeedbackRow.run_id,
                FeedbackRow.thread_id,
                FeedbackRow.rating,
                func.substr(FeedbackRow.comment, 1, 2000).label("comment"),
                FeedbackRow.created_at,
            )
            .where(FeedbackRow.created_at >= start, FeedbackRow.created_at <= end)
            .order_by(desc(FeedbackRow.created_at))
            .execution_options(yield_per=500)
        )
        feedback_stream = await session.stream(feedback_stmt)
        feedback_rows = [
            _FeedbackRef(
                run_id=row.run_id,
                thread_id=row.thread_id,
                rating=int(row.rating),
                comment=row.comment,
                created_at=row.created_at,
            )
            async for row in feedback_stream
        ]

        disposition_stmt = (
            select(OperationDispositionRow)
            .options(
                load_only(
                    OperationDispositionRow.run_id,
                    OperationDispositionRow.status,
                    OperationDispositionRow.action,
                    OperationDispositionRow.created_at,
                )
            )
            .where(OperationDispositionRow.created_at >= start, OperationDispositionRow.created_at <= end)
            .order_by(desc(OperationDispositionRow.created_at))
        )
        disposition_rows = list((await session.execute(disposition_stmt)).scalars().all())

        click_stmt = (
            select(AuditLogRow)
            .options(load_only(AuditLogRow.resource_id, AuditLogRow.user_id, AuditLogRow.created_at))
            .where(
                AuditLogRow.created_at >= start,
                AuditLogRow.created_at <= end,
                AuditLogRow.action == "external_agent.click",
            )
            .execution_options(yield_per=1000)
        )
        click_buckets: dict[str, dict[str, Any]] = {}
        external_click_stream = await session.stream(click_stmt)
        async for row in external_click_stream.scalars():
            if not row.resource_id:
                continue
            bucket = click_buckets.setdefault(row.resource_id, {"clicks": 0, "users": set()})
            bucket["clicks"] += 1
            if row.user_id:
                bucket["users"].add(row.user_id)

    if include_knowledge:
        weknora_usage, external_platform_clicks = await asyncio.gather(
            _load_dashboard_knowledge_usage(start=start, end=end, limit=limit),
            _load_external_platform_click_summary(),
        )
    else:
        weknora_usage = empty_weknora_dashboard_analytics()
        external_platform_clicks = await _load_external_platform_click_summary()
    scheduled_task_usage = _summarize_scheduled_task_usage(
        scheduled_task_rows,
        scheduled_task_run_rows,
        scheduled_creation_audits,
        users_by_id,
        start=start,
        end=end,
        limit=limit,
    )

    all_feedback_rows = feedback_rows

    normalized_status = status if status and status != "all" else None
    normalized_model = model if model and model != "all" else None
    normalized_agent = agent if agent and agent != "all" else None
    normalized_feature = feature if feature and feature != "all" else None

    def _matches_filters(run: _RunRef) -> bool:
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
    feedback_rows = [row for row in all_feedback_rows if row.run_id in run_ids]
    run_features = defaultdict(set, {run_id: features for run_id, features in run_features.items() if run_id in run_ids})
    business_events = [event for run_id, event in latest_business_event_by_run.items() if run_id in run_ids]
    artifact_events = [(event, path) for run_id in run_ids for (_, path), event in artifact_events_by_run.get(run_id, {}).items()]

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
            features=sorted(event.features),
            snippet=event.snippet,
        )
        for event in business_events[: min(limit, 12)]
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

    external_agent_clicks = [ExternalAgentClickItem(name=name, clicks=bucket["clicks"], users=len(bucket["users"])) for name, bucket in sorted(click_buckets.items(), key=lambda item: item[1]["clicks"], reverse=True)]
    external_platform_agent_clicks = external_platform_clicks

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
            display_name=(users_by_id.get(run.user_id).display_name if run.user_id and run.user_id in users_by_id else None),
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


async def _build_compact_dashboard_summary(
    *,
    start: datetime,
    end: datetime,
    window_hours: int,
    limit: int,
    value_profile: str,
) -> DashboardSummary:
    """Build the normal dashboard surface from bounded, mergeable facts."""

    selection = await load_hybrid_contribution(
        _session_factory(),
        start=start,
        end=end,
        minimum_rollup_days=_dashboard_rollup_min_days(),
        minimum_hourly_rollup_hours=_dashboard_hourly_rollup_min_hours(),
    )
    contribution: DashboardContribution = selection.contribution

    total_runs = 0
    successful_runs = 0
    failed_runs = 0
    running_runs = 0
    terminal_runs = 0
    total_tokens = 0
    llm_calls = 0
    duration_total_ms = 0
    duration_count = 0
    active_users: set[str] = set()

    user_buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"runs": 0, "terminal_runs": 0, "successes": 0, "threads": 0, "tokens": 0})
    agent_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "name": "默认智能体",
            "runs": 0,
            "terminal_runs": 0,
            "successes": 0,
            "users": set(),
            "duration_total_ms": 0,
            "duration_count": 0,
        }
    )
    model_buckets: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "runs": 0,
            "terminal_runs": 0,
            "successes": 0,
            "users": set(),
            "duration_total_ms": 0,
            "duration_count": 0,
            "total_tokens": 0,
            "llm_calls": 0,
        }
    )
    activity_bucket_days = _activity_bucket_days(start, end)
    activity_buckets: dict[str, dict[str, Any]] = {period_start: {"end_date": period_end, "users": set(), "threads": 0, "runs": 0} for period_start, period_end in _iter_activity_periods(start, end, activity_bucket_days)}

    for (day, user_id, assistant_id, model_name, run_status), metric in contribution.dimensions.items():
        total_runs += metric.runs
        successful_runs += metric.runs if run_status == "success" else 0
        failed_runs += metric.runs if run_status in _FAILED_STATUSES else 0
        terminal_runs += metric.runs if run_status in _TERMINAL_STATUSES else 0
        running_runs += metric.runs if run_status not in _TERMINAL_STATUSES else 0
        total_tokens += metric.total_tokens
        llm_calls += metric.llm_calls
        duration_total_ms += metric.duration_total_ms
        duration_count += metric.duration_count
        local_day = datetime.combine(day, datetime.min.time(), tzinfo=_BUSINESS_TIMEZONE)
        activity_key = _activity_bucket_key(local_day, start, activity_bucket_days)
        activity = activity_buckets.setdefault(
            activity_key,
            {"end_date": activity_key, "users": set(), "threads": 0, "runs": 0},
        )
        activity["runs"] += metric.runs
        if user_id:
            active_users.add(user_id)
            activity["users"].add(user_id)
            user = user_buckets[user_id]
            user["runs"] += metric.runs
            user["terminal_runs"] += metric.runs if run_status in _TERMINAL_STATUSES else 0
            user["successes"] += metric.runs if run_status == "success" else 0
            user["tokens"] += metric.total_tokens

        agent = agent_buckets[assistant_id]
        agent["name"] = metric.agent_name or agent["name"]
        agent["runs"] += metric.runs
        agent["terminal_runs"] += metric.runs if run_status in _TERMINAL_STATUSES else 0
        agent["successes"] += metric.runs if run_status == "success" else 0
        agent["duration_total_ms"] += metric.duration_total_ms
        agent["duration_count"] += metric.duration_count
        if user_id:
            agent["users"].add(user_id)

        model_bucket = model_buckets[model_name]
        model_bucket["runs"] += metric.runs
        model_bucket["terminal_runs"] += metric.runs if run_status in _TERMINAL_STATUSES else 0
        model_bucket["successes"] += metric.runs if run_status == "success" else 0
        model_bucket["duration_total_ms"] += metric.duration_total_ms
        model_bucket["duration_count"] += metric.duration_count
        model_bucket["total_tokens"] += metric.total_tokens
        model_bucket["llm_calls"] += metric.llm_calls
        if user_id:
            model_bucket["users"].add(user_id)

    for day, count in contribution.thread_creations.items():
        local_day = datetime.combine(day, datetime.min.time(), tzinfo=_BUSINESS_TIMEZONE)
        activity_key = _activity_bucket_key(local_day, start, activity_bucket_days)
        activity = activity_buckets.setdefault(
            activity_key,
            {"end_date": activity_key, "users": set(), "threads": 0, "runs": 0},
        )
        activity["threads"] += count
        activity["users"].update(contribution.thread_creation_users.get(day, set()))
        active_users.update(contribution.thread_creation_users.get(day, set()))
    for (_, user_id), count in contribution.thread_creations_by_user.items():
        user_buckets[user_id]["threads"] += count

    top_user_items = sorted(
        user_buckets.items(),
        key=lambda item: (item[1]["runs"], item[1]["tokens"]),
        reverse=True,
    )[:limit]
    supporting = await _load_compact_supporting_metrics(
        start=start,
        end=end,
        limit=limit,
        profile_user_ids={user_id for user_id, _ in top_user_items},
    )
    external_platform_clicks = await _load_external_platform_click_summary()

    user_usage = [
        UserUsageItem(
            user_id=user_id,
            email=(supporting.users_by_id[user_id].email if user_id in supporting.users_by_id else None),
            display_name=(supporting.users_by_id[user_id].display_name if user_id in supporting.users_by_id else None),
            organization_name=(supporting.users_by_id[user_id].organization_name if user_id in supporting.users_by_id else None),
            runs=bucket["runs"],
            threads=bucket["threads"],
            total_tokens=bucket["tokens"],
            success_rate=_success_rate(bucket["successes"], bucket["terminal_runs"]),
        )
        for user_id, bucket in top_user_items
    ]
    agent_usage = [
        AgentUsageItem(
            assistant_id=None if assistant_id == "default" else assistant_id,
            name=str(bucket["name"]),
            runs=int(bucket["runs"]),
            users=len(bucket["users"]),
            success_rate=_success_rate(int(bucket["successes"]), int(bucket["terminal_runs"])),
            avg_duration_ms=(round(int(bucket["duration_total_ms"]) / int(bucket["duration_count"])) if bucket["duration_count"] else 0),
        )
        for assistant_id, bucket in sorted(agent_buckets.items(), key=lambda item: item[1]["runs"], reverse=True)[:limit]
    ]
    model_usage = [
        ModelUsageItem(
            model_name=model_name,
            runs=int(bucket["runs"]),
            users=len(bucket["users"]),
            success_rate=_success_rate(int(bucket["successes"]), int(bucket["terminal_runs"])),
            avg_duration_ms=(round(int(bucket["duration_total_ms"]) / int(bucket["duration_count"])) if bucket["duration_count"] else 0),
            total_tokens=int(bucket["total_tokens"]),
            llm_calls=int(bucket["llm_calls"]),
        )
        for model_name, bucket in sorted(
            model_buckets.items(),
            key=lambda item: (item[1]["runs"], item[1]["total_tokens"]),
            reverse=True,
        )[:limit]
    ]
    user_activity = [
        UserActivityPoint(
            date=key,
            end_date=str(bucket["end_date"]),
            active_users=len(bucket["users"]),
            threads=int(bucket["threads"]),
            runs=int(bucket["runs"]),
        )
        for key, bucket in sorted(activity_buckets.items())
    ]
    external_agent_clicks = [ExternalAgentClickItem(name=name, clicks=metric.clicks, users=len(metric.user_ids)) for name, metric in sorted(contribution.external_clicks.items(), key=lambda item: item[1].clicks, reverse=True)]
    feedback_total = contribution.feedback_positive + contribution.feedback_negative
    feedback_summary = FeedbackSummary(
        total=feedback_total,
        positive=contribution.feedback_positive,
        negative=contribution.feedback_negative,
        satisfaction_rate=_success_rate(contribution.feedback_positive, feedback_total),
        negative_rate=_success_rate(contribution.feedback_negative, feedback_total),
        recent_comments=[],
        recent_negative=[],
        by_feature=[],
    )
    resolved_value_profile, value_contributions, _ = _business_value_contributions(Counter(), profile=value_profile)
    empty_artifacts = ArtifactSummary(total=0, by_type=[], previewable=0, recent=[])
    data_source = f"{selection.source}+raw" if selection.source in {"rollup", "hourly_rollup"} and selection.raw_segments else selection.source
    return DashboardSummary(
        window=DashboardWindow(start_at=start, end_at=end, hours=window_hours),
        overview=DashboardOverview(
            total_users=supporting.total_users,
            new_users=supporting.new_users,
            active_users=len(active_users),
            threads=sum(contribution.thread_creations.values()),
            all_time_threads=supporting.all_time_threads,
            existing_threads=supporting.existing_threads,
            runs=total_runs,
            terminal_runs=terminal_runs,
            successful_runs=successful_runs,
            failed_runs=failed_runs,
            running_runs=running_runs,
            success_rate=_success_rate(successful_runs, terminal_runs),
            avg_run_duration_ms=(round(duration_total_ms / duration_count) if duration_count else 0),
            total_tokens=total_tokens,
            llm_calls=llm_calls,
            artifact_runs=0,
            knowledge_runs=0,
            feedback_total=feedback_total,
            satisfaction_rate=feedback_summary.satisfaction_rate,
            estimated_minutes_saved=0,
        ),
        activity_bucket=_activity_bucket_name(activity_bucket_days),
        user_activity=user_activity,
        user_usage=user_usage,
        external_agent_clicks=external_agent_clicks,
        external_platform_agent_clicks=external_platform_clicks,
        feature_usage=[],
        recent_business_events=[],
        agent_usage=agent_usage,
        model_usage=model_usage,
        business_value=BusinessValueSummary(
            artifact_runs=0,
            knowledge_runs=0,
            file_processing_runs=0,
            presentation_runs=0,
            image_runs=0,
            estimated_minutes_saved=0,
            profile=resolved_value_profile,
            contributions=value_contributions,
        ),
        artifact_summary=empty_artifacts,
        feedback=feedback_summary,
        knowledge_usage=KnowledgeUsageSummary(**empty_weknora_dashboard_analytics()),
        scheduled_task_usage=supporting.scheduled_task_usage,
        recent_failures=[],
        aggregation=DashboardAggregationInfo(
            source=data_source,
            schema_version=ROLLUP_SCHEMA_VERSION,
            rollup_days=selection.rollup_days,
            rollup_hours=selection.rollup_hours,
            raw_segments=len(selection.raw_segments),
        ),
    )
