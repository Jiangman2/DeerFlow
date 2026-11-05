"""PPT transport state only; official choice payloads remain Skill-owned."""

from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ConfirmationTaskRow(Base):
    __tablename__ = "ppt_confirmation_tasks"

    task_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False)
    auto_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    current_gate_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_ppt_confirmation_task_revision"),
        Index("ix_ppt_confirmation_task_owner_thread", "owner_id", "thread_id", "task_id"),
    )

    def __repr__(self) -> str:
        return "ConfirmationTaskRow(<private>)"


class ConfirmationGateRow(Base):
    __tablename__ = "ppt_confirmation_gates"

    gate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(128), ForeignKey("ppt_confirmation_tasks.task_id"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    due_at: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
    document: Mapped[dict] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        CheckConstraint("revision >= 0", name="ck_ppt_confirmation_gate_revision"),
        Index("ix_ppt_confirmation_gate_task", "task_id", "created_at", "gate_id"),
        Index("ix_ppt_confirmation_gate_due", "state", "due_at", "gate_id"),
    )

    def __repr__(self) -> str:
        return "ConfirmationGateRow(<private>)"
