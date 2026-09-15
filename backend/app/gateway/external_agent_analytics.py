"""External-platform agent click normalization and dashboard aggregation."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

EXTERNAL_PLATFORM_CLICK_ACTION = "external_agent.remote_click"
EXTERNAL_PLATFORM_SOURCES = ("海能平台", "协同平台")
EXTERNAL_PLATFORM_AGENT_NAMES = (
    "专业文档解析专家",
    "地震数据提频专家",
    "专业报告校审专家",
    "自动数采专家",
    "前期研究报告编写专家",
)

_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
_AGENT_ALIASES = {
    "专业文档解析专家": "专业文档解析专家",
    "勘探开发图文智能检索工具": "专业文档解析专家",
    "地震数据提频专家": "地震数据提频专家",
    "地震数据分辨率提升工具": "地震数据提频专家",
    "专业报告校审专家": "专业报告校审专家",
    "油气专业文档智能审查工具": "专业报告校审专家",
    "自动数采专家": "自动数采专家",
    "源头采集自动化工具": "自动数采专家",
    "前期研究报告编写专家": "前期研究报告编写专家",
    "科研报告自动生产智能体": "前期研究报告编写专家",
}
_SOURCE_ALIASES = {
    "海能": "海能平台",
    "海能平台": "海能平台",
    "协同": "协同平台",
    "协同平台": "协同平台",
}


def normalize_external_agent_name(value: str) -> str | None:
    """Return the canonical DeerFlow card name for a reported external agent."""
    return _AGENT_ALIASES.get(value.strip())


def normalize_external_agent_source(value: str) -> str | None:
    """Normalize the two external source platforms supported by this dashboard."""
    return _SOURCE_ALIASES.get(value.strip())


def external_platform_user_id(*, source: str, user_email: str, user_name: str) -> str:
    """Build a privacy-preserving, stable user key scoped to one source platform."""
    identity = user_email.strip().casefold() or user_name.strip().casefold()
    digest = hashlib.sha256(f"{source}\0{identity}".encode()).hexdigest()
    return f"ext:{digest[:60]}"


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class ExternalPlatformClickAccumulator:
    """Incrementally aggregate rows so all click history is never materialized."""

    def __init__(self, *, now: datetime | None = None, days: int = 30) -> None:
        if days < 1:
            raise ValueError("days must be positive")
        current = _as_utc(now or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE)
        self.today = current.date()
        self.days = days
        self.first_trend_date = self.today - timedelta(days=days - 1)
        self.date_keys = [(self.first_trend_date + timedelta(days=offset)).isoformat() for offset in range(days)]
        self.metrics: dict[str, dict[str, Any]] = {}
        self.trend_users: dict[str, dict[str, dict[str, set[str]]]] = {}
        for agent_name in EXTERNAL_PLATFORM_AGENT_NAMES:
            self.metrics[agent_name] = {
                "name": agent_name,
                "today_users": 0,
                "total_users": 0,
                "today_clicks": 0,
                "total_clicks": 0,
                "by_source": {
                    source: {
                        "source": source,
                        "today_users": set(),
                        "total_users": set(),
                        "today_clicks": 0,
                        "total_clicks": 0,
                    }
                    for source in EXTERNAL_PLATFORM_SOURCES
                },
            }
            self.trend_users[agent_name] = {}

    def add(self, row: Any) -> None:
        agent_name = normalize_external_agent_name(str(getattr(row, "resource_id", "") or ""))
        details = getattr(row, "details_json", None) or {}
        source = normalize_external_agent_source(str(details.get("source", "") or ""))
        created_at = getattr(row, "created_at", None)
        if agent_name is None or source is None or not isinstance(created_at, datetime):
            return

        local_day = _as_utc(created_at).astimezone(_BUSINESS_TIMEZONE).date()
        identity = str(getattr(row, "user_id", "") or getattr(row, "user_email", "") or details.get("user_name", "") or "")
        source_metrics = self.metrics[agent_name]["by_source"][source]
        self.metrics[agent_name]["total_clicks"] += 1
        source_metrics["total_clicks"] += 1
        if identity:
            source_metrics["total_users"].add(identity)
        if local_day == self.today:
            self.metrics[agent_name]["today_clicks"] += 1
            source_metrics["today_clicks"] += 1
            if identity:
                source_metrics["today_users"].add(identity)
        if self.first_trend_date <= local_day <= self.today and identity:
            day_users = self.trend_users[agent_name].setdefault(local_day.isoformat(), {})
            day_users.setdefault(source, set()).add(identity)

    def result(self) -> dict[str, Any]:
        agent_metrics: list[dict[str, Any]] = []
        for agent_name in EXTERNAL_PLATFORM_AGENT_NAMES:
            metric = self.metrics[agent_name]
            by_source: list[dict[str, Any]] = []
            today_users = 0
            total_users = 0
            for source in EXTERNAL_PLATFORM_SOURCES:
                source_metric = metric["by_source"][source]
                item = {
                    "source": source,
                    "today_users": len(source_metric["today_users"]),
                    "total_users": len(source_metric["total_users"]),
                    "today_clicks": source_metric["today_clicks"],
                    "total_clicks": source_metric["total_clicks"],
                }
                by_source.append(item)
                today_users += item["today_users"]
                total_users += item["total_users"]
            agent_metrics.append(
                {
                    "name": agent_name,
                    "today_users": today_users,
                    "total_users": total_users,
                    "today_clicks": metric["today_clicks"],
                    "total_clicks": metric["total_clicks"],
                    "by_source": by_source,
                }
            )

        series = {agent_name: [sum(len(users) for users in self.trend_users[agent_name].get(date_key, {}).values()) for date_key in self.date_keys] for agent_name in EXTERNAL_PLATFORM_AGENT_NAMES}
        return {
            "today": self.today.isoformat(),
            "days": self.days,
            "agents": agent_metrics,
            "trend": {"dates": self.date_keys, "series": series},
        }


def summarize_external_platform_clicks(
    rows: Iterable[Any],
    *,
    now: datetime | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """Summarize rows using the incremental implementation."""

    accumulator = ExternalPlatformClickAccumulator(now=now, days=days)
    for row in rows:
        accumulator.add(row)
    return accumulator.result()
