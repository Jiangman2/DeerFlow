"""Group metadata cache for the S08 IM archive.

``im_group_catalog`` only caches a group's display name and lifecycle status.
It never decides whether a group directory exists in the sidebar: that is
derived exclusively from ``im_thread_registry`` rows (S08-15).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, Index, Integer, String, UniqueConstraint, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ImGroupCatalogRow(Base):
    """Cached name/status for one Feishu chat."""

    __tablename__ = "im_group_catalog"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    tenant_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    bot_app_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    chat_id: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    name_source: Mapped[str] = mapped_column(String(16), nullable=False, default="UNKNOWN", server_default="UNKNOWN")
    chat_type: Mapped[str] = mapped_column(String(16), nullable=False, default="", server_default="")
    chat_status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE", server_default="ACTIVE")
    avatar_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    member_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_error: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        UniqueConstraint("tenant_key", "bot_app_id", "chat_id", name="uq_im_group_catalog_scope"),
        Index("ix_im_group_catalog_chat", "chat_id"),
    )


@dataclass(frozen=True)
class GroupMeta:
    """Read model for the group catalog."""

    chat_id: str
    display_name: str | None
    name_source: str
    chat_type: str
    chat_status: str
    member_count: int | None
    last_synced_at: datetime | None
    sync_error: str


class ImGroupCatalogRepository:
    """Persistence for cached group metadata."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def ensure(
        self,
        *,
        chat_id: str,
        provider: str = "feishu",
        tenant_key: str = "",
        bot_app_id: str = "",
        chat_type: str = "",
    ) -> None:
        """Make sure a catalog row exists (idempotent)."""
        if not chat_id:
            return
        now = datetime.now(UTC)
        try:
            async with self.session_factory() as session:
                session.add(
                    ImGroupCatalogRow(
                        id=_new_id(),
                        tenant_key=tenant_key,
                        bot_app_id=bot_app_id,
                        chat_id=chat_id,
                        chat_type=chat_type,
                        created_at=now,
                        updated_at=now,
                    ),
                )
                await session.commit()
        except IntegrityError:
            return

    async def needs_sync(
        self,
        chat_id: str,
        *,
        ttl_seconds: int,
        tenant_key: str = "",
        bot_app_id: str = "",
    ) -> bool:
        """True when the cached row is missing or older than ``ttl_seconds``."""
        async with self.session_factory() as session:
            last_synced = (await session.execute(select(ImGroupCatalogRow.last_synced_at).where(*self._scope(chat_id, tenant_key, bot_app_id)))).scalar_one_or_none()
        if last_synced is None:
            return True
        synced_at = _aware(last_synced)
        assert synced_at is not None
        return synced_at < datetime.now(UTC) - timedelta(seconds=max(ttl_seconds, 0))

    async def try_claim_sync(self, chat_id: str, *, now: datetime, ttl_seconds: int) -> bool:
        """Atomically claim the right to refresh one group's metadata."""
        cutoff = now - timedelta(seconds=max(ttl_seconds, 0))
        async with self.session_factory() as session:
            result = await session.execute(
                update(ImGroupCatalogRow)
                .where(
                    ImGroupCatalogRow.chat_id == chat_id,
                    (ImGroupCatalogRow.last_synced_at.is_(None)) | (ImGroupCatalogRow.last_synced_at < cutoff),
                )
                .values(last_synced_at=now, updated_at=now),
            )
            await session.commit()
            return bool(result.rowcount)

    async def update_meta(
        self,
        *,
        chat_id: str,
        display_name: str | None,
        name_source: str,
        chat_status: str | None = None,
        member_count: int | None = None,
        sync_error: str = "",
        tenant_key: str = "",
        bot_app_id: str = "",
    ) -> None:
        values: dict[str, object] = {
            "display_name": display_name,
            "name_source": name_source,
            "sync_error": sync_error,
            "updated_at": datetime.now(UTC),
        }
        if chat_status is not None:
            values["chat_status"] = chat_status
        if member_count is not None:
            values["member_count"] = member_count
        async with self.session_factory() as session:
            await session.execute(
                update(ImGroupCatalogRow).where(*self._scope(chat_id, tenant_key, bot_app_id)).values(**values),
            )
            await session.commit()

    async def mark_status(
        self,
        chat_id: str,
        chat_status: str,
        *,
        tenant_key: str = "",
        bot_app_id: str = "",
    ) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(ImGroupCatalogRow).where(*self._scope(chat_id, tenant_key, bot_app_id)).values(chat_status=chat_status, updated_at=datetime.now(UTC)),
            )
            await session.commit()

    async def get_many(self, chat_ids: Sequence[str]) -> dict[str, GroupMeta]:
        ids = [chat_id for chat_id in dict.fromkeys(chat_ids) if chat_id]
        if not ids:
            return {}
        async with self.session_factory() as session:
            rows = (await session.execute(select(ImGroupCatalogRow).where(ImGroupCatalogRow.chat_id.in_(ids)))).scalars().all()
        return {
            row.chat_id: GroupMeta(
                chat_id=row.chat_id,
                display_name=row.display_name,
                name_source=row.name_source,
                chat_type=row.chat_type,
                chat_status=row.chat_status,
                member_count=row.member_count,
                last_synced_at=_aware(row.last_synced_at),
                sync_error=row.sync_error,
            )
            for row in rows
        }

    @staticmethod
    def _scope(chat_id: str, tenant_key: str, bot_app_id: str):
        clauses = [ImGroupCatalogRow.chat_id == chat_id]
        if bot_app_id:
            clauses.append(ImGroupCatalogRow.bot_app_id == bot_app_id)
        if tenant_key:
            clauses.append(ImGroupCatalogRow.tenant_key == tenant_key)
        return clauses
