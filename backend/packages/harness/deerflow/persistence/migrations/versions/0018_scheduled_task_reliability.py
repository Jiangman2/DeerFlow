"""Add durable scheduled-task queue and occurrence ordering.

Revision ID: 0018_scheduled_task_reliability
Revises: 0017_dashboard_hourly_rollups
Create Date: 2026-09-12
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0018_scheduled_task_reliability"
down_revision: str | Sequence[str] | None = "0017_dashboard_hourly_rollups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_ACTIVE_INDEX = "uq_scheduled_task_run_active"
_OCCURRENCE_INDEX = "uq_scheduled_task_run_occurrence_seq"


def _existing_indexes(table_name: str) -> set[str]:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(table_name):
        return set()
    return {str(index["name"]) for index in sa.inspect(bind).get_indexes(table_name) if index.get("name")}


def _dedupe_active_runs() -> None:
    """Keep the newest active row for each task before adding uniqueness."""
    bind = op.get_bind()
    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT id, task_id
                FROM scheduled_task_runs AS r1
                WHERE r1.status IN ('queued', 'running')
                  AND EXISTS (
                    SELECT 1
                    FROM scheduled_task_runs AS r2
                    WHERE r2.task_id = r1.task_id
                      AND r2.status IN ('queued', 'running')
                      AND r2.id <> r1.id
                      AND (
                        r2.created_at > r1.created_at
                        OR (r2.created_at = r1.created_at AND r2.id > r1.id)
                      )
                  )
                """
            )
        ).fetchall()
    )
    if not rows:
        return
    for run_id, task_id in rows:
        logger.warning(
            "Superseding duplicate active scheduled run %s on task %s",
            run_id,
            task_id,
        )
    bind.execute(
        sa.text(
            """
            UPDATE scheduled_task_runs
            SET status = 'interrupted',
                error = :error_message,
                finished_at = :finished_at
            WHERE status IN ('queued', 'running')
              AND EXISTS (
                SELECT 1
                FROM scheduled_task_runs AS r2
                WHERE r2.task_id = scheduled_task_runs.task_id
                  AND r2.status IN ('queued', 'running')
                  AND r2.id <> scheduled_task_runs.id
                  AND (
                    r2.created_at > scheduled_task_runs.created_at
                    OR (
                      r2.created_at = scheduled_task_runs.created_at
                      AND r2.id > scheduled_task_runs.id
                    )
                  )
              )
            """
        ).bindparams(
            sa.bindparam("error_message"),
            sa.bindparam("finished_at", type_=sa.DateTime(timezone=True)),
        ),
        {
            "error_message": ("interrupted during scheduled-task reliability migration: superseded by a newer active run"),
            "finished_at": datetime.now(UTC),
        },
    )


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_add_column

    # Legacy queued rows predate durable queue semantics and may already have
    # launched. Never replay them during the upgrade.
    op.execute(
        sa.text("UPDATE scheduled_task_runs SET status = 'interrupted', error = 'interrupted: gateway upgraded before the queued run reached a terminal state', finished_at = COALESCE(finished_at, CURRENT_TIMESTAMP) WHERE status = 'queued'")
    )
    safe_add_column(
        "scheduled_task_runs",
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
    )
    safe_add_column(
        "scheduled_task_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    safe_add_column(
        "scheduled_task_runs",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    safe_add_column(
        "scheduled_tasks",
        sa.Column(
            "last_occurrence_seq",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    safe_add_column(
        "scheduled_task_runs",
        sa.Column("occurrence_seq", sa.BigInteger(), nullable=True),
    )
    safe_add_column(
        "scheduled_task_runs",
        sa.Column("launch_accounted", sa.Boolean(), nullable=True),
    )

    indexes = _existing_indexes("scheduled_task_runs")
    if _ACTIVE_INDEX in indexes:
        op.drop_index(_ACTIVE_INDEX, table_name="scheduled_task_runs")
    _dedupe_active_runs()
    predicate = sa.text("status IN ('queued', 'launching', 'running')")
    op.create_index(
        _ACTIVE_INDEX,
        "scheduled_task_runs",
        ["task_id"],
        unique=True,
        sqlite_where=predicate,
        postgresql_where=predicate,
    )
    if _OCCURRENCE_INDEX not in _existing_indexes("scheduled_task_runs"):
        op.create_index(
            _OCCURRENCE_INDEX,
            "scheduled_task_runs",
            ["task_id", "occurrence_seq"],
            unique=True,
        )
    op.execute(sa.text("UPDATE scheduled_tasks SET overlap_policy = 'enqueue' WHERE overlap_policy = 'skip'"))


def downgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_drop_column

    indexes = _existing_indexes("scheduled_task_runs")
    if _OCCURRENCE_INDEX in indexes:
        op.drop_index(_OCCURRENCE_INDEX, table_name="scheduled_task_runs")
    if _ACTIVE_INDEX in indexes:
        op.drop_index(_ACTIVE_INDEX, table_name="scheduled_task_runs")
    op.execute(sa.text("UPDATE scheduled_task_runs SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL WHERE status = 'launching'"))
    op.execute(sa.text("UPDATE scheduled_tasks SET overlap_policy = 'skip' WHERE overlap_policy = 'enqueue'"))
    safe_drop_column("scheduled_task_runs", "launch_accounted")
    safe_drop_column("scheduled_task_runs", "occurrence_seq")
    safe_drop_column("scheduled_tasks", "last_occurrence_seq")
    safe_drop_column("scheduled_task_runs", "attempt_count")
    safe_drop_column("scheduled_task_runs", "lease_expires_at")
    safe_drop_column("scheduled_task_runs", "lease_owner")
