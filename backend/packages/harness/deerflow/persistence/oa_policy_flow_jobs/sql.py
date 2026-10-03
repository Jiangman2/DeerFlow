from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.oa_policy_flow_jobs.model import OAPolicyFlowJobRow
from deerflow.utils.time import coerce_iso

_PROCESSABLE_STATUSES = ("queued", "launching", "running")


class OAPolicyFlowJobRepository:
    """SQL repository with portable, conditional leases for multi-worker safety."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _row_to_dict(row: OAPolicyFlowJobRow) -> dict[str, Any]:
        data = row.to_dict()
        for key in (
            "next_attempt_at",
            "lease_expires_at",
            "prepared_at",
            "completed_at",
            "delivered_at",
            "created_at",
            "updated_at",
        ):
            if data.get(key) is not None:
                data[key] = coerce_iso(data[key])
        return data

    async def create_or_get(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Create a job, or return the winner of an idempotency-key race."""

        row = OAPolicyFlowJobRow(**values)
        async with self._sf() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                stmt = select(OAPolicyFlowJobRow).where(OAPolicyFlowJobRow.idempotency_key_hash == values["idempotency_key_hash"])
                existing = (await session.execute(stmt)).scalar_one_or_none()
                if existing is None:
                    raise
                return self._row_to_dict(existing), False
            await session.refresh(row)
            return self._row_to_dict(row), True

    async def get(self, job_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(OAPolicyFlowJobRow, job_id)
            return self._row_to_dict(row) if row is not None else None

    async def get_by_idempotency_hash(self, key_hash: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = (await session.execute(select(OAPolicyFlowJobRow).where(OAPolicyFlowJobRow.idempotency_key_hash == key_hash))).scalar_one_or_none()
            return self._row_to_dict(row) if row is not None else None

    async def claim_due(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        limit: int,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Atomically lease due process or callback work across Gateway workers."""

        now = now or datetime.now(UTC)
        expires_at = now + timedelta(seconds=lease_seconds)
        due = and_(
            OAPolicyFlowJobRow.next_attempt_at.is_not(None),
            OAPolicyFlowJobRow.next_attempt_at <= now,
            or_(
                OAPolicyFlowJobRow.lease_expires_at.is_(None),
                OAPolicyFlowJobRow.lease_expires_at <= now,
            ),
            or_(
                OAPolicyFlowJobRow.status.in_(_PROCESSABLE_STATUSES),
                OAPolicyFlowJobRow.callback_status == "pending",
            ),
        )
        async with self._sf() as session:
            ids = list((await session.execute(select(OAPolicyFlowJobRow.id).where(due).order_by(OAPolicyFlowJobRow.next_attempt_at, OAPolicyFlowJobRow.created_at).limit(limit))).scalars())
            claimed_ids: list[str] = []
            for job_id in ids:
                result = await session.execute(
                    update(OAPolicyFlowJobRow)
                    .where(
                        OAPolicyFlowJobRow.id == job_id,
                        or_(
                            OAPolicyFlowJobRow.lease_expires_at.is_(None),
                            OAPolicyFlowJobRow.lease_expires_at <= now,
                        ),
                    )
                    .values(
                        lease_owner=worker_id,
                        lease_expires_at=expires_at,
                        updated_at=now,
                    )
                )
                if result.rowcount == 1:
                    claimed_ids.append(job_id)
            await session.commit()
            if not claimed_ids:
                return []
            rows = (await session.execute(select(OAPolicyFlowJobRow).where(OAPolicyFlowJobRow.id.in_(claimed_ids)).order_by(OAPolicyFlowJobRow.created_at))).scalars()
            return [self._row_to_dict(row) for row in rows]

    async def patch(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
        clear_lease: bool = True,
        **values: Any,
    ) -> bool:
        values["updated_at"] = datetime.now(UTC)
        if clear_lease:
            values["lease_owner"] = None
            values["lease_expires_at"] = None
        stmt = update(OAPolicyFlowJobRow).where(OAPolicyFlowJobRow.id == job_id)
        if lease_owner is not None:
            stmt = stmt.where(OAPolicyFlowJobRow.lease_owner == lease_owner)
        async with self._sf() as session:
            result = await session.execute(stmt.values(**values))
            await session.commit()
            return result.rowcount == 1

    async def renew_lease(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        async with self._sf() as session:
            result = await session.execute(
                update(OAPolicyFlowJobRow)
                .where(
                    OAPolicyFlowJobRow.id == job_id,
                    OAPolicyFlowJobRow.lease_owner == lease_owner,
                )
                .values(
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            await session.commit()
            return result.rowcount == 1
