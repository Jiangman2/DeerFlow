"""Async checkpointer factory.

Provides an **async context manager** for long-running async servers that need
proper resource cleanup.

Supported backends: memory, sqlite, postgres.

Usage (e.g. FastAPI lifespan)::

    from deerflow.runtime.checkpointer.async_provider import make_checkpointer

    async with make_checkpointer() as checkpointer:
        app.state.checkpointer = checkpointer  # InMemorySaver if not configured

For sync usage see :mod:`deerflow.runtime.checkpointer.provider`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any

from langgraph.types import Checkpointer

from deerflow.config.app_config import AppConfig, get_app_config
from deerflow.run_timing import (
    begin_checkpoint_timing,
    checkpoint_db_session_label,
    checkpoint_db_session_labels_enabled,
    record_checkpoint_timing,
    run_timing_logs_enabled,
)
from deerflow.runtime.checkpointer.provider import (
    POSTGRES_CONN_REQUIRED,
    POSTGRES_INSTALL,
    SQLITE_INSTALL,
)
from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir, resolve_sqlite_conn_str

logger = logging.getLogger(__name__)


_POSTGRES_POOL_MIN_SIZE_ENV = "DEERFLOW_CHECKPOINTER_POSTGRES_POOL_MIN_SIZE"
_POSTGRES_POOL_MAX_SIZE_ENV = "DEERFLOW_CHECKPOINTER_POSTGRES_POOL_MAX_SIZE"
_POSTGRES_POOL_CONCURRENT_CURSOR_ENV = "DEERFLOW_CHECKPOINTER_POSTGRES_POOL_CONCURRENT_CURSOR"
_DEFAULT_POSTGRES_POOL_SIZE = 4


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive integer pool setting, retaining the historic default."""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer; got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer; got {raw!r}")
    return value


def _postgres_pool_sizes() -> tuple[int, int]:
    """Return validated PostgreSQL checkpointer pool bounds from the environment."""
    min_size = _positive_int_env(_POSTGRES_POOL_MIN_SIZE_ENV, _DEFAULT_POSTGRES_POOL_SIZE)
    max_size = _positive_int_env(_POSTGRES_POOL_MAX_SIZE_ENV, _DEFAULT_POSTGRES_POOL_SIZE)
    if max_size < min_size:
        raise ValueError(
            f"{_POSTGRES_POOL_MAX_SIZE_ENV} ({max_size}) must be greater than or equal to "
            f"{_POSTGRES_POOL_MIN_SIZE_ENV} ({min_size})"
        )
    return min_size, max_size


def _bool_env(name: str, default: bool = False) -> bool:
    """Read a strict boolean environment setting.

    Checkpointer startup configuration must fail closed for typos: silently
    treating an invalid value as either enabled or disabled would make a
    performance experiment difficult to audit or roll back.
    """

    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean (true/false); got {raw!r}")


def _postgres_pool_concurrent_cursor_enabled() -> bool:
    """Return whether a pooled PostgreSQL saver may avoid its instance lock.

    This is deliberately opt-in.  A pooled saver can safely borrow independent
    connections for independent cursor operations, while a saver backed by one
    connection or an explicit pipeline must retain LangGraph's original lock.
    """

    return _bool_env(_POSTGRES_POOL_CONCURRENT_CURSOR_ENV)


def _prepare_sqlite_checkpointer_path(raw: str) -> str:
    conn_str = resolve_sqlite_conn_str(raw)
    ensure_sqlite_parent_dir(conn_str)
    return conn_str


def _prepare_database_sqlite_checkpointer_path(db_config) -> str:
    conn_str = db_config.checkpointer_sqlite_path
    ensure_sqlite_parent_dir(conn_str)
    return conn_str


async def _time_postgres_checkpoint_operation(operation: str, awaitable: Any) -> Any:
    """Await one checkpointer operation without retaining SQL or checkpoint data."""

    timing_token = begin_checkpoint_timing(operation=operation)
    started_at = time.monotonic()
    outcome = "completed"
    try:
        return await awaitable
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        record_checkpoint_timing(
            operation=operation,
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            outcome=outcome,
            token=timing_token,
        )


def _instrument_postgres_saver_class(saver_class: type) -> type:
    """Return a saver subclass so LangGraph's checkpoint type contract remains intact."""

    if not run_timing_logs_enabled():
        return saver_class

    class TimedAsyncPostgresSaver(saver_class):
        """Measure only the two PostgreSQL checkpoint persistence APIs."""

        async def aput(self, *args: Any, **kwargs: Any) -> Any:
            return await _time_postgres_checkpoint_operation("aput", super().aput(*args, **kwargs))

        async def aput_writes(self, *args: Any, **kwargs: Any) -> Any:
            return await _time_postgres_checkpoint_operation("aput_writes", super().aput_writes(*args, **kwargs))

    return TimedAsyncPostgresSaver


