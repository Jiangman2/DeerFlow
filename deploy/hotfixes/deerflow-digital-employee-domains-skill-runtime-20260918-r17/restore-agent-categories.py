#!/usr/bin/env python3
"""Capture or exactly restore ``agents.config.category_id``.

The snapshot deliberately contains only category-key presence and value.  A
rollback therefore preserves unrelated configuration changes and any rows
created after the snapshot while restoring every snapshotted row exactly.
"""

from __future__ import print_function

import argparse
import json
import sys


SNAPSHOT_VERSION = 1
CATEGORY_KEY = "category_id"


def build_snapshot(rows):
    snapshot_rows = []
    seen = set()
    for row in sorted(rows, key=lambda item: str(item.id)):
        row_id = str(row.id)
        if not row_id or row_id in seen:
            raise ValueError("invalid or duplicate Agent id in category snapshot: " + row_id)
        seen.add(row_id)
        config = dict(row.config or {})
        item = {
            "id": row_id,
            "has_category": CATEGORY_KEY in config,
        }
        if item["has_category"]:
            item["category"] = config[CATEGORY_KEY]
        snapshot_rows.append(item)
    return {"version": SNAPSHOT_VERSION, "rows": snapshot_rows}


def validate_snapshot(payload):
    if not isinstance(payload, dict) or payload.get("version") != SNAPSHOT_VERSION:
        raise ValueError("unsupported Agent category snapshot version")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Agent category snapshot rows must be a list")
    normalized = []
    seen = set()
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError("Agent category snapshot row must be an object")
        row_id = item.get("id")
        has_category = item.get("has_category")
        if not isinstance(row_id, str) or not row_id or row_id in seen:
            raise ValueError("invalid or duplicate Agent id in category snapshot")
        if not isinstance(has_category, bool):
            raise ValueError("has_category must be a boolean for Agent " + row_id)
        if has_category and "category" not in item:
            raise ValueError("category value missing for Agent " + row_id)
        if not has_category and "category" in item:
            raise ValueError("category value present for absent key on Agent " + row_id)
        seen.add(row_id)
        normalized.append(dict(item))
    return normalized


def restore_configurations(current_by_id, snapshot):
    snapshot_rows = validate_snapshot(snapshot)
    missing = sorted(item["id"] for item in snapshot_rows if item["id"] not in current_by_id)
    if missing:
        raise ValueError("snapshotted Agent rows are missing: " + ", ".join(missing[:20]))
    restored = {str(row_id): dict(config or {}) for row_id, config in current_by_id.items()}
    for item in snapshot_rows:
        config = restored[item["id"]]
        if item["has_category"]:
            config[CATEGORY_KEY] = item["category"]
        else:
            config.pop(CATEGORY_KEY, None)
    return restored


def _database_context():
    from deerflow.config.app_config import get_app_config
    from deerflow.persistence.agents.model import AgentRow
    from deerflow.persistence.agents.sql import get_sync_sessionmaker

    app_config = get_app_config()
    database = app_config.database
    if database.backend != "postgres":
        raise RuntimeError(
            "Agent category snapshot requires postgres; observed=" + str(database.backend)
        )
    Session = get_sync_sessionmaker(database.app_sync_sqlalchemy_url)
    return AgentRow, Session


def capture():
    from sqlalchemy import select, text

    AgentRow, Session = _database_context()
    with Session() as session:
        with session.begin():
            session.execute(text("LOCK TABLE agents IN SHARE ROW EXCLUSIVE MODE"))
            rows = list(session.execute(select(AgentRow).with_for_update()).scalars())
            payload = build_snapshot(rows)
    json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")


def restore(snapshot_path):
    from sqlalchemy import select, text

    with open(snapshot_path, encoding="utf-8") as handle:
        snapshot = json.load(handle)
    snapshot_rows = validate_snapshot(snapshot)
    AgentRow, Session = _database_context()
    changed = 0
    with Session() as session:
        with session.begin():
            session.execute(text("LOCK TABLE agents IN SHARE ROW EXCLUSIVE MODE"))
            rows = list(session.execute(select(AgentRow).with_for_update()).scalars())
            rows_by_id = {str(row.id): row for row in rows}
            restored = restore_configurations(
                {row_id: dict(row.config or {}) for row_id, row in rows_by_id.items()},
                snapshot,
            )
            for item in snapshot_rows:
                row = rows_by_id[item["id"]]
                target = restored[item["id"]]
                if dict(row.config or {}) != target:
                    row.config = target
                    changed += 1
            session.flush()
            observed = {row_id: dict(row.config or {}) for row_id, row in rows_by_id.items()}
            verified = restore_configurations(observed, snapshot)
            for item in snapshot_rows:
                if observed[item["id"]] != verified[item["id"]]:
                    raise RuntimeError("Agent category rollback verification failed: " + item["id"])
    print("database_agent_category_rows_snapshotted=%d" % len(snapshot_rows))
    print("database_agent_category_rows_restored=%d" % changed)
    print("database_agent_category_exact_restore=PASS")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--capture", action="store_true")
    group.add_argument("--restore", metavar="SNAPSHOT")
    args = parser.parse_args()
    if args.capture:
        capture()
    else:
        restore(args.restore)


if __name__ == "__main__":
    main()
