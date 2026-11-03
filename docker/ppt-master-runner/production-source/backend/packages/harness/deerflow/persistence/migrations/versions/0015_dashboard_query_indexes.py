"""Add covering time indexes for bounded dashboard queries.

Revision ID: 0015_dashboard_query_indexes
Revises: 0014_oa_policy_flow_jobs
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_dashboard_query_indexes"
down_revision: str | Sequence[str] | None = "0014_oa_policy_flow_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_users_created_at", "users", ("created_at",)),
    ("ix_runs_created_at", "runs", ("created_at",)),
    ("ix_runs_status_created_at", "runs", ("status", "created_at")),
    ("ix_runs_model_created_at", "runs", ("model_name", "created_at")),
    ("ix_runs_assistant_created_at", "runs", ("assistant_id", "created_at")),
    ("ix_runs_user_created_at", "runs", ("user_id", "created_at")),
    ("ix_run_events_created_at", "run_events", ("created_at",)),
    ("ix_run_events_type_created_at", "run_events", ("event_type", "created_at")),
    ("ix_run_events_run_created_at", "run_events", ("run_id", "created_at")),
    ("ix_feedback_created_at", "feedback", ("created_at",)),
    ("ix_feedback_run_created_at", "feedback", ("run_id", "created_at")),
    ("ix_scheduled_task_runs_created_at", "scheduled_task_runs", ("created_at",)),
    ("ix_scheduled_task_runs_task_created_at", "scheduled_task_runs", ("task_id", "created_at")),
    ("ix_audit_logs_action_time", "audit_logs", ("action", "created_at")),
)


def _existing_indexes(bind: sa.Connection, table_name: str) -> set[str]:
    if not sa.inspect(bind).has_table(table_name):
        return set()
    return {str(item["name"]) for item in sa.inspect(bind).get_indexes(table_name) if item.get("name")}


def upgrade() -> None:
    bind = op.get_bind()
    pending = [item for item in _INDEXES if item[0] not in _existing_indexes(bind, item[1])]
    if bind.dialect.name == "postgresql":
        # Dashboard tables can be large on an existing server.  Concurrent
        # creation avoids holding a write-blocking table lock during hot update.
        with op.get_context().autocommit_block():
            for index_name, table_name, columns in pending:
                column_sql = ", ".join(f'"{column}"' for column in columns)
                op.execute(sa.text(f'CREATE INDEX CONCURRENTLY IF NOT EXISTS "{index_name}" ON "{table_name}" ({column_sql})'))
        return

    for index_name, table_name, columns in pending:
        op.create_index(index_name, table_name, list(columns), unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    existing_by_table = {table_name: _existing_indexes(bind, table_name) for _, table_name, _ in _INDEXES}
    removable = [(index_name, table_name) for index_name, table_name, _ in _INDEXES if index_name in existing_by_table[table_name]]
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for index_name, _ in reversed(removable):
                op.execute(sa.text(f'DROP INDEX CONCURRENTLY IF EXISTS "{index_name}"'))
        return

    for index_name, table_name in reversed(removable):
        op.drop_index(index_name, table_name=table_name)
