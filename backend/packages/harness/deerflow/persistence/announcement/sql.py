"""SQL repository for workspace announcements."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, desc, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.announcement.model import AnnouncementRow


def _now() -> datetime:
    return datetime.now(UTC)


def _row_dict(row: AnnouncementRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "content": row.content,
        "severity": row.severity,
        "status": row.status,
        "dismissible": row.dismissible,
        "starts_at": row.starts_at.isoformat() if row.starts_at else None,
        "ends_at": row.ends_at.isoformat() if row.ends_at else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "created_by_user_id": row.created_by_user_id,
        "created_by_email": row.created_by_email,
        "updated_by_user_id": row.updated_by_user_id,
        "updated_by_email": row.updated_by_email,
    }


class AnnouncementRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None):
        self._session_factory = session_factory

    def _require_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Announcement persistence is not available")
        return self._session_factory

    async def list_announcements(self, *, limit: int = 100, include_archived: bool = True) -> list[dict[str, Any]]:
        sf = self._require_session_factory()
        stmt = select(AnnouncementRow).order_by(desc(AnnouncementRow.updated_at)).limit(limit)
        if not include_archived:
            stmt = stmt.where(AnnouncementRow.status != "archived")
        async with sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_dict(row) for row in rows]

    async def get(self, announcement_id: str) -> dict[str, Any] | None:
        sf = self._require_session_factory()
        async with sf() as session:
            row = await session.get(AnnouncementRow, announcement_id)
            return _row_dict(row) if row else None

    async def get_active(self, *, at: datetime | None = None) -> dict[str, Any] | None:
        sf = self._require_session_factory()
        at = at or _now()
        stmt = (
            select(AnnouncementRow)
            .where(
                AnnouncementRow.status == "published",
                or_(AnnouncementRow.starts_at.is_(None), AnnouncementRow.starts_at <= at),
                or_(AnnouncementRow.ends_at.is_(None), AnnouncementRow.ends_at > at),
            )
            .order_by(desc(AnnouncementRow.starts_at), desc(AnnouncementRow.updated_at))
            .limit(1)
        )
        async with sf() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
            return _row_dict(row) if row else None

    async def create(self, values: dict[str, Any]) -> dict[str, Any]:
        sf = self._require_session_factory()
        now = _now()
        row = AnnouncementRow(
            id=values.get("id") or str(uuid4()),
            title=values["title"],
            content=values["content"],
            severity=values.get("severity") or "info",
            status=values.get("status") or "draft",
            dismissible=values.get("dismissible", True),
            starts_at=values.get("starts_at"),
            ends_at=values.get("ends_at"),
            created_at=now,
            updated_at=now,
            created_by_user_id=values.get("created_by_user_id"),
            created_by_email=values.get("created_by_email"),
            updated_by_user_id=values.get("updated_by_user_id"),
            updated_by_email=values.get("updated_by_email"),
        )
        async with sf() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _row_dict(row)

    async def update(self, announcement_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        sf = self._require_session_factory()
        async with sf() as session:
            row = await session.get(AnnouncementRow, announcement_id)
            if row is None:
                return None
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
        return _row_dict(row)

    async def set_status(
        self,
        announcement_id: str,
        *,
        status: str,
        updated_by_user_id: str | None = None,
        updated_by_email: str | None = None,
    ) -> dict[str, Any] | None:
        return await self.update(
            announcement_id,
            {
                "status": status,
                "updated_by_user_id": updated_by_user_id,
                "updated_by_email": updated_by_email,
            },
        )

    async def find_overlapping_published(self, *, starts_at: datetime | None, ends_at: datetime | None, exclude_id: str | None = None) -> list[str]:
        sf = self._require_session_factory()
        starts_filter = true() if starts_at is None else or_(AnnouncementRow.ends_at.is_(None), AnnouncementRow.ends_at > starts_at)
        ends_filter = true() if ends_at is None else or_(AnnouncementRow.starts_at.is_(None), AnnouncementRow.starts_at < ends_at)
        filters = [AnnouncementRow.status == "published", starts_filter, ends_filter]
        if exclude_id:
            filters.append(AnnouncementRow.id != exclude_id)
        stmt = select(AnnouncementRow.id).where(and_(*filters)).limit(5)
        async with sf() as session:
            return list((await session.execute(stmt)).scalars().all())
