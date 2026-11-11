import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.scheduler.service import ScheduledTaskService
from app.scheduler.work_notifications import WorkNotificationDispatcher
from deerflow.persistence.base import Base
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.scheduled_task_runs import ScheduledTaskRunRepository
from deerflow.persistence.scheduled_task_runs.model import ScheduledTaskRunRow
from deerflow.persistence.scheduled_tasks import ScheduledTaskRepository
from deerflow.persistence.scheduled_tasks.model import ScheduledTaskRow
from deerflow.persistence.work.model import WorkRecordRow
from deerflow.runtime import RunStatus
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode


@asynccontextmanager
async def _database(tmp_path, backend="sqlite"):
    postgres_url = os.getenv("WORK_NOTIFICATION_TEST_POSTGRES_URL") if backend == "postgres" else None
    if backend == "postgres" and not postgres_url:
        pytest.skip("isolated PostgreSQL notification test database not configured")
    if postgres_url:
        schema = f"notification_qa_{uuid.uuid4().hex}"
        admin_engine = create_async_engine(postgres_url)
        async with admin_engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
        engine = create_async_engine(postgres_url, connect_args={"server_settings": {"search_path": schema}})
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'completion.db'}")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[RunRow.__table__, ScheduledTaskRow.__table__, ScheduledTaskRunRow.__table__, WorkRecordRow.__table__]))
        yield engine, async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
        if postgres_url:
            try:
                async with admin_engine.begin() as connection:
                    await connection.execute(DropSchema(schema, cascade=True))
            finally:
                await admin_engine.dispose()


async def _launching_occurrence(sf, *, schedule_type, trigger="manual", run_context=None):
    now = datetime(2026, 9, 9, 8, tzinfo=UTC)
    tasks = ScheduledTaskRepository(sf)
    occurrences = ScheduledTaskRunRepository(sf)
    spec = {"run_at": now.isoformat()} if schedule_type == "once" else {"cron": "0 9 * * *"}
    await tasks.create(
        task_id="task-completion",
        user_id="user-1",
        thread_id=None,
        context_mode="fresh_thread_per_run",
        assistant_id=None,
        title="completion",
        prompt="test",
        schedule_type=schedule_type,
        schedule_spec=spec,
        timezone="UTC",
        next_run_at=now,
        run_context=run_context,
    )
    await occurrences.create(
        run_record_id="occurrence-completion",
        task_id="task-completion",
        thread_id="thread-completion",
        scheduled_for=now,
        trigger=trigger,
        status="queued",
    )
    claimed = await occurrences.claim_queued_run(
        "occurrence-completion",
        lease_owner="launcher",
        now=now,
        lease_seconds=120,
        global_max_concurrent_runs=3,
    )
    assert claimed is not None
    await tasks.update(
        "task-completion",
        user_id="user-1",
        updates={"status": "running" if schedule_type == "once" else "enabled", "last_error": "previous error"},
    )
    return tasks, occurrences, now


def _service(tasks, occurrences, *, notifications=None):
    return ScheduledTaskService(
        task_repo=tasks,
        task_run_repo=occurrences,
        launch_run=lambda **_kwargs: None,
        poll_interval_seconds=5,
        lease_seconds=120,
        max_concurrent_runs=3,
        work_notifications=notifications,
    )


def _completion(status, error):
    return RunRecord(
        run_id="run-completion",
        thread_id="thread-completion",
        assistant_id=None,
        status=status,
        on_disconnect=DisconnectMode.continue_,
        metadata={"scheduled_task_id": "task-completion", "scheduled_task_run_id": "occurrence-completion"},
        user_id="user-1",
        error=error,
    )


async def _snapshot(sf):
    async with sf() as session:
        task = await session.get(ScheduledTaskRow, "task-completion")
        occurrence = await session.get(ScheduledTaskRunRow, "occurrence-completion")
        assert task is not None and occurrence is not None
        return task.to_dict(), occurrence.to_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize("schedule_type", ["once", "cron"])
