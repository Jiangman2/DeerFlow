from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


def test_upgrade_adds_nullable_last_login_and_index(tmp_path) -> None:
    database_path = tmp_path / "user-last-login.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.upgrade(config, "0011_user_sso_profile")

    engine = create_engine(f"sqlite:///{database_path}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                """
                INSERT INTO users (
                    id, email, password_hash, system_role, created_at,
                    oauth_provider, oauth_id, needs_setup, is_disabled,
                    token_version
                ) VALUES (
                    'user-existing', 'existing@example.com', NULL, 'user',
                    CURRENT_TIMESTAMP, 'cnooc', 'CNOOC-001', false, false, 0
                )
                """
            )
        )

    command.upgrade(config, "head")

    columns = {column["name"]: column for column in inspect(engine).get_columns("users")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("users")}
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT last_login_at FROM users WHERE id = 'user-existing'"
        ).one()
        version = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()

    assert version == "0012_user_last_login"
    assert row == (None,)
    assert columns["last_login_at"]["nullable"] is True
    assert "ix_users_last_login_at" in indexes


def test_downgrade_removes_last_login_and_index(tmp_path) -> None:
    database_path = tmp_path / "user-last-login-downgrade.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{database_path}")
    command.downgrade(config, "0011_user_sso_profile")

    columns = {column["name"] for column in inspect(engine).get_columns("users")}
    indexes = {index["name"] for index in inspect(engine).get_indexes("users")}
    with engine.connect() as connection:
        version = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one()

    assert version == "0011_user_sso_profile"
    assert "last_login_at" not in columns
    assert "ix_users_last_login_at" not in indexes
