"""Parent projection and launch accounting under the scheduled-task row lock."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

from sqlalchemy.orm.attributes import flag_modified

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
    from deerflow.persistence.scheduled_tasks.model import ScheduledTaskRow

logger = logging.getLogger(__name__)

WORK_NOTIFICATION_CONTEXT_KEY = "work_notification"


class ScheduledWorkNotificationHook(Protocol):
    async def __call__(
        self,
        session: AsyncSession,
        task: ScheduledTaskRow,
        occurrence: ScheduledTaskRunRow,
        *,
        result_text: str | None,
        thread_id: str,
        recovered: bool = False,
    ) -> None: ...


async def enqueue_work_notification_in_completion(
    session: AsyncSession,
    task: ScheduledTaskRow,
    occurrence: ScheduledTaskRunRow,
    enqueue: Callable[[AsyncSession, ScheduledTaskRow, ScheduledTaskRunRow], Awaitable[None]],
) -> None:
    if not (task.run_context or {}).get(WORK_NOTIFICATION_CONTEXT_KEY):
        return
    # Flush scheduled-task bookkeeping first so its own failures aren't
    # suppressed. The optional outbox joins this transaction on success.
    await session.flush()
    try:
        async with session.begin_nested():
            await enqueue(session, task, occurrence)
    except Exception:
        logger.warning("Scheduled Work notification enqueue failed")


def can_project(task: ScheduledTaskRow, occurrence: ScheduledTaskRunRow) -> bool:
    """Keep unsequenced history best-effort until a sequenced run is admitted."""
    if occurrence.occurrence_seq is None:
        return task.last_occurrence_seq == 0
    return occurrence.occurrence_seq == task.last_occurrence_seq


def account_launch(task: ScheduledTaskRow, occurrence: ScheduledTaskRunRow, run_id: str) -> bool:
    """Count a proven launch once, in the same transaction as its marker.

    Migrated NULL markers retain the old last_run_id inference for their first
    repair; historical accounting cannot be reconstructed from occurrence times.
    """
    if occurrence.launch_accounted is True:
        return False
    legacy = occurrence.launch_accounted is None
    occurrence.launch_accounted = True
    if legacy and task.last_run_id == run_id:
        return False
    task.run_count += 1
    # A stale occurrence may change the count, but not the current projection's
    # timestamp. Callers that also project a result explicitly set updated_at.
    flag_modified(task, "updated_at")
    return True
