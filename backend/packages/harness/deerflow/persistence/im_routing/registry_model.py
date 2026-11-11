"""ORM model for the S08 IM thread registry.

``im_thread_registry`` is the single source of truth for *which user owns which
IM thread* and *whether that thread belongs to a private chat or a group*.
Titles and ``updated_at`` keep coming from ``threads_meta``; the registry only
adds ownership, classification and an independent ``last_active_at`` sort key.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ImThreadRegistryRow(Base):
    """Ownership + classification of one IM thread."""

    __tablename__ = "im_thread_registry"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="feishu", server_default="feishu")
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    tenant_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    bot_app_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False)
    external_conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_topic_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    source_platform: Mapped[str] = mapped_column(String(16), nullable=False, default="feishu", server_default="feishu")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active", server_default="active")
    classification_source: Mapped[str] = mapped_column(String(24), nullable=False, default="RUNTIME", server_default="RUNTIME")
    title_snapshot: Mapped[str | None] = mapped_column(String(256), nullable=True)
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index(
            "ix_im_thread_registry_owner_scope",
            "owner_user_id",
            "channel_type",
            "external_conversation_id",
            "last_active_at",
        ),
        Index("ix_im_thread_registry_owner_active", "owner_user_id", "last_active_at"),
        Index("ix_im_thread_registry_scope", "external_conversation_id"),
    )
