"""Low-impact, versioned daily and hourly dashboard facts.

The Gateway never builds rollups in a request or at startup.  An operator-run
maintenance command refreshes completed Asia/Shanghai calendar days and clock
hours using a single database connection. Dashboard requests consume only
complete rows and fail fast for an uncovered required range instead of scanning
millions of source records and competing with normal DeerFlow traffic.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.gateway.external_agent_analytics import (
    EXTERNAL_PLATFORM_AGENT_NAMES,
    EXTERNAL_PLATFORM_CLICK_ACTION,
    EXTERNAL_PLATFORM_SOURCES,
    normalize_external_agent_name,
    normalize_external_agent_source,
)
from deerflow.persistence.dashboard_rollup.model import (
    DashboardDailyRollupRow,
    DashboardHourlyRollupRow,
    DashboardRollupStateRow,
)
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.observability.model import AuditLogRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow

ROLLUP_SCHEMA_VERSION = 1
ROLLUP_STATE_KEY = "daily-dashboard-v1"
HOURLY_ROLLUP_STATE_KEY = "hourly-dashboard-v1"
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
FAILED_STATUSES = frozenset({"error", "timeout", "interrupted"})
TERMINAL_STATUSES = frozenset({"success", *FAILED_STATUSES})
EXTERNAL_AGENT_CLICK_ACTION = "external_agent.click"
RUN_RANKING_CANDIDATE_LIMIT = 50

_refresh_lock = asyncio.Lock()


def _bounded_env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


_query_semaphore = asyncio.Semaphore(_bounded_env_int("DEER_FLOW_DASHBOARD_MAX_CONCURRENT_QUERIES", 1, minimum=1, maximum=4))


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def business_day(value: datetime) -> date:
    return _aware_utc(value).astimezone(BUSINESS_TIMEZONE).date()


def business_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=BUSINESS_TIMEZONE).astimezone(UTC)
    return start, start + timedelta(days=1)


def business_hour_floor(value: datetime) -> datetime:
    """Return the UTC timestamp for the containing Shanghai clock hour."""

    local = _aware_utc(value).astimezone(BUSINESS_TIMEZONE)
    return local.replace(minute=0, second=0, microsecond=0).astimezone(UTC)


def business_hour_bounds(bucket_start: datetime) -> tuple[datetime, datetime]:
    start = business_hour_floor(bucket_start)
    return start, start + timedelta(hours=1)


def _pending_days(values: list[Any] | None) -> set[date]:
    parsed: set[date] = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        try:
            parsed.add(date.fromisoformat(value))
        except ValueError:
            continue
    return parsed


def _pending_hours(values: list[Any] | None) -> set[datetime]:
    parsed: set[datetime] = set()
    for value in values or []:
        if not isinstance(value, str):
            continue
        try:
            parsed.add(business_hour_floor(datetime.fromisoformat(value)))
        except ValueError:
            continue
    return parsed


def fully_covered_business_days(start: datetime, end: datetime) -> list[date]:
    """Return business days whose complete half-open interval is in the range."""

    start = _aware_utc(start)
    end = _aware_utc(end)
    if start >= end:
        return []
    first = business_day(start)
    last = business_day(end - timedelta(microseconds=1))
    days: list[date] = []
    current = first
    while current <= last:
        day_start, day_end = business_day_bounds(current)
        if day_start >= start and day_end <= end:
            days.append(current)
        current += timedelta(days=1)
    return days


def fully_covered_business_hours(start: datetime, end: datetime) -> list[datetime]:
    """Return hour buckets whose complete half-open interval is in the range."""

    start = _aware_utc(start)
    end = _aware_utc(end)
    if start >= end:
        return []
    current = business_hour_floor(start)
    if current < start:
        current += timedelta(hours=1)
    hours: list[datetime] = []
    while current + timedelta(hours=1) <= end:
        hours.append(current)
        current += timedelta(hours=1)
    return hours


def hourly_rollup_retention_hours() -> int:
    return _bounded_env_int(
        "DEER_FLOW_DASHBOARD_HOURLY_RETENTION_HOURS",
        72,
        minimum=24,
        maximum=336,
    )


def _require_hourly_rollups() -> bool:
    return os.getenv("DEER_FLOW_DASHBOARD_REQUIRE_HOURLY_ROLLUPS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def dashboard_rollups_enabled() -> bool:
    """Return whether dashboard reads may require prepared rollup facts.

    Existing installations start in compatibility mode so applying the additive
    migrations cannot make historical ranges unavailable before operators have
    backfilled and scheduled the rollups.  Production enables this only after
    the readiness endpoint reports no gaps for the supported windows.
    """

    return os.getenv("DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _metadata_name(display_name: str | None, metadata: dict[str, Any] | None) -> str | None:
    for key in ("agent_name", "assistant_name", "name"):
        value = (metadata or {}).get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return display_name.strip() if isinstance(display_name, str) and display_name.strip() else None


@dataclass
class RunDimensionMetric:
    runs: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    duration_total_ms: int = 0
    duration_count: int = 0
    agent_name: str = "默认智能体"


@dataclass
class ExternalClickMetric:
    clicks: int = 0
    user_ids: set[str] = field(default_factory=set)


@dataclass
class ExternalPlatformMetric:
    clicks: int = 0
    user_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class RunRankingFact:
    run_id: str
    thread_id: str
    model_name: str | None
    assistant_id: str | None
    created_at: datetime
    duration_ms: int
    total_tokens: int


def _retain_top_candidate(
    candidates: list[RunRankingFact],
    incoming: RunRankingFact,
    *,
    key,
) -> None:  # type: ignore[no-untyped-def]
    candidates.append(incoming)
    candidates.sort(key=key, reverse=True)
    del candidates[RUN_RANKING_CANDIDATE_LIMIT:]


DimensionKey = tuple[date, str | None, str, str, str]


@dataclass
class DashboardContribution:
    """Mergeable exact contribution from raw rows or completed daily facts."""

    dimensions: dict[DimensionKey, RunDimensionMetric] = field(default_factory=dict)
    thread_creations: dict[date, int] = field(default_factory=lambda: defaultdict(int))
    thread_creation_users: dict[date, set[str]] = field(default_factory=lambda: defaultdict(set))
    thread_creations_by_user: dict[tuple[date, str], int] = field(default_factory=lambda: defaultdict(int))
    feedback_positive: int = 0
    feedback_negative: int = 0
    external_clicks: dict[str, ExternalClickMetric] = field(default_factory=dict)
    external_platform_clicks: dict[tuple[str, str], ExternalPlatformMetric] = field(default_factory=dict)
    slow_run_candidates: list[RunRankingFact] = field(default_factory=list)
    high_token_candidates: list[RunRankingFact] = field(default_factory=list)
    source_run_count: int = 0
    source_thread_count: int = 0
    source_feedback_count: int = 0
    source_external_click_count: int = 0

    def add_run(
        self,
        *,
        created_at: datetime,
        updated_at: datetime | None,
        run_id: str,
        user_id: str | None,
        thread_id: str,
        assistant_id: str | None,
        meta_assistant_id: str | None,
        meta_display_name: str | None,
        meta_json: dict[str, Any] | None,
        model_name: str | None,
        status: str,
        total_tokens: int,
        llm_calls: int,
    ) -> None:
        day = business_day(created_at)
        resolved_assistant = assistant_id or meta_assistant_id or "default"
        agent_name = _metadata_name(meta_display_name, meta_json) or ("默认智能体" if resolved_assistant == "default" else resolved_assistant)
        key: DimensionKey = (
            day,
            user_id,
            resolved_assistant,
            model_name or "未记录模型",
            status,
        )
        metric = self.dimensions.setdefault(key, RunDimensionMetric(agent_name=agent_name))
        metric.runs += 1
        metric.total_tokens += max(0, int(total_tokens or 0))
        metric.llm_calls += max(0, int(llm_calls or 0))
        if status in TERMINAL_STATUSES and updated_at is not None:
            duration_ms = max(0, int((_aware_utc(updated_at) - _aware_utc(created_at)).total_seconds() * 1000))
            metric.duration_total_ms += duration_ms
            metric.duration_count += 1
            candidate = RunRankingFact(
                run_id=run_id,
                thread_id=thread_id,
                model_name=model_name,
                assistant_id=resolved_assistant,
                created_at=_aware_utc(created_at),
                duration_ms=duration_ms,
                total_tokens=max(0, int(total_tokens or 0)),
            )
            _retain_top_candidate(
                self.slow_run_candidates,
                candidate,
                key=lambda item: (item.duration_ms, item.created_at),
            )
            _retain_top_candidate(
                self.high_token_candidates,
                candidate,
                key=lambda item: (item.total_tokens, item.created_at),
            )
        self.source_run_count += 1

    def add_thread_creation(self, *, created_at: datetime, user_id: str | None) -> None:
        day = business_day(created_at)
        self.thread_creations[day] += 1
        if user_id:
            self.thread_creation_users[day].add(user_id)
            self.thread_creations_by_user[(day, user_id)] += 1
        self.source_thread_count += 1

    def add_feedback(self, rating: int) -> None:
        if rating > 0:
            self.feedback_positive += 1
        elif rating < 0:
            self.feedback_negative += 1
        self.source_feedback_count += 1

    def add_external_click(self, *, name: str | None, user_id: str | None) -> None:
        if not name:
            return
        metric = self.external_clicks.setdefault(name, ExternalClickMetric())
        metric.clicks += 1
        if user_id:
            metric.user_ids.add(user_id)
        self.source_external_click_count += 1

    def add_external_platform_click(
        self,
        *,
        name: str | None,
        source: str | None,
        identity: str | None,
    ) -> None:
        agent_name = normalize_external_agent_name(name or "")
        source_name = normalize_external_agent_source(source or "")
        if agent_name is None or source_name is None:
            return
        metric = self.external_platform_clicks.setdefault((agent_name, source_name), ExternalPlatformMetric())
        metric.clicks += 1
        if identity:
            metric.user_ids.add(hashlib.sha256(identity.encode("utf-8")).hexdigest())
        self.source_external_click_count += 1

    def merge(self, other: DashboardContribution) -> None:
        for key, incoming in other.dimensions.items():
            metric = self.dimensions.setdefault(key, RunDimensionMetric(agent_name=incoming.agent_name))
            metric.runs += incoming.runs
            metric.total_tokens += incoming.total_tokens
            metric.llm_calls += incoming.llm_calls
            metric.duration_total_ms += incoming.duration_total_ms
            metric.duration_count += incoming.duration_count
        for day, count in other.thread_creations.items():
            self.thread_creations[day] += count
        for day, users in other.thread_creation_users.items():
            self.thread_creation_users[day].update(users)
        for key, count in other.thread_creations_by_user.items():
            self.thread_creations_by_user[key] += count
        self.feedback_positive += other.feedback_positive
        self.feedback_negative += other.feedback_negative
        for name, incoming in other.external_clicks.items():
            metric = self.external_clicks.setdefault(name, ExternalClickMetric())
            metric.clicks += incoming.clicks
            metric.user_ids.update(incoming.user_ids)
        for key, incoming in other.external_platform_clicks.items():
            metric = self.external_platform_clicks.setdefault(key, ExternalPlatformMetric())
            metric.clicks += incoming.clicks
            metric.user_ids.update(incoming.user_ids)
        for candidate in other.slow_run_candidates:
            _retain_top_candidate(
                self.slow_run_candidates,
                candidate,
                key=lambda item: (item.duration_ms, item.created_at),
            )
        for candidate in other.high_token_candidates:
            _retain_top_candidate(
                self.high_token_candidates,
                candidate,
                key=lambda item: (item.total_tokens, item.created_at),
            )
        self.source_run_count += other.source_run_count
        self.source_thread_count += other.source_thread_count
        self.source_feedback_count += other.source_feedback_count
        self.source_external_click_count += other.source_external_click_count

    def to_daily_payload(self, day: date) -> dict[str, Any]:
        dimensions = []
        for (metric_day, user_id, assistant_id, model_name, status), metric in sorted(self.dimensions.items(), key=lambda item: tuple(str(value or "") for value in item[0])):
            if metric_day != day:
                continue
            dimensions.append(
                {
                    "user_id": user_id,
                    "assistant_id": assistant_id,
                    "agent_name": metric.agent_name,
                    "model_name": model_name,
                    "status": status,
                    "runs": metric.runs,
                    "total_tokens": metric.total_tokens,
                    "llm_calls": metric.llm_calls,
                    "duration_total_ms": metric.duration_total_ms,
                    "duration_count": metric.duration_count,
                }
            )
        return {
            "version": ROLLUP_SCHEMA_VERSION,
            "day": day.isoformat(),
            "dimensions": dimensions,
            "thread_creations": self.thread_creations.get(day, 0),
            "thread_creation_users": sorted(self.thread_creation_users.get(day, set())),
            "thread_creations_by_user": [{"user_id": user_id, "threads": count} for (metric_day, user_id), count in sorted(self.thread_creations_by_user.items()) if metric_day == day],
            "feedback_positive": self.feedback_positive,
            "feedback_negative": self.feedback_negative,
            "external_clicks": [
                {
                    "name": name,
                    "clicks": metric.clicks,
                    "user_ids": sorted(metric.user_ids),
                }
                for name, metric in sorted(self.external_clicks.items())
            ],
            "external_platform_clicks": [
                {
                    "agent_name": agent_name,
                    "source": source,
                    "clicks": metric.clicks,
                    "user_ids": sorted(metric.user_ids),
                }
                for (agent_name, source), metric in sorted(self.external_platform_clicks.items())
            ],
            "slow_run_candidates": [
                {
                    "run_id": item.run_id,
                    "thread_id": item.thread_id,
                    "model_name": item.model_name,
                    "assistant_id": item.assistant_id,
                    "created_at": item.created_at.isoformat(),
                    "duration_ms": item.duration_ms,
                    "total_tokens": item.total_tokens,
                }
                for item in self.slow_run_candidates
                if business_day(item.created_at) == day
            ],
            "high_token_candidates": [
                {
                    "run_id": item.run_id,
                    "thread_id": item.thread_id,
                    "model_name": item.model_name,
                    "assistant_id": item.assistant_id,
                    "created_at": item.created_at.isoformat(),
                    "duration_ms": item.duration_ms,
                    "total_tokens": item.total_tokens,
                }
                for item in self.high_token_candidates
                if business_day(item.created_at) == day
            ],
        }

    @classmethod
    def from_daily_payload(cls, day: date, payload: dict[str, Any]) -> DashboardContribution:
        if int(payload.get("version") or 0) != ROLLUP_SCHEMA_VERSION:
            raise ValueError("dashboard rollup schema version mismatch")
        contribution = cls()
        for item in payload.get("dimensions") or []:
            if not isinstance(item, dict):
                continue
            key: DimensionKey = (
                day,
                str(item["user_id"]) if item.get("user_id") else None,
                str(item.get("assistant_id") or "default"),
                str(item.get("model_name") or "未记录模型"),
                str(item.get("status") or "unknown"),
            )
            contribution.dimensions[key] = RunDimensionMetric(
                runs=max(0, int(item.get("runs") or 0)),
                total_tokens=max(0, int(item.get("total_tokens") or 0)),
                llm_calls=max(0, int(item.get("llm_calls") or 0)),
                duration_total_ms=max(0, int(item.get("duration_total_ms") or 0)),
                duration_count=max(0, int(item.get("duration_count") or 0)),
                agent_name=str(item.get("agent_name") or "默认智能体"),
            )
        contribution.thread_creations[day] = max(0, int(payload.get("thread_creations") or 0))
        contribution.thread_creation_users[day].update(str(value) for value in payload.get("thread_creation_users") or [] if value)
        for item in payload.get("thread_creations_by_user") or []:
            if isinstance(item, dict) and item.get("user_id"):
                contribution.thread_creations_by_user[(day, str(item["user_id"]))] += max(0, int(item.get("threads") or 0))
        contribution.feedback_positive = max(0, int(payload.get("feedback_positive") or 0))
        contribution.feedback_negative = max(0, int(payload.get("feedback_negative") or 0))
        for item in payload.get("external_clicks") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            contribution.external_clicks[str(item["name"])] = ExternalClickMetric(
                clicks=max(0, int(item.get("clicks") or 0)),
                user_ids={str(value) for value in item.get("user_ids") or [] if value},
            )
        for item in payload.get("external_platform_clicks") or []:
            if not isinstance(item, dict):
                continue
            agent_name = normalize_external_agent_name(str(item.get("agent_name") or ""))
            source = normalize_external_agent_source(str(item.get("source") or ""))
            if agent_name is None or source is None:
                continue
            contribution.external_platform_clicks[(agent_name, source)] = ExternalPlatformMetric(
                clicks=max(0, int(item.get("clicks") or 0)),
                user_ids={str(value) for value in item.get("user_ids") or [] if value},
            )
        for payload_key, target, key in (
            (
                "slow_run_candidates",
                contribution.slow_run_candidates,
                lambda item: (item.duration_ms, item.created_at),
            ),
            (
                "high_token_candidates",
                contribution.high_token_candidates,
                lambda item: (item.total_tokens, item.created_at),
            ),
        ):
            for item in payload.get(payload_key) or []:
                if not isinstance(item, dict) or not item.get("run_id") or not item.get("thread_id"):
                    continue
                try:
                    created_at = _aware_utc(datetime.fromisoformat(str(item.get("created_at"))))
                except (TypeError, ValueError):
                    continue
                _retain_top_candidate(
                    target,
                    RunRankingFact(
                        run_id=str(item["run_id"]),
                        thread_id=str(item["thread_id"]),
                        model_name=str(item["model_name"]) if item.get("model_name") else None,
                        assistant_id=str(item["assistant_id"]) if item.get("assistant_id") else None,
                        created_at=created_at,
                        duration_ms=max(0, int(item.get("duration_ms") or 0)),
                        total_tokens=max(0, int(item.get("total_tokens") or 0)),
                    ),
                    key=key,
                )
        contribution.source_run_count = sum(metric.runs for metric in contribution.dimensions.values())
        contribution.source_thread_count = contribution.thread_creations[day]
        contribution.source_feedback_count = contribution.feedback_positive + contribution.feedback_negative
        contribution.source_external_click_count = sum(metric.clicks for metric in contribution.external_clicks.values()) + sum(metric.clicks for metric in contribution.external_platform_clicks.values())
        return contribution


async def apply_dashboard_statement_limits(session: AsyncSession, *, maintenance: bool = False) -> None:
    """Bound PostgreSQL dashboard work so it cannot monopolize normal traffic."""

    bind = session.get_bind()
    if bind.dialect.name != "postgresql":
        return
    env_name = "DEER_FLOW_DASHBOARD_ROLLUP_STATEMENT_TIMEOUT_MS" if maintenance else "DEER_FLOW_DASHBOARD_STATEMENT_TIMEOUT_MS"
    default = 30_000 if maintenance else 15_000
    try:
        timeout_ms = int(os.getenv(env_name, str(default)))
    except ValueError:
        timeout_ms = default
    timeout_ms = max(1_000, min(timeout_ms, 120_000 if maintenance else 60_000))
    await session.execute(
        text("SELECT set_config('statement_timeout', :timeout, true)"),
        {"timeout": f"{timeout_ms}ms"},
    )
    await session.execute(text("SELECT set_config('lock_timeout', '1000ms', true)"))


async def _source_replay_bound(session: AsyncSession, requested: datetime) -> datetime:
    """Do not advance change cursors beyond a standby's replayed commit time."""

    requested = _aware_utc(requested)
    if session.get_bind().dialect.name != "postgresql":
        return requested
    if not bool(await session.scalar(text("SELECT pg_is_in_recovery()"))):
        return requested
    replayed_at = await session.scalar(text("SELECT pg_last_xact_replay_timestamp()"))
    if not isinstance(replayed_at, datetime):
        return requested
    return min(requested, _aware_utc(replayed_at))


