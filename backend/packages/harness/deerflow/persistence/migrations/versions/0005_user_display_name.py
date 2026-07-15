"""Add user display name.

Revision ID: 0005_user_display_name
Revises: 0004_run_ownership
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column


revision = "0005_user_display_name"
down_revision = "0004_run_ownership"
branch_labels = None
depends_on = None


def upgrade() -> None:
    safe_add_column("users", sa.Column("display_name", sa.String(length=128), nullable=True))


def downgrade() -> None:
    safe_drop_column("users", "display_name")
