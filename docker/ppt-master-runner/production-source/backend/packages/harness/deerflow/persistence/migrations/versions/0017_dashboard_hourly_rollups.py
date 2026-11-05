"""Add completed-hour dashboard rollups.

Revision ID: 0017_dashboard_hourly_rollups
Revises: 0016_dashboard_daily_rollups
Create Date: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017_dashboard_hourly_rollups"
down_revision: str | Sequence[str] | None = "0016_dashboard_daily_rollups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("dashboard_hourly_rollups"):
        op.create_table(
            "dashboard_hourly_rollups",
            sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("source_run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_thread_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_feedback_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("source_external_click_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("build_duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("bucket_start"),
        )
        op.create_index(
            "ix_dashboard_hourly_rollups_refreshed_at",
            "dashboard_hourly_rollups",
            ["refreshed_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("dashboard_hourly_rollups"):
        op.drop_index("ix_dashboard_hourly_rollups_refreshed_at", table_name="dashboard_hourly_rollups")
        op.drop_table("dashboard_hourly_rollups")