class _NoopAsyncLock:
    """An async context manager used only for the safe pooled-saver branch.

    ``AsyncPostgresSaver`` serializes cursor access with an instance lock even
    when its ``conn`` is an ``AsyncConnectionPool``.  In that specific topology
    each cursor borrow receives a pool connection, so an instance-wide lock
    defeats the pool.  Do not use this for a single connection or a pipeline.
    """

    async def __aenter__(self) -> _NoopAsyncLock:
        return self

    async def __aexit__(self, _exc_type: Any, _exc_value: Any, _traceback: Any) -> bool:
        return False


def _instrument_pooled_postgres_saver_class(saver_class: type, pool_class: type) -> type:
    """Opt in to concurrent cursors only for a real pooled, non-pipeline saver.

    LangGraph's saver lock protects shared connection/pipeline use.  This
    provider always constructs the PostgreSQL saver with an
    ``AsyncConnectionPool`` and no pipeline, but the conditional is retained
    here as a defence against future factory changes.  The default remains the
    upstream lock behavior until the environment flag is explicitly enabled.
    """

    if not _postgres_pool_concurrent_cursor_enabled():
        return saver_class

    class PooledConcurrentAsyncPostgresSaver(saver_class):
        """Keep upstream behavior except for the verified pooled cursor path."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            # Preserve the upstream constructor's positional and keyword
            # contract (including its optional serializer) rather than
            # reconstructing it here.
            super().__init__(*args, **kwargs)
            conn = kwargs["conn"] if "conn" in kwargs else args[0]
            pipe = kwargs["pipe"] if "pipe" in kwargs else (args[1] if len(args) > 1 else None)
            if isinstance(conn, pool_class) and getattr(self, "pipe", pipe) is None:
                # This is intentionally not a general lock removal: the
                # condition above excludes both single-connection and pipeline
                # savers, whose shared protocol still requires serialization.
                self.lock = _NoopAsyncLock()
                logger.info("PostgreSQL checkpointer pooled concurrent cursor mode enabled")

    return PooledConcurrentAsyncPostgresSaver


def _build_postgres_pool(conn_string: str):
    """Build an AsyncConnectionPool with TCP keepalive and connection checking."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    min_size, max_size = _postgres_pool_sizes()
    logger.info("PostgreSQL checkpointer pool configured: min_size=%d max_size=%d", min_size, max_size)

    pool_class = AsyncConnectionPool
    pool_kwargs: dict[str, Any] = {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": dict_row,
        "keepalives": 1,
        "keepalives_idle": 60,
        "keepalives_interval": 10,
        "keepalives_count": 6,
    }
    if run_timing_logs_enabled() or checkpoint_db_session_labels_enabled():
        from psycopg import AsyncCursor

        class _TimedAsyncCursor(AsyncCursor):
            """Measure DB round trips without retaining SQL text or parameters."""

            async def execute(self, *args: Any, **kwargs: Any) -> Any:
                return await _time_postgres_checkpoint_operation("sql_execute", super().execute(*args, **kwargs))

            async def executemany(self, *args: Any, **kwargs: Any) -> Any:
                return await _time_postgres_checkpoint_operation("sql_execute", super().executemany(*args, **kwargs))

        class _TimedAsyncConnectionPool(AsyncConnectionPool):
            """Measure checkpointer pool wait/hold time and label active sessions."""

            @staticmethod
            async def _apply_diagnostic_session_label(connection: Any) -> None:
                """Set an opt-in, Run-only label without exposing SQL or user data."""

                label = checkpoint_db_session_label()
                if label is None:
                    return
                try:
                    # Use the base cursor to keep this diagnostic SET outside
                    # the checkpointer SQL round-trip aggregate.
                    async with AsyncCursor(connection) as cursor:
                        await cursor.execute("SELECT set_config('application_name', %s, false)", (label,))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # The label is diagnostic-only: a permissions or transient
                    # failure must not break checkpoint persistence.
                    logger.warning("Could not apply PostgreSQL checkpointer session label")

            @contextlib.asynccontextmanager
            async def connection(self, timeout: float | None = None) -> AsyncIterator[Any]:
                started_at = time.monotonic()
                acquired = False
                acquire_token = begin_checkpoint_timing(operation="pool_acquire")
                try:
                    async with super().connection(timeout=timeout) as connection:
                        acquired = True
                        record_checkpoint_timing(
                            operation="pool_acquire",
                            elapsed_ms=round((time.monotonic() - started_at) * 1000),
                            outcome="completed",
                            token=acquire_token,
                        )
                        await self._apply_diagnostic_session_label(connection)
                        hold_started_at = time.monotonic()
                        hold_outcome = "completed"
                        hold_token = begin_checkpoint_timing(operation="pool_hold")
                        try:
                            yield connection
                        except asyncio.CancelledError:
                            hold_outcome = "cancelled"
                            raise
                        except Exception:
                            hold_outcome = "error"
                            raise
                        finally:
                            record_checkpoint_timing(
                                operation="pool_hold",
                                elapsed_ms=round((time.monotonic() - hold_started_at) * 1000),
                                outcome=hold_outcome,
                                token=hold_token,
                            )
                except asyncio.CancelledError:
                    if not acquired:
                        record_checkpoint_timing(
                            operation="pool_acquire",
                            elapsed_ms=round((time.monotonic() - started_at) * 1000),
                            outcome="cancelled",
                            token=acquire_token,
                        )
                    raise
                except Exception:
                    if not acquired:
                        record_checkpoint_timing(
                            operation="pool_acquire",
                            elapsed_ms=round((time.monotonic() - started_at) * 1000),
                            outcome="error",
                            token=acquire_token,
                        )
                    raise

        pool_class = _TimedAsyncConnectionPool
        if run_timing_logs_enabled():
            pool_kwargs["cursor_factory"] = _TimedAsyncCursor

    return pool_class(
        conn_string,
        min_size=min_size,
        max_size=max_size,
        kwargs=pool_kwargs,
        check=AsyncConnectionPool.check_connection,
    )


