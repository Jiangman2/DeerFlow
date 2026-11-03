"""Add durable OA policy-to-JointJS jobs.

Revision ID: 0014_oa_policy_flow_jobs
Revises: 0013_data_lake_auth_challenges
Create Date: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_oa_policy_flow_jobs"
down_revision: str | Sequence[str] | None = "0013_data_lake_auth_challenges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Keep this additive revision idempotent for enterprise installations that
    # may already have received the table through a staged hotfix/bootstrap.
    if sa.inspect(op.get_bind()).has_table("oa_policy_flow_jobs"):
        return
    op.create_table(
        "oa_policy_flow_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("owner_user_id", sa.String(length=64), nullable=False),
        sa.Column("assistant_id", sa.String(length=128), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("callback_status", sa.String(length=24), nullable=False),
        sa.Column("document_filename", sa.String(length=255), nullable=False),
        sa.Column("key_fields", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("output_virtual_path", sa.Text(), nullable=True),
        sa.Column("output_filename", sa.String(length=255), nullable=True),
        sa.Column("output_sha256", sa.String(length=64), nullable=True),
        sa.Column("process_attempts", sa.Integer(), nullable=False),
        sa.Column("callback_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("last_error_detail", sa.Text(), nullable=True),
        sa.Column("last_callback_error", sa.Text(), nullable=True),
        sa.Column("last_callback_status_code", sa.Integer(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key_hash"),
        sa.UniqueConstraint("thread_id"),
    )
    op.create_index("ix_oa_policy_flow_jobs_request_id", "oa_policy_flow_jobs", ["request_id"])
    op.create_index("ix_oa_policy_flow_jobs_owner_user_id", "oa_policy_flow_jobs", ["owner_user_id"])
    op.create_index("ix_oa_policy_flow_jobs_run_id", "oa_policy_flow_jobs", ["run_id"])
    op.create_index("ix_oa_policy_flow_jobs_status", "oa_policy_flow_jobs", ["status"])
    op.create_index("ix_oa_policy_flow_jobs_callback_status", "oa_policy_flow_jobs", ["callback_status"])
    op.create_index("ix_oa_policy_flow_jobs_next_attempt_at", "oa_policy_flow_jobs", ["next_attempt_at"])
    op.create_index("ix_oa_policy_flow_jobs_due", "oa_policy_flow_jobs", ["next_attempt_at", "lease_expires_at"])
    op.create_index("ix_oa_policy_flow_jobs_status_callback", "oa_policy_flow_jobs", ["status", "callback_status"])


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("oa_policy_flow_jobs"):
        op.drop_table("oa_policy_flow_jobs")
