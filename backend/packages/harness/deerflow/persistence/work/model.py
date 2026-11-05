"""Shared encrypted records. Resource data and user credentials are never SQL plaintext."""

from sqlalchemy import BigInteger, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class WorkRecordRow(Base):
    __tablename__ = "work_integration_records"
    __table_args__ = (Index("ix_work_records_expiry", "namespace", "kind", "expires_at"), Index("ix_work_records_namespace_expiry", "namespace", "expires_at"))

    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(24), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    expires_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self):
        return f"WorkRecordRow(kind={self.kind!r}, status={self.status!r})"
