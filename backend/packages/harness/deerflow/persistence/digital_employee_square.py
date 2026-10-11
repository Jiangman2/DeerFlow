"""Public-catalog presentation overrides, independent of private agent definitions."""

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, select, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class DigitalEmployeeSquareRow(Base):
    __tablename__ = "digital_employee_square"

    key: Mapped[str] = mapped_column(String(256), primary_key=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SquareSettingsConflict(Exception):
    """An administrator edited an out-of-date catalog entry."""


class SquareSettingsBusy(Exception):
    """A catalog writer could not obtain the cooperative database lock."""


def _item(row):
    return {"key": row.key, "metadata": row.metadata_json, "hidden": row.hidden, "sort_order": row.sort_order, "version": row.version}


class _SquareEdit:
    """Transaction-bound operations; callers must not recursively acquire edit()."""

    def __init__(self, session, key):
        self.session = session
        self.key = key

    async def get(self):
        row = await self.session.get(DigitalEmployeeSquareRow, self.key)
        return _item(row) if row is not None else None

    async def patch(self, *, version: int, metadata: dict, updated_by: str, **changes):
        row = await self.session.get(DigitalEmployeeSquareRow, self.key)
        if (row.version if row else 0) != version:
            raise SquareSettingsConflict(self.key)
        values = {"metadata_json": {**(row.metadata_json if row else {}), **metadata}, "version": version + 1, "updated_by": updated_by, "updated_at": datetime.now(UTC), **changes}
        if row is None:
            row = DigitalEmployeeSquareRow(key=self.key, **values)
            self.session.add(row)
        else:
            result = await self.session.execute(update(DigitalEmployeeSquareRow).where(DigitalEmployeeSquareRow.key == self.key, DigitalEmployeeSquareRow.version == version).values(**values))
            if result.rowcount != 1:
                raise SquareSettingsConflict(self.key)
        await self.session.flush()
        await self.session.refresh(row)
        return _item(row)


class DigitalEmployeeSquareRepository:
    def __init__(self, session_factory):
        self.sf = session_factory

    async def list(self) -> list[dict]:
        async with self.sf() as session:
            rows = (await session.execute(select(DigitalEmployeeSquareRow).order_by(DigitalEmployeeSquareRow.key))).scalars().all()
            return [_item(row) for row in rows]

    async def get(self, key: str) -> dict | None:
        async with self.sf() as session:
            row = await session.get(DigitalEmployeeSquareRow, key)
            return _item(row) if row is not None else None

    @asynccontextmanager
    async def edit(self, key: str):
        """Serialize permission reads, publication IO and status edits across Pods.

        PostgreSQL transaction advisory locks also cover absent settings rows.
        SQLite reserves the database writer before reading. This is cooperative
        serialization, not a distributed transaction with the shared filesystem.
        """
        async with self.sf() as session:
            dialect = session.get_bind().dialect.name
            try:
                async with session.begin():
                    if dialect == "postgresql":
                        lock_id = int.from_bytes(hashlib.sha256(("deerflow.square:" + key).encode()).digest()[:8], "big", signed=True)
                        deadline = time.monotonic() + 5.0
                        while not (await session.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_id})).scalar_one():
                            if time.monotonic() >= deadline:
                                raise SquareSettingsBusy(key)
                            await asyncio.sleep(0.05)
                    elif dialect == "sqlite":
                        # BEGIN (deferred) is insufficient: two readers may both
                        # approve a publication before either claims the writer.
                        await session.execute(text("BEGIN IMMEDIATE"))
                    else:
                        raise SquareSettingsBusy("Unsupported square locking dialect")
                    yield _SquareEdit(session, key)
            except IntegrityError as exc:
                raise SquareSettingsConflict(key) from exc
            except OperationalError as exc:
                if dialect == "sqlite" and (getattr(exc.orig, "sqlite_errorcode", 0) & 255) in {5, 6}:
                    raise SquareSettingsBusy(key) from exc
                raise

    async def patch(self, key: str, *, version: int, metadata: dict, updated_by: str, **changes) -> dict:
        """CAS plus the same cross-Pod lock used by snapshot publication."""
        async with self.edit(key) as edit:
            return await edit.patch(version=version, metadata=metadata, updated_by=updated_by, **changes)
