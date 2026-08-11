"""Track the most recent successful user login for admin reporting."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0012_user_last_login"
down_revision: str | Sequence[str] | None = "0011_user_sso_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    bind = op.get_bind()
    if "users" not in sa.inspect(bind).get_table_names():
        return
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("users")}
    if "ix_users_last_login_at" not in indexes:
        op.create_index("ix_users_last_login_at", "users", ["last_login_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if "users" in sa.inspect(bind).get_table_names():
        indexes = {index["name"] for index in sa.inspect(bind).get_indexes("users")}
        if "ix_users_last_login_at" in indexes:
            op.drop_index("ix_users_last_login_at", table_name="users")
    safe_drop_column("users", "last_login_at")
