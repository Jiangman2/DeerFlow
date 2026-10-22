"""ORM model for workspace announcements."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class AnnouncementRow(Base):
    __tablename__ = "announcements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="info")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft", index=True)
    dismissible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    created_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    updated_by_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    updated_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    __table_args__ = (
        Index("ix_announcements_status_window", "status", "starts_at", "ends_at"),
        Index("ix_announcements_updated", "updated_at"),
    )
