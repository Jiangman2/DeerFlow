from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


def test_upgrade_adds_empty_run_context_to_existing_scheduled_tasks(tmp_path) -> None:
    database_path = tmp_path / "scheduled-task-run-context.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.upgrade(config, "0009_thread_creation_ledger")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO scheduled_tasks (
                    id, user_id, thread_id, context_mode, assistant_id,
                    title, prompt, schedule_type, schedule_spec, timezone,
                    status, overlap_policy, run_count, created_at, updated_at
                ) VALUES (
                    'task-existing', 'user-1', NULL, 'fresh_thread_per_run',
                    'lead_agent', 'Existing task', 'Run me', 'cron',
                    '{"cron":"0 9 * * *"}', 'UTC', 'enabled', 'skip', 0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO scheduled_task_runs (
                    id, task_id, thread_id, run_id, scheduled_for, trigger,
                    status, error, started_at, finished_at, created_at
                ) VALUES (
                    'task-run-existing', 'task-existing', 'thread-existing', NULL,
                    CURRENT_TIMESTAMP, 'scheduled', 'queued', NULL, NULL, NULL,
                    CURRENT_TIMESTAMP
                )
                """
            )
        )

    command.upgrade(config, "head")

    task_columns = {column["name"]: column for column in inspect(engine).get_columns("scheduled_tasks")}
    run_columns = {column["name"]: column for column in inspect(engine).get_columns("scheduled_task_runs")}
    run_indexes = {index["name"] for index in inspect(engine).get_indexes("scheduled_task_runs")}
    with engine.connect() as connection:
        stored = connection.exec_driver_sql("SELECT run_context, overlap_policy, last_occurrence_seq FROM scheduled_tasks WHERE id = 'task-existing'").one()
        migrated_run = connection.exec_driver_sql("SELECT status, attempt_count, occurrence_seq, launch_accounted FROM scheduled_task_runs WHERE id = 'task-run-existing'").one()
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    assert version == "0021_thread_operation_kind"
    assert task_columns["run_context"]["nullable"] is False
    assert json.loads(stored.run_context) == {}
    assert stored.overlap_policy == "enqueue"
    assert stored.last_occurrence_seq == 0
    assert {"lease_owner", "lease_expires_at", "attempt_count", "occurrence_seq", "launch_accounted"} <= set(run_columns)
    assert migrated_run.status == "interrupted"
    assert migrated_run.attempt_count == 0
    assert migrated_run.occurrence_seq is None
    assert migrated_run.launch_accounted is None
    assert "uq_scheduled_task_run_active" in run_indexes
    assert "uq_scheduled_task_run_occurrence_seq" in run_indexes
