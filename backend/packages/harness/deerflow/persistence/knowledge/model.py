from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from deerflow.persistence.base import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeFolderRow(Base):
    __tablename__ = "knowledge_folders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        UniqueConstraint("user_id", "path", name="uq_knowledge_folders_user_path"),
        Index("ix_knowledge_folders_user_parent", "user_id", "parent_path"),
    )


class KnowledgeFileRow(Base):
    __tablename__ = "knowledge_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    folder_path: Mapped[str] = mapped_column(Text, nullable=False, default="")
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="indexed")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    markdown_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now, onupdate=_utc_now)

    chunks: Mapped[list[KnowledgeChunkRow]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "sha256", "folder_path", name="uq_knowledge_files_user_sha256_folder"),
        Index("ix_knowledge_files_user_status", "user_id", "status"),
        Index("ix_knowledge_files_user_folder", "user_id", "folder_path"),
    )


class KnowledgeChunkRow(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    file_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("knowledge_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    heading: Mapped[str | None] = mapped_column(String(512), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    file: Mapped[KnowledgeFileRow] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("file_id", "chunk_index", name="uq_knowledge_chunks_file_index"),
        Index("ix_knowledge_chunks_user_file", "user_id", "file_id"),
        Index("ix_knowledge_chunks_user_embedding", "user_id", "embedding_model", "embedding_dim"),
    )
