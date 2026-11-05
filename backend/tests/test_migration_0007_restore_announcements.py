from __future__ import annotations

from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


def test_upgrade_repairs_database_stamped_at_0006_without_announcements(tmp_path) -> None:
    database_path = tmp_path / "missing-announcements.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)

    command.stamp(config, "0006_enterprise_platform")
    engine = create_engine(f"sqlite:///{database_path}")
    assert "announcements" not in inspect(engine).get_table_names()

    command.upgrade(config, "0007_restore_announcements")

    inspector = inspect(engine)
    assert "announcements" in inspector.get_table_names()
    assert {column["name"] for column in inspector.get_columns("announcements")} >= {
        "id",
        "title",
        "content",
        "status",
        "starts_at",
        "ends_at",
    }
    with engine.connect() as connection:
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    assert version == "0007_restore_announcements"
