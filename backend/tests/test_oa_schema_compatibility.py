"""Old consumers must restart safely across only the audited OA expansion."""

import shutil

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence import bootstrap
from deerflow.persistence.oa_schema_compatibility import ADDITIVE_REVISION, BASE_REVISION, accepts_oa_additive_schema


def seed(connection, *, revision=ADDITIVE_REVISION, protocol="VARCHAR(8) NOT NULL DEFAULT 'v1'", files="JSON"):
    connection.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    connection.execute(sa.text("INSERT INTO alembic_version VALUES (:version)"), {"version": revision})
    connection.execute(sa.text(f"CREATE TABLE oa_policy_flow_jobs (id VARCHAR(64) PRIMARY KEY, result_protocol {protocol}, result_files {files})"))
    connection.execute(sa.text("INSERT INTO oa_policy_flow_jobs(id) VALUES ('legacy')"))


@pytest.mark.parametrize("revision", [BASE_REVISION, "0022_unknown", "0019_digital_employee_agents"])
def test_unapproved_revision_keeps_normal_migration(revision):
    with sa.create_engine("sqlite://").begin() as connection:
        seed(connection, revision=revision)
        assert accepts_oa_additive_schema(connection, local_head=BASE_REVISION) is False


@pytest.mark.parametrize(
    "protocol,files",
    [
        ("VARCHAR(8) DEFAULT 'v1'", "JSON"),
        ("VARCHAR(9) NOT NULL DEFAULT 'v1'", "JSON"),
        ("VARCHAR(8) NOT NULL DEFAULT 'v2'", "JSON"),
        ("VARCHAR(8) NOT NULL DEFAULT 'v1'", "TEXT"),
        ("VARCHAR(8) NOT NULL DEFAULT 'v1'", "JSON DEFAULT '[]'"),
    ],
)
def test_malformed_expansion_is_rejected(protocol, files):
    with sa.create_engine("sqlite://").begin() as connection:
        seed(connection, protocol=protocol, files=files)
        with pytest.raises(RuntimeError, match="oa_additive_schema_shape_mismatch"):
            accepts_oa_additive_schema(connection, local_head=BASE_REVISION)


def test_read_only_acceptance_and_current_head_unchanged():
    with sa.create_engine("sqlite://").begin() as connection:
        seed(connection)
        assert accepts_oa_additive_schema(connection, local_head=BASE_REVISION)
        assert not accepts_oa_additive_schema(connection, local_head=ADDITIVE_REVISION)
        assert connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar() == ADDITIVE_REVISION
        assert connection.execute(sa.text("SELECT result_protocol FROM oa_policy_flow_jobs WHERE id='legacy'")).scalar() == "v1"


@pytest.mark.asyncio
async def test_old_migration_tree_bootstrap_after_expand_and_unknown_rejected(tmp_path, monkeypatch):
    # Actual old migration tree: 0021 is absent, rather than merely faking head.
    migrations = tmp_path / "migrations"
    shutil.copytree(bootstrap._MIGRATIONS_DIR, migrations, ignore=shutil.ignore_patterns("__pycache__", "0021_oa_result_sets.py"))
    monkeypatch.setattr(bootstrap, "_MIGRATIONS_DIR", migrations)
    monkeypatch.setattr(bootstrap, "_HEAD_REVISION", None)
    assert bootstrap._get_head_revision() == BASE_REVISION
    engine = create_async_engine("sqlite+aiosqlite:///" + str(tmp_path / "consumer.db"))
    try:
        async with engine.begin() as connection:
            await connection.run_sync(seed)
        await bootstrap.bootstrap_schema(engine, backend="sqlite")
        async with engine.begin() as connection:
            assert (await connection.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar() == ADDITIVE_REVISION
            await connection.execute(sa.text("UPDATE alembic_version SET version_num='0022_unknown'"))
        with pytest.raises(Exception, match="0022_unknown"):
            await bootstrap.bootstrap_schema(engine, backend="sqlite")
    finally:
        await engine.dispose()
