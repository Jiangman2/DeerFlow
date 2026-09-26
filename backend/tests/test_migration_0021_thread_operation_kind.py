"""Incremental enterprise migration, legacy writers and conservative rollback."""

import importlib

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from test_migration_0020_run_cancel_request import existing_database as _existing_database
from test_migration_0020_run_cancel_request import snapshot

existing_database = _existing_database

PREVIOUS = "0020_run_cancel_request"
REVISION = "0021_thread_operation_kind"


def test_additive_repeat_preserves_employees_runs_and_cancel_request(existing_database):
    config, engine, tables = existing_database
    command.upgrade(config, PREVIOUS)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE runs SET cancel_action='interrupt', cancel_requested_at=CURRENT_TIMESTAMP WHERE run_id='run-a'"))
    before = snapshot(engine, tables)
    command.upgrade(config, REVISION)
    command.upgrade(config, REVISION)
    migration = importlib.import_module("deerflow.persistence.migrations.versions." + REVISION)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        migration.upgrade()
    assert snapshot(engine, tables) == before
    column = next(c for c in sa.inspect(engine).get_columns("runs") if c["name"] == "operation_kind")
    assert not column["nullable"] and "run" in column["default"]
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT DISTINCT operation_kind FROM runs").all() == [("run",)]
        assert conn.exec_driver_sql("SELECT cancel_action FROM runs WHERE run_id='run-a'").scalar_one() == "interrupt"
    assert "uq_runs_thread_active" in {i["name"] for i in sa.inspect(engine).get_indexes("runs")}


def test_old_writers_insert_and_update_without_new_column(existing_database):
    config, engine, tables = existing_database
    before = snapshot(engine, tables)
    command.upgrade(config, REVISION)
    legacy_run = dict(before["runs"][1], run_id="old-writer", thread_id="old-writer-thread")
    with engine.begin() as conn:
        conn.execute(tables["runs"].insert().values(**legacy_run))
        conn.execute(tables["runs"].update().where(tables["runs"].c.run_id == "run-b").values(last_ai_message="旧写入兼容"))
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT operation_kind FROM runs WHERE run_id='old-writer'").scalar_one() == "run"
        assert conn.exec_driver_sql("SELECT last_ai_message FROM runs WHERE run_id='run-b'").scalar_one() == "旧写入兼容"
    assert snapshot(engine, {"agents": tables["agents"], "threads_meta": tables["threads_meta"]}) == {name: before[name] for name in ("agents", "threads_meta")}


@pytest.mark.parametrize("status", ["pending", "error"])
def test_downgrade_refuses_to_reinterpret_internal_records(existing_database, status):
    config, engine, tables = existing_database
    command.upgrade(config, REVISION)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE runs SET operation_kind='checkpoint_write', status=:status WHERE run_id='run-a'"), {"status": status})
    before = snapshot(engine, tables)
    with pytest.raises(RuntimeError, match="reservation records exist"):
        command.downgrade(config, PREVIOUS)
    assert snapshot(engine, tables) == before
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT operation_kind FROM runs WHERE run_id='run-a'").scalar_one() == "checkpoint_write"
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one() == REVISION


def test_schema_roundtrip_when_no_internal_records_exist(existing_database):
    config, engine, tables = existing_database
    before = snapshot(engine, tables)
    command.upgrade(config, REVISION)
    command.downgrade(config, PREVIOUS)
    assert "operation_kind" not in {c["name"] for c in sa.inspect(engine).get_columns("runs")}
    assert snapshot(engine, tables) == before
    command.upgrade(config, REVISION)
    assert snapshot(engine, tables) == before
