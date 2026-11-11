"""User-level Feishu identity bindings (S08 D-01).

Fixes the Q1 defect where ``channel_connections.workspace_id`` stored the chat
id, so an owner could only be resolved inside the chat where ``/connect`` ran.
A binding here is scoped to the *user*, not to a chat, and therefore resolves
across every private chat and group of the same Feishu account.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Index, String, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base

_ACTIVE = "ACTIVE"
_REVOKED = "REVOKED"


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ImUserBindingRow(Base):
    """One Feishu identity bound to one DeerFlow Web user."""

    __tablename__ = "im_user_bindings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_new_id)
    tenant_key: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    bot_app_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    feishu_id_type: Mapped[str] = mapped_column(String(16), nullable=False, default="open_id", server_default="open_id")
    feishu_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=_ACTIVE, server_default="ACTIVE")
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="CONNECT_CODE", server_default="CONNECT_CODE")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now)

    __table_args__ = (
        Index("ix_im_user_bindings_owner", "owner_user_id"),
        Index("ix_im_user_bindings_identity", "feishu_user_id"),
        Index(
            "uq_im_user_binding_active",
            "tenant_key",
            "bot_app_id",
            "feishu_id_type",
            "feishu_user_id",
            unique=True,
            sqlite_where=text("status = 'ACTIVE'"),
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )


class ImUserBindingRepository:
    """Persistence for user-level IM identity bindings."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def find_active(
        self,
        *,
        feishu_user_id: str,
        tenant_key: str = "",
        bot_app_id: str = "",
        feishu_id_type: str = "open_id",
    ) -> str | None:
        """Resolve the bound owner.

        Exact ``(tenant_key, bot_app_id, feishu_id_type, feishu_user_id)`` first,
        then a loose ``(bot_app_id, feishu_user_id)`` lookup for bindings created
        before the tenant was recorded. Several distinct owners in the loose
        lookup are ambiguous: return ``None`` instead of guessing.
        """
        if not feishu_user_id:
            return None
        async with self.session_factory() as session:
            exact = (
                await session.execute(
                    select(ImUserBindingRow.owner_user_id)
                    .where(
                        ImUserBindingRow.tenant_key == tenant_key,
                        ImUserBindingRow.bot_app_id == bot_app_id,
                        ImUserBindingRow.feishu_id_type == feishu_id_type,
                        ImUserBindingRow.feishu_user_id == feishu_user_id,
                        ImUserBindingRow.status == _ACTIVE,
                    )
                    .limit(1),
                )
            ).scalar_one_or_none()
            if exact:
                return str(exact)
            owners = (
                (
                    await session.execute(
                        select(ImUserBindingRow.owner_user_id)
                        .where(
                            ImUserBindingRow.bot_app_id == bot_app_id,
                            ImUserBindingRow.feishu_user_id == feishu_user_id,
                            ImUserBindingRow.status == _ACTIVE,
                        )
                        .distinct(),
                    )
                )
                .scalars()
                .all()
            )
        unique_owners = {str(owner) for owner in owners if owner}
        if len(unique_owners) == 1:
            return next(iter(unique_owners))
        return None

    async def upsert_active(
        self,
        *,
        owner_user_id: str,
        feishu_user_id: str,
        tenant_key: str = "",
        bot_app_id: str = "",
        feishu_id_type: str = "open_id",
        source: str = "CONNECT_CODE",
    ) -> None:
        """Bind an identity to ``owner_user_id``, revoking older ACTIVE rows.

        Rebinding to a different owner keeps the previous row as ``REVOKED`` for
        audit instead of deleting it.
        """
        if not owner_user_id or not feishu_user_id:
            return
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(ImUserBindingRow).where(
                            ImUserBindingRow.tenant_key == tenant_key,
                            ImUserBindingRow.bot_app_id == bot_app_id,
                            ImUserBindingRow.feishu_id_type == feishu_id_type,
                            ImUserBindingRow.feishu_user_id == feishu_user_id,
                            ImUserBindingRow.status == _ACTIVE,
                        ),
                    )
                )
                .scalars()
                .all()
            )
            already_bound = next((row for row in rows if row.owner_user_id == owner_user_id), None)
            if already_bound is not None:
                already_bound.source = source
                already_bound.verified_at = now
                already_bound.updated_at = now
                await session.commit()
                return

            if rows:
                # The identity currently belongs to someone else: revoke first
                # so the partial unique index stays satisfiable.
                await session.execute(
                    update(ImUserBindingRow)
                    .where(
                        ImUserBindingRow.tenant_key == tenant_key,
                        ImUserBindingRow.bot_app_id == bot_app_id,
                        ImUserBindingRow.feishu_id_type == feishu_id_type,
                        ImUserBindingRow.feishu_user_id == feishu_user_id,
                        ImUserBindingRow.status == _ACTIVE,
                    )
                    .values(status=_REVOKED, updated_at=now),
                )

            # A user has exactly one active identity per bot: retargeting to a
            # new Feishu account revokes the previous one.
            await session.execute(
                update(ImUserBindingRow)
                .where(
                    ImUserBindingRow.owner_user_id == owner_user_id,
                    ImUserBindingRow.bot_app_id == bot_app_id,
                    ImUserBindingRow.status == _ACTIVE,
                    (ImUserBindingRow.feishu_user_id != feishu_user_id)
                    | (ImUserBindingRow.feishu_id_type != feishu_id_type),
                )
                .values(status=_REVOKED, updated_at=now),
            )

            session.add(
                ImUserBindingRow(
                    id=_new_id(),
                    tenant_key=tenant_key,
                    bot_app_id=bot_app_id,
                    feishu_id_type=feishu_id_type,
                    feishu_user_id=feishu_user_id,
                    owner_user_id=owner_user_id,
                    status=_ACTIVE,
                    source=source,
                    verified_at=now,
                    created_at=now,
                    updated_at=now,
                ),
            )
            try:
                await session.commit()
            except IntegrityError:
                # Lost a concurrent bind race; the winner's row is authoritative.
                await session.rollback()

    async def revoke(
        self,
        *,
        feishu_user_id: str,
        tenant_key: str = "",
        bot_app_id: str = "",
    ) -> None:
        if not feishu_user_id:
            return
        clauses = [
            ImUserBindingRow.feishu_user_id == feishu_user_id,
            ImUserBindingRow.status == _ACTIVE,
        ]
        if bot_app_id:
            clauses.append(ImUserBindingRow.bot_app_id == bot_app_id)
        if tenant_key:
            clauses.append(ImUserBindingRow.tenant_key == tenant_key)
        async with self.session_factory() as session:
            await session.execute(
                update(ImUserBindingRow).where(*clauses).values(status=_REVOKED, updated_at=datetime.now(UTC)),
            )
            await session.commit()