@pytest.mark.parametrize(
    ("run_status", "error", "occurrence_status", "once_status", "expected_error"),
    [
        (RunStatus.success, None, "success", "completed", None),
        (RunStatus.error, "boom", "failed", "failed", "boom"),
        (RunStatus.timeout, "time limit", "failed", "failed", "time limit"),
        (RunStatus.interrupted, None, "interrupted", "cancelled", "run was interrupted before completion"),
    ],
)
async def test_completion_before_launch_bookkeeping_is_durable_and_counted_once(tmp_path, schedule_type, run_status, error, occurrence_status, once_status, expected_error):
    async with _database(tmp_path) as (_engine, sf):
        tasks, occurrences, now = await _launching_occurrence(sf, schedule_type=schedule_type)
        service = _service(tasks, occurrences)
        record = _completion(run_status, error)
        await service.handle_run_completion(record)

        task, occurrence = await _snapshot(sf)
        assert task["status"] == (once_status if schedule_type == "once" else "enabled")
        assert task["last_error"] == expected_error
        assert task["last_run_id"] == "run-completion"
        assert task["last_thread_id"] == "thread-completion"
        assert task["run_count"] == 1
        assert occurrence["status"] == occurrence_status
        assert occurrence["error"] == expected_error
        assert occurrence["run_id"] == "run-completion"
        assert occurrence["finished_at"] is not None
        assert occurrence["launch_accounted"] is True
        assert occurrence["lease_owner"] is None
        assert occurrence["lease_expires_at"] is None

        # The late launcher and a repeated delivery must not undo the terminal
        # outcome or count the same launch a second time.
        await tasks.update_after_launch(
            "task-completion",
            task_run_id="occurrence-completion",
            status="running" if schedule_type == "once" else "enabled",
            next_run_at=None if schedule_type == "once" else now + timedelta(hours=1),
            last_run_at=now,
            last_run_id="run-completion",
            last_thread_id="thread-completion",
            last_error=None,
            increment_run_count=True,
            protect_terminal=True,
        )
        task_after_launch, _ = await _snapshot(sf)
        assert task_after_launch["run_count"] == 1
        assert task_after_launch["status"] == task["status"]
        assert task_after_launch["last_error"] == expected_error
        await service.handle_run_completion(record)
        task_after_retry, _ = await _snapshot(sf)
        assert task_after_retry["run_count"] == 1
        assert task_after_retry["status"] == task["status"]
        assert task_after_retry["last_error"] == expected_error


@pytest.mark.asyncio
async def test_completion_commit_failure_rolls_back_outcome_and_accounting(tmp_path):
    async with _database(tmp_path) as (engine, sf):
        tasks, occurrences, _now = await _launching_occurrence(sf, schedule_type="once")
        before = await _snapshot(sf)

        class FailingCommitSession(AsyncSession):
            async def commit(self):
                await self.flush()
                raise RuntimeError("completion commit failed")

        failing_sf = async_sessionmaker(engine, class_=FailingCommitSession, expire_on_commit=False)
        failing_service = _service(ScheduledTaskRepository(failing_sf), occurrences)
        record = _completion(RunStatus.success, None)
        with pytest.raises(RuntimeError, match="completion commit failed"):
            await failing_service.handle_run_completion(record)

        # In particular, failure cannot leave a terminal child that active-run
        # recovery would skip while the parent count still lacks this launch.
        assert await _snapshot(sf) == before
        service = _service(tasks, occurrences)
        await service.handle_run_completion(record)
        await service.handle_run_completion(record)
        task, occurrence = await _snapshot(sf)
        assert task["status"] == "completed"
        assert task["run_count"] == 1
        assert occurrence["status"] == "success"
        assert occurrence["launch_accounted"] is True


