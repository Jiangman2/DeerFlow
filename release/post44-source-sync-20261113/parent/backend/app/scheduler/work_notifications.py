"""Bounded private notifications for successful scheduled occurrences."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from deerflow.persistence.scheduled_task_runs.projection import WORK_NOTIFICATION_CONTEXT_KEY
from deerflow.persistence.scheduled_tasks.model import ScheduledTaskRow
from deerflow.persistence.work.model import WorkRecordRow

logger = logging.getLogger(__name__)

_KIND = "scheduled_notification"
_POLL_SECONDS = 10
_SEND_SECONDS = 12
_CLAIM_SECONDS = 60
_PENDING_SECONDS = 86400
_RECEIPT_SECONDS = 7 * 86400
_BATCH_SIZE = 4
_SWEEP_LIMIT = 32
_MAX_ATTEMPTS = 3

DeliveryResult = Literal["sent", "failed", "uncertain", "retry"]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class WorkNotification:
    owner_user_id: str
    connection_id: str
    binding_key: str
    task_id: str
    task_run_id: str
    title: str
    result_text: str
    thread_id: str
    delivery_id: str
    finished_at: str = ""
    task_status: str = ""
    once_recovering: bool = False


class WorkNotificationDispatcher:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        key: str,
        send: Callable[[WorkNotification], Awaitable[DeliveryResult]],
        profile: str = "production",
    ) -> None:
        self._session_factory = session_factory
        self._namespace = _hash(f"scheduled-work-notifications:v1:{profile}")
        try:
            raw = base64.b64decode(key, altchars=b"-_", validate=True)
            if len(raw) != 32:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Work notification encryption key must be a base64-encoded 32-byte key") from None
        self._cipher = Fernet(base64.urlsafe_b64encode(hmac.digest(raw, self._namespace.encode(), "sha256")))
        self._send = send
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._next_sweep = 0.0

    def _scope(self):
        return (WorkRecordRow.namespace == self._namespace, WorkRecordRow.kind == _KIND)

    def _record_key(self, key_hash: str):
        return (*self._scope(), WorkRecordRow.key_hash == key_hash)

    async def enqueue(self, session: AsyncSession, task: ScheduledTaskRow, occurrence: ScheduledTaskRunRow, *, result_text: str | None, thread_id: str, recovered: bool = False) -> None:
        if occurrence.thread_id != thread_id:
            return
        binding = (task.run_context or {}).get(WORK_NOTIFICATION_CONTEXT_KEY)
        if not isinstance(binding, dict):
            return
        connection_id = binding.get("connection_id")
        binding_key = binding.get("binding_key")
        if not isinstance(connection_id, str) or not connection_id or not isinstance(binding_key, str) or not binding_key:
            return
        now = time.time()
        finished_at = occurrence.finished_at
        if finished_at is not None and finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=UTC)
        notification = WorkNotification(
            owner_user_id=task.user_id,
            connection_id=connection_id,
            binding_key=binding_key,
            task_id=task.id,
            task_run_id=occurrence.id,
            title=task.title[:200],
            result_text=(result_text or "").strip()[:1200],
            thread_id=occurrence.thread_id,
            delivery_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self._namespace}:{occurrence.id}:{connection_id}:{binding_key}")),
            finished_at=finished_at.isoformat() if finished_at is not None else "",
            task_status="enabled" if task.schedule_type != "once" and task.status == "running" else task.status,
            once_recovering=recovered and task.schedule_type == "once" and task.status == "running",
        )
        payload = {"namespace": self._namespace, **asdict(notification)}
        session.add(
            WorkRecordRow(
                namespace=self._namespace,
                kind=_KIND,
                key_hash=_hash(occurrence.id),
                owner_hash=_hash(task.user_id),
                ciphertext=self._cipher.encrypt(json.dumps(payload, ensure_ascii=False).encode()).decode("ascii"),
                status="pending",
                revision=0,
                updated_at=now,
                expires_at=now + _PENDING_SECONDS + _RECEIPT_SECONDS,
            )
        )

    async def get_statuses(self, task_run_ids: Sequence[str], *, owner_user_id: str) -> dict[str, str]:
        by_hash = {_hash(run_id): run_id for run_id in task_run_ids}
        if not by_hash:
            return {}
        now = time.time()
        async with self._session_factory() as session:
            rows = await session.execute(
                select(WorkRecordRow.key_hash, WorkRecordRow.status, WorkRecordRow.updated_at, WorkRecordRow.expires_at).where(
                    *self._scope(), WorkRecordRow.key_hash.in_(by_hash), WorkRecordRow.owner_hash == _hash(owner_user_id), WorkRecordRow.expires_at > now
                )
            )
            statuses = {}
            for key_hash, status, updated_at, expires_at in rows:
                if status == "sending" and updated_at <= now - _CLAIM_SECONDS:
                    status = "uncertain"
                if status == "pending" and expires_at <= now + _RECEIPT_SECONDS:
                    status = "expired"
                statuses[by_hash[key_hash]] = status
            return statuses

    def _decode(self, row: WorkRecordRow) -> WorkNotification:
        payload = json.loads(self._cipher.decrypt(row.ciphertext.encode("ascii")))
        if payload.pop("namespace") != self._namespace:
            raise ValueError("invalid Work notification namespace")
        notification = WorkNotification(**payload)
        if _hash(notification.owner_user_id) != row.owner_hash or _hash(notification.task_run_id) != row.key_hash:
            raise ValueError("invalid Work notification identity")
        return notification

    async def _claim(self) -> list[tuple[WorkNotification, int]]:
        now = time.time()
        claimed = []
        async with self._session_factory.begin() as session:
            rows = await session.scalars(
                select(WorkRecordRow)
                .where(*self._scope(), WorkRecordRow.status == "pending", WorkRecordRow.updated_at <= now, WorkRecordRow.expires_at > now + _RECEIPT_SECONDS)
                .order_by(WorkRecordRow.expires_at, WorkRecordRow.key_hash)
                .limit(_BATCH_SIZE)
                .with_for_update(skip_locked=True)
            )
            for row in rows:
                revision = row.revision + 1
                result = await session.execute(
                    update(WorkRecordRow)
                    .where(*self._record_key(row.key_hash), WorkRecordRow.status == "pending", WorkRecordRow.revision == row.revision)
                    .values(status="sending", updated_at=now, revision=revision)
                    .execution_options(synchronize_session=False)
                )
                if not result.rowcount:
                    continue
                try:
                    claimed.append((self._decode(row), revision))
                except (InvalidToken, AttributeError, KeyError, TypeError, ValueError):
                    await self._finish(session, row.key_hash, revision, "failed", now=now)
                    logger.warning("Stored scheduled Work notification could not be decoded")
        return claimed

    async def _finish(self, session: AsyncSession, key_hash: str, revision: int, result: DeliveryResult | Literal["skipped"], *, now: float) -> None:
        if result == "retry" and revision < _MAX_ATTEMPTS:
            values = {"status": "pending", "updated_at": now + 30 * revision}
        else:
            values = {"status": "failed" if result == "retry" else result, "updated_at": now, "ciphertext": ""}
        await session.execute(update(WorkRecordRow).where(*self._record_key(key_hash), WorkRecordRow.status == "sending", WorkRecordRow.revision == revision).values(**values).execution_options(synchronize_session=False))

    async def _still_current(self, notification: WorkNotification) -> bool:
        # Single-run recovery can project the durable success to the parent in
        # the following transaction. Both snapshots belong to the same outcome.
        valid_statuses = [notification.task_status]
        if notification.once_recovering:
            valid_statuses.append("completed")
        status_matches = ScheduledTaskRow.status.in_(valid_statuses)
        if notification.task_status == "enabled":
            # A recurring task can hold a dispatch lease for its next run.
            status_matches = or_(status_matches, and_(ScheduledTaskRow.schedule_type != "once", ScheduledTaskRow.status == "running"))
        async with self._session_factory() as session:
            run_context = await session.scalar(
                select(ScheduledTaskRow.run_context)
                .join(ScheduledTaskRunRow, ScheduledTaskRunRow.task_id == ScheduledTaskRow.id)
                .where(
                    ScheduledTaskRow.id == notification.task_id,
                    ScheduledTaskRow.user_id == notification.owner_user_id,
                    status_matches,
                    ScheduledTaskRunRow.id == notification.task_run_id,
                    ScheduledTaskRunRow.thread_id == notification.thread_id,
                    ScheduledTaskRunRow.status == "success",
                )
            )
        binding = (run_context or {}).get(WORK_NOTIFICATION_CONTEXT_KEY)
        return binding == {"connection_id": notification.connection_id, "binding_key": notification.binding_key}

    async def _deliver(self, notification: WorkNotification, revision: int) -> None:
        try:
            still_current = await asyncio.wait_for(self._still_current(notification), timeout=_SEND_SECONDS)
        except Exception:
            result = "retry"
        else:
            if still_current:
                try:
                    result = await asyncio.wait_for(self._send(notification), timeout=_SEND_SECONDS)
                except Exception:
                    # Once the send has begun an ambiguous outcome is never retried.
                    result = "uncertain"
                if result not in {"sent", "failed", "uncertain", "retry"}:
                    result = "uncertain"
            else:
                result = "skipped"
        async with self._session_factory.begin() as session:
            await self._finish(session, _hash(notification.task_run_id), revision, result, now=time.time())

    async def _sweep(self, now: float) -> None:
        async with self._session_factory.begin() as session:
            for current_status, new_status, deadline in (
                ("sending", "uncertain", WorkRecordRow.updated_at <= now - _CLAIM_SECONDS),
                ("pending", "expired", WorkRecordRow.expires_at <= now + _RECEIPT_SECONDS),
            ):
                condition = (*self._scope(), WorkRecordRow.status == current_status, deadline)
                keys = select(WorkRecordRow.key_hash).where(*condition).limit(_SWEEP_LIMIT).with_for_update(skip_locked=True)
                await session.execute(update(WorkRecordRow).where(*condition, WorkRecordRow.key_hash.in_(keys)).values(status=new_status, updated_at=now, ciphertext=""))
            expired = select(WorkRecordRow.key_hash).where(*self._scope(), WorkRecordRow.expires_at <= now).limit(_SWEEP_LIMIT).with_for_update(skip_locked=True)
            await session.execute(delete(WorkRecordRow).where(*self._scope(), WorkRecordRow.key_hash.in_(expired), WorkRecordRow.expires_at <= now))

    async def dispatch_once(self) -> None:
        now = time.time()
        if now >= self._next_sweep:
            await self._sweep(now)
            self._next_sweep = now + 60
        claimed = await self._claim()
        outcomes = await asyncio.gather(*(self._deliver(notification, revision) for notification, revision in claimed), return_exceptions=True)
        if any(isinstance(outcome, Exception) for outcome in outcomes):
            logger.warning("Scheduled Work notification outcome could not be persisted")

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _run_loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.dispatch_once()
            except Exception:
                logger.warning("Scheduled Work notification dispatcher will retry polling")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=_POLL_SECONDS)
            except TimeoutError:
                pass
