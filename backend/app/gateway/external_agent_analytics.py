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


def summarize_external_platform_clicks(
    rows: Iterable[Any],
    *,
    now: datetime | None = None,
    days: int = 30,
) -> dict[str, Any]:
    """Summarize external click events using the legacy dashboard's user semantics.

    Users are de-duplicated independently inside each source platform and then
    source counts are summed. A person present in both platforms therefore
    contributes one active user to each platform, matching ``app_v4.py``.
    """
    if days < 1:
        raise ValueError("days must be positive")

    current = _as_utc(now or datetime.now(UTC)).astimezone(_BUSINESS_TIMEZONE)
    today = current.date()
    first_trend_date = today - timedelta(days=days - 1)
    dates = [first_trend_date + timedelta(days=offset) for offset in range(days)]
    date_keys = [day.isoformat() for day in dates]

    metrics: dict[str, dict[str, Any]] = {}
    trend_users: dict[str, dict[str, dict[str, set[str]]]] = {}
    for agent_name in EXTERNAL_PLATFORM_AGENT_NAMES:
        metrics[agent_name] = {
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
        trend_users[agent_name] = {}

    for row in rows:
        agent_name = normalize_external_agent_name(str(getattr(row, "resource_id", "") or ""))
        details = getattr(row, "details_json", None) or {}
        source = normalize_external_agent_source(str(details.get("source", "") or ""))
        created_at = getattr(row, "created_at", None)
        if agent_name is None or source is None or not isinstance(created_at, datetime):
            continue

        local_day = _as_utc(created_at).astimezone(_BUSINESS_TIMEZONE).date()
        identity = str(getattr(row, "user_id", "") or getattr(row, "user_email", "") or details.get("user_name", "") or "")
        source_metrics = metrics[agent_name]["by_source"][source]
        metrics[agent_name]["total_clicks"] += 1
        source_metrics["total_clicks"] += 1
        if identity:
            source_metrics["total_users"].add(identity)

        if local_day == today:
            metrics[agent_name]["today_clicks"] += 1
            source_metrics["today_clicks"] += 1
            if identity:
                source_metrics["today_users"].add(identity)

        if first_trend_date <= local_day <= today and identity:
            day_users = trend_users[agent_name].setdefault(local_day.isoformat(), {})
            day_users.setdefault(source, set()).add(identity)

    agent_metrics: list[dict[str, Any]] = []
    for agent_name in EXTERNAL_PLATFORM_AGENT_NAMES:
        metric = metrics[agent_name]
        by_source: list[dict[str, Any]] = []
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
            metric["today_users"] += item["today_users"]
            metric["total_users"] += item["total_users"]
        metric["by_source"] = by_source
        agent_metrics.append(metric)

    series = {agent_name: [sum(len(users) for users in trend_users[agent_name].get(date_key, {}).values()) for date_key in date_keys] for agent_name in EXTERNAL_PLATFORM_AGENT_NAMES}
    return {
        "today": today.isoformat(),
        "days": days,
        "agents": agent_metrics,
        "trend": {"dates": date_keys, "series": series},
    }
