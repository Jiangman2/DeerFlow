"""Unified database backend configuration.

Controls BOTH the LangGraph checkpointer and the DeerFlow application
persistence layer (runs, threads metadata, users, etc.). The user
configures one backend; the system handles physical separation details.

SQLite mode: checkpointer and app share a single .db file
({sqlite_dir}/deerflow.db) with WAL journal mode enabled on every
connection. WAL allows concurrent readers and a single writer without
blocking, making a unified file safe for both workloads.  Writers
that contend for the lock wait via the default 5-second sqlite3
busy timeout rather than failing immediately.

Postgres mode: both use the same database URL but maintain independent
connection pools with different lifecycles.

Memory mode: checkpointer uses MemorySaver, app uses in-memory stores.
No database is initialized.

Sensitive values (postgres_url) should use $VAR syntax in config.yaml
to reference environment variables from .env:

    database:
      backend: postgres
      postgres_url: $DATABASE_URL

The $VAR resolution is handled by AppConfig.resolve_env_variables()
before this config is instantiated -- DatabaseConfig itself does not
need to do any environment variable processing.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from deerflow.config.postgres_schema import POSTGRES_SCHEMA_PATTERN, validate_postgres_schema


class DatabaseConfig(BaseModel):
    backend: Literal["memory", "sqlite", "postgres"] = Field(
        default="memory",
        description=("Storage backend for both checkpointer and application data. 'memory' for development (no persistence across restarts), 'sqlite' for single-node deployment, 'postgres' for production multi-node deployment."),
    )
    sqlite_dir: str = Field(
        default=".deer-flow/data",
        description=("Directory for the SQLite database file. Both checkpointer and application data share {sqlite_dir}/deerflow.db."),
    )
    postgres_url: str = Field(
        default="",
        description=(
            "PostgreSQL connection URL, shared by checkpointer and app. "
            "Use $DATABASE_URL in config.yaml to reference .env. "
            "Example: postgresql://user:pass@host:5432/deerflow "
            "(the +asyncpg driver suffix is added automatically where needed)."
        ),
    )
    postgres_schema: str = Field(
        default="",
        description=(
            "PostgreSQL schema shared by application, agent, checkpointer and store tables. "
            "Empty keeps the server default search_path. Opt-in, restart required; does not move existing data. "
            f"Plain lowercase identifiers only: {POSTGRES_SCHEMA_PATTERN}."
        ),
    )

    @field_validator("postgres_schema")
    @classmethod
    def _validate_postgres_schema(cls, value: str) -> str:
        return validate_postgres_schema(value)

    echo_sql: bool = Field(
        default=False,
        description="Echo all SQL statements to log (debug only).",
    )
    pool_size: int = Field(
        default=5,
        description="Connection pool size for the app ORM engine (postgres only).",
    )
    pool_recycle: int = Field(
        default=300,
        gt=0,
        description="Seconds before app ORM PostgreSQL connections are recycled.",
    )
    command_timeout: float | None = Field(
        default=30,
        gt=0,
        description="Timeout in seconds for app ORM PostgreSQL commands. Set to null to disable the command timeout.",
    )

    # -- Derived helpers (not user-configured) --

    @property
    def _resolved_sqlite_dir(self) -> str:
        """Resolve sqlite_dir to an absolute path (relative to CWD)."""
        from pathlib import Path

        return str(Path(self.sqlite_dir).resolve())

    @property
    def sqlite_path(self) -> str:
        """Unified SQLite file path shared by checkpointer and app."""
        return os.path.join(self._resolved_sqlite_dir, "deerflow.db")

    # Backward-compatible aliases
    @property
    def checkpointer_sqlite_path(self) -> str:
        """SQLite file path for the LangGraph checkpointer (alias for sqlite_path)."""
        return self.sqlite_path

    @property
    def app_sqlite_path(self) -> str:
        """SQLite file path for application ORM data (alias for sqlite_path)."""
        return self.sqlite_path

    @property
    def app_sqlalchemy_url(self) -> str:
        """SQLAlchemy async URL for the application ORM engine."""
        if self.backend == "sqlite":
            return f"sqlite+aiosqlite:///{self.sqlite_path}"
        if self.backend == "postgres":
            url = self.postgres_url
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            elif url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+asyncpg://", 1)
            return url
        raise ValueError(f"No SQLAlchemy URL for backend={self.backend!r}")

    @property
    def app_sync_sqlalchemy_url(self) -> str:
        """Synchronous URL used by the custom-agent definition store.

        Agent construction and the setup/update tools are synchronous and can
        execute in graph worker processes, so they cannot reuse the gateway's
        async SQLAlchemy engine.  This URL points at the same durable database
        with the synchronous driver that already ships with the application.
        """
        if self.backend == "sqlite":
            return f"sqlite:///{self.sqlite_path}"
        if self.backend == "postgres":
            url = self.postgres_url
            if url.startswith("postgresql+asyncpg://"):
                url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
            elif url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+psycopg://", 1)
            elif url.startswith("postgres://"):
                url = url.replace("postgres://", "postgresql+psycopg://", 1)
            if self.postgres_schema:
                from sqlalchemy.engine import make_url

                from deerflow.persistence.postgres_schema import dsn_with_search_path

                # The URL is also the employee connection-pool cache key.
                # Include schema while retaining driver, credentials and other options.
                driver = make_url(url).drivername
                url = make_url(dsn_with_search_path(url, self.postgres_schema)).set(drivername=driver).render_as_string(hide_password=False)
            return url
        raise ValueError(f"No synchronous SQLAlchemy URL for backend={self.backend!r}")
