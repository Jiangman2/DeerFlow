"""Add enterprise platform administration tables and account disable flag."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from deerflow.persistence.migrations._helpers import safe_add_column

revision: str = "0006_enterprise_platform"
down_revision: str | Sequence[str] | None = "0005_run_stop_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    safe_add_column(
        "users",
        sa.Column("is_disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("announcements"):
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

    if not inspector.has_table("audit_logs"):
        op.create_table(
            "audit_logs",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("user_id", sa.String(length=64), nullable=True),
            sa.Column("user_email", sa.String(length=320), nullable=True),
            sa.Column("user_role", sa.String(length=32), nullable=True),
            sa.Column("action", sa.String(length=96), nullable=False),
            sa.Column("resource_type", sa.String(length=64), nullable=True),
            sa.Column("resource_id", sa.String(length=256), nullable=True),
            sa.Column("method", sa.String(length=12), nullable=False),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("result", sa.String(length=16), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("client_ip", sa.String(length=64), nullable=True),
            sa.Column("user_agent", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("details_json", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])
        op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"])
        op.create_index("ix_audit_logs_user_email", "audit_logs", ["user_email"])
        op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
        op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type"])
        op.create_index("ix_audit_logs_status_code", "audit_logs", ["status_code"])
        op.create_index("ix_audit_logs_result", "audit_logs", ["result"])
        op.create_index("ix_audit_logs_resource", "audit_logs", ["resource_type", "resource_id"])
        op.create_index("ix_audit_logs_user_time", "audit_logs", ["user_id", "created_at"])

    if not inspector.has_table("operation_dispositions"):
        op.create_table(
            "operation_dispositions",
            sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("run_id", sa.String(length=64), nullable=False),
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("action", sa.String(length=48), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=True),
            sa.Column("note", sa.Text(), nullable=True),
            sa.Column("user_id", sa.String(length=64), nullable=True),
            sa.Column("user_email", sa.String(length=320), nullable=True),
            sa.Column("result", sa.String(length=16), nullable=False),
            sa.Column("details_json", sa.JSON(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_operation_dispositions_created_at", "operation_dispositions", ["created_at"])
        op.create_index("ix_operation_dispositions_run_id", "operation_dispositions", ["run_id"])
        op.create_index("ix_operation_dispositions_thread_id", "operation_dispositions", ["thread_id"])
        op.create_index("ix_operation_dispositions_action", "operation_dispositions", ["action"])
        op.create_index("ix_operation_dispositions_user_id", "operation_dispositions", ["user_id"])
        op.create_index("ix_operation_dispositions_run_time", "operation_dispositions", ["run_id", "created_at"])
        op.create_index("ix_operation_dispositions_thread_time", "operation_dispositions", ["thread_id", "created_at"])

    if not inspector.has_table("site_navigation"):
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


def downgrade() -> None:
    op.drop_table("site_navigation")
    op.drop_table("operation_dispositions")
    op.drop_table("audit_logs")
    op.drop_table("announcements")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_disabled")
