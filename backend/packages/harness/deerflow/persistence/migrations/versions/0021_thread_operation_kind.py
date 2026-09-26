"""Exclusive checkpoint-write reservations (upstream #4437, enterprise chain).

Revision ID: 0021_thread_operation_kind
Revises: 0020_run_cancel_request
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

revision: str = "0021_thread_operation_kind"
down_revision: str | Sequence[str] | None = "0020_run_cancel_request"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    from deerflow.persistence.migrations._helpers import safe_add_column

    safe_add_column("runs", sa.Column("operation_kind", sa.String(length=32), nullable=False, server_default=sa.text("'run'")))


def downgrade() -> None:
    from alembic import op

    from deerflow.persistence.migrations._helpers import safe_drop_column

    connection = op.get_bind()
    if "operation_kind" not in {column["name"] for column in sa.inspect(connection).get_columns("runs")}:
        return
    # Old runtimes cannot distinguish internal reservations (including orphan
    # evidence) from user runs. Retain the discriminator; never silently delete
    # evidence or expose it as chat history during an automatic downgrade.
    if connection.execute(sa.text("SELECT COUNT(*) FROM runs WHERE operation_kind <> 'run'")).scalar():
        raise RuntimeError("Cannot downgrade while checkpoint-write reservation records exist")
    safe_drop_column("runs", "operation_kind")
