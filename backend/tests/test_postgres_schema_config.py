"""Schema config, driver encoding and enterprise employee pool boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.engine import make_url

from deerflow.config.checkpointer_config import CheckpointerConfig
from deerflow.config.database_config import DatabaseConfig
from deerflow.persistence.postgres_schema import build_asyncpg_connect_args, build_psycopg_options
from deerflow.runtime.checkpointer.provider import _resolve_checkpointer_config
from deerflow.runtime.store.provider import _resolve_store_config


@pytest.mark.parametrize("schema", ["", "public", "enterprise_21", "_tenant", "a" * 63])
def test_schema_validated_by_both_config_paths(schema):
    assert DatabaseConfig(postgres_schema=schema).postgres_schema == schema
    assert CheckpointerConfig(type="postgres", postgres_schema=schema).postgres_schema == schema


@pytest.mark.parametrize("schema", [" a", "a ", "a\n", "A", "a.b", "a,b", "1a", "a" * 64, 'a";DROP SCHEMA public;--'])
def test_schema_rejected_before_any_connection(schema):
    for factory in (DatabaseConfig, lambda **kw: CheckpointerConfig(type="postgres", **kw)):
        with pytest.raises(ValueError):
            factory(postgres_schema=schema)
    for builder in (build_asyncpg_connect_args, build_psycopg_options):
        with pytest.raises(ValueError):
            builder(schema)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql", "postgresql+asyncpg", "postgresql+psycopg"])
def test_employee_url_preserves_credentials_and_options_and_separates_pool_keys(scheme):
    url = scheme + "://user:p%40ss%25@localhost/db?options=-c%20statement_timeout%3D5000&sslmode=require"
    first = DatabaseConfig(backend="postgres", postgres_url=url, postgres_schema="tenant_a")
    second = first.model_copy(update={"postgres_schema": "tenant_b"})
    assert first.app_sync_sqlalchemy_url != second.app_sync_sqlalchemy_url
    parsed = make_url(first.app_sync_sqlalchemy_url)
    assert parsed.drivername == "postgresql+psycopg"
    assert parsed.password == "p@ss%"
    assert parsed.query["sslmode"] == "require"
    assert parsed.query["options"] == "-c statement_timeout=5000 -c search_path=tenant_a"


def test_default_urls_do_not_redirect_existing_data():
    config = DatabaseConfig(backend="postgres", postgres_url="postgresql://u:p@localhost/db")
    assert config.postgres_schema == ""
    assert config.app_sqlalchemy_url == "postgresql+asyncpg://u:p@localhost/db"
    assert config.app_sync_sqlalchemy_url == "postgresql+psycopg://u:p@localhost/db"
    assert build_asyncpg_connect_args(config.postgres_schema) == {}


@pytest.mark.parametrize("resolve", [_resolve_checkpointer_config, _resolve_store_config])
def test_legacy_config_retains_precedence_and_unified_schema_is_forwarded(resolve):
    db = DatabaseConfig(backend="postgres", postgres_url="postgresql://localhost/db", postgres_schema="app_data")
    config = SimpleNamespace(checkpointer=None, database=db)
    assert resolve(config).postgres_schema == "app_data"
    config.checkpointer = CheckpointerConfig(type="postgres", connection_string=db.postgres_url, postgres_schema="legacy")
    assert resolve(config) is config.checkpointer


@pytest.mark.anyio
async def test_engine_from_config_forwards_schema():
    from deerflow.persistence.engine import init_engine_from_config

    config = DatabaseConfig(backend="postgres", postgres_url="postgresql://localhost/db", postgres_schema="enterprise")
    with patch("deerflow.persistence.engine.init_engine", new_callable=AsyncMock) as init:
        await init_engine_from_config(config)
    assert init.await_args.kwargs["postgres_schema"] == "enterprise"


@pytest.mark.parametrize("field", ["command_timeout", "pool_recycle"])
@pytest.mark.parametrize("value", [0, -1])
def test_invalid_pool_deadlines_are_rejected(field, value):
    with pytest.raises(ValueError):
        DatabaseConfig(**{field: value})