class DashboardQueryBusy(RuntimeError):
    """Raised before DB work when another expensive dashboard query is active."""


@asynccontextmanager
async def dashboard_query_slot():
    """Admit only a tiny number of cache-miss queries, with no long queue."""

    try:
        wait_ms = int(os.getenv("DEER_FLOW_DASHBOARD_QUERY_QUEUE_TIMEOUT_MS", "250"))
    except ValueError:
        wait_ms = 250
    try:
        await asyncio.wait_for(_query_semaphore.acquire(), timeout=max(0.05, min(wait_ms / 1000, 2.0)))
    except TimeoutError as exc:
        raise DashboardQueryBusy("dashboard query capacity is busy") from exc
    try:
        yield
    finally:
        _query_semaphore.release()


async def build_raw_contribution(session: AsyncSession, *, start: datetime, end: datetime) -> DashboardContribution:
    """Stream a bounded time slice and retain only mergeable metrics."""

    start = _aware_utc(start)
    end = _aware_utc(end)
    contribution = DashboardContribution()
    run_stream = await session.stream(
        select(
            RunRow.run_id,
            RunRow.thread_id,
            RunRow.user_id,
            RunRow.assistant_id,
            RunRow.model_name,
            RunRow.status,
            RunRow.total_tokens,
            RunRow.llm_call_count,
            RunRow.created_at,
            RunRow.updated_at,
            ThreadMetaRow.assistant_id.label("meta_assistant_id"),
            ThreadMetaRow.display_name.label("meta_display_name"),
            ThreadMetaRow.metadata_json.label("meta_json"),
        )
        .outerjoin(ThreadMetaRow, ThreadMetaRow.thread_id == RunRow.thread_id)
        .where(RunRow.created_at >= start, RunRow.created_at < end)
        .execution_options(yield_per=1000)
    )
    async for row in run_stream:
        contribution.add_run(
            created_at=row.created_at,
            updated_at=row.updated_at,
            run_id=row.run_id,
            user_id=row.user_id,
            thread_id=row.thread_id,
            assistant_id=row.assistant_id,
            meta_assistant_id=row.meta_assistant_id,
            meta_display_name=row.meta_display_name,
            meta_json=row.meta_json,
            model_name=row.model_name,
            status=row.status,
            total_tokens=int(row.total_tokens or 0),
            llm_calls=int(row.llm_call_count or 0),
        )

    thread_stream = await session.stream(select(ThreadCreationRow.user_id, ThreadCreationRow.created_at).where(ThreadCreationRow.created_at >= start, ThreadCreationRow.created_at < end).execution_options(yield_per=1000))
    async for row in thread_stream:
        contribution.add_thread_creation(created_at=row.created_at, user_id=row.user_id)

    feedback_stream = await session.stream(select(FeedbackRow.rating).where(FeedbackRow.created_at >= start, FeedbackRow.created_at < end).execution_options(yield_per=1000))
    async for rating in feedback_stream.scalars():
        contribution.add_feedback(int(rating or 0))

    click_stream = await session.stream(
        select(
            AuditLogRow.action,
            AuditLogRow.resource_id,
            AuditLogRow.user_id,
            AuditLogRow.user_email,
            AuditLogRow.details_json,
        )
        .where(
            AuditLogRow.action.in_((EXTERNAL_AGENT_CLICK_ACTION, EXTERNAL_PLATFORM_CLICK_ACTION)),
            AuditLogRow.created_at >= start,
            AuditLogRow.created_at < end,
        )
        .execution_options(yield_per=1000)
    )
    async for row in click_stream:
        if row.action == EXTERNAL_PLATFORM_CLICK_ACTION:
            details = row.details_json or {}
            contribution.add_external_platform_click(
                name=row.resource_id,
                source=str(details.get("source") or ""),
                identity=str(row.user_id or row.user_email or details.get("user_name") or "") or None,
            )
        else:
            contribution.add_external_click(name=row.resource_id, user_id=row.user_id)
    return contribution


