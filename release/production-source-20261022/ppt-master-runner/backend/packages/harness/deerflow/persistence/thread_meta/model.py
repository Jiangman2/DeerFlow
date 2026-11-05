"""ORM model for thread metadata."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ThreadMetaRow(Base):
    __tablename__ = "threads_meta"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assistant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(20), default="idle")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))


class ThreadCreationRow(Base):
    """Append-only creation ledger used for historical dashboard counts."""

    __tablename__ = "thread_creations"
    __table_args__ = (Index("ix_thread_creations_user_time", "user_id", "created_at"),)

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    assistant_id: Mapped[str | None] = mapped_column(String(128), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, default=lambda: datetime.now(UTC))
