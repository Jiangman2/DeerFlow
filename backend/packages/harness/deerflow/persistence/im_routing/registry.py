"""Repository for the S08 ``im_thread_registry`` table.

All reads/writes are single statements (or one short transaction) so the table
stays safe under concurrent Gateway workers: registration uses an idempotent
insert, status changes are guard-conditioned updates and ``last_active_at`` is
only moved forward.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from deerflow.persistence.im_routing.registry_model import ImThreadRegistryRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

#: Statuses that still show up in the sidebar tree.
VISIBLE_STATUSES = ("active", "archived")

#: One shared alias for ``threads_meta`` so the display-name filter in
#: :meth:`ImThreadRegistryRepository.search` joins the *same* table instance as
#: the projection instead of adding a second, cartesian FROM element.
_META = aliased(ThreadMetaRow)

_ACTIVE = "active"
_ARCHIVED = "archived"
_DELETED = "deleted"
_ORPHANED = "orphaned"

#: Allowed status transitions. ``deleted``/``orphaned`` are terminal, and a
#: thread is never revived or re-classified.
_ALLOWED_PREVIOUS: dict[str, tuple[str, ...]] = {
    _ACTIVE: (),
    _ARCHIVED: (_ACTIVE,),
    _DELETED: (_ACTIVE, _ARCHIVED),
    _ORPHANED: (_ACTIVE,),
}


def _aware(value: datetime) -> datetime:
    """Return ``value`` as an aware UTC datetime (SQLite drops tzinfo)."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class RegistryThread:
    """A registry row joined with its ``threads_meta`` title."""

    thread_id: str
    owner_user_id: str
    channel_type: str
    external_conversation_id: str
    status: str
    last_active_at: datetime
    title: str | None
    thread_exists: bool


@dataclass(frozen=True)
class GroupAggregate:
    """One group directory entry: how many visible threads and how recent."""

    external_conversation_id: str
    thread_count: int
    last_active_at: datetime