async def build_external_platform_raw_contribution(
    session: AsyncSession,
    *,
    start: datetime,
    end: datetime,
) -> DashboardContribution:
    """Read only bounded remote-click audit columns for the fixed click panel."""

    contribution = DashboardContribution()
    click_stream = await session.stream(
        select(
            AuditLogRow.resource_id,
            AuditLogRow.user_id,
            AuditLogRow.user_email,
            AuditLogRow.details_json,
        )
        .where(
            AuditLogRow.action == EXTERNAL_PLATFORM_CLICK_ACTION,
            AuditLogRow.created_at >= _aware_utc(start),
            AuditLogRow.created_at < _aware_utc(end),
        )
        .execution_options(yield_per=1000)
    )
    async for row in click_stream:
        details = row.details_json or {}
        contribution.add_external_platform_click(
            name=row.resource_id,
            source=str(details.get("source") or ""),
            identity=str(row.user_id or row.user_email or details.get("user_name") or "") or None,
        )
    return contribution


@dataclass(frozen=True)
class RollupRefreshResult:
    day: date
    status: str
    duration_ms: int
    source_run_count: int = 0


async def _upsert_rollup(session: AsyncSession, values: dict[str, Any]) -> None:
    dialect = session.get_bind().dialect.name
    update_values = {key: value for key, value in values.items() if key != "day"}
    if dialect == "postgresql":
        statement = postgresql_insert(DashboardDailyRollupRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["day"], set_=update_values))
    elif dialect == "sqlite":
        statement = sqlite_insert(DashboardDailyRollupRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["day"], set_=update_values))
    else:
        await session.merge(DashboardDailyRollupRow(**values))


