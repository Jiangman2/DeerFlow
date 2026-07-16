"""ORM model for administrator-managed site navigation."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class SiteNavigationRow(Base):
    __tablename__ = "site_navigation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
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
        UniqueConstraint("name", name="uq_site_navigation_name"),
        Index("ix_site_navigation_enabled_order", "enabled", "sort_order", "name"),
        Index("ix_site_navigation_updated", "updated_at"),
    )