class ImThreadRegistryRepository:
    """Persistence for IM thread ownership and classification."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    # -- writes ------------------------------------------------------------

    async def register(
        self,
        *,
        thread_id: str,
        owner_user_id: str,
        channel_type: str,
        external_conversation_id: str,
        provider: str = "feishu",
        connection_id: str = "",
        tenant_key: str = "",
        bot_app_id: str = "",
        external_topic_id: str = "",
        generation: int = 1,
        status: str = "active",
        classification_source: str = "RUNTIME",
        title_snapshot: str | None = None,
        last_active_at: datetime | None = None,
    ) -> None:
        """Register a thread. Idempotent: an existing row always wins."""
        if not thread_id or not owner_user_id:
            return
        now = datetime.now(UTC)
        at = _aware(last_active_at) if last_active_at is not None else now
        try:
            async with self.session_factory() as session:
                session.add(
                    ImThreadRegistryRow(
                        thread_id=thread_id,
                        provider=provider,
                        connection_id=connection_id,
                        tenant_key=tenant_key,
                        bot_app_id=bot_app_id,
                        owner_user_id=owner_user_id,
                        channel_type=channel_type,
                        external_conversation_id=external_conversation_id,
                        external_topic_id=external_topic_id,
                        source_platform=provider,
                        generation=generation,
                        status=status,
                        classification_source=classification_source,
                        title_snapshot=title_snapshot,
                        last_active_at=at,
                        created_at=now,
                        updated_at=now,
                    ),
                )
                await session.commit()
        except IntegrityError:
            # The thread was already registered (possibly by a racing worker).
            # The first registration is the ownership fact; never overwrite it.
            return

    async def touch(
        self,
        thread_id: str,
        *,
        at: datetime,
        min_interval: timedelta = timedelta(seconds=0),
    ) -> None:
        """Move ``last_active_at`` forward, never backward and never per touch."""
        at = _aware(at)
        cutoff = at - min_interval
        async with self.session_factory() as session:
            await session.execute(
                update(ImThreadRegistryRow)
                .where(
                    ImThreadRegistryRow.thread_id == thread_id,
                    ImThreadRegistryRow.last_active_at < cutoff,
                )
                .values(last_active_at=at, updated_at=at),
            )
            await session.commit()

    async def mark_status(self, thread_id: str, status: str) -> bool:
        """Apply a status transition from the allowed matrix.

        Returns ``True`` when this call performed the transition. ``deleted``
        and ``orphaned`` are terminal, so a second call returns ``False``.
        """
        previous = _ALLOWED_PREVIOUS.get(status)
        if previous is None:
            return False
        now = datetime.now(UTC)
        async with self.session_factory() as session:
            stmt = update(ImThreadRegistryRow).where(ImThreadRegistryRow.thread_id == thread_id)
            if previous:
                stmt = stmt.where(ImThreadRegistryRow.status.in_(previous))
            else:
                # ``active`` can only be reached by a fresh insert.
                stmt = stmt.where(ImThreadRegistryRow.status.is_(None))
            result = await session.execute(stmt.values(status=status, updated_at=now))
            await session.commit()
            return bool(result.rowcount)

    # -- reads -------------------------------------------------------------

    async def get(self, thread_id: str) -> RegistryThread | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(self._select_threads().where(ImThreadRegistryRow.thread_id == thread_id).limit(1))
            ).first()
            return self._to_thread(row) if row is not None else None

    async def list_private(
        self,
        *,
        owner_user_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None = None,
    ) -> list[RegistryThread]:
        async with self.session_factory() as session:
            stmt = (
                self._select_threads()
                .where(
                    ImThreadRegistryRow.owner_user_id == owner_user_id,
                    ImThreadRegistryRow.channel_type == "private",
                    ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
                )
                .order_by(ImThreadRegistryRow.last_active_at.desc(), ImThreadRegistryRow.thread_id.desc())
                .limit(limit)
            )
            if cursor is not None:
                stmt = stmt.where(self._keyset_before(cursor))
            rows = (await session.execute(stmt)).all()
            return [self._to_thread(row) for row in rows]

    async def list_group_threads(
        self,
        *,
        owner_user_id: str,
        external_conversation_id: str,
        limit: int,
        cursor: tuple[datetime, str] | None = None,
    ) -> list[RegistryThread]:
        async with self.session_factory() as session:
            stmt = (
                self._select_threads()
                .where(
                    ImThreadRegistryRow.owner_user_id == owner_user_id,
                    ImThreadRegistryRow.channel_type == "group",
                    ImThreadRegistryRow.external_conversation_id == external_conversation_id,
                    ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
                )
                .order_by(ImThreadRegistryRow.last_active_at.desc(), ImThreadRegistryRow.thread_id.desc())
                .limit(limit)
            )
            if cursor is not None:
                stmt = stmt.where(self._keyset_before(cursor))
            rows = (await session.execute(stmt)).all()
            return [self._to_thread(row) for row in rows]

    async def aggregate_groups(self, *, owner_user_id: str, limit: int, offset: int = 0) -> list[GroupAggregate]:
        last_active = func.max(ImThreadRegistryRow.last_active_at)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(ImThreadRegistryRow.external_conversation_id, func.count(), last_active)
                    .where(
                        ImThreadRegistryRow.owner_user_id == owner_user_id,
                        ImThreadRegistryRow.channel_type == "group",
                        ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
                    )
                    .group_by(ImThreadRegistryRow.external_conversation_id)
                    .order_by(last_active.desc(), ImThreadRegistryRow.external_conversation_id.asc())
                    .limit(limit)
                    .offset(offset),
                )
            ).all()
            return [
                GroupAggregate(
                    external_conversation_id=chat_id,
                    thread_count=int(count),
                    last_active_at=_aware(latest),
                )
                for chat_id, count, latest in rows
            ]

    async def count_private(self, *, owner_user_id: str) -> int:
        """Count every visible private thread, independent of pagination."""
        async with self.session_factory() as session:
            total = await session.scalar(
                select(func.count())
                .select_from(ImThreadRegistryRow)
                .where(
                    ImThreadRegistryRow.owner_user_id == owner_user_id,
                    ImThreadRegistryRow.channel_type == "private",
                    ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
                ),
            )
            return int(total or 0)

    async def count_groups(self, *, owner_user_id: str) -> int:
        """Count distinct groups that own at least one visible thread."""
        async with self.session_factory() as session:
            total = await session.scalar(
                select(func.count(func.distinct(ImThreadRegistryRow.external_conversation_id))).where(
                    ImThreadRegistryRow.owner_user_id == owner_user_id,
                    ImThreadRegistryRow.channel_type == "group",
                    ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
                ),
            )
            return int(total or 0)

    async def first_threads_per_group(
        self,
        *,
        owner_user_id: str,
        chat_ids: Sequence[str],
        per_group: int,
    ) -> dict[str, list[RegistryThread]]:
        """Return the newest ``per_group`` threads for each requested group."""
        if not chat_ids:
            return {}
        ranked = (
            select(
                ImThreadRegistryRow.thread_id.label("thread_id"),
                ImThreadRegistryRow.owner_user_id.label("owner_user_id"),
                ImThreadRegistryRow.channel_type.label("channel_type"),
                ImThreadRegistryRow.external_conversation_id.label("external_conversation_id"),
                ImThreadRegistryRow.status.label("status"),
                ImThreadRegistryRow.last_active_at.label("last_active_at"),
                ImThreadRegistryRow.title_snapshot.label("title_snapshot"),
                func.row_number()
                .over(
                    partition_by=ImThreadRegistryRow.external_conversation_id,
                    order_by=(ImThreadRegistryRow.last_active_at.desc(), ImThreadRegistryRow.thread_id.desc()),
                )
                .label("rn"),
            )
            .where(
                ImThreadRegistryRow.owner_user_id == owner_user_id,
                ImThreadRegistryRow.channel_type == "group",
                ImThreadRegistryRow.external_conversation_id.in_(list(chat_ids)),
                ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
            )
            .subquery()
        )
        meta = aliased(ThreadMetaRow)
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(
                        ranked.c.thread_id,
                        ranked.c.owner_user_id,
                        ranked.c.channel_type,
                        ranked.c.external_conversation_id,
                        ranked.c.status,
                        ranked.c.last_active_at,
                        ranked.c.title_snapshot,
                        meta.display_name,
                        meta.thread_id.label("meta_thread_id"),
                    )
                    .select_from(ranked)
                    .outerjoin(meta, meta.thread_id == ranked.c.thread_id)
                    .where(ranked.c.rn <= per_group)
                    .order_by(
                        ranked.c.external_conversation_id.asc(),
                        ranked.c.last_active_at.desc(),
                        ranked.c.thread_id.desc(),
                    ),
                )
            ).all()
        result: dict[str, list[RegistryThread]] = {}
        for row in rows:
            thread = self._to_thread(row)
            result.setdefault(thread.external_conversation_id, []).append(thread)
        return result

    async def search(self, *, owner_user_id: str, query: str, limit: int, offset: int) -> list[RegistryThread]:
        needle = f"%{query.strip().lower()}%"
        if needle == "%%":
            return []
        group_name = func.lower(func.coalesce(ImThreadRegistryRow.title_snapshot, ""))
        meta_name = func.lower(func.coalesce(_META.display_name, ""))
        from deerflow.persistence.im_routing.catalog import ImGroupCatalogRow

        catalog = aliased(ImGroupCatalogRow)
        catalog_name = func.lower(func.coalesce(catalog.display_name, ""))
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    self._select_threads()
                    .outerjoin(
                        catalog,
                        and_(
                            catalog.chat_id == ImThreadRegistryRow.external_conversation_id,
                            catalog.bot_app_id == ImThreadRegistryRow.bot_app_id,
                        ),
                    )
                    .where(
                        ImThreadRegistryRow.owner_user_id == owner_user_id,
                        ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
                        or_(meta_name.like(needle), group_name.like(needle), catalog_name.like(needle)),
                    )
                    .order_by(ImThreadRegistryRow.last_active_at.desc(), ImThreadRegistryRow.thread_id.desc())
                    .limit(limit)
                    .offset(offset),
                )
            ).all()
            return [self._to_thread(row) for row in rows]

    async def list_stale(self, owner_user_id: str, *, limit: int = 500) -> list[str]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(ImThreadRegistryRow.thread_id)
                    .select_from(ImThreadRegistryRow)
                    .outerjoin(ThreadMetaRow, ThreadMetaRow.thread_id == ImThreadRegistryRow.thread_id)
                    .where(
                        ImThreadRegistryRow.owner_user_id == owner_user_id,
                        ImThreadRegistryRow.status.in_(VISIBLE_STATUSES),
                        ThreadMetaRow.thread_id.is_(None),
                    )
                    .order_by(ImThreadRegistryRow.last_active_at.desc())
                    .limit(limit),
                )
            ).all()
            return [row[0] for row in rows]

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _select_threads():
        return (
            select(
                ImThreadRegistryRow.thread_id,
                ImThreadRegistryRow.owner_user_id,
                ImThreadRegistryRow.channel_type,
                ImThreadRegistryRow.external_conversation_id,
                ImThreadRegistryRow.status,
                ImThreadRegistryRow.last_active_at,
                ImThreadRegistryRow.title_snapshot,
                _META.display_name,
                _META.thread_id.label("meta_thread_id"),
            )
            .select_from(ImThreadRegistryRow)
            .outerjoin(_META, _META.thread_id == ImThreadRegistryRow.thread_id)
        )

    @staticmethod
    def _to_thread(row) -> RegistryThread:
        mapping = row._mapping
        display_name = mapping.get("display_name")
        title = display_name if display_name else mapping.get("title_snapshot")
        return RegistryThread(
            thread_id=mapping["thread_id"],
            owner_user_id=mapping["owner_user_id"],
            channel_type=mapping["channel_type"],
            external_conversation_id=mapping["external_conversation_id"],
            status=mapping["status"],
            last_active_at=_aware(mapping["last_active_at"]),
            title=str(title) if title else None,
            thread_exists=mapping.get("meta_thread_id") is not None,
        )

    @staticmethod
    def _keyset_before(cursor: tuple[datetime, str]):
        at, thread_id = cursor
        return or_(
            ImThreadRegistryRow.last_active_at < _aware(at),
            and_(
                ImThreadRegistryRow.last_active_at == _aware(at),
                ImThreadRegistryRow.thread_id < thread_id,
            ),
        )