async def _upsert_hourly_rollup(session: AsyncSession, values: dict[str, Any]) -> None:
    dialect = session.get_bind().dialect.name
    update_values = {key: value for key, value in values.items() if key != "bucket_start"}
    if dialect == "postgresql":
        statement = postgresql_insert(DashboardHourlyRollupRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["bucket_start"], set_=update_values))
    elif dialect == "sqlite":
        statement = sqlite_insert(DashboardHourlyRollupRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["bucket_start"], set_=update_values))
    else:
        await session.merge(DashboardHourlyRollupRow(**values))


async def refresh_rollup_day(
    session_factory: async_sessionmaker[AsyncSession],
    day: date,
    *,
    source_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> RollupRefreshResult:
    """Rebuild one completed day atomically, skipping an already-running peer."""

    if day >= datetime.now(BUSINESS_TIMEZONE).date():
        return RollupRefreshResult(day=day, status="open_day", duration_ms=0)
    started = perf_counter()
    source_factory = source_session_factory or session_factory
    async with _refresh_lock:
        start, end = business_day_bounds(day)
        async with source_factory() as source_session:
            await apply_dashboard_statement_limits(source_session, maintenance=True)
            contribution = await build_raw_contribution(source_session, start=start, end=end)
        async with session_factory() as session:
            await apply_dashboard_statement_limits(session, maintenance=True)
            if session.get_bind().dialect.name == "postgresql":
                acquired = await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"deerflow-dashboard-rollup:{day.isoformat()}"},
                )
                if not acquired:
                    await session.rollback()
                    return RollupRefreshResult(day=day, status="busy", duration_ms=0)
            duration_ms = int((perf_counter() - started) * 1000)
            now = datetime.now(UTC)
            await _upsert_rollup(
                session,
                {
                    "day": day,
                    "schema_version": ROLLUP_SCHEMA_VERSION,
                    "payload_json": contribution.to_daily_payload(day),
                    "source_run_count": contribution.source_run_count,
                    "source_thread_count": contribution.source_thread_count,
                    "source_feedback_count": contribution.source_feedback_count,
                    "source_external_click_count": contribution.source_external_click_count,
                    "build_duration_ms": duration_ms,
                    "refreshed_at": now,
                },
            )
            await session.commit()
            return RollupRefreshResult(
                day=day,
                status="refreshed",
                duration_ms=duration_ms,
                source_run_count=contribution.source_run_count,
            )


