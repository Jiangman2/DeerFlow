"""Persist the selected model and mode for scheduled task runs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0010_scheduled_task_run_context"
down_revision: str | Sequence[str] | None = "0009_thread_creation_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "scheduled_tasks",
        sa.Column(
            "run_context",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    safe_drop_column("scheduled_tasks", "run_context")
