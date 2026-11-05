"""Operate dashboard rollups outside the Gateway process.

Examples (run from ``backend``):

    uv run python scripts/manage_dashboard_rollups.py status
    uv run python scripts/manage_dashboard_rollups.py backfill --max-days 1
    uv run python scripts/manage_dashboard_rollups.py refresh-changes
    uv run python scripts/manage_dashboard_rollups.py backfill-hours --max-hours 24

The command is deliberately single-process and bucket-bounded. In strict
enterprise mode it scans the read replica and writes only compact fact/state
rows to the primary. It never runs from Gateway startup or an HTTP request, so
dashboard maintenance cannot form a dependency of normal conversations and
role-expert execution.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Match the other repository scripts when invoked directly from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.gateway.dashboard_rollups import (  # noqa: E402
    ROLLUP_SCHEMA_VERSION,
    apply_dashboard_statement_limits,
    backfill_hourly_rollup_batch,
    backfill_rollup_batch,
    business_day_bounds,
    get_rollup_status,
    prune_hourly_rollups,
    refresh_changed_rollup_days,
    refresh_changed_rollup_hours,
)
from deerflow.config import get_app_config  # noqa: E402


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, default=str, indent=2))


def _env_bool(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}


def _async_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


async def _verify_read_source(session_factory) -> None:  # type: ignore[no-untyped-def]
    async with session_factory() as session:
        if session.get_bind().dialect.name != "postgresql":
            raise RuntimeError("strict dashboard maintenance requires a PostgreSQL read replica")
        read_only = str(await session.scalar(text("SHOW transaction_read_only"))).lower()
        in_recovery = bool(await session.scalar(text("SELECT pg_is_in_recovery()")))
        if read_only != "on" or not in_recovery:
            raise RuntimeError("dashboard maintenance source is not a read-only physical standby")


async def _explain(session_factory, *, start_day: date, end_day: date, analyze: bool) -> dict[str, object]:
    if start_day > end_day:
        raise ValueError("start-day must not be later than end-day")
    prefix = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" if analyze else "EXPLAIN (COSTS, FORMAT JSON)"
    start_at, _ = business_day_bounds(start_day)
    _, end_at = business_day_bounds(end_day)
    raw_edge_end = min(end_at, start_at + timedelta(hours=1))
    hourly_end = min(end_at, start_at + timedelta(hours=24))
    async with session_factory() as session:
        if session.get_bind().dialect.name != "postgresql":
            raise RuntimeError("explain is supported only for PostgreSQL deployments")
        await apply_dashboard_statement_limits(session, maintenance=analyze)
        rollup_plan = await session.scalar(
            text(f"{prefix} SELECT day, payload_json FROM dashboard_daily_rollups WHERE day >= :start_day AND day <= :end_day AND schema_version = :version"),
            {"start_day": start_day, "end_day": end_day, "version": ROLLUP_SCHEMA_VERSION},
        )
        raw_edge_plan = await session.scalar(
            text(f"{prefix} SELECT thread_id, user_id, assistant_id, model_name, status, total_tokens, llm_call_count, created_at, updated_at FROM runs WHERE created_at >= :start_at AND created_at < :end_at"),
            {"start_at": start_at, "end_at": raw_edge_end},
        )
        hourly_plan = await session.scalar(
            text(f"{prefix} SELECT bucket_start, payload_json FROM dashboard_hourly_rollups WHERE bucket_start >= :start_at AND bucket_start < :end_at AND schema_version = :version"),
            {"start_at": start_at, "end_at": hourly_end, "version": ROLLUP_SCHEMA_VERSION},
        )
        await session.rollback()
    return {
        "analyze": analyze,
        "daily_rollup": rollup_plan,
        "hourly_rollup": hourly_plan,
        "raw_edge": raw_edge_plan,
    }


async def _run(args: argparse.Namespace) -> int:
    config = get_app_config()
    if config.database.backend == "memory":
        print("Dashboard rollups require SQLite or PostgreSQL persistence.", file=sys.stderr)
        return 2
    target_engine = create_async_engine(
        config.database.app_sqlalchemy_url,
        poolclass=NullPool,
        json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
    )
    session_factory = async_sessionmaker(target_engine, expire_on_commit=False)
    source_engine = None
    source_session_factory = session_factory
    try:
        source_url = os.getenv("DEER_FLOW_DASHBOARD_DATABASE_URL", "").strip()
        require_replica = _env_bool("DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA")
        if source_url:
            async_source_url = _async_url(source_url)
            connect_args = (
                {
                    "timeout": 2,
                    "server_settings": {
                        "application_name": "deerflow-dashboard-maintenance",
                        "default_transaction_read_only": "on",
                    },
                }
                if async_source_url.startswith("postgresql+asyncpg://")
                else {}
            )
            source_engine = create_async_engine(
                async_source_url,
                poolclass=NullPool,
                json_serializer=lambda value: json.dumps(value, ensure_ascii=False),
                connect_args=connect_args,
            )
            source_session_factory = async_sessionmaker(source_engine, expire_on_commit=False)
            if require_replica or _env_bool("DEER_FLOW_DASHBOARD_VERIFY_READ_REPLICA"):
                await _verify_read_source(source_session_factory)
        elif require_replica:
            print("Dashboard maintenance requires DEER_FLOW_DASHBOARD_DATABASE_URL.", file=sys.stderr)
            return 2
        if args.command == "status":
            _print_json(asdict(await get_rollup_status(source_session_factory)))
            return 0
        if args.command == "backfill":
            results = await backfill_rollup_batch(
                session_factory,
                max_days=args.max_days,
                source_session_factory=source_session_factory,
            )
            _print_json([asdict(result) for result in results])
            return 0 if all(result.status in {"refreshed", "open_day"} for result in results) else 1
        if args.command == "refresh-changes":
            results = await refresh_changed_rollup_days(
                session_factory,
                max_days=args.max_days,
                source_session_factory=source_session_factory,
            )
            _print_json([asdict(result) for result in results])
            return 0 if all(result.status in {"refreshed", "open_day"} for result in results) else 1
        if args.command == "backfill-hours":
            results = await backfill_hourly_rollup_batch(
                session_factory,
                max_hours=args.max_hours,
                source_session_factory=source_session_factory,
            )
            _print_json([asdict(result) for result in results])
            return 0 if all(result.status in {"refreshed", "open_hour"} for result in results) else 1
        if args.command == "refresh-changed-hours":
            results = await refresh_changed_rollup_hours(
                session_factory,
                max_hours=args.max_hours,
                source_session_factory=source_session_factory,
            )
            _print_json([asdict(result) for result in results])
            return 0 if all(result.status in {"refreshed", "open_hour"} for result in results) else 1
        if args.command == "maintain":
            changed = await refresh_changed_rollup_days(
                session_factory,
                max_days=args.max_changed_days,
                source_session_factory=source_session_factory,
            )
            backfilled = (
                await backfill_rollup_batch(
                    session_factory,
                    max_days=args.max_backfill_days,
                    source_session_factory=source_session_factory,
                )
                if args.max_backfill_days
                else []
            )
            changed_hours = await refresh_changed_rollup_hours(
                session_factory,
                max_hours=args.max_changed_hours,
                source_session_factory=source_session_factory,
            )
            backfilled_hours = await backfill_hourly_rollup_batch(
                session_factory,
                max_hours=args.max_backfill_hours,
                source_session_factory=source_session_factory,
            )
            pruned_hours = await prune_hourly_rollups(session_factory)
            _print_json(
                {
                    "changed_days": [asdict(result) for result in changed],
                    "backfilled_days": [asdict(result) for result in backfilled],
                    "changed_hours": [asdict(result) for result in changed_hours],
                    "backfilled_hours": [asdict(result) for result in backfilled_hours],
                    "pruned_hours": pruned_hours,
                }
            )
            daily_ok = all(result.status in {"refreshed", "open_day"} for result in [*changed, *backfilled])
            hourly_ok = all(result.status in {"refreshed", "open_hour"} for result in [*changed_hours, *backfilled_hours])
            return 0 if daily_ok and hourly_ok else 1
        if args.command == "explain":
            _print_json(
                await _explain(
                    source_session_factory,
                    start_day=args.start_day,
                    end_day=args.end_day,
                    analyze=args.analyze,
                )
            )
            return 0
        raise RuntimeError(f"unsupported command: {args.command}")
    finally:
        if source_engine is not None:
            await source_engine.dispose()
        await target_engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("status", help="show completed and missing daily facts")
    backfill = subcommands.add_parser("backfill", help="build the oldest missing completed days")
    backfill.add_argument(
        "--max-days",
        type=int,
        default=1,
        choices=range(1, 32),
        metavar="1..31",
        help="maximum days in this invocation (default: 1)",
    )
    refresh = subcommands.add_parser("refresh-changes", help="refresh late-changed completed days")
    refresh.add_argument(
        "--max-days",
        type=int,
        default=1,
        choices=range(1, 32),
        metavar="1..31",
        help="maximum changed days in this invocation (default: 1)",
    )
    backfill_hours = subcommands.add_parser(
        "backfill-hours",
        help="build the newest missing completed hours in the hot window",
    )
    backfill_hours.add_argument(
        "--max-hours",
        type=int,
        default=24,
        choices=range(1, 337),
        metavar="1..336",
    )
    refresh_hours = subcommands.add_parser(
        "refresh-changed-hours",
        help="refresh recent completed hours changed since the previous scan",
    )
    refresh_hours.add_argument(
        "--max-hours",
        type=int,
        default=2,
        choices=range(1, 25),
        metavar="1..24",
    )
    maintain = subcommands.add_parser(
        "maintain",
        help="run one bounded late-change refresh and one bounded backfill batch",
    )
    maintain.add_argument("--max-changed-days", type=int, default=1, choices=range(1, 32), metavar="1..31")
    maintain.add_argument("--max-backfill-days", type=int, default=0, choices=range(0, 32), metavar="0..31")
    maintain.add_argument("--max-changed-hours", type=int, default=2, choices=range(1, 25), metavar="1..24")
    maintain.add_argument("--max-backfill-hours", type=int, default=2, choices=range(1, 25), metavar="1..24")
    explain = subcommands.add_parser("explain", help="inspect PostgreSQL plans without ANALYZE by default")
    explain.add_argument("--start-day", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    explain.add_argument("--end-day", type=date.fromisoformat, required=True, metavar="YYYY-MM-DD")
    explain.add_argument(
        "--analyze",
        action="store_true",
        help="execute the read-only plans under the maintenance statement timeout",
    )
    return parser


def main() -> None:
    raise SystemExit(asyncio.run(_run(_parser().parse_args())))


if __name__ == "__main__":
    main()