@dataclass(frozen=True)
class HourlyRollupRefreshResult:
    bucket_start: datetime
    status: str
    duration_ms: int
    source_run_count: int = 0


async def refresh_rollup_hour(
    session_factory: async_sessionmaker[AsyncSession],
    bucket_start: datetime,
    *,
    as_of: datetime | None = None,
    source_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> HourlyRollupRefreshResult:
    """Rebuild one completed clock hour atomically."""

    bucket_start = business_hour_floor(bucket_start)
    current_hour = business_hour_floor(as_of or datetime.now(UTC))
    if bucket_start >= current_hour:
        return HourlyRollupRefreshResult(bucket_start, "open_hour", 0)
    started = perf_counter()
    source_factory = source_session_factory or session_factory
    async with _refresh_lock:
        start, end = business_hour_bounds(bucket_start)
        async with source_factory() as source_session:
            await apply_dashboard_statement_limits(source_session, maintenance=True)
            contribution = await build_raw_contribution(source_session, start=start, end=end)
        async with session_factory() as session:
            await apply_dashboard_statement_limits(session, maintenance=True)
            if session.get_bind().dialect.name == "postgresql":
                acquired = await session.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
                    {"key": f"deerflow-dashboard-hourly-rollup:{bucket_start.isoformat()}"},
                )
                if not acquired:
                    await session.rollback()
                    return HourlyRollupRefreshResult(bucket_start, "busy", 0)
            duration_ms = int((perf_counter() - started) * 1000)
            await _upsert_hourly_rollup(
                session,
                {
                    "bucket_start": bucket_start,
                    "schema_version": ROLLUP_SCHEMA_VERSION,
                    "payload_json": contribution.to_daily_payload(business_day(bucket_start)),
                    "source_run_count": contribution.source_run_count,
                    "source_thread_count": contribution.source_thread_count,
                    "source_feedback_count": contribution.source_feedback_count,
                    "source_external_click_count": contribution.source_external_click_count,
                    "build_duration_ms": duration_ms,
                    "refreshed_at": datetime.now(UTC),
                },
            )
            await session.commit()
            return HourlyRollupRefreshResult(
                bucket_start,
                "refreshed",
                duration_ms,
                contribution.source_run_count,
            )


@dataclass(frozen=True)
class RollupStatus:
    schema_version: int
    source_first_day: date | None
    source_last_complete_day: date | None
    expected_days: int
    ready_days: int
    missing_days: int
    oldest_missing_day: date | None
    newest_ready_day: date | None
    last_change_scan_at: datetime | None
    pending_refresh_days: int
    hourly_retention_hours: int
    expected_hours: int
    ready_hours: int
    missing_hours: int
    oldest_missing_hour: datetime | None
    newest_ready_hour: datetime | None
    last_hourly_change_scan_at: datetime | None
    pending_refresh_hours: int


async def _source_min_timestamp(session: AsyncSession) -> datetime | None:
    timestamps: list[datetime] = []
    for model in (RunRow, ThreadCreationRow, FeedbackRow):
        value = await session.scalar(select(func.min(model.created_at)))
        if isinstance(value, datetime):
            timestamps.append(_aware_utc(value))
    click_min = await session.scalar(select(func.min(AuditLogRow.created_at)).where(AuditLogRow.action.in_((EXTERNAL_AGENT_CLICK_ACTION, EXTERNAL_PLATFORM_CLICK_ACTION))))
    if isinstance(click_min, datetime):
        timestamps.append(_aware_utc(click_min))
    return min(timestamps) if timestamps else None


async def _source_bounds(
    session: AsyncSession,
    *,
    as_of: datetime | None = None,
    source_min: datetime | None = None,
) -> tuple[date | None, date | None]:
    source_min = source_min or await _source_min_timestamp(session)
    if source_min is None:
        return None, None
    first = business_day(source_min)
    reference = _aware_utc(as_of or datetime.now(UTC)).astimezone(BUSINESS_TIMEZONE)
    last_complete = reference.date() - timedelta(days=1)
    return first, last_complete if first <= last_complete else None


async def get_rollup_status(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    as_of: datetime | None = None,
) -> RollupStatus:
    async with session_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=False)
        source_min = await _source_min_timestamp(session)
        first, last = await _source_bounds(session, as_of=as_of, source_min=source_min)
        ready_rows = (await session.execute(select(DashboardDailyRollupRow.day).where(DashboardDailyRollupRow.schema_version == ROLLUP_SCHEMA_VERSION))).scalars()
        ready = set(ready_rows)
        state = await session.get(DashboardRollupStateRow, ROLLUP_STATE_KEY)
        ready_hour_rows = (await session.execute(select(DashboardHourlyRollupRow.bucket_start).where(DashboardHourlyRollupRow.schema_version == ROLLUP_SCHEMA_VERSION))).scalars()
        ready_hours = {business_hour_floor(value) for value in ready_hour_rows}
        hourly_state = await session.get(DashboardRollupStateRow, HOURLY_ROLLUP_STATE_KEY)
    if first is None or last is None:
        expected: list[date] = []
    else:
        expected = [first + timedelta(days=offset) for offset in range((last - first).days + 1)]
    missing = [day for day in expected if day not in ready]
    current_hour = business_hour_floor(as_of or datetime.now(UTC))
    retention_hours = hourly_rollup_retention_hours()
    first_hour = max(business_hour_floor(source_min), current_hour - timedelta(hours=retention_hours)) if source_min is not None else None
    expected_hour_values: list[datetime] = []
    cursor = first_hour
    while cursor is not None and cursor < current_hour:
        expected_hour_values.append(cursor)
        cursor += timedelta(hours=1)
    missing_hour_values = [value for value in expected_hour_values if value not in ready_hours]
    return RollupStatus(
        schema_version=ROLLUP_SCHEMA_VERSION,
        source_first_day=first,
        source_last_complete_day=last,
        expected_days=len(expected),
        ready_days=len([day for day in expected if day in ready]),
        missing_days=len(missing),
        oldest_missing_day=missing[0] if missing else None,
        newest_ready_day=max(ready) if ready else None,
        last_change_scan_at=state.last_change_scan_at if state else None,
        pending_refresh_days=len(state.pending_days_json or []) if state else 0,
        hourly_retention_hours=retention_hours,
        expected_hours=len(expected_hour_values),
        ready_hours=len([value for value in expected_hour_values if value in ready_hours]),
        missing_hours=len(missing_hour_values),
        oldest_missing_hour=missing_hour_values[0] if missing_hour_values else None,
        newest_ready_hour=max(ready_hours) if ready_hours else None,
        last_hourly_change_scan_at=(hourly_state.last_change_scan_at if hourly_state else None),
        pending_refresh_hours=len(hourly_state.pending_days_json or []) if hourly_state else 0,
    )


