"""Drop the retired administrator-managed site navigation table."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_drop_site_navigation"
down_revision: str | Sequence[str] | None = "0007_restore_announcements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("site_navigation"):
        op.drop_table("site_navigation")


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("site_navigation"):
        return

    op.create_table(
        "site_navigation",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("created_by_email", sa.String(length=320), nullable=True),
        sa.Column("updated_by_user_id", sa.String(length=64), nullable=True),
        sa.Column("updated_by_email", sa.String(length=320), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_site_navigation_name"),
    )
    op.create_index("ix_site_navigation_enabled", "site_navigation", ["enabled"])
    op.create_index("ix_site_navigation_created_at", "site_navigation", ["created_at"])
    op.create_index("ix_site_navigation_created_by_user_id", "site_navigation", ["created_by_user_id"])
    op.create_index("ix_site_navigation_updated_by_user_id", "site_navigation", ["updated_by_user_id"])
    op.create_index("ix_site_navigation_enabled_order", "site_navigation", ["enabled", "sort_order", "name"])
    op.create_index("ix_site_navigation_updated", "site_navigation", ["updated_at"])
