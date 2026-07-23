from __future__ import annotations

from alembic import command
from sqlalchemy import create_engine, inspect
from sqlalchemy.ext.asyncio import create_async_engine

from deerflow.persistence.bootstrap import _get_alembic_config


def test_upgrade_removes_site_navigation_left_by_0006(tmp_path) -> None:
    database_path = tmp_path / "retired-site-navigation.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)

    command.upgrade(config, "0007_restore_announcements")
    engine = create_engine(f"sqlite:///{database_path}")
    assert "site_navigation" in inspect(engine).get_table_names()

    command.upgrade(config, "0008_drop_site_navigation")

    assert "site_navigation" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    assert version == "0008_drop_site_navigation"


def test_upgrade_is_safe_when_retired_table_was_already_removed(tmp_path) -> None:
    database_path = tmp_path / "already-removed.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    config = _get_alembic_config(async_engine)
    command.stamp(config, "0007_restore_announcements")

    command.upgrade(config, "0008_drop_site_navigation")

    engine = create_engine(f"sqlite:///{database_path}")
    assert "site_navigation" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        version = connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()
    assert version == "0008_drop_site_navigation"
