"""ORM model for operational audit logs."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class AuditLogRow(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    user_email: Mapped[str | None] = mapped_column(String(320), index=True)
    user_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(256))
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    client_ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_audit_logs_resource", "resource_type", "resource_id"),
        Index("ix_audit_logs_user_time", "user_id", "created_at"),
    )


class OperationDispositionRow(Base):
    __tablename__ = "operation_dispositions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        index=True,
    )
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    user_email: Mapped[str | None] = mapped_column(String(320), index=True)
    result: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    details_json: Mapped[dict] = mapped_column(JSON, default=dict)

    __table_args__ = (
        Index("ix_operation_dispositions_run_time", "run_id", "created_at"),
        Index("ix_operation_dispositions_thread_time", "thread_id", "created_at"),
    )
