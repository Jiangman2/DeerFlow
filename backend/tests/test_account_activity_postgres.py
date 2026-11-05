"""Opt-in real PostgreSQL test. Creates and drops its own synthetic database."""

import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_postgres_owner_readonly_global_admission_and_production_probe():
    base = os.environ.get("TEST_USAGE_POSTGRES_URL")
    if not base:
        pytest.skip("Dedicated synthetic PostgreSQL required")
    import asyncpg
    from fastapi import HTTPException
    from sqlalchemy import insert, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.gateway import account_activity as activity
    from deerflow.persistence.run.model import RunRow

    name = "usage_test_" + uuid.uuid4().hex
    admin = await asyncpg.connect(base)
    await admin.execute('CREATE DATABASE "' + name + '"')
    url = base.rsplit("/", 1)[0] + "/" + name
    engine = create_async_engine(url.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with engine.begin() as con:
            await con.run_sync(RunRow.__table__.create)
            await con.execute(text("CREATE TABLE alembic_version (version_num varchar(64))"))
            await con.execute(text("INSERT INTO alembic_version VALUES ('0022_work_integration_state')"))
            at = datetime(2026, 1, 1, 16, 1, tzinfo=UTC)
            for batch in range(10):
                await con.execute(
                    insert(RunRow),
                    [
                        {
                            "run_id": str(batch * 2000 + i),
                            "thread_id": str(batch * 2000 + i),
                            "user_id": "owner" if i % 2 else "other",
                            "status": "success",
                            "created_at": at,
                            "updated_at": at + timedelta(seconds=90),
                            "total_tokens": 100 if i % 2 else 9999,
                            "total_input_tokens": 80,
                            "total_output_tokens": 20,
                        }
                        for i in range(2000)
                    ],
                )
            await con.execute(insert(RunRow), [{"run_id": "live", "thread_id": "live", "user_id": "owner", "status": "running", "created_at": at, "updated_at": at + timedelta(hours=3), "total_tokens": 5}])
        sessions = async_sessionmaker(engine)
        result = await activity.get_account_activity("owner", sessions)
        assert result["total_tokens"] == 1000005 and result["run_count"] == 10001
        assert result["peak_daily_tokens"] == 1000005 and result["longest_task_seconds"] == 90
        # A second analytics transaction is rejected without a long queue.
        async with sessions() as locked:
            await locked.execute(text("SELECT pg_advisory_xact_lock(732492018)"))
            with pytest.raises(HTTPException) as exc:
                await activity.get_account_activity("fresh-owner", sessions)
            assert exc.value.status_code == 503
        # Run the exact production readiness query in the isolated test database.
        probe_path = Path(__file__).resolve().parents[2] / "deploy/enterprise/usage_monitor_preflight_child_20261102.py"
        source = probe_path.read_text(encoding="utf-8")
        namespace = {"__name__": "synthetic_probe"}
        exec(compile(source.replace("INSPECT_SERVICES_PLACEHOLDER", "False"), "probe", "exec"), namespace)
        probe = await namespace["database_probe"]({"database": {"backend": "postgres", "postgres_url": url}})
        assert probe["status"] == "PASS" and probe["transaction_read_only"]
        assert probe["owner_exported"] is False and probe["aggregate_days"] == 1
        print({"synthetic_rows": 20001, "probe_ms": probe["elapsed_ms"], "readonly": True})
    finally:
        await engine.dispose()
        await admin.execute('DROP DATABASE "' + name + '" WITH (FORCE)')
        await admin.close()
