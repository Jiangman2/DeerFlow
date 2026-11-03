from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class OAPolicyFlowJobRow(Base):
    """One durable OA request and its DeerFlow/callback lifecycle."""

    __tablename__ = "oa_policy_flow_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    owner_user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    assistant_id: Mapped[str] = mapped_column(String(128))
    thread_id: Mapped[str] = mapped_column(String(64), unique=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    callback_status: Mapped[str] = mapped_column(String(24), default="not_ready", index=True)
    document_filename: Mapped[str] = mapped_column(String(255))
    key_fields: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    output_virtual_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)

    process_attempts: Mapped[int] = mapped_column(Integer, default=0)
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_callback_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_callback_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)

    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        Index("ix_oa_policy_flow_jobs_due", "next_attempt_at", "lease_expires_at"),
        Index("ix_oa_policy_flow_jobs_status_callback", "status", "callback_status"),
    )
