from __future__ import annotations

from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config, _get_head_revision


def test_upgrade_backfills_retained_and_deleted_threads(tmp_path) -> None:
    database_path = tmp_path / "thread-creation-ledger.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.upgrade(config, "0008_drop_site_navigation")

    engine = create_engine(f"sqlite:///{database_path}")
    metadata = sa.MetaData()
    threads_meta = sa.Table("threads_meta", metadata, autoload_with=engine)
    runs = sa.Table("runs", metadata, autoload_with=engine)
    retained_created_at = datetime(2026, 7, 20, 8, 0, tzinfo=UTC)
    deleted_first_run_at = retained_created_at + timedelta(hours=1)
    deleted_last_run_at = deleted_first_run_at + timedelta(hours=2)

    with engine.begin() as connection:
        connection.execute(
            threads_meta.insert().values(
                thread_id="retained-thread",
                assistant_id="retained-agent",
                user_id="retained-user",
                display_name="Retained",
                status="idle",
                metadata_json={},
                created_at=retained_created_at,
                updated_at=retained_created_at,
            )
        )
        for run_id, created_at in (
            ("deleted-run-1", deleted_first_run_at),
            ("deleted-run-2", deleted_last_run_at),
        ):
            connection.execute(
                runs.insert().values(
                    run_id=run_id,
                    thread_id="deleted-thread",
                    assistant_id="deleted-agent",
                    user_id="deleted-user",
                    status="success",
                    multitask_strategy="reject",
                    metadata_json={},
                    kwargs_json={},
                    error=None,
                    message_count=0,
                    first_human_message=None,
                    last_ai_message=None,
                    total_input_tokens=0,
                    total_output_tokens=0,
                    total_tokens=0,
                    llm_call_count=0,
                    lead_agent_tokens=0,
                    subagent_tokens=0,
                    middleware_tokens=0,
                    token_usage_by_model={},
                    created_at=created_at,
                    updated_at=created_at,
                )
            )

    command.upgrade(config, "head")

    assert "thread_creations" in inspect(engine).get_table_names()
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                """
                SELECT thread_id, assistant_id, user_id, created_at
                FROM thread_creations
                ORDER BY thread_id
                """
            )
        ).mappings()
        creations = {row["thread_id"]: row for row in rows}
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    assert version == _get_head_revision()
    assert set(creations) == {"deleted-thread", "retained-thread"}
    assert creations["retained-thread"]["assistant_id"] == "retained-agent"
    assert creations["retained-thread"]["user_id"] == "retained-user"
    assert datetime.fromisoformat(creations["retained-thread"]["created_at"]) == retained_created_at.replace(tzinfo=None)
    assert creations["deleted-thread"]["assistant_id"] == "deleted-agent"
    assert creations["deleted-thread"]["user_id"] == "deleted-user"
    assert datetime.fromisoformat(creations["deleted-thread"]["created_at"]) == deleted_first_run_at.replace(tzinfo=None)
