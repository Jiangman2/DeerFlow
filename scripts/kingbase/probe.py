"""Run real-driver probes in an explicitly designated disposable database.

No credentials in arguments or reports. This is preliminary evidence, not release
certification. LangGraph setup creates durable tables in the scratch database.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import uuid4


def validate_target(dsn: str, confirmed: str) -> str:
    parsed = urlsplit(dsn)
    name = unquote(parsed.path.lstrip("/"))
    if parsed.scheme != "postgresql" or not parsed.hostname:
        raise ValueError("Expected a postgresql URL in KINGBASE_TEST_DSN")
    if not re.fullmatch(r"deerflow_kingbase_poc_[a-z0-9_]+", name):
        raise ValueError("Use a dedicated deerflow_kingbase_poc_* database")
    if name != confirmed:
        raise ValueError("--confirm-scratch-database must match the target")
    if parsed.query or parsed.fragment:
        raise ValueError("DSN query overrides are not allowed in the PoC")
    return name


def is_kingbase_v9(version: str) -> bool:
    return bool(re.search(r"Kingbase", version, re.I) and re.search(r"\bV?0*9(?:\.|R\d|\b)", version, re.I))


class ProbeFailure(RuntimeError):
    """A diagnostic authored by this probe, never a driver-supplied message."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


