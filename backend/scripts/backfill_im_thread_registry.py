#!/usr/bin/env python
"""Backfill ``im_thread_registry`` from historical ``im_message_links``.

Run once after upgrading to migration ``0024_im_s08_archive`` so that IM threads
created before S08 appear in the Web session sidebar. The script is deliberately
explicit and offline-only: nothing in the request path calls it.

Safety properties:

* dry-run by default (``--dry-run``), nothing is written without ``--apply``;
* idempotent: ``thread_id`` is the primary key and an existing row always wins,
  so a second run inserts nothing (S08-X7);
* batch-addressable: ``--batch-id`` suffixes ``classification_source`` with
  ``#<batch>`` and ``--undo-batch`` deletes exactly those rows;
* never guesses an owner: links with an empty ``owner_user_id`` are reported as
  ``NO_OWNER`` for manual repair (``--repair-owner`` prints the list);
* reports both JSON and CSV so the ``HEURISTIC`` rows (the ones most likely to be
  misclassified) can be reviewed by hand (S08-X8).

Usage::

    python backend/scripts/backfill_im_thread_registry.py --dry-run
    python backend/scripts/backfill_im_thread_registry.py --apply --batch-id <uuid>
    python backend/scripts/backfill_im_thread_registry.py --undo-batch <uuid>
    python backend/scripts/backfill_im_thread_registry.py --repair-owner
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.im_routing.model import ImMessageLinkRow, ImSessionPointerRow
from deerflow.persistence.im_routing.registry import ImThreadRegistryRepository
from deerflow.persistence.im_routing.registry_model import ImThreadRegistryRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow

STATUS_SKIPPED = "SKIPPED"
STATUS_WOULD_INSERT = "WOULD_INSERT"
STATUS_INSERTED = "INSERTED"
STATUS_CONFLICT = "CONFLICT"

REASON_NO_OWNER = "NO_OWNER"
REASON_THREAD_MISSING = "THREAD_MISSING"

SOURCE_POINTER = "BACKFILL_POINTER"
SOURCE_HEURISTIC = "HEURISTIC"

REPORT_FIELDS = (
    "thread_id",
    "owner_user_id",
    "external_conversation_id",
    "channel_type",
    "classification_source",
    "last_active_at",
    "status",
    "reason",
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str:
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), max(size, 1)):
        yield items[start : start + max(size, 1)]


async def _collect_candidates(session: AsyncSession) -> list[dict[str, Any]]:
    """Group inbound links by thread: newest link, owner and chat."""
    statement = (
        select(
            ImMessageLinkRow.thread_id,
            func.max(ImMessageLinkRow.owner_user_id),
            func.max(ImMessageLinkRow.external_conversation_id),
            func.max(ImMessageLinkRow.created_at),
        )
        .where(ImMessageLinkRow.direction == "inbound")
        .group_by(ImMessageLinkRow.thread_id)
        .order_by(ImMessageLinkRow.thread_id)
    )
    rows = (await session.execute(statement)).all()
    return [
        {
            "thread_id": thread_id or "",
            "owner_user_id": owner or "",
            "external_conversation_id": chat_id or "",
            "last_link_at": last_at,
        }
        for thread_id, owner, chat_id, last_at in rows
    ]


async def _collect_pointers(session: AsyncSession) -> dict[tuple[str, str], tuple[str, int]]:
    statement = select(
        ImSessionPointerRow.owner_user_id,
        ImSessionPointerRow.external_conversation_id,
        ImSessionPointerRow.channel_type,
        ImSessionPointerRow.generation,
    ).where(ImSessionPointerRow.external_topic_id == "")
    rows = (await session.execute(statement)).all()
    return {(owner, chat): (channel_type, generation) for owner, chat, channel_type, generation in rows}


async def _collect_sender_counts(session: AsyncSession) -> dict[tuple[str, str], int]:
    """Distinct inbound senders per ``(owner, chat)`` — the HEURISTIC signal."""
    statement = (
        select(
            ImMessageLinkRow.owner_user_id,
            ImMessageLinkRow.external_conversation_id,
            func.count(func.distinct(ImMessageLinkRow.sender_external_id)),
        )
        .where(ImMessageLinkRow.direction == "inbound")
        .group_by(ImMessageLinkRow.owner_user_id, ImMessageLinkRow.external_conversation_id)
    )
    rows = (await session.execute(statement)).all()
    return {(owner, chat): int(count or 0) for owner, chat, count in rows}


async def _collect_thread_meta(session: AsyncSession, thread_ids: list[str]) -> dict[str, tuple[str | None, datetime | None]]:
    if not thread_ids:
        return {}
    statement = select(ThreadMetaRow.thread_id, ThreadMetaRow.display_name, ThreadMetaRow.updated_at).where(
        ThreadMetaRow.thread_id.in_(thread_ids)
    )
    rows = (await session.execute(statement)).all()
    return {thread_id: (display_name, updated_at) for thread_id, display_name, updated_at in rows}


async def _collect_existing(session: AsyncSession, thread_ids: list[str]) -> set[str]:
    if not thread_ids:
        return set()
    statement = select(ImThreadRegistryRow.thread_id).where(ImThreadRegistryRow.thread_id.in_(thread_ids))
    return set((await session.execute(statement)).scalars())


def _write_reports(report_dir: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    mode = payload["mode"]
    json_path = report_dir / f"im-registry-backfill-{mode}-{stamp}.json"
    csv_path = report_dir / f"im-registry-backfill-{mode}-{stamp}.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(REPORT_FIELDS))
        writer.writeheader()
        for row in payload["rows"]:
            writer.writerow({key: row.get(key, "") for key in REPORT_FIELDS})
    return json_path, csv_path


async def _undo_batch(session: AsyncSession, batch_id: str) -> int:
    # ``like`` on a suffix: the batch id is a UUID, so no pattern escaping is needed.
    result = await session.execute(
        delete(ImThreadRegistryRow).where(ImThreadRegistryRow.classification_source.like(f"%#{batch_id}"))
    )
    await session.commit()
    return int(result.rowcount or 0)


async def backfill(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    apply: bool = False,
    batch_id: str = "",
    limit: int = 500,
    report_dir: Path | None = None,
    undo_batch: str = "",
    repair_owner: bool = False,
) -> dict[str, Any]:
    """Execute one backfill pass and return the report payload.

    ``apply=False`` (the default) only reports; no row is written.
    """
    batch_id = batch_id.strip()
    report: dict[str, Any] = {
        "generated_at": _utc_now().isoformat(),
        "mode": "undo" if undo_batch else ("repair-owner" if repair_owner else ("apply" if apply else "dry-run")),
        "batch_id": batch_id,
        "limit": limit,
        "totals": {
            "candidates": 0,
            STATUS_SKIPPED: 0,
            STATUS_WOULD_INSERT: 0,
            STATUS_INSERTED: 0,
            STATUS_CONFLICT: 0,
            "deleted": 0,
        },
        "rows": [],
        "heuristic": [],
        "no_owner": [],
    }

    async with session_factory() as session:
        if undo_batch:
            deleted = await _undo_batch(session, undo_batch)
            report["totals"]["deleted"] = deleted
            report_dir = report_dir or Path("./reports")
            json_path, csv_path = _write_reports(report_dir, report)
            report["report_paths"] = {"json": str(json_path), "csv": str(csv_path)}
            return report

        candidates = await _collect_candidates(session)
        report["totals"]["candidates"] = len(candidates)

        if repair_owner:
            # Manual-repair listing only: an empty owner has no trustworthy target.
            report["no_owner"] = [
                {
                    "thread_id": item["thread_id"],
                    "external_conversation_id": item["external_conversation_id"],
                    "owner_user_id": "",
                }
                for item in candidates
                if not item["owner_user_id"]
            ]
            report["totals"][STATUS_SKIPPED] = len(report["no_owner"])
            report_dir = report_dir or Path("./reports")
            json_path, csv_path = _write_reports(report_dir, report)
            report["report_paths"] = {"json": str(json_path), "csv": str(csv_path)}
            return report

        pointers = await _collect_pointers(session)
        sender_counts = await _collect_sender_counts(session)
        meta = await _collect_thread_meta(session, [item["thread_id"] for item in candidates])
        existing = await _collect_existing(session, [item["thread_id"] for item in candidates])
        repo = ImThreadRegistryRepository(session_factory)

    for chunk in _chunks(candidates, limit):
        pending: list[dict[str, Any]] = []
        for item in chunk:
            row = _plan_row(item, pointers, sender_counts, meta, existing, batch_id)
            report["rows"].append(row)
            if row["status"] == STATUS_SKIPPED:
                report["totals"][STATUS_SKIPPED] += 1
                if row["reason"] == REASON_NO_OWNER:
                    report["no_owner"].append(
                        {
                            "thread_id": row["thread_id"],
                            "external_conversation_id": row["external_conversation_id"],
                            "owner_user_id": "",
                        }
                    )
                continue
            if row["classification_source"].startswith(SOURCE_HEURISTIC):
                report["heuristic"].append(row)
            if row["status"] == STATUS_CONFLICT:
                report["totals"][STATUS_CONFLICT] += 1
                continue
            if not apply:
                report["totals"][STATUS_WOULD_INSERT] += 1
                continue
            pending.append(row)

        if apply:
            for row in pending:
                await repo.register(
                    thread_id=row["thread_id"],
                    owner_user_id=row["owner_user_id"],
                    channel_type=row["channel_type"],
                    external_conversation_id=row["external_conversation_id"],
                    generation=row["generation"],
                    classification_source=row["classification_source"],
                    title_snapshot=row["title_snapshot"],
                    last_active_at=row["last_active_at_value"],
                )
                report["totals"][STATUS_INSERTED] += 1
                row["status"] = STATUS_INSERTED

    for row in report["rows"]:
        row.pop("last_active_at_value", None)
        row.pop("generation", None)
        row.pop("title_snapshot", None)

    report_dir = report_dir or Path("./reports")
    json_path, csv_path = _write_reports(report_dir, report)
    report["report_paths"] = {"json": str(json_path), "csv": str(csv_path)}
    return report


def _plan_row(
    item: dict[str, Any],
    pointers: dict[tuple[str, str], tuple[str, int]],
    sender_counts: dict[tuple[str, str], int],
    meta: dict[str, tuple[str | None, datetime | None]],
    existing: set[str],
    batch_id: str,
) -> dict[str, Any]:
    thread_id = item["thread_id"]
    owner = item["owner_user_id"]
    chat_id = item["external_conversation_id"]
    row: dict[str, Any] = {
        "thread_id": thread_id,
        "owner_user_id": owner,
        "external_conversation_id": chat_id,
        "channel_type": "",
        "classification_source": "",
        "last_active_at": "",
        "status": STATUS_SKIPPED,
        "reason": "",
        "generation": 1,
        "title_snapshot": None,
        "last_active_at_value": None,
    }

    if not owner:
        row["reason"] = REASON_NO_OWNER
        return row
    if thread_id not in meta:
        row["reason"] = REASON_THREAD_MISSING
        return row

    display_name, updated_at = meta[thread_id]
    pointer = pointers.get((owner, chat_id))
    if pointer is not None:
        channel_type, generation = pointer
        source = SOURCE_POINTER
    else:
        senders = sender_counts.get((owner, chat_id), 0)
        channel_type = "group" if senders >= 2 else "private"
        generation = 1
        source = SOURCE_HEURISTIC
    if batch_id:
        source = f"{source}#{batch_id}"

    last_active = item["last_link_at"] or updated_at or _utc_now()
    row.update(
        {
            "channel_type": channel_type,
            "classification_source": source,
            "last_active_at": _iso(last_active),
            "status": STATUS_CONFLICT if thread_id in existing else STATUS_WOULD_INSERT,
            "generation": generation,
            "title_snapshot": display_name,
            "last_active_at_value": last_active,
        }
    )
    return row


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill im_thread_registry from historical im_message_links (dry-run by default).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Report only; this is already the default.")
    parser.add_argument("--apply", action="store_true", help="Write the planned rows.")
    parser.add_argument("--batch-id", default="", help="Suffix classification_source with #<batch> so it can be undone.")
    parser.add_argument("--undo-batch", default="", help="Delete the rows inserted by one batch id.")
    parser.add_argument("--repair-owner", action="store_true", help="Only list rows whose owner is empty.")
    parser.add_argument("--limit", type=int, default=500, help="Rows per write batch (default 500).")
    parser.add_argument("--report-dir", default="./reports", help="Directory for the JSON and CSV reports.")
    return parser


async def _run(args: argparse.Namespace) -> int:
    # Imported lazily so ``--help`` works without a configured database.
    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine_from_config

    config = get_app_config()
    database = config.database
    if getattr(database, "backend", None) not in ("sqlite", "postgres"):
        print(
            f"database.backend is {getattr(database, 'backend', None)!r}; need 'sqlite' or 'postgres' in config.yaml.",
            file=sys.stderr,
        )
        return 1

    await init_engine_from_config(database)
    session_factory = get_session_factory()
    if session_factory is None:
        print("Persistence engine did not initialize.", file=sys.stderr)
        return 1
    try:
        batch_id = args.batch_id.strip()
        if args.apply and not batch_id:
            batch_id = uuid.uuid4().hex
        report = await backfill(
            session_factory,
            apply=args.apply and not args.dry_run,
            batch_id=batch_id,
            limit=args.limit,
            report_dir=Path(args.report_dir),
            undo_batch=args.undo_batch.strip(),
            repair_owner=args.repair_owner,
        )
    finally:
        await close_engine()

    totals = report["totals"]
    print(f"mode={report['mode']} batch_id={report['batch_id'] or '-'} limit={report['limit']}")
    print(
        "candidates={candidates} inserted={INSERTED} would_insert={WOULD_INSERT} conflict={CONFLICT} "
        "skipped={SKIPPED} deleted={deleted}".format(**totals)
    )
    if report["heuristic"]:
        print(f"HEURISTIC rows needing review: {len(report['heuristic'])}")
        for row in report["heuristic"]:
            print(f"  {row['thread_id']} chat={row['external_conversation_id']} -> {row['channel_type']}")
    if report["no_owner"]:
        print(f"NO_OWNER rows needing manual repair: {len(report['no_owner'])}")
        for row in report["no_owner"]:
            print(f"  thread={row['thread_id']} chat={row['external_conversation_id']}")
    print(f"report: {report['report_paths']['json']}")
    print(f"report: {report['report_paths']['csv']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.apply and args.undo_batch:
        print("--apply and --undo-batch are mutually exclusive.", file=sys.stderr)
        return 1
    if args.undo_batch and args.repair_owner:
        print("--undo-batch and --repair-owner are mutually exclusive.", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_run(args))
    except Exception as exc:  # noqa: BLE001 - operator CLI: report, never traceback-swallow silently
        print(f"backfill failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())