async def backfill_rollup_batch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_days: int = 1,
    source_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[RollupRefreshResult]:
    """Refresh the oldest missing days; intended for a throttled external job."""

    max_days = max(1, min(max_days, 31))
    source_factory = source_session_factory or session_factory
    status = await get_rollup_status(source_factory)
    if status.source_first_day is None or status.source_last_complete_day is None:
        return []
    async with source_factory() as session:
        ready = set(
            (
                await session.execute(
                    select(DashboardDailyRollupRow.day).where(
                        DashboardDailyRollupRow.schema_version == ROLLUP_SCHEMA_VERSION,
                        DashboardDailyRollupRow.day >= status.source_first_day,
                        DashboardDailyRollupRow.day <= status.source_last_complete_day,
                    )
                )
            ).scalars()
        )
    targets: list[date] = []
    current = status.source_first_day
    while current <= status.source_last_complete_day and len(targets) < max_days:
        if current not in ready:
            targets.append(current)
        current += timedelta(days=1)
    results: list[RollupRefreshResult] = []
    for day in targets:
        results.append(
            await refresh_rollup_day(
                session_factory,
                day,
                source_session_factory=source_factory,
            )
        )
    return results


async def backfill_hourly_rollup_batch(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_hours: int = 2,
    as_of: datetime | None = None,
    source_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[HourlyRollupRefreshResult]:
    """Build the newest missing completed hours inside the bounded hot window."""

    max_hours = max(1, min(max_hours, hourly_rollup_retention_hours()))
    source_factory = source_session_factory or session_factory
    current_hour = business_hour_floor(as_of or datetime.now(UTC))
    oldest_allowed = current_hour - timedelta(hours=hourly_rollup_retention_hours())
    async with source_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=True)
        source_min = await _source_min_timestamp(session)
        if source_min is None:
            return []
        first_hour = max(business_hour_floor(source_min), oldest_allowed)
        ready = {
            business_hour_floor(value)
            for value in (
                await session.execute(
                    select(DashboardHourlyRollupRow.bucket_start).where(
                        DashboardHourlyRollupRow.schema_version == ROLLUP_SCHEMA_VERSION,
                        DashboardHourlyRollupRow.bucket_start >= first_hour,
                        DashboardHourlyRollupRow.bucket_start < current_hour,
                    )
                )
            ).scalars()
        }
    targets: list[datetime] = []
    cursor = current_hour - timedelta(hours=1)
    while cursor >= first_hour and len(targets) < max_hours:
        if cursor not in ready:
            targets.append(cursor)
        cursor -= timedelta(hours=1)
    return [
        await refresh_rollup_hour(
            session_factory,
            bucket_start,
            as_of=as_of,
            source_session_factory=source_factory,
        )
        for bucket_start in targets
    ]


async def _upsert_state(
    session: AsyncSession,
    *,
    last_change_scan_at: datetime,
    pending_days: set[date],
) -> None:
    values = {
        "key": ROLLUP_STATE_KEY,
        "schema_version": ROLLUP_SCHEMA_VERSION,
        "last_change_scan_at": last_change_scan_at,
        "pending_days_json": [day.isoformat() for day in sorted(pending_days)],
        "updated_at": datetime.now(UTC),
    }
    update_values = {key: value for key, value in values.items() if key != "key"}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DashboardRollupStateRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["key"], set_=update_values))
    elif dialect == "sqlite":
        statement = sqlite_insert(DashboardRollupStateRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["key"], set_=update_values))
    else:
        await session.merge(DashboardRollupStateRow(**values))


async def _upsert_hourly_state(
    session: AsyncSession,
    *,
    last_change_scan_at: datetime,
    pending_hours: set[datetime],
) -> None:
    values = {
        "key": HOURLY_ROLLUP_STATE_KEY,
        "schema_version": ROLLUP_SCHEMA_VERSION,
        "last_change_scan_at": last_change_scan_at,
        "pending_days_json": [value.isoformat() for value in sorted(pending_hours)],
        "updated_at": datetime.now(UTC),
    }
    update_values = {key: value for key, value in values.items() if key != "key"}
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DashboardRollupStateRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["key"], set_=update_values))
    elif dialect == "sqlite":
        statement = sqlite_insert(DashboardRollupStateRow).values(**values)
        await session.execute(statement.on_conflict_do_update(index_elements=["key"], set_=update_values))
    else:
        await session.merge(DashboardRollupStateRow(**values))


async def refresh_changed_rollup_days(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_days: int = 1,
    source_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[RollupRefreshResult]:
    """Refresh completed days changed since the last successful maintenance scan."""

    requested_scan_until = datetime.now(UTC)
    max_days = max(1, min(max_days, 31))
    source_factory = source_session_factory or session_factory
    async with source_factory() as source_session:
        await apply_dashboard_statement_limits(source_session, maintenance=True)
        scan_until = await _source_replay_bound(source_session, requested_scan_until)
    async with session_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=True)
        state = await session.get(DashboardRollupStateRow, ROLLUP_STATE_KEY)
        if state is None or state.schema_version != ROLLUP_SCHEMA_VERSION or state.last_change_scan_at is None:
            await _upsert_state(session, last_change_scan_at=scan_until, pending_days=set())
            await session.commit()
            return []
        scan_from = _aware_utc(state.last_change_scan_at)
        changed_days = _pending_days(state.pending_days_json)
    if scan_until <= scan_from and not changed_days:
        return []
    today = datetime.now(BUSINESS_TIMEZONE).date()
    async with source_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=True)
        statements = (
            select(RunRow.created_at).where(RunRow.updated_at > scan_from, RunRow.updated_at <= scan_until),
            select(ThreadCreationRow.created_at).where(
                ThreadCreationRow.created_at > scan_from,
                ThreadCreationRow.created_at <= scan_until,
            ),
            select(FeedbackRow.created_at).where(
                FeedbackRow.created_at > scan_from,
                FeedbackRow.created_at <= scan_until,
            ),
            select(AuditLogRow.created_at).where(
                AuditLogRow.action.in_((EXTERNAL_AGENT_CLICK_ACTION, EXTERNAL_PLATFORM_CLICK_ACTION)),
                AuditLogRow.created_at > scan_from,
                AuditLogRow.created_at <= scan_until,
            ),
        )
        for statement in statements:
            stream = await session.stream(statement.execution_options(yield_per=1000))
            async for value in stream.scalars():
                if isinstance(value, datetime):
                    day = business_day(value)
                    if day < today:
                        changed_days.add(day)
    targets = sorted(changed_days)[:max_days]
    results = [
        await refresh_rollup_day(
            session_factory,
            day,
            source_session_factory=source_factory,
        )
        for day in targets
    ]
    completed = {result.day for result in results if result.status in {"refreshed", "open_day"}}
    async with session_factory() as session:
        await _upsert_state(
            session,
            last_change_scan_at=max(scan_until, scan_from),
            pending_days=changed_days - completed,
        )
        await session.commit()
    return results