async def probe(dsn: str, report: dict) -> None:
    import asyncpg
    from psycopg import AsyncConnection
    from langgraph.checkpoint.base import empty_checkpoint
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

    report["stage"] = "asyncpg_identity_sql"
    conn = await asyncpg.connect(dsn, timeout=15, command_timeout=30)
    try:
        version = await conn.fetchval("SELECT version()")
        require(is_kingbase_v9(version), "Expected Kingbase V9")
        require(await conn.fetchval("SHOW server_encoding") == "UTF8", "Expected UTF8")
        report["product_check"] = "Kingbase V9 banner matched; vendor PG mode evidence still required"
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE TEMP TABLE deerflow_poc (id int PRIMARY KEY, payload jsonb, raw bytea, ts timestamptz)")
            await conn.execute("CREATE INDEX ON deerflow_poc (id) WHERE id > 0")
            now = datetime.now(timezone.utc)
            for _ in range(2):
                row = await conn.fetchrow("INSERT INTO deerflow_poc VALUES (1,$1::jsonb,$2,$3) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload RETURNING payload->>'text' AS text, raw, ts", json.dumps({"text": "金仓验证"}), b"\x00\xff", now)
                require(row["text"] == "金仓验证" and row["raw"] == b"\x00\xff" and row["ts"] == now, "Round trip mismatch")
            lock = int(uuid4().int % (2**62))
            await conn.execute("SELECT pg_advisory_xact_lock($1::bigint)", lock)
            other = await asyncpg.connect(dsn, timeout=15, command_timeout=30)
            try:
                require(not await other.fetchval("SELECT pg_try_advisory_xact_lock($1::bigint)", lock), "Transaction lock did not exclude peer")
                await tx.rollback()
                tx = None
                require(await other.fetchval("SELECT pg_try_advisory_xact_lock($1::bigint)", lock), "Transaction lock was not released")
                await conn.execute("SELECT pg_advisory_lock($1::bigint)", lock)
                require(not await other.fetchval("SELECT pg_try_advisory_lock($1::bigint)", lock), "Session lock did not exclude peer")
                require(await conn.fetchval("SELECT pg_advisory_unlock($1::bigint)", lock), "Session unlock failed")
                require(await other.fetchval("SELECT pg_try_advisory_lock($1::bigint)", lock), "Session lock was not released")
            finally:
                await other.close()
        finally:
            if tx is not None:
                await tx.rollback()
    finally:
        await conn.close()
    report["passed"].append("asyncpg_sql_types_and_two_connection_locks")

    report["stage"] = "psycopg3_pipeline"
    async with await AsyncConnection.connect(dsn, connect_timeout=15, options="-c statement_timeout=30000") as connection:
        async with connection.pipeline():
            cursor = await connection.execute("SELECT %s::bytea", (b"\x00\xff",))
        require((await cursor.fetchone())[0] == b"\x00\xff", "Psycopg pipeline mismatch")
    report["passed"].append("psycopg3_pipeline")

    report["stage"] = "langgraph_checkpoint_reopen"
    config = {"configurable": {"thread_id": "poc-" + uuid4().hex, "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"probe": "金仓验证"}
    checkpoint["channel_versions"] = {"probe": 1}
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        saved = await saver.aput(config, checkpoint, {"source": "input", "step": 0, "parents": {}}, {"probe": 1})
        await saver.aput_writes(saved, [("probe", "pending")], "poc-task")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        value = await saver.aget_tuple(saved)
        require(value is not None and value.checkpoint["channel_values"]["probe"] == "金仓验证", "Checkpoint reopen mismatch")
        require(any(item[1:] == ("probe", "pending") for item in value.pending_writes), "Pending writes missing")
        require(bool([item async for item in saver.alist(config)]), "Checkpoint history missing")
    report["passed"].append("langgraph_checkpoint_and_pending_writes_reopen")

    report["stage"] = "langgraph_interrupted_graph_resume"
    from acceptance_graph import build_graph
    from langgraph.types import Command

    graph_config = {"configurable": {"thread_id": "graph-poc-" + uuid4().hex}}
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(saver)
        interrupted = await graph.ainvoke({"request": "确认恢复"}, graph_config)
        require(bool(interrupted.get("__interrupt__")), "Graph did not interrupt")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        graph = build_graph(saver)
        resumed = await graph.ainvoke(Command(resume="已恢复"), graph_config)
        require(resumed.get("result") == "已恢复", "Graph did not resume")
    report["passed"].append("graph_interrupt_reopen_resume")

    report["stage"] = "application_schema_bootstrap"
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from deerflow.persistence.bootstrap import bootstrap_schema

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://", 1), connect_args={"timeout": 15, "command_timeout": 30})
    try:
        # Exercise initial bootstrap and the already-managed migration path.
        await bootstrap_schema(engine, backend="postgres")
        await bootstrap_schema(engine, backend="postgres")
        async with engine.connect() as sql_conn:
            revisions = list((await sql_conn.execute(text("SELECT version_num FROM alembic_version"))).scalars())
            require(bool(revisions), "Application migration state missing")
            report["alembic_revisions"] = revisions
    finally:
        await engine.dispose()
    report["passed"].append("application_bootstrap_and_repeat")

    report["stage"] = "langgraph_store_reopen"
    namespace = ("kingbase_poc", uuid4().hex)
    async with AsyncPostgresStore.from_conn_string(dsn) as store:
        await store.setup()
        await store.aput(namespace, "probe", {"text": "金仓验证"})
    async with AsyncPostgresStore.from_conn_string(dsn) as store:
        item = await store.aget(namespace, "probe")
        require(item is not None and item.value == {"text": "金仓验证"}, "Store reopen mismatch")
        require(bool(await store.asearch(namespace)), "Store search mismatch")
        await store.adelete(namespace, "probe")
        require(await store.aget(namespace, "probe") is None, "Store delete mismatch")
    report["passed"].append("langgraph_store_reopen_search_delete")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-scratch-database", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {"status": "failed", "stage": "target_validation", "passed": [], "production_certified": False, "architecture": platform.machine(), "python": platform.python_version(), "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        dsn = os.environ.get("KINGBASE_TEST_DSN", "")
        validate_target(dsn, args.confirm_scratch_database)
        report["stage"] = "dependency_validation"
        report["dependencies"] = {name: importlib.metadata.version(name) for name in ("SQLAlchemy", "asyncpg", "psycopg", "psycopg-pool", "langgraph", "langgraph-checkpoint-postgres")}
        asyncio.run(asyncio.wait_for(probe(dsn, report), timeout=240))
        report["status"] = "preliminary_probes_passed"
        report["stage"] = "complete"
    except Exception as exc:
        # Driver error strings can contain DSNs, SQL or user data. Never emit them.
        report["error_type"] = type(exc).__name__
        if isinstance(exc, ProbeFailure):
            report["diagnostic"] = str(exc)
        state = getattr(exc, "sqlstate", None)
        if isinstance(state, str) and re.fullmatch(r"[A-Z0-9]{5}", state):
            report["sqlstate"] = state
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "preliminary_probes_passed" else 1


if __name__ == "__main__":
    sys.exit(main())
