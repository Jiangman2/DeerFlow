"""Optional isolated read pool for the administrative dashboard.

Normal DeerFlow repositories never use this engine.  Production can point
``DEER_FLOW_DASHBOARD_DATABASE_URL`` at a read replica; pointing it at the
primary still gives dashboard traffic a hard one-connection pool boundary.
When ``DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA`` is enabled, an absent,
unreachable, writable, or non-recovery database disables only the dashboard;
it never falls back to the primary application database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_unavailable_reason: str | None = None
_retry_task: asyncio.Task[None] | None = None


def _json_serializer(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def dashboard_read_replica_required() -> bool:
    return _env_bool("DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA")


def get_dashboard_read_database_unavailable_reason() -> str | None:
    return _unavailable_reason


async def start_dashboard_read_database_retry() -> None:
    """Retry a configured strict standby without coupling it to app readiness."""

    global _retry_task
    if _retry_task is not None or not dashboard_read_replica_required() or _session_factory is not None or not os.getenv("DEER_FLOW_DASHBOARD_DATABASE_URL", "").strip():
        return

    async def _retry() -> None:
        while _session_factory is None:
            try:
                delay = int(os.getenv("DEER_FLOW_DASHBOARD_REPLICA_RETRY_SECONDS", "10"))
            except ValueError:
                delay = 10
            await asyncio.sleep(max(5, min(delay, 300)))
            await init_dashboard_read_database()

    _retry_task = asyncio.create_task(_retry(), name="dashboard-read-replica-retry")


async def init_dashboard_read_database() -> None:
    """Configure and, in strict mode, verify the dashboard-only read pool."""

    global _engine, _session_factory, _unavailable_reason
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
    _unavailable_reason = None
    url = os.getenv("DEER_FLOW_DASHBOARD_DATABASE_URL", "").strip()
    if not url:
        if dashboard_read_replica_required():
            _unavailable_reason = "required dashboard read-replica URL is not configured"
            logger.error("Dashboard disabled: %s", _unavailable_reason)
        return
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if dashboard_read_replica_required() and not url.startswith("postgresql+asyncpg://"):
        _unavailable_reason = "strict dashboard isolation requires PostgreSQL"
        logger.error("Dashboard disabled: %s", _unavailable_reason)
        return
    try:
        connect_args = {}
        if url.startswith("postgresql+asyncpg://"):
            connect_args = {
                "timeout": 2,
                "server_settings": {
                    "application_name": "deerflow-dashboard",
                    "default_transaction_read_only": "on",
                },
            }
        candidate = create_async_engine(
            url,
            pool_size=1,
            max_overflow=0,
            pool_timeout=0.25,
            pool_pre_ping=True,
            json_serializer=_json_serializer,
            connect_args=connect_args,
        )
        verify_replica = dashboard_read_replica_required() or _env_bool("DEER_FLOW_DASHBOARD_VERIFY_READ_REPLICA")
        if verify_replica:

            async def _verify() -> None:
                async with candidate.connect() as connection:
                    read_only = str(await connection.scalar(text("SHOW transaction_read_only"))).lower()
                    in_recovery = bool(await connection.scalar(text("SELECT pg_is_in_recovery()")))
                    if read_only != "on":
                        raise RuntimeError("dashboard database is not transaction read-only")
                    if not in_recovery:
                        raise RuntimeError("dashboard database is not a physical standby")

            await asyncio.wait_for(_verify(), timeout=3)
        _engine = candidate
        _session_factory = async_sessionmaker(candidate, expire_on_commit=False)
        logger.info(
            "Dashboard isolated read pool configured (maximum one read-only connection%s)",
            ", verified physical standby" if verify_replica else "",
        )
    except Exception as exc:
        candidate_engine = locals().get("candidate")
        if isinstance(candidate_engine, AsyncEngine):
            await candidate_engine.dispose()
        _engine = None
        _session_factory = None
        _unavailable_reason = str(exc)[:240] or type(exc).__name__
        if dashboard_read_replica_required():
            logger.exception("Dashboard read replica verification failed; dashboard remains disabled")
        else:
            logger.exception("Dashboard read pool configuration failed; using bounded primary fallback")


def get_dashboard_read_session_factory() -> async_sessionmaker[AsyncSession] | None:
    return _session_factory


async def close_dashboard_read_database() -> None:
    global _engine, _session_factory, _unavailable_reason, _retry_task
    if _retry_task is not None:
        _retry_task.cancel()
        try:
            await _retry_task
        except asyncio.CancelledError:
            pass
        _retry_task = None
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    _unavailable_reason = None