_BINDING = {"connection_id": "binding-original", "binding_key": "a" * 64}
_CONTEXT = {"model_name": "test", "work_notification": _BINDING}
_OCCURRENCE = "occurrence-completion"


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["manual", "scheduled"])
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_work_notification_uses_committed_success_and_sends_once_after_restart(tmp_path, trigger, backend):
    async with _database(tmp_path, backend) as (_engine, sf):
        tasks, occurrences, _now = await _launching_occurrence(sf, schedule_type="once", trigger=trigger, run_context=_CONTEXT)
        send = AsyncMock(return_value="sent")
        key = Fernet.generate_key().decode()
        dispatcher = WorkNotificationDispatcher(sf, key=key, send=send, profile="uat")
        service = _service(tasks, occurrences, notifications=dispatcher)
        record = _completion(RunStatus.success, None)
        record.last_ai_message = "  这次执行结果\n" + "补" * 2000
        await service.handle_run_completion(record)
        send.assert_not_awaited()
        task, occurrence = await _snapshot(sf)
        assert task["status"] == "completed" and occurrence["status"] == "success"
        assert await dispatcher.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {_OCCURRENCE: "pending"}
        assert await dispatcher.get_statuses([_OCCURRENCE], owner_user_id="someone-else") == {}
        async with sf() as session:
            ciphertext = await session.scalar(select(WorkRecordRow.ciphertext))
            assert "执行结果" not in ciphertext and _BINDING["connection_id"] not in ciphertext

        await service.handle_run_completion(record)
        await WorkNotificationDispatcher(sf, key=key, send=send).dispatch_once()
        send.assert_not_awaited()
        restarted = WorkNotificationDispatcher(sf, key=key, send=send, profile="uat")
        other_instance = WorkNotificationDispatcher(sf, key=key, send=send, profile="uat")
        await asyncio.gather(restarted.dispatch_once(), other_instance.dispatch_once())
        send.assert_awaited_once()
        notification = send.await_args.args[0]
        assert notification.connection_id == _BINDING["connection_id"] and notification.binding_key == _BINDING["binding_key"]
        assert notification.thread_id == record.thread_id and notification.result_text.startswith("这次执行结果\n") and len(notification.result_text) == 1200
        assert str(uuid.UUID(notification.delivery_id)) == notification.delivery_id
        assert datetime.fromisoformat(notification.finished_at).utcoffset() == timedelta(0)
        assert await restarted.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {_OCCURRENCE: "sent"}
        async with sf() as session:
            assert await session.scalar(select(WorkRecordRow.ciphertext)) == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["failed_run", "wrong_thread", "not_configured", "enqueue_error", "commit_error"])
async def test_work_notification_isolates_failures_and_obeys_completion_transaction(tmp_path, mode):
    async with _database(tmp_path) as (engine, sf):
        tasks, occurrences, _now = await _launching_occurrence(sf, schedule_type="once", run_context=None if mode == "not_configured" else _CONTEXT)
        before = await _snapshot(sf)
        send = AsyncMock(return_value="sent")
        dispatcher = WorkNotificationDispatcher(sf, key=Fernet.generate_key().decode(), send=send)
        record = _completion(RunStatus.error if mode == "failed_run" else RunStatus.success, None)
        if mode == "wrong_thread":
            record.thread_id = "different-thread"
        if mode == "enqueue_error":
            enqueue = dispatcher.enqueue

            async def enqueue_then_fail(*args, **kwargs):
                await enqueue(*args, **kwargs)
                await args[0].flush()
                raise RuntimeError("outbox failure")

            dispatcher.enqueue = enqueue_then_fail
        if mode == "commit_error":

            class FailingCommitSession(AsyncSession):
                async def commit(self):
                    await self.flush()
                    raise RuntimeError("completion commit failed")

            tasks = ScheduledTaskRepository(async_sessionmaker(engine, class_=FailingCommitSession, expire_on_commit=False))

        service = _service(tasks, occurrences, notifications=dispatcher)
        if mode == "commit_error":
            with pytest.raises(RuntimeError, match="completion commit failed"):
                await service.handle_run_completion(record)
            assert await _snapshot(sf) == before
        else:
            await service.handle_run_completion(record)
            task, occurrence = await _snapshot(sf)
            assert occurrence["status"] == ("failed" if mode == "failed_run" else "success")
            assert task["run_count"] == 1
        await dispatcher.dispatch_once()
        send.assert_not_awaited()
        assert await dispatcher.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["failed", "uncertain", "retry", "crash", "expired", "pause", "disable", "rebind", "delete"])
async def test_work_notification_delivery_states_do_not_duplicate_ambiguous_sends(tmp_path, mode):
    async with _database(tmp_path) as (_engine, sf):
        tasks, occurrences, _now = await _launching_occurrence(sf, schedule_type="once", run_context=_CONTEXT)
        send = AsyncMock(return_value=mode)
        key = Fernet.generate_key().decode()
        dispatcher = WorkNotificationDispatcher(sf, key=key, send=send)
        await _service(tasks, occurrences, notifications=dispatcher).handle_run_completion(_completion(RunStatus.success, None))

        if mode == "crash":
            claims = await dispatcher._claim()
            assert len(claims) == 1
            async with sf.begin() as session:
                await session.execute(update(WorkRecordRow).values(updated_at=time.time() - 61))
        if mode == "expired":
            async with sf.begin() as session:
                await session.execute(update(WorkRecordRow).values(expires_at=time.time() + 1000))
        changed = {
            "pause": {"status": "paused"},
            "disable": {"work_notification": None},
            "rebind": {"work_notification": {"connection_id": "other-binding", "binding_key": "b" * 64}},
        }
        if mode in changed:
            await tasks.update("task-completion", user_id="user-1", updates=changed[mode])
        if mode == "delete":
            await tasks.delete("task-completion", user_id="user-1")
        replacement = WorkNotificationDispatcher(sf, key=key, send=send)
        await replacement.dispatch_once()
        await replacement.dispatch_once()

        expected = {"crash": "uncertain", "retry": "pending", "delete": "skipped", **dict.fromkeys(changed, "skipped")}.get(mode, mode)
        assert await replacement.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {_OCCURRENCE: expected}
        assert send.await_count == (0 if mode in {"crash", "expired", "delete", *changed} else 1)
        if mode == "retry":
            previous_id = send.await_args.args[0].delivery_id
            for _ in range(2):
                async with sf.begin() as session:
                    await session.execute(update(WorkRecordRow).values(updated_at=time.time() - 1))
                await replacement.dispatch_once()
            assert send.await_count == 3
            assert all(call.args[0].delivery_id == previous_id for call in send.await_args_list)
            assert await replacement.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {_OCCURRENCE: "failed"}


