"""ORM model for short-lived Data Lake authentication challenges."""

from datetime import UTC, datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DataLakeAuthChallengeRow(Base):
    """A user-bound, one-time challenge stored only as a SHA-256 digest."""

    __tablename__ = "data_lake_auth_challenges"

    challenge_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    callback_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    callback_timestamp: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    callback_payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