async def refresh_changed_rollup_hours(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    max_hours: int = 2,
    as_of: datetime | None = None,
    source_session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> list[HourlyRollupRefreshResult]:
    """Refresh recent completed hours whose source rows changed after aggregation."""

    requested_scan_until = _aware_utc(as_of or datetime.now(UTC))
    max_hours = max(1, min(max_hours, 24))
    source_factory = source_session_factory or session_factory
    async with source_factory() as source_session:
        await apply_dashboard_statement_limits(source_session, maintenance=True)
        scan_until = await _source_replay_bound(source_session, requested_scan_until)
    current_hour = business_hour_floor(scan_until)
    oldest_allowed = current_hour - timedelta(hours=hourly_rollup_retention_hours())
    async with session_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=True)
        state = await session.get(DashboardRollupStateRow, HOURLY_ROLLUP_STATE_KEY)
        if state is None or state.schema_version != ROLLUP_SCHEMA_VERSION or state.last_change_scan_at is None:
            await _upsert_hourly_state(session, last_change_scan_at=scan_until, pending_hours=set())
            await session.commit()
            return []
        scan_from = _aware_utc(state.last_change_scan_at)
        changed_hours = {value for value in _pending_hours(state.pending_days_json) if oldest_allowed <= value < current_hour}
    if scan_until <= scan_from and not changed_hours:
        return []
    async with source_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=True)
        statements = (
            select(RunRow.created_at).where(RunRow.updated_at > scan_from, RunRow.updated_at <= scan_until),
            select(ThreadCreationRow.created_at).where(
                ThreadCreationRow.created_at > scan_from,
                ThreadCreationRow.created_at <= scan_until,
            ),
            select(FeedbackRow.created_at).where(
                FeedbackRow.created_at > scan_from,
                FeedbackRow.created_at <= scan_until,
            ),
            select(AuditLogRow.created_at).where(
                AuditLogRow.action.in_((EXTERNAL_AGENT_CLICK_ACTION, EXTERNAL_PLATFORM_CLICK_ACTION)),
                AuditLogRow.created_at > scan_from,
                AuditLogRow.created_at <= scan_until,
            ),
        )
        for statement in statements:
            stream = await session.stream(statement.execution_options(yield_per=1000))
            async for value in stream.scalars():
                if isinstance(value, datetime):
                    bucket_start = business_hour_floor(value)
                    if oldest_allowed <= bucket_start < current_hour:
                        changed_hours.add(bucket_start)
    targets = sorted(changed_hours, reverse=True)[:max_hours]
    results = [
        await refresh_rollup_hour(
            session_factory,
            bucket_start,
            as_of=scan_until,
            source_session_factory=source_factory,
        )
        for bucket_start in targets
    ]
    completed = {result.bucket_start for result in results if result.status in {"refreshed", "open_hour"}}
    async with session_factory() as session:
        await _upsert_hourly_state(
            session,
            last_change_scan_at=max(scan_until, scan_from),
            pending_hours=changed_hours - completed,
        )
        await session.commit()
    return results


async def prune_hourly_rollups(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    as_of: datetime | None = None,
) -> int:
    """Delete expired hot-window facts in one short maintenance transaction."""

    cutoff = business_hour_floor(as_of or datetime.now(UTC)) - timedelta(hours=hourly_rollup_retention_hours())
    async with session_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=True)
        result = await session.execute(delete(DashboardHourlyRollupRow).where(DashboardHourlyRollupRow.bucket_start < cutoff))
        await session.commit()
        return max(0, int(result.rowcount or 0))


class DashboardRollupsNotReady(RuntimeError):
    def __init__(
        self,
        missing_days: list[date] | None = None,
        *,
        missing_hours: list[datetime] | None = None,
    ) -> None:
        self.missing_days = missing_days or []
        self.missing_hours = missing_hours or []
        if self.missing_hours:
            message = f"dashboard rollups are missing {len(self.missing_hours)} completed hour(s)"
        else:
            message = f"dashboard rollups are missing {len(self.missing_days)} completed day(s)"
        super().__init__(message)


@dataclass(frozen=True)
class ContributionSelection:
    contribution: DashboardContribution
    source: str
    rollup_days: int
    raw_segments: tuple[tuple[datetime, datetime], ...]
    rollup_hours: int = 0


