from __future__ import annotations

import sqlalchemy as sa
from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


def test_upgrade_adds_nullable_sso_profile_columns_to_existing_users(tmp_path) -> None:
    database_path = tmp_path / "user-sso-profile.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.upgrade(config, "0010_scheduled_task_run_context")

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
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            """
            SELECT display_name, organization_id, organization_code,
                   organization_name
            FROM users WHERE id = 'user-existing'
            """
        ).one()
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()

    assert version == "0020_work_integration_state"
    assert row == (None, None, None, None)
    for name in (
        "display_name",
        "organization_id",
        "organization_code",
        "organization_name",
    ):
        assert columns[name]["nullable"] is True
