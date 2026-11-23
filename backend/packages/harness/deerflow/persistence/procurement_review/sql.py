"""Compare-and-swap leases work on both PostgreSQL and SQLite."""

import time

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import IntegrityError

from .model import ProcurementReviewRow as Row


def as_dict(row):
    return {column.name: getattr(row, column.name) for column in Row.__table__.columns}


class ReviewRepository:
    def __init__(self, session_factory):
        self.sf = session_factory

    async def create(self, *, job_id, request_id, fingerprint, payload):
        now = time.time()
        async with self.sf() as session:
            row = Row(id=job_id, request_id=request_id, fingerprint=fingerprint, payload=payload, state="queued", created_at=now, due_at=now, lease_until=0)
            session.add(row)
            try:
                await session.commit()
                return as_dict(row), True
            except IntegrityError:
                await session.rollback()
                existing = await session.scalar(select(Row).where(Row.request_id == request_id))
                if existing is None:
                    raise
                return as_dict(existing), False

    async def get(self, job_id):
        async with self.sf() as session:
            row = await session.get(Row, job_id)
            return as_dict(row) if row else None

    async def claim(self, owner, seconds, max_active=4):
        now = time.time()
        due = (Row.state.in_(("queued", "launching", "running", "callback")), Row.due_at <= now, Row.lease_until < now)
        async with self.sf() as session:
            # Serialize dispatch capacity decisions across Gateway processes.
            # PostgreSQL's transaction lock is released on commit/disconnect;
            # SQLite's write transaction provides the same single-node fence.
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                await session.execute(text("SELECT pg_advisory_xact_lock(73194620261117)"))
            elif dialect == "sqlite":
                await session.execute(text("BEGIN IMMEDIATE"))
            else:
                raise ValueError("Unsupported review queue database")
            active = await session.scalar(
                select(func.count())
                .select_from(Row)
                .where(
                    or_(
                        Row.state.in_(("launching", "running")),
                        (Row.state == "queued") & (Row.lease_until >= now),
                    )
                )
            )
            if active >= max_active:
                due += (Row.state != "queued",)
            ids = (await session.scalars(select(Row.id).where(*due).order_by(Row.due_at).limit(10))).all()
            for job_id in ids:
                result = await session.execute(update(Row).where(Row.id == job_id, *due).values(lease_owner=owner, lease_until=now + seconds))
                if result.rowcount == 1:
                    await session.commit()
                    return as_dict(await session.get(Row, job_id))
            await session.commit()
        return None

    async def save(self, job, owner, *, release=True, seconds=90):
        now = time.time()
        async with self.sf() as session:
            result = await session.execute(
                update(Row)
                .where(Row.id == job["id"], Row.lease_owner == owner, Row.lease_until > now)
                .values(
                    state=job["state"],
                    payload=job["payload"],
                    due_at=job["due_at"],
                    lease_owner=None if release else owner,
                    lease_until=0 if release else now + seconds,
                )
            )
            await session.commit()
            return result.rowcount == 1

    async def renew(self, job_id, owner, seconds):
        now = time.time()
        async with self.sf() as session:
            result = await session.execute(update(Row).where(Row.id == job_id, Row.lease_owner == owner, Row.lease_until > now).values(lease_until=now + seconds))
            await session.commit()
            return result.rowcount == 1
