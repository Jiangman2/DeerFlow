"""Existing enterprise records survive additive cancellation-state migration."""

import asyncio
import importlib
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config

PREVIOUS = "0019_digital_employee_agents"
REVISION = "0020_run_cancel_request"


@pytest.fixture
def existing_database(tmp_path):
    path = tmp_path / "existing-employees.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    engine = sa.create_engine(f"sqlite:///{path}")
    config = _get_alembic_config(async_engine)
    try:
        command.upgrade(config, PREVIOUS)
        metadata = sa.MetaData()
        tables = {name: sa.Table(name, metadata, autoload_with=engine) for name in ("agents", "threads_meta", "runs")}
        now = datetime(2026, 9, 24, tzinfo=UTC)
        with engine.begin() as connection:
            for suffix, status in (("a", "running"), ("b", "success")):
                connection.execute(
                    tables["agents"]
                    .insert()
                    .values(
                        id="employee-" + suffix,
                        user_id="user-" + suffix,
                        name="保留的数字员工",
                        config={"skills": ["private-skill-" + suffix], "unknown_enterprise_field": {"keep": True}},
                        soul="现有中文职责：不能丢失",
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    tables["threads_meta"]
                    .insert()
                    .values(
                        thread_id="thread-" + suffix,
                        assistant_id="employee-" + suffix,
                        user_id="user-" + suffix,
                        display_name="已有对话",
                        status="idle",
                        metadata_json={"keep": "线程信息"},
                        created_at=now,
                        updated_at=now,
                    )
                )
                connection.execute(
                    tables["runs"]
                    .insert()
                    .values(
                        run_id="run-" + suffix,
                        thread_id="thread-" + suffix,
                        assistant_id="employee-" + suffix,
                        user_id="user-" + suffix,
                        status=status,
                        multitask_strategy="reject",
                        metadata_json={"keep": "运行信息"},
                        kwargs_json={"keep": "请求内容"},
                        message_count=3,
                        total_input_tokens=4,
                        total_output_tokens=5,
                        total_tokens=9,
                        llm_call_count=1,
                        lead_agent_tokens=9,
                        subagent_tokens=0,
                        middleware_tokens=0,
                        token_usage_by_model={},
                        created_at=now,
                        updated_at=now,
                    )
                )
        yield config, engine, tables
    finally:
        engine.dispose()
        asyncio.run(async_engine.dispose())


def snapshot(engine, tables):
    with engine.connect() as connection:
        return {name: [dict(row) for row in connection.execute(sa.select(table).order_by(*table.primary_key.columns)).mappings()] for name, table in tables.items()}


def test_additive_upgrade_and_repeat_preserve_existing_records(existing_database):
    config, engine, tables = existing_database
    before = snapshot(engine, tables)
    command.upgrade(config, REVISION)
    command.upgrade(config, REVISION)
    # Exercise the helper too; Alembic's second upgrade otherwise only checks its stamp.
    migration = importlib.import_module("deerflow.persistence.migrations.versions.0020_run_cancel_request")
    with engine.begin() as connection, Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
    assert snapshot(engine, tables) == before
    columns = {item["name"]: item for item in sa.inspect(engine).get_columns("runs")}
    assert columns["cancel_action"]["nullable"] is True
    assert columns["cancel_requested_at"]["nullable"] is True
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT cancel_action, cancel_requested_at FROM runs").all() == [(None, None), (None, None)]
        assert connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == REVISION
    assert "uq_runs_thread_active" in {item["name"] for item in sa.inspect(engine).get_indexes("runs")}


def test_old_column_writes_on_upgraded_schema_keep_cancellation(existing_database):
    config, engine, tables = existing_database
    before = snapshot(engine, {"agents": tables["agents"], "threads_meta": tables["threads_meta"]})
    command.upgrade(config, REVISION)
    with engine.begin() as connection:
        connection.execute(sa.text("UPDATE runs SET cancel_action='interrupt', cancel_requested_at=CURRENT_TIMESTAMP WHERE run_id='run-a'"))
        # A reflected pre-upgrade table is deliberately used like the old ORM.
        connection.execute(tables["runs"].update().where(tables["runs"].c.run_id == "run-b").values(last_ai_message="旧镜像仍能写入"))
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT cancel_action FROM runs WHERE run_id='run-a'").scalar_one() == "interrupt"
        assert connection.exec_driver_sql("SELECT last_ai_message FROM runs WHERE run_id='run-b'").scalar_one() == "旧镜像仍能写入"
    assert snapshot(engine, {"agents": tables["agents"], "threads_meta": tables["threads_meta"]}) == before


def test_schema_roundtrip_preserves_legacy_records(existing_database):
    config, engine, tables = existing_database
    before = snapshot(engine, tables)
    command.upgrade(config, REVISION)
    command.downgrade(config, PREVIOUS)
    assert snapshot(engine, tables) == before
    assert "cancel_action" not in {item["name"] for item in sa.inspect(engine).get_columns("runs")}
    command.upgrade(config, REVISION)
    assert snapshot(engine, tables) == before
