"""Public-catalog presentation overrides, independent of private agent definitions."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, select, update
from sqlalchemy.exc import IntegrityError
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


def _item(row):
    return {"key": row.key, "metadata": row.metadata_json, "hidden": row.hidden, "sort_order": row.sort_order, "version": row.version}


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

    async def patch(self, key: str, *, version: int, metadata: dict, updated_by: str, **changes) -> dict:
        """Optimistic compare-and-swap prevents silent lost edits across replicas."""
        async with self.sf() as session:
            row = await session.get(DigitalEmployeeSquareRow, key)
            if (row.version if row else 0) != version:
                raise SquareSettingsConflict(key)
            values = {"metadata_json": {**(row.metadata_json if row else {}), **metadata}, "version": version + 1, "updated_by": updated_by, "updated_at": datetime.now(UTC), **changes}
            if row is None:
                row = DigitalEmployeeSquareRow(key=key, **values)
                session.add(row)
            else:
                result = await session.execute(update(DigitalEmployeeSquareRow).where(DigitalEmployeeSquareRow.key == key, DigitalEmployeeSquareRow.version == version).values(**values))
                if result.rowcount != 1:
                    raise SquareSettingsConflict(key)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise SquareSettingsConflict(key) from exc
            await session.refresh(row)
            return _item(row)
