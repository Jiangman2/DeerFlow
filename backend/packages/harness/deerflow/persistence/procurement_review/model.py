from sqlalchemy import JSON, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ProcurementReviewRow(Base):
    __tablename__ = "procurement_review_jobs"
    __table_args__ = (Index("ix_procurement_review_due", "state", "due_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    due_at: Mapped[float] = mapped_column(Float, nullable=False)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[float] = mapped_column(Float, nullable=False, default=0)
