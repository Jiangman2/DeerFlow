from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects.postgresql import dialect

from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence import engine


@pytest.mark.asyncio
@pytest.mark.parametrize("maintenance", ["postgres", "template1"])
async def test_maintenance_connection_and_identifier_quoting(monkeypatch, maintenance):
    connection = MagicMock()
    connection.dialect = dialect()
    connection.execute = AsyncMock()
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=connection)
    context.__aexit__ = AsyncMock(return_value=False)
    database_engine = MagicMock()
    database_engine.connect.return_value = context
    database_engine.dispose = AsyncMock()
    create = MagicMock(return_value=database_engine)
    monkeypatch.setattr(engine, "create_async_engine", create)
    await engine._auto_create_postgres_db('postgresql+asyncpg://user@localhost/a"b', maintenance)
    assert create.call_args.args[0].database == maintenance
    assert str(connection.execute.call_args.args[0]) == 'CREATE DATABASE "a""b"'
    database_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_selected_maintenance_forwarded(monkeypatch):
    initialize = AsyncMock()
    monkeypatch.setattr(engine, "init_engine", initialize)
    config = DatabaseConfig(backend="postgres", postgres_url="postgresql://localhost/app", postgres_maintenance_database="template1")
    await engine.init_engine_from_config(config)
    assert initialize.call_args.kwargs["postgres_maintenance_database"] == "template1"
    assert DatabaseConfig().postgres_maintenance_database == "postgres"


@pytest.mark.parametrize("value", ["", "bad\x00name"])
def test_invalid_maintenance_rejected(value):
    with pytest.raises(ValueError):
        DatabaseConfig(postgres_maintenance_database=value)
