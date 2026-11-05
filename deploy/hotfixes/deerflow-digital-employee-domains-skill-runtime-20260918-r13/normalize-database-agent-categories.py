#!/usr/bin/env python3
"""Normalize production PostgreSQL Agent category IDs in one transaction.

The audited server uses the database Agent store backed by PostgreSQL.  This
operator step updates only ``agents.config.category_id`` and intentionally fails
closed if the live image is configured for another database backend.
"""

from __future__ import print_function

from sqlalchemy import select, text

from app.gateway.routers.agents import (
    CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES,
    resolve_digital_employee_category,
)
from deerflow.config.app_config import get_app_config
from deerflow.persistence.agents.model import AgentRow
from deerflow.persistence.agents.sql import get_sync_sessionmaker


def main() -> None:
    app_config = get_app_config()
    database = app_config.database
    database_backend = database.backend
    if database_backend != "postgres":
        raise SystemExit(
            "database_agent_category_normalization=FAIL expected_postgres_backend observed="
            + str(database_backend)
        )

    configured_store = getattr(getattr(app_config, "agent_storage", None), "backend", "file")
    allowed = {item.id for item in CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES.items}
    Session = get_sync_sessionmaker(database.app_sync_sqlalchemy_url)
    rows_seen = 0
    rows_migrated = 0

    with Session() as session:
        with session.begin():
            # This brief lock closes the insert/update race between the snapshot,
            # normalization, and in-transaction audit.
            session.execute(text("LOCK TABLE agents IN SHARE ROW EXCLUSIVE MODE"))
            rows = list(session.execute(select(AgentRow).with_for_update()).scalars())
            rows_seen = len(rows)
            for row in rows:
                payload = dict(row.config or {})
                classification_input = dict(payload)
                classification_input.setdefault("name", row.name)
                target = resolve_digital_employee_category(classification_input)
                if target not in allowed:
                    raise RuntimeError("resolver returned an unsupported category: " + str(target))
                if payload.get("category_id") == target:
                    continue
                payload["category_id"] = target
                row.config = payload
                rows_migrated += 1

            session.flush()
            invalid = [
                "%s/%s" % (row.user_id, row.name)
                for row in rows
                if (row.config or {}).get("category_id") not in allowed
            ]
            if invalid:
                raise RuntimeError(
                    "in-transaction category audit failed: " + ", ".join(invalid[:20])
                )

    print("configured_agent_storage_backend=" + str(configured_store))
    print("database_backend=" + str(database_backend))
    print("database_agent_category_rows_seen=%d" % rows_seen)
    print("database_agent_category_rows_migrated=%d" % rows_migrated)
    print("database_agent_category_normalization=PASS")


if __name__ == "__main__":
    main()
