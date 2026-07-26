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

    command.upgrade(config, "head")

    columns = {column["name"]: column for column in inspect(engine).get_columns("scheduled_tasks")}
    with engine.connect() as connection:
        stored = connection.exec_driver_sql("SELECT run_context FROM scheduled_tasks WHERE id = 'task-existing'").scalar_one()
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    assert version == "0010_scheduled_task_run_context"
    assert columns["run_context"]["nullable"] is False
    assert json.loads(stored) == {}
