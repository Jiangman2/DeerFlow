from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.gateway.external_agent_analytics import (
    EXTERNAL_PLATFORM_AGENT_NAMES,
    normalize_external_agent_name,
    normalize_external_agent_source,
    summarize_external_platform_clicks,
)


@dataclass
class _Click:
    created_at: datetime
    resource_id: str
    user_id: str | None
    user_email: str | None = None
    details_json: dict | None = None


def test_external_platform_aliases_are_normalized() -> None:
    assert normalize_external_agent_name("勘探开发图文智能检索工具") == "专业文档解析专家"
    assert normalize_external_agent_name("地震数据分辨率提升工具") == "地震数据提频专家"
    assert normalize_external_agent_name("专业报告校审专家") == "专业报告校审专家"
    assert normalize_external_agent_name("unknown") is None
    assert normalize_external_agent_source("海能") == "海能平台"
    assert normalize_external_agent_source("协同平台") == "协同平台"
    assert normalize_external_agent_source("数据湖平台") is None
    assert normalize_external_agent_source("通用智能体平台") is None


def test_external_platform_summary_keeps_original_user_semantics() -> None:
    now = datetime(2026, 7, 30, 4, 0, tzinfo=UTC)  # 2026-07-30 12:00 Asia/Shanghai
    rows = [
        _Click(
            created_at=datetime(2026, 7, 30, 1, 0, tzinfo=UTC),
            resource_id="专业文档解析专家",
            user_id="same-user",
            details_json={"source": "海能平台"},
        ),
        _Click(
            created_at=datetime(2026, 7, 30, 2, 0, tzinfo=UTC),
            resource_id="专业文档解析专家",
            user_id="same-user",
            details_json={"source": "海能平台"},
        ),
        _Click(
            created_at=datetime(2026, 7, 30, 3, 0, tzinfo=UTC),
            resource_id="专业文档解析专家",
            user_id="same-user",
            details_json={"source": "协同平台"},
        ),
        _Click(
            created_at=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
            resource_id="专业文档解析专家",
            user_id="old-user",
            details_json={"source": "海能平台"},
        ),
    ]

    summary = summarize_external_platform_clicks(rows, now=now)
    item = next(agent for agent in summary["agents"] if agent["name"] == "专业文档解析专家")

    assert item["today_clicks"] == 3
    assert item["total_clicks"] == 4
    assert item["today_users"] == 2  # same identity counts once per source, then sources are summed
    assert item["total_users"] == 3
    assert item["by_source"][0] == {
        "source": "海能平台",
        "today_users": 1,
        "total_users": 2,
        "today_clicks": 2,
        "total_clicks": 3,
    }
    assert len(summary["trend"]["dates"]) == 30
    assert summary["trend"]["dates"][-1] == "2026-07-30"
    assert summary["trend"]["series"]["专业文档解析专家"][-1] == 2
    assert sum(summary["trend"]["series"]["专业文档解析专家"]) == 2
    assert set(summary["trend"]["series"]) == set(EXTERNAL_PLATFORM_AGENT_NAMES)


def test_external_platform_summary_uses_shanghai_calendar_day() -> None:
    now = datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    rows = [
        _Click(
            created_at=datetime(2026, 7, 29, 16, 30, tzinfo=UTC),
            resource_id="自动数采专家",
            user_id="user-1",
            details_json={"source": "协同平台"},
        )
    ]

    summary = summarize_external_platform_clicks(rows, now=now)
    item = next(agent for agent in summary["agents"] if agent["name"] == "自动数采专家")
    assert summary["today"] == "2026-07-30"
    assert item["today_clicks"] == 1
    assert item["today_users"] == 1
