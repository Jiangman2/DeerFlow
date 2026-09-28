"""Persist Data Lake authentication challenges.

Revision ID: 0013_data_lake_auth_challenges
Revises: 0012_user_last_login
Create Date: 2026-08-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_data_lake_auth_challenges"
down_revision: str | Sequence[str] | None = "0012_user_last_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    if "cnooc_platform_token_hash" not in user_columns:
        with op.batch_alter_table("users", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "cnooc_platform_token_hash",
                    sa.String(length=64),
                    nullable=True,
                )
            )
            batch_op.create_index(
                "uq_users_cnooc_platform_token_hash",
                ["cnooc_platform_token_hash"],
                unique=True,
            )

    if not inspector.has_table("data_lake_auth_challenges"):
        op.create_table(
            "data_lake_auth_challenges",
            sa.Column("challenge_hash", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("callback_id", sa.String(length=128), nullable=True),
            sa.Column("callback_timestamp", sa.BigInteger(), nullable=True),
            sa.Column("callback_payload_hash", sa.String(length=64), nullable=True),
            sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("challenge_hash"),
        )
        with op.batch_alter_table("data_lake_auth_challenges", schema=None) as batch_op:
            batch_op.create_index("ix_data_lake_auth_challenges_user_id", ["user_id"], unique=False)
            batch_op.create_index(
                "ix_data_lake_auth_challenges_expires_at",
                ["expires_at"],
                unique=False,
            )


def downgrade() -> None:
    with op.batch_alter_table("data_lake_auth_challenges", schema=None) as batch_op:
        batch_op.drop_index("ix_data_lake_auth_challenges_expires_at")
        batch_op.drop_index("ix_data_lake_auth_challenges_user_id")
    op.drop_table("data_lake_auth_challenges")
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index("uq_users_cnooc_platform_token_hash")
        batch_op.drop_column("cnooc_platform_token_hash")
