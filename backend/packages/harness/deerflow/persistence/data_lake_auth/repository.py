"""Repository for persisted, one-time Data Lake authentication challenges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .model import DataLakeAuthChallengeRow

CallbackStatus = Literal["accepted", "invalid_or_expired", "replayed", "payload_mismatch"]


@dataclass(frozen=True, slots=True)
class CallbackResolution:
    status: CallbackStatus
    user_id: str | None = None


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class DataLakeAuthChallengeRepository:
    """Store challenges in the shared SQL database for cross-worker callbacks."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def create(self, *, challenge_hash: str, user_id: str, expires_at: datetime) -> None:
        now = datetime.now(UTC)
        async with self._sf() as session:
            # Bound table growth without making expiry depend on a background job.
            await session.execute(delete(DataLakeAuthChallengeRow).where(DataLakeAuthChallengeRow.expires_at < now - timedelta(minutes=5)))
            session.add(
                DataLakeAuthChallengeRow(
                    challenge_hash=challenge_hash,
                    user_id=user_id,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
            await session.commit()

    async def accept_callback(
        self,
        *,
        challenge_hash: str,
        callback_id: str | None,
        callback_timestamp: int | None,
        payload_hash: str,
        now: datetime | None = None,
    ) -> CallbackResolution:
        accepted_at = now or datetime.now(UTC)
        async with self._sf() as session:
            # A conditional UPDATE makes first consumption atomic on both
            # SQLite and Postgres. A SELECT ... FOR UPDATE alone is not enough
            # on SQLite, where row locks are ignored.
            consume = (
                update(DataLakeAuthChallengeRow)
                .where(
                    DataLakeAuthChallengeRow.challenge_hash == challenge_hash,
                    DataLakeAuthChallengeRow.consumed_at.is_(None),
                    DataLakeAuthChallengeRow.expires_at > accepted_at,
                )
                .values(
                    callback_id=callback_id,
                    callback_timestamp=callback_timestamp,
                    callback_payload_hash=payload_hash,
                    consumed_at=accepted_at,
                )
                .returning(DataLakeAuthChallengeRow.user_id)
            )
            consumed_user_id = (await session.execute(consume)).scalar_one_or_none()
            await session.commit()
            if consumed_user_id is not None:
                return CallbackResolution("accepted", consumed_user_id)

            row = (await session.execute(select(DataLakeAuthChallengeRow).where(DataLakeAuthChallengeRow.challenge_hash == challenge_hash))).scalar_one_or_none()
            if row is None:
                return CallbackResolution("invalid_or_expired")
            if row.consumed_at is not None:
                if row.callback_payload_hash == payload_hash:
                    return CallbackResolution("accepted", row.user_id)
                if row.callback_id and callback_id and row.callback_id != callback_id:
                    return CallbackResolution("replayed")
                return CallbackResolution("payload_mismatch")
            if _as_utc(row.expires_at) <= accepted_at:
                return CallbackResolution("invalid_or_expired")
            # A concurrent transaction changed the row between the conditional
            # update and this read. Fail closed instead of accepting twice.
            return CallbackResolution("invalid_or_expired")