def _ensure_postgres_imports():
    """Import and return (AsyncPostgresSaver, AsyncConnectionPool), raising ImportError on failure."""
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        raise ImportError(POSTGRES_INSTALL) from exc

    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError as exc:
        raise ImportError(POSTGRES_INSTALL) from exc

    return AsyncPostgresSaver, AsyncConnectionPool


# ---------------------------------------------------------------------------
# Async factory
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _async_checkpointer(config) -> AsyncIterator[Checkpointer]:
    """Async context manager that constructs and tears down a checkpointer."""
    if config.type == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    if config.type == "sqlite":
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        conn_str = await asyncio.to_thread(_prepare_sqlite_checkpointer_path, config.connection_string or "store.db")
        async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            yield saver
        return

    if config.type == "postgres":
        if not config.connection_string:
            raise ValueError(POSTGRES_CONN_REQUIRED)

        AsyncPostgresSaver, AsyncConnectionPool = _ensure_postgres_imports()
        AsyncPostgresSaver = _instrument_pooled_postgres_saver_class(AsyncPostgresSaver, AsyncConnectionPool)
        AsyncPostgresSaver = _instrument_postgres_saver_class(AsyncPostgresSaver)
        pool = _build_postgres_pool(config.connection_string)
        async with pool:
            saver = AsyncPostgresSaver(conn=pool)
            await saver.setup()
            yield saver
        return

    raise ValueError(f"Unknown checkpointer type: {config.type!r}")


# ---------------------------------------------------------------------------
# Public async context manager
# ---------------------------------------------------------------------------


@contextlib.asynccontextmanager
async def _async_checkpointer_from_database(db_config) -> AsyncIterator[Checkpointer]:
    """Async context manager that constructs a checkpointer from unified DatabaseConfig."""
    if db_config.backend == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return

    if db_config.backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:
            raise ImportError(SQLITE_INSTALL) from exc

        conn_str = await asyncio.to_thread(_prepare_database_sqlite_checkpointer_path, db_config)
        async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            yield saver
        return

    if db_config.backend == "postgres":
        if not db_config.postgres_url:
            raise ValueError("database.postgres_url is required for the postgres backend")

        AsyncPostgresSaver, AsyncConnectionPool = _ensure_postgres_imports()
        AsyncPostgresSaver = _instrument_pooled_postgres_saver_class(AsyncPostgresSaver, AsyncConnectionPool)
        AsyncPostgresSaver = _instrument_postgres_saver_class(AsyncPostgresSaver)
        pool = _build_postgres_pool(db_config.postgres_url)
        async with pool:
            saver = AsyncPostgresSaver(conn=pool)
            await saver.setup()
            yield saver
        return

    raise ValueError(f"Unknown database backend: {db_config.backend!r}")


@contextlib.asynccontextmanager
async def make_checkpointer(app_config: AppConfig | None = None) -> AsyncIterator[Checkpointer]:
    """Async context manager that yields a checkpointer for the caller's lifetime.
    Resources are opened on enter and closed on exit -- no global state::

        async with make_checkpointer(app_config) as checkpointer:
            app.state.checkpointer = checkpointer

    Yields an ``InMemorySaver`` when no checkpointer is configured in *config.yaml*.

    Priority:
    1. Legacy ``checkpointer:`` config section (backward compatible)
    2. Unified ``database:`` config section
    3. Default InMemorySaver
    """

    if app_config is None:
        app_config = get_app_config()

    # Legacy: standalone checkpointer config takes precedence
    if app_config.checkpointer is not None:
        async with _async_checkpointer(app_config.checkpointer) as saver:
            yield saver
            return

    # Unified database config
    db_config = getattr(app_config, "database", None)
    if db_config is not None and db_config.backend != "memory":
        async with _async_checkpointer_from_database(db_config) as saver:
            yield saver
            return

    # Default: in-memory
    from langgraph.checkpoint.memory import InMemorySaver

    yield InMemorySaver()