@pytest.mark.asyncio
async def test_work_notification_settings_merge_without_overwriting_run_context(tmp_path):
    async with _database(tmp_path) as (_engine, sf):
        tasks, _, _now = await _launching_occurrence(sf, schedule_type="once", run_context=_CONTEXT)
        updated = await tasks.update("task-completion", user_id="user-1", updates={"run_context": {"model_name": "new", "work_notification": {"connection_id": "unvalidated"}}})
        assert updated["run_context"] == {"model_name": "new", "work_notification": _BINDING}
        updated = await tasks.update("task-completion", user_id="user-1", updates={"work_notification": None})
        assert updated["run_context"] == {"model_name": "new"}


@pytest.mark.asyncio
async def test_work_notification_remains_current_when_next_recurring_run_claims_a_lease(tmp_path):
    async with _database(tmp_path) as (_engine, sf):
        tasks, occurrences, _now = await _launching_occurrence(sf, schedule_type="cron", run_context=_CONTEXT)
        send = AsyncMock(return_value="sent")
        dispatcher = WorkNotificationDispatcher(sf, key=Fernet.generate_key().decode(), send=send)
        await _service(tasks, occurrences, notifications=dispatcher).handle_run_completion(_completion(RunStatus.success, None))
        await tasks.update("task-completion", user_id="user-1", updates={"status": "running"})
        await dispatcher.dispatch_once()
        send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["peer", "restart", "lease", "foreign_owner"])
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_work_notification_recovery_queues_durable_success_before_late_callback(tmp_path, mode, backend):
    async with _database(tmp_path, backend) as (_engine, sf):
        tasks, occurrences, now = await _launching_occurrence(sf, schedule_type="once", run_context=_CONTEXT)
        send = AsyncMock(return_value="sent")
        dispatcher = WorkNotificationDispatcher(sf, key=Fernet.generate_key().decode(), send=send)
        record = _completion(RunStatus.success, None)
        async with sf.begin() as session:
            session.add(
                RunRow(
                    run_id=record.run_id,
                    thread_id=record.thread_id,
                    user_id="someone-else" if mode == "foreign_owner" else record.user_id,
                    status="success",
                    metadata_json=record.metadata,
                    last_ai_message="恢复时从本次持久记录读取的结果",
                )
            )
        service = _service(tasks, occurrences, notifications=dispatcher)
        recovery_at = now + timedelta(minutes=3)
        if mode == "restart":
            await occurrences.mark_stale_active_runs(error="test restart", on_success=dispatcher.enqueue)
            await tasks.cancel_stuck_once_tasks(error="test restart")
        elif mode == "lease":
            await occurrences.recover_expired_launch_claims(error="test lease", now=recovery_at, on_success=dispatcher.enqueue)
        else:
            await service._reconcile_active_state(now=recovery_at)
        if mode == "foreign_owner":
            assert await dispatcher.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {}
            await dispatcher.dispatch_once()
            send.assert_not_awaited()
            return

        assert await dispatcher.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {_OCCURRENCE: "pending"}
        await service.handle_run_completion(record)
        await dispatcher.dispatch_once()
        send.assert_awaited_once()
        assert send.await_args.args[0].result_text == "恢复时从本次持久记录读取的结果"
        assert await dispatcher.get_statuses([_OCCURRENCE], owner_user_id="user-1") == {_OCCURRENCE: "sent"}
