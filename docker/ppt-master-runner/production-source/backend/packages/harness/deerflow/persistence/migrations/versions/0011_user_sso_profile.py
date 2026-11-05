"""Store display name and organization fields synchronized from SSO."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from deerflow.persistence.migrations._helpers import safe_add_column, safe_drop_column

revision: str = "0011_user_sso_profile"
down_revision: str | Sequence[str] | None = "0010_scheduled_task_run_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column("users", sa.Column("display_name", sa.String(length=255), nullable=True))
    safe_add_column("users", sa.Column("organization_id", sa.String(length=128), nullable=True))
    safe_add_column("users", sa.Column("organization_code", sa.String(length=128), nullable=True))
    safe_add_column("users", sa.Column("organization_name", sa.String(length=255), nullable=True))


def downgrade() -> None:
    safe_drop_column("users", "organization_name")
    safe_drop_column("users", "organization_code")
    safe_drop_column("users", "organization_id")
    safe_drop_column("users", "display_name")
