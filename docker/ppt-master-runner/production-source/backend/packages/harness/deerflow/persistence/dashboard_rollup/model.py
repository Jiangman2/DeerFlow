"""Versioned, rebuildable daily and hourly administrative dashboard facts."""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class DashboardDailyRollupRow(Base):
    """One atomic metric payload for a completed Asia/Shanghai calendar day."""

    __tablename__ = "dashboard_daily_rollups"
    __table_args__ = (Index("ix_dashboard_daily_rollups_refreshed_at", "refreshed_at"),)

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_thread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_feedback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_external_click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    build_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class DashboardHourlyRollupRow(Base):
    """One atomic metric payload for a completed Asia/Shanghai clock hour."""

    __tablename__ = "dashboard_hourly_rollups"
    __table_args__ = (Index("ix_dashboard_hourly_rollups_refreshed_at", "refreshed_at"),)

    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    source_run_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_thread_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_feedback_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    source_external_click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    build_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class DashboardRollupStateRow(Base):
    """Durable refresh cursor shared by every Gateway replica."""

    __tablename__ = "dashboard_rollup_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    last_change_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pending_days_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
