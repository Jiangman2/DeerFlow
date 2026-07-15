"""Add scheduled task run context.

Revision ID: 0006_scheduled_task_run_context
Revises: 0005_user_display_name
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column


revision = "0006_scheduled_task_run_context"
down_revision = "0005_user_display_name"
branch_labels = None
depends_on = None


def upgrade() -> None:
    safe_add_column("scheduled_tasks", sa.Column("run_context", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade() -> None:
    safe_drop_column("scheduled_tasks", "run_context")
