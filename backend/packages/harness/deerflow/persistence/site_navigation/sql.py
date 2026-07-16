"""SQL repository for administrator-managed site navigation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import asc, desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.site_navigation.model import SiteNavigationRow


def _now() -> datetime:
    return datetime.now(UTC)


def _clean_aliases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.split(",")
    else:
        raw = list(value)
    aliases: list[str] = []
    seen: set[str] = set()
    for item in raw:
        alias = str(item).strip()
        key = alias.casefold()
        if alias and key not in seen:
            aliases.append(alias)
            seen.add(key)
    return aliases


def _row_dict(row: SiteNavigationRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "aliases": _clean_aliases(row.aliases),
        "description": row.description or "",
        "category": row.category or "",
        "sort_order": row.sort_order,
        "enabled": row.enabled,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "created_by_user_id": row.created_by_user_id,
        "created_by_email": row.created_by_email,
        "updated_by_user_id": row.updated_by_user_id,
        "updated_by_email": row.updated_by_email,
    }


class SiteNavigationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None):
        self._session_factory = session_factory

    def _require_session_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise RuntimeError("Site navigation persistence is not available")
        return self._session_factory

    async def list_sites(self, *, include_disabled: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        sf = self._require_session_factory()
        stmt = select(SiteNavigationRow).order_by(asc(SiteNavigationRow.sort_order), asc(SiteNavigationRow.name)).limit(limit)
        if not include_disabled:
            stmt = stmt.where(SiteNavigationRow.enabled.is_(True))
        async with sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_dict(row) for row in rows]

    async def get(self, site_id: str) -> dict[str, Any] | None:
        sf = self._require_session_factory()
        async with sf() as session:
            row = await session.get(SiteNavigationRow, site_id)
            return _row_dict(row) if row else None

    async def create(self, values: dict[str, Any]) -> dict[str, Any]:
        sf = self._require_session_factory()
        now = _now()
        row = SiteNavigationRow(
            id=values.get("id") or str(uuid4()),
            name=str(values["name"]).strip(),
            url=str(values["url"]).strip(),
            aliases=_clean_aliases(values.get("aliases")),
            description=str(values.get("description") or "").strip(),
            category=str(values.get("category") or "").strip(),
            sort_order=int(values.get("sort_order") or 0),
            enabled=bool(values.get("enabled", True)),
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

    async def update(self, site_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        sf = self._require_session_factory()
        async with sf() as session:
            row = await session.get(SiteNavigationRow, site_id)
            if row is None:
                return None
            for key, value in values.items():
                if key == "aliases":
                    value = _clean_aliases(value)
                elif key in {"name", "url", "description", "category"} and value is not None:
                    value = str(value).strip()
                setattr(row, key, value)
            row.updated_at = _now()
            await session.commit()
            await session.refresh(row)
        return _row_dict(row)

    async def delete(self, site_id: str) -> bool:
        sf = self._require_session_factory()
        async with sf() as session:
            row = await session.get(SiteNavigationRow, site_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
        return True

    async def find_recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        sf = self._require_session_factory()
        stmt = select(SiteNavigationRow).order_by(desc(SiteNavigationRow.updated_at)).limit(limit)
        async with sf() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_row_dict(row) for row in rows]
