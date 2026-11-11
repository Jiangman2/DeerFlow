"""ORM models for IM session routing (pointers, message links, event inbox).

These tables back the V2 routing contract described in
``1-寮€鍙戞枃妗?椋炰功 脳 DeerFlow 浼佷笟绾у垎缇や細璇濊矾鐢辩郴缁?md``:

- ``im_session_pointers`` 鈥?the *current* DeerFlow thread for one IM routing
  key ``(connection, owner, channel_type, chat_id, topic_id)``. ``generation``
  is a monotonic counter used for compare-and-swap updates, so ``/new`` and
  concurrent readers never clobber each other across processes.
- ``im_message_links`` 鈥?maps platform message ids (inbound user messages and
  outbound bot messages/cards) to the DeerFlow thread that produced or
  consumed them. This is what makes "reply to an old bot message restores
  that conversation" verifiable instead of guessed.
- ``im_event_inbox`` 鈥?durable per-message idempotency claim. A repeated
  Feishu delivery of the same ``message_id`` (``/new`` included) is rejected
  by the unique key instead of by an in-process cache.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ImSessionPointerRow(Base):
    """Current thread pointer for one IM routing key."""

    __tablename__ = "im_session_pointers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Empty string when the deployment still uses the legacy (non
    # connection-scoped) channel store; the unique key keeps both scopes apart.
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="", index=True)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # "private" | "group" 鈥?the routing key's channel dimension.
    channel_type: Mapped[str] = mapped_column(String(16), nullable=False, default="group", server_default="group")
    external_conversation_id: Mapped[str] = mapped_column(String(128), nullable=False)
    external_topic_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Monotonic pointer version. Every successful switch (/new) bumps it, and a
    # writer must present the generation it read to update the row.
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        UniqueConstraint(
            "connection_id",
            "owner_user_id",
            "channel_type",
            "external_conversation_id",
            "external_topic_id",
            name="uq_im_session_pointer_scope",
        ),
    )


class ImMessageLinkRow(Base):
    """Platform message id -> DeerFlow thread mapping."""

    __tablename__ = "im_message_links"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_conversation_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(128), nullable=False)
    # "inbound" (user -> agent) or "outbound" (agent -> user).
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    message_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="", server_default="")
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sender_external_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    root_message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    parent_message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    task_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)

    __table_args__ = (
        # A platform message id is globally unique per provider (Feishu message
        # ids include the tenant namespace), so the same id can never point at
        # two threads.
        UniqueConstraint("provider", "message_id", name="uq_im_message_link_provider_message"),
        Index("idx_im_message_links_thread", "thread_id", "created_at"),
    )


class ImEventInboxRow(Base):
    """Durable idempotency claim for one inbound platform event."""

    __tablename__ = "im_event_inbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Logical message key: ``<provider>:<chat_id>:<message_id>``. Repeated
    # deliveries (Feishu retries, multi-instance fan-out) collide here.
    event_key: Mapped[str] = mapped_column(String(255), nullable=False)
    connection_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    external_conversation_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    message_id: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    # RECEIVED | PROCESSING | COMPLETED | FAILED | REJECTED
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECEIVED", server_default="RECEIVED")
    route_reason: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (UniqueConstraint("provider", "event_key", name="uq_im_event_inbox_key"),)