async def load_hybrid_contribution(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    start: datetime,
    end: datetime,
    minimum_rollup_days: int = 14,
    minimum_hourly_rollup_hours: int = 6,
) -> ContributionSelection:
    """Read daily/hourly facts plus only the incomplete edge slices as raw."""

    start = _aware_utc(start)
    end = _aware_utc(end)
    full_days = fully_covered_business_days(start, end)
    if len(full_days) >= max(1, minimum_rollup_days):
        async with session_factory() as session:
            await apply_dashboard_statement_limits(session, maintenance=False)
            rows = (
                (
                    await session.execute(
                        select(DashboardDailyRollupRow).where(
                            DashboardDailyRollupRow.day >= full_days[0],
                            DashboardDailyRollupRow.day <= full_days[-1],
                            DashboardDailyRollupRow.schema_version == ROLLUP_SCHEMA_VERSION,
                        )
                    )
                )
                .scalars()
                .all()
            )
        rows_by_day = {row.day: row for row in rows}
        missing = [day for day in full_days if day not in rows_by_day]
        if missing:
            raise DashboardRollupsNotReady(missing)

        contribution = DashboardContribution()
        for day in full_days:
            contribution.merge(DashboardContribution.from_daily_payload(day, rows_by_day[day].payload_json or {}))

        first_start, _ = business_day_bounds(full_days[0])
        _, last_end = business_day_bounds(full_days[-1])
        edge_segments = tuple(segment for segment in ((start, min(first_start, end)), (max(last_end, start), end)) if segment[0] < segment[1])
        raw_segments: list[tuple[datetime, datetime]] = []
        rollup_hours = 0
        for segment_start, segment_end in edge_segments:
            edge = await load_hybrid_contribution(
                session_factory,
                start=segment_start,
                end=segment_end,
                minimum_rollup_days=367,
                minimum_hourly_rollup_hours=1,
            )
            contribution.merge(edge.contribution)
            rollup_hours += edge.rollup_hours
            raw_segments.extend(edge.raw_segments)
        return ContributionSelection(
            contribution,
            "rollup",
            len(full_days),
            tuple(raw_segments),
            rollup_hours,
        )

    full_hours = fully_covered_business_hours(start, end)
    current_hour = business_hour_floor(datetime.now(UTC))
    hourly_cutoff = current_hour - timedelta(hours=hourly_rollup_retention_hours())
    use_hourly = len(full_hours) >= max(1, minimum_hourly_rollup_hours) and full_hours[0] >= hourly_cutoff and full_hours[-1] < current_hour
    if use_hourly:
        async with session_factory() as session:
            await apply_dashboard_statement_limits(session, maintenance=False)
            hourly_rows = (
                (
                    await session.execute(
                        select(DashboardHourlyRollupRow).where(
                            DashboardHourlyRollupRow.bucket_start >= full_hours[0],
                            DashboardHourlyRollupRow.bucket_start <= full_hours[-1],
                            DashboardHourlyRollupRow.schema_version == ROLLUP_SCHEMA_VERSION,
                        )
                    )
                )
                .scalars()
                .all()
            )
        rows_by_hour = {business_hour_floor(row.bucket_start): row for row in hourly_rows}
        missing_hours = [value for value in full_hours if value not in rows_by_hour]
        if missing_hours and (hourly_rows or _require_hourly_rollups()):
            raise DashboardRollupsNotReady(missing_hours=missing_hours)
        if not missing_hours:
            contribution = DashboardContribution()
            for bucket_start in full_hours:
                row = rows_by_hour[bucket_start]
                contribution.merge(
                    DashboardContribution.from_daily_payload(
                        business_day(bucket_start),
                        row.payload_json or {},
                    )
                )
            first_start, _ = business_hour_bounds(full_hours[0])
            _, last_end = business_hour_bounds(full_hours[-1])
            raw_segments = tuple(segment for segment in ((start, min(first_start, end)), (max(last_end, start), end)) if segment[0] < segment[1])
            for segment_start, segment_end in raw_segments:
                async with session_factory() as session:
                    await apply_dashboard_statement_limits(session, maintenance=False)
                    contribution.merge(
                        await build_raw_contribution(
                            session,
                            start=segment_start,
                            end=segment_end,
                        )
                    )
            return ContributionSelection(
                contribution,
                "hourly_rollup",
                0,
                raw_segments,
                len(full_hours),
            )

    async with session_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=False)
        contribution = await build_raw_contribution(session, start=start, end=end)
    return ContributionSelection(contribution, "raw", 0, ((start, end),))


async def load_external_platform_rollup_summary(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    now: datetime | None = None,
    days: int = 30,
) -> dict[str, Any] | None:
    """Build exact external-platform totals without scanning historical audit rows.

    ``None`` means completed daily facts are not ready. Callers must surface a
    partial-data state rather than falling back to an all-history source scan.
    """

    current = _aware_utc(now or datetime.now(UTC)).astimezone(BUSINESS_TIMEZONE)
    today = current.date()
    async with session_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=False)
        source_min = await session.scalar(select(func.min(AuditLogRow.created_at)).where(AuditLogRow.action == EXTERNAL_PLATFORM_CLICK_ACTION))
        rows: list[DashboardDailyRollupRow] = []
        if isinstance(source_min, datetime) and business_day(source_min) < today:
            rows = list(
                (
                    await session.execute(
                        select(DashboardDailyRollupRow)
                        .where(
                            DashboardDailyRollupRow.schema_version == ROLLUP_SCHEMA_VERSION,
                            DashboardDailyRollupRow.day >= business_day(source_min),
                            DashboardDailyRollupRow.day < today,
                        )
                        .order_by(DashboardDailyRollupRow.day)
                    )
                )
                .scalars()
                .all()
            )
    if isinstance(source_min, datetime):
        first_day = business_day(source_min)
        expected_days = {first_day + timedelta(days=offset) for offset in range(max(0, (today - first_day).days))}
        if expected_days - {row.day for row in rows}:
            return None

    totals: dict[tuple[str, str], ExternalPlatformMetric] = {}
    daily_users: dict[tuple[date, str, str], set[str]] = defaultdict(set)
    for row in rows:
        day_contribution = DashboardContribution.from_daily_payload(row.day, row.payload_json or {})
        for key, incoming in day_contribution.external_platform_clicks.items():
            metric = totals.setdefault(key, ExternalPlatformMetric())
            metric.clicks += incoming.clicks
            metric.user_ids.update(incoming.user_ids)
            daily_users[(row.day, key[0], key[1])].update(incoming.user_ids)

    today_start, _ = business_day_bounds(today)
    async with session_factory() as session:
        await apply_dashboard_statement_limits(session, maintenance=False)
        today_contribution = await build_external_platform_raw_contribution(
            session,
            start=today_start,
            end=_aware_utc(now or datetime.now(UTC)),
        )
    for key, incoming in today_contribution.external_platform_clicks.items():
        metric = totals.setdefault(key, ExternalPlatformMetric())
        metric.clicks += incoming.clicks
        metric.user_ids.update(incoming.user_ids)
        daily_users[(today, key[0], key[1])].update(incoming.user_ids)

    first_trend_day = today - timedelta(days=max(1, days) - 1)
    trend_dates = [(first_trend_day + timedelta(days=offset)).isoformat() for offset in range(max(1, days))]
    agents: list[dict[str, Any]] = []
    trend_series: dict[str, list[int]] = {}
    for agent_name in EXTERNAL_PLATFORM_AGENT_NAMES:
        by_source: list[dict[str, Any]] = []
        for source in EXTERNAL_PLATFORM_SOURCES:
            total = totals.get((agent_name, source), ExternalPlatformMetric())
            today_users = daily_users.get((today, agent_name, source), set())
            today_clicks = today_contribution.external_platform_clicks.get((agent_name, source), ExternalPlatformMetric()).clicks
            by_source.append(
                {
                    "source": source,
                    "today_users": len(today_users),
                    "total_users": len(total.user_ids),
                    "today_clicks": today_clicks,
                    "total_clicks": total.clicks,
                }
            )
        agents.append(
            {
                "name": agent_name,
                "today_users": sum(item["today_users"] for item in by_source),
                "total_users": sum(item["total_users"] for item in by_source),
                "today_clicks": sum(item["today_clicks"] for item in by_source),
                "total_clicks": sum(item["total_clicks"] for item in by_source),
                "by_source": by_source,
            }
        )
        trend_series[agent_name] = [sum(len(daily_users.get((date.fromisoformat(day_value), agent_name, source), set())) for source in EXTERNAL_PLATFORM_SOURCES) for day_value in trend_dates]
    return {
        "available": True,
        "error": None,
        "today": today.isoformat(),
        "days": max(1, days),
        "agents": agents,
        "trend": {"dates": trend_dates, "series": trend_series},
    }
