"""Restore announcements for databases already stamped at enterprise platform.

The announcement feature was temporarily removed after revision 0006 had been
applied in some deployments. Those databases retained the Alembic revision but
lost the table, so replaying 0006 could not repair them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_restore_announcements"
down_revision: str | Sequence[str] | None = "0006_enterprise_platform"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("announcements"):
        return

    op.create_table(
        "announcements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("dismissible", sa.Boolean(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_by_email", sa.String(length=320), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("updated_by_email", sa.String(length=320), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_announcements_status", "announcements", ["status"])
    op.create_index("ix_announcements_starts_at", "announcements", ["starts_at"])
    op.create_index("ix_announcements_ends_at", "announcements", ["ends_at"])
    op.create_index("ix_announcements_created_at", "announcements", ["created_at"])
    op.create_index("ix_announcements_created_by_user_id", "announcements", ["created_by_user_id"])
    op.create_index("ix_announcements_updated_by_user_id", "announcements", ["updated_by_user_id"])
    op.create_index("ix_announcements_status_window", "announcements", ["status", "starts_at", "ends_at"])
    op.create_index("ix_announcements_updated", "announcements", ["updated_at"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("announcements"):
        op.drop_table("announcements")
