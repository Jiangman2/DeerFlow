from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


def test_upgrade_adds_data_lake_token_binding_and_challenge_table(tmp_path) -> None:
    database_path = tmp_path / "data-lake-auth.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.upgrade(config, "0012_user_last_login")

    engine = create_engine(f"sqlite:///{database_path}")
    assert "cnooc_platform_token_hash" not in {column["name"] for column in inspect(engine).get_columns("users")}

    command.upgrade(config, "head")

    inspector = inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("users")}
    indexes = {index["name"]: index for index in inspector.get_indexes("users")}
    tables = set(inspector.get_table_names())
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == "0020_work_integration_state"
    assert columns["cnooc_platform_token_hash"]["nullable"] is True
    assert indexes["uq_users_cnooc_platform_token_hash"]["unique"] == 1
    assert "data_lake_auth_challenges" in tables


def test_downgrade_removes_data_lake_token_binding_and_challenge_table(
    tmp_path,
) -> None:
    database_path = tmp_path / "data-lake-auth-downgrade.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    command.downgrade(config, "0012_user_last_login")

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("users")}
    indexes = {index["name"] for index in inspector.get_indexes("users")}
    tables = set(inspector.get_table_names())
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == "0012_user_last_login"
    assert "cnooc_platform_token_hash" not in columns
    assert "uq_users_cnooc_platform_token_hash" not in indexes
    assert "data_lake_auth_challenges" not in tables
