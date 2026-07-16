from datetime import UTC, datetime, timedelta

import pytest

from app.scheduler.service import ScheduledTaskService
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config
from deerflow.persistence.scheduled_task_runs import ScheduledTaskRunRepository
from deerflow.persistence.scheduled_tasks import ScheduledTaskRepository
from deerflow.runtime import RunStatus
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode


@pytest.mark.asyncio
async def test_due_cron_task_runs_once_and_reaches_success_through_real_repositories(tmp_path):
    """Exercise claim -> launch -> bookkeeping -> completion with a real SQL DB."""
    await init_engine_from_config(DatabaseConfig(backend="sqlite", sqlite_dir=str(tmp_path)))
    try:
        session_factory = get_session_factory()
        assert session_factory is not None
        task_repo = ScheduledTaskRepository(session_factory)
        run_repo = ScheduledTaskRunRepository(session_factory)
        launched: list[dict[str, object]] = []

        async def fake_launch(**kwargs):
            launched.append(kwargs)
            return {"run_id": "run-e2e", "thread_id": kwargs["thread_id"]}

        service = ScheduledTaskService(
            task_repo=task_repo,
            task_run_repo=run_repo,
            launch_run=fake_launch,
            poll_interval_seconds=5,
            lease_seconds=120,
            max_concurrent_runs=3,
        )
        now = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
        await task_repo.create(
            task_id="task-e2e",
            user_id="user-e2e",
            thread_id=None,
            context_mode="fresh_thread_per_run",
            assistant_id="lead_agent",
            title="Daily report",
            prompt="Produce the fully specified daily report.",
            schedule_type="cron",
            schedule_spec={"cron": "0 11 * * *"},
            timezone="Asia/Shanghai",
            next_run_at=now - timedelta(minutes=1),
        )

        await service.run_once(now=now)

        assert len(launched) == 1
        launch_metadata = launched[0]["metadata"]
        assert isinstance(launch_metadata, dict)
        task_run_id = launch_metadata["scheduled_task_run_id"]
        task = await task_repo.get("task-e2e", user_id="user-e2e")
        assert task is not None
        assert task["status"] == "enabled"
        assert task["run_count"] == 1
        assert task["next_run_at"] == "2026-07-17T03:00:00+00:00"
        runs = await run_repo.list_by_task("task-e2e")
        assert runs[0]["status"] == "running"
        assert runs[0]["run_id"] == "run-e2e"

        await service.handle_run_completion(
            RunRecord(
                run_id="run-e2e",
                thread_id=str(launched[0]["thread_id"]),
                assistant_id="lead_agent",
                status=RunStatus.success,
                on_disconnect=DisconnectMode.continue_,
                metadata={
                    "scheduled_task_id": "task-e2e",
                    "scheduled_task_run_id": task_run_id,
                },
                user_id="user-e2e",
            )
        )

        runs = await run_repo.list_by_task("task-e2e")
        assert runs[0]["status"] == "success"
        assert runs[0]["finished_at"] is not None

        # The next occurrence is in the future, so another poll at the same
        # instant must not duplicate the run.
        await service.run_once(now=now)
        assert len(launched) == 1
        assert len(await run_repo.list_by_task("task-e2e")) == 1
    finally:
        await close_engine()
