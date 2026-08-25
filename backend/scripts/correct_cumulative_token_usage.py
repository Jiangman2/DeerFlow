"""Audit or quarantine token totals corrupted by cumulative stream snapshots.

The 2026-08 enterprise OpenAI-compatible provider emitted cumulative usage on
every streamed frame. Older adapters let LangChain add those snapshots, so one
LLM call could be persisted as millions or billions of tokens. The final
provider snapshot was not stored separately, which means the exact historical
value cannot be reconstructed from ``runs`` or ``run_events`` after the fact.

This maintenance command therefore uses a conservative and reversible policy:

* ``audit`` is read-only and is the default operation;
* only impossible per-call totals above an explicit threshold are quarantined;
* unaffected calls and external/subagent contributions remain counted;
* ``apply`` stores every original run aggregate in a dedicated backup table in
  the same transaction before changing ``runs``;
* ``rollback`` restores the exact original aggregates and metadata.

Run from ``/app/backend`` in the Gateway container, for example::

    PYTHONPATH=packages/harness:. .venv/bin/python \
      scripts/correct_cumulative_token_usage.py audit \
      --from-time 2026-08-24T21:18:00+08:00 \
      --to-time 2026-08-25T14:13:00+08:00

No operation mutates ``run_events``; they remain the immutable audit trail.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig

DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_SUSPICIOUS_CALL_THRESHOLD = 1_000_000


@dataclass(frozen=True)
class UsageContribution:
    seq: int
    call_key: str
    caller: str
    model_name: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_read_tokens: int


@dataclass(frozen=True)
class RunCorrection:
    run_id: str
    suspicious_calls: tuple[UsageContribution, ...]
    original: dict[str, Any]
    corrected: dict[str, Any]


def _non_negative_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _usage_contribution(row: dict[str, Any]) -> UsageContribution | None:
    metadata = _mapping(row.get("event_metadata"))
    usage = _mapping(metadata.get("usage"))
    input_tokens = _non_negative_int(usage.get("input_tokens"))
    output_tokens = _non_negative_int(usage.get("output_tokens"))
    total_tokens = _non_negative_int(usage.get("total_tokens"))
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    if total_tokens <= 0:
        return None

    content = _mapping(row.get("content"))
    response_metadata = _mapping(content.get("response_metadata"))
    model_name = str(response_metadata.get("model_name") or response_metadata.get("model") or row.get("model_name") or "unknown")
    caller = str(metadata.get("caller") or "lead_agent")
    call_index = metadata.get("llm_call_index")
    seq = _non_negative_int(row.get("seq"))
    call_key = f"index:{call_index}" if call_index not in (None, "") else f"seq:{seq}"
    input_details = _mapping(usage.get("input_token_details"))
    return UsageContribution(
        seq=seq,
        call_key=call_key,
        caller=caller,
        model_name=model_name,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_read_tokens=_non_negative_int(input_details.get("cache_read")),
    )


def _resolve_model_bucket(
    usage_by_model: dict[str, Any],
    contribution: UsageContribution,
    run_model_name: str,
) -> str | None:
    for candidate in (contribution.model_name, run_model_name, "unknown"):
        if candidate in usage_by_model:
            return candidate
    if len(usage_by_model) == 1:
        return next(iter(usage_by_model))
    return None


def _subtract_bucket(bucket: dict[str, Any], contribution: UsageContribution) -> None:
    for key, amount in (
        ("input_tokens", contribution.input_tokens),
        ("output_tokens", contribution.output_tokens),
        ("total_tokens", contribution.total_tokens),
        ("cache_read_tokens", contribution.cache_read_tokens),
    ):
        if key in bucket or amount > 0:
            bucket[key] = max(_non_negative_int(bucket.get(key)) - amount, 0)
    if _non_negative_int(bucket.get("cache_read_tokens")) == 0:
        bucket.pop("cache_read_tokens", None)


def build_run_correction(
    run: dict[str, Any],
    event_rows: list[dict[str, Any]],
    *,
    suspicious_call_threshold: int,
) -> RunCorrection | None:
    """Build a conservative correction without mutating input mappings."""

    seen_calls: set[str] = set()
    suspicious: list[UsageContribution] = []
    for row in sorted(event_rows, key=lambda item: _non_negative_int(item.get("seq"))):
        contribution = _usage_contribution(row)
        if contribution is None or contribution.call_key in seen_calls:
            continue
        seen_calls.add(contribution.call_key)
        if max(contribution.input_tokens, contribution.total_tokens) >= suspicious_call_threshold:
            suspicious.append(contribution)

    if not suspicious:
        return None

    original = {
        "total_input_tokens": _non_negative_int(run.get("total_input_tokens")),
        "total_output_tokens": _non_negative_int(run.get("total_output_tokens")),
        "total_tokens": _non_negative_int(run.get("total_tokens")),
        "lead_agent_tokens": _non_negative_int(run.get("lead_agent_tokens")),
        "subagent_tokens": _non_negative_int(run.get("subagent_tokens")),
        "middleware_tokens": _non_negative_int(run.get("middleware_tokens")),
        "token_usage_by_model": copy.deepcopy(_mapping(run.get("token_usage_by_model"))),
        "metadata_json": copy.deepcopy(_mapping(run.get("metadata_json"))),
    }
    corrected = copy.deepcopy(original)
    run_model_name = str(run.get("model_name") or "unknown")

    for contribution in suspicious:
        corrected["total_input_tokens"] = max(
            corrected["total_input_tokens"] - contribution.input_tokens,
            0,
        )
        corrected["total_output_tokens"] = max(
            corrected["total_output_tokens"] - contribution.output_tokens,
            0,
        )
        corrected["total_tokens"] = max(
            corrected["total_tokens"] - contribution.total_tokens,
            0,
        )

        if contribution.caller.startswith("subagent:"):
            caller_bucket = "subagent_tokens"
        elif contribution.caller.startswith("middleware:"):
            caller_bucket = "middleware_tokens"
        else:
            caller_bucket = "lead_agent_tokens"
        corrected[caller_bucket] = max(
            corrected[caller_bucket] - contribution.total_tokens,
            0,
        )

        model_bucket_name = _resolve_model_bucket(
            corrected["token_usage_by_model"],
            contribution,
            run_model_name,
        )
        if model_bucket_name is not None:
            bucket = corrected["token_usage_by_model"].get(model_bucket_name)
            if isinstance(bucket, dict):
                _subtract_bucket(bucket, contribution)
                if _non_negative_int(bucket.get("total_tokens")) == 0:
                    corrected["token_usage_by_model"].pop(model_bucket_name, None)

    return RunCorrection(
        run_id=str(run["run_id"]),
        suspicious_calls=tuple(suspicious),
        original=original,
        corrected=corrected,
    )


def build_corrections(
    rows: list[dict[str, Any]],
    *,
    suspicious_call_threshold: int,
) -> list[RunCorrection]:
    runs: dict[str, dict[str, Any]] = {}
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        run_id = str(row["run_id"])
        runs.setdefault(run_id, row)
        events[run_id].append(row)

    corrections = [
        correction
        for run_id, run in runs.items()
        if (
            correction := build_run_correction(
                run,
                events[run_id],
                suspicious_call_threshold=suspicious_call_threshold,
            )
        )
        is not None
    ]
    return sorted(corrections, key=lambda item: item.run_id)


_AUDIT_QUERY = text(
    """
    SELECT
        r.run_id,
        r.model_name,
        r.total_input_tokens,
        r.total_output_tokens,
        r.total_tokens,
        r.lead_agent_tokens,
        r.subagent_tokens,
        r.middleware_tokens,
        r.token_usage_by_model,
        r.metadata_json,
        e.seq,
        e.content,
        e.event_metadata
    FROM runs AS r
    JOIN run_events AS e ON e.run_id = r.run_id
    WHERE r.created_at >= :from_time
      AND r.created_at < :to_time
      AND lower(COALESCE(r.model_name, '')) = lower(:model_name)
      AND e.event_type = 'llm.ai.response'
    ORDER BY r.run_id, e.seq
    """
)

_BACKUP_DDL = text(
    """
    CREATE TABLE IF NOT EXISTS token_usage_correction_backups (
        correction_id VARCHAR(96) NOT NULL,
        run_id VARCHAR(64) NOT NULL,
        original_total_input_tokens BIGINT NOT NULL,
        original_total_output_tokens BIGINT NOT NULL,
        original_total_tokens BIGINT NOT NULL,
        original_lead_agent_tokens BIGINT NOT NULL,
        original_subagent_tokens BIGINT NOT NULL,
        original_middleware_tokens BIGINT NOT NULL,
        original_token_usage_by_model JSONB NOT NULL,
        original_metadata_json JSONB NOT NULL,
        correction_detail JSONB NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        rolled_back_at TIMESTAMPTZ,
        PRIMARY KEY (correction_id, run_id)
    )
    """
)


async def _load_corrections(
    engine: AsyncEngine,
    args: argparse.Namespace,
) -> list[RunCorrection]:
    async with engine.connect() as connection:
        result = await connection.execute(
            _AUDIT_QUERY,
            {
                "from_time": args.from_time,
                "to_time": args.to_time,
                "model_name": args.model,
            },
        )
        rows = [dict(row) for row in result.mappings().all()]
    return build_corrections(
        rows,
        suspicious_call_threshold=args.minimum_suspicious_call_tokens,
    )


def _summary(corrections: list[RunCorrection]) -> dict[str, Any]:
    return {
        "affected_runs": len(corrections),
        "suspicious_calls": sum(len(item.suspicious_calls) for item in corrections),
        "original_total_tokens": sum(item.original["total_tokens"] for item in corrections),
        "retained_total_tokens": sum(item.corrected["total_tokens"] for item in corrections),
        "quarantined_total_tokens": sum(item.original["total_tokens"] - item.corrected["total_tokens"] for item in corrections),
    }


def _print_audit(corrections: list[RunCorrection], args: argparse.Namespace) -> None:
    report = {
        "operation": args.operation,
        "policy": "quarantine_impossible_cumulative_stream_calls",
        "model": args.model,
        "from_time": args.from_time.isoformat(),
        "to_time": args.to_time.isoformat(),
        "minimum_suspicious_call_tokens": args.minimum_suspicious_call_tokens,
        "summary": _summary(corrections),
        "runs": [
            {
                "run_id": item.run_id,
                "suspicious_calls": [asdict(call) for call in item.suspicious_calls],
                "original_total_tokens": item.original["total_tokens"],
                "retained_total_tokens": item.corrected["total_tokens"],
            }
            for item in corrections
        ],
        "accuracy_note": ("The exact final provider snapshots are not recoverable from historical aggregates. This report removes only impossible calls and preserves all other recorded usage."),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


def _confirmed(args: argparse.Namespace) -> None:
    if not args.correction_id:
        raise SystemExit("--correction-id is required for apply and rollback")
    if args.confirm != args.correction_id:
        raise SystemExit("Refusing mutation: --confirm must exactly match --correction-id")


async def _apply(
    engine: AsyncEngine,
    corrections: list[RunCorrection],
    args: argparse.Namespace,
) -> None:
    _confirmed(args)
    if not corrections:
        raise SystemExit("No suspicious runs matched; nothing was changed")

    async with engine.begin() as connection:
        await connection.execute(_BACKUP_DDL)
        existing = await connection.scalar(
            text("SELECT count(*) FROM token_usage_correction_backups WHERE correction_id = :correction_id"),
            {"correction_id": args.correction_id},
        )
        if _non_negative_int(existing) > 0:
            raise RuntimeError(f"correction_id {args.correction_id!r} already exists; use a new id or rollback it")

        for item in corrections:
            detail = {
                "policy": "quarantine_impossible_cumulative_stream_calls",
                "model": args.model,
                "from_time": args.from_time.isoformat(),
                "to_time": args.to_time.isoformat(),
                "minimum_suspicious_call_tokens": args.minimum_suspicious_call_tokens,
                "suspicious_calls": [asdict(call) for call in item.suspicious_calls],
                "corrected": item.corrected,
            }
            await connection.execute(
                text(
                    """
                    INSERT INTO token_usage_correction_backups (
                        correction_id, run_id,
                        original_total_input_tokens, original_total_output_tokens,
                        original_total_tokens, original_lead_agent_tokens,
                        original_subagent_tokens, original_middleware_tokens,
                        original_token_usage_by_model, original_metadata_json,
                        correction_detail
                    ) VALUES (
                        :correction_id, :run_id,
                        :total_input_tokens, :total_output_tokens,
                        :total_tokens, :lead_agent_tokens,
                        :subagent_tokens, :middleware_tokens,
                        CAST(:token_usage_by_model AS JSONB),
                        CAST(:metadata_json AS JSONB),
                        CAST(:correction_detail AS JSONB)
                    )
                    """
                ),
                {
                    "correction_id": args.correction_id,
                    "run_id": item.run_id,
                    **{
                        key: item.original[key]
                        for key in (
                            "total_input_tokens",
                            "total_output_tokens",
                            "total_tokens",
                            "lead_agent_tokens",
                            "subagent_tokens",
                            "middleware_tokens",
                        )
                    },
                    "token_usage_by_model": json.dumps(
                        item.original["token_usage_by_model"],
                        ensure_ascii=False,
                    ),
                    "metadata_json": json.dumps(
                        item.original["metadata_json"],
                        ensure_ascii=False,
                    ),
                    "correction_detail": json.dumps(detail, ensure_ascii=False),
                },
            )

            corrected_metadata = copy.deepcopy(item.corrected["metadata_json"])
            corrected_metadata["token_usage_correction"] = {
                "correction_id": args.correction_id,
                "policy": "quarantine_impossible_cumulative_stream_calls",
                "suspicious_call_count": len(item.suspicious_calls),
            }
            await connection.execute(
                text(
                    """
                    UPDATE runs
                    SET total_input_tokens = :total_input_tokens,
                        total_output_tokens = :total_output_tokens,
                        total_tokens = :total_tokens,
                        lead_agent_tokens = :lead_agent_tokens,
                        subagent_tokens = :subagent_tokens,
                        middleware_tokens = :middleware_tokens,
                        token_usage_by_model = CAST(:token_usage_by_model AS JSONB),
                        metadata_json = CAST(:metadata_json AS JSONB),
                        updated_at = now()
                    WHERE run_id = :run_id
                    """
                ),
                {
                    "run_id": item.run_id,
                    **{
                        key: item.corrected[key]
                        for key in (
                            "total_input_tokens",
                            "total_output_tokens",
                            "total_tokens",
                            "lead_agent_tokens",
                            "subagent_tokens",
                            "middleware_tokens",
                        )
                    },
                    "token_usage_by_model": json.dumps(
                        item.corrected["token_usage_by_model"],
                        ensure_ascii=False,
                    ),
                    "metadata_json": json.dumps(
                        corrected_metadata,
                        ensure_ascii=False,
                    ),
                },
            )

    print(
        json.dumps(
            {
                "operation": "apply",
                "correction_id": args.correction_id,
                "status": "PASS",
                "summary": _summary(corrections),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def _rollback(engine: AsyncEngine, args: argparse.Namespace) -> None:
    _confirmed(args)
    async with engine.begin() as connection:
        table_exists = await connection.scalar(text("SELECT to_regclass('public.token_usage_correction_backups')"))
        if table_exists is None:
            raise RuntimeError("backup table does not exist; nothing can be rolled back")
        result = await connection.execute(
            text(
                """
                SELECT *
                FROM token_usage_correction_backups
                WHERE correction_id = :correction_id
                  AND rolled_back_at IS NULL
                ORDER BY run_id
                FOR UPDATE
                """
            ),
            {"correction_id": args.correction_id},
        )
        backups = [dict(row) for row in result.mappings().all()]
        if not backups:
            raise RuntimeError(f"no active backup rows found for correction_id {args.correction_id!r}")

        for row in backups:
            await connection.execute(
                text(
                    """
                    UPDATE runs
                    SET total_input_tokens = :total_input_tokens,
                        total_output_tokens = :total_output_tokens,
                        total_tokens = :total_tokens,
                        lead_agent_tokens = :lead_agent_tokens,
                        subagent_tokens = :subagent_tokens,
                        middleware_tokens = :middleware_tokens,
                        token_usage_by_model = CAST(:token_usage_by_model AS JSONB),
                        metadata_json = CAST(:metadata_json AS JSONB),
                        updated_at = now()
                    WHERE run_id = :run_id
                    """
                ),
                {
                    "run_id": row["run_id"],
                    "total_input_tokens": row["original_total_input_tokens"],
                    "total_output_tokens": row["original_total_output_tokens"],
                    "total_tokens": row["original_total_tokens"],
                    "lead_agent_tokens": row["original_lead_agent_tokens"],
                    "subagent_tokens": row["original_subagent_tokens"],
                    "middleware_tokens": row["original_middleware_tokens"],
                    "token_usage_by_model": json.dumps(
                        row["original_token_usage_by_model"],
                        ensure_ascii=False,
                    ),
                    "metadata_json": json.dumps(
                        row["original_metadata_json"],
                        ensure_ascii=False,
                    ),
                },
            )
        await connection.execute(
            text(
                """
                UPDATE token_usage_correction_backups
                SET rolled_back_at = now()
                WHERE correction_id = :correction_id
                  AND rolled_back_at IS NULL
                """
            ),
            {"correction_id": args.correction_id},
        )

    print(
        json.dumps(
            {
                "operation": "rollback",
                "correction_id": args.correction_id,
                "restored_runs": len(backups),
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include an explicit UTC offset")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("audit", "apply", "rollback"),
        nargs="?",
        default="audit",
    )
    parser.add_argument("--from-time", type=_datetime)
    parser.add_argument("--to-time", type=_datetime)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--minimum-suspicious-call-tokens",
        type=int,
        default=DEFAULT_SUSPICIOUS_CALL_THRESHOLD,
    )
    parser.add_argument("--correction-id")
    parser.add_argument("--confirm")
    parser.add_argument("--config", help="Optional explicit config.yaml path")
    return parser


def _config(args: argparse.Namespace) -> AppConfig:
    return AppConfig.from_file(args.config) if args.config else get_app_config()


async def _run(args: argparse.Namespace) -> None:
    config = _config(args)
    if config.database.backend != "postgres":
        raise SystemExit("This incident repair command supports PostgreSQL deployments only")
    engine = create_async_engine(
        config.database.app_sqlalchemy_url,
        pool_pre_ping=True,
    )
    try:
        if args.operation == "rollback":
            await _rollback(engine, args)
            return
        if args.from_time is None or args.to_time is None:
            raise SystemExit("--from-time and --to-time are required for audit and apply")
        if args.from_time >= args.to_time:
            raise SystemExit("--from-time must be earlier than --to-time")
        if args.minimum_suspicious_call_tokens <= 0:
            raise SystemExit("--minimum-suspicious-call-tokens must be positive")
        corrections = await _load_corrections(engine, args)
        if args.operation == "audit":
            _print_audit(corrections, args)
        else:
            await _apply(engine, corrections, args)
    finally:
        await engine.dispose()


def main() -> None:
    args = _parser().parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
