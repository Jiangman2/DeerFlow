"""Add versioned daily dashboard rollups and refresh state.

Revision ID: 0016_dashboard_daily_rollups
Revises: 0015_dashboard_query_indexes
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_dashboard_daily_rollups"
down_revision: str | Sequence[str] | None = "0015_dashboard_query_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("dashboard_daily_rollups"):
        op.create_table(
            "dashboard_daily_rollups",
            sa.Column("day", sa.Date(), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("source_run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_thread_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_feedback_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_external_click_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("build_duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("day"),
        )
        op.create_index(
            "ix_dashboard_daily_rollups_refreshed_at",
            "dashboard_daily_rollups",
            ["refreshed_at"],
            unique=False,
        )
    if not sa.inspect(bind).has_table("dashboard_rollup_state"):
        op.create_table(
            "dashboard_rollup_state",
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("last_change_scan_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("pending_days_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("key"),
        )

    # ``updated_at`` drives inexpensive late-change discovery for historical
    # run days.  Create it online because ``runs`` is normally the largest
    # mutable dashboard source on an existing server.
    run_indexes = {str(item["name"]) for item in sa.inspect(bind).get_indexes("runs") if item.get("name")}
    if "ix_runs_updated_at" not in run_indexes:
        if bind.dialect.name == "postgresql":
            with op.get_context().autocommit_block():
                op.execute(sa.text('CREATE INDEX CONCURRENTLY IF NOT EXISTS "ix_runs_updated_at" ON "runs" ("updated_at")'))
        else:
            op.create_index("ix_runs_updated_at", "runs", ["updated_at"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("runs"):
        run_indexes = {str(item["name"]) for item in sa.inspect(bind).get_indexes("runs") if item.get("name")}
        if "ix_runs_updated_at" in run_indexes:
            if bind.dialect.name == "postgresql":
                with op.get_context().autocommit_block():
                    op.execute(sa.text('DROP INDEX CONCURRENTLY IF EXISTS "ix_runs_updated_at"'))
            else:
                op.drop_index("ix_runs_updated_at", table_name="runs")
    if sa.inspect(bind).has_table("dashboard_rollup_state"):
        op.drop_table("dashboard_rollup_state")
    if sa.inspect(bind).has_table("dashboard_daily_rollups"):
        op.drop_index("ix_dashboard_daily_rollups_refreshed_at", table_name="dashboard_daily_rollups")
        op.drop_table("dashboard_daily_rollups")
