"""Durable procurement review jobs and callback outbox.

Revision ID: 0023_procurement_review_jobs
Revises: 0022_work_integration_state
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_procurement_review_jobs"
down_revision = "0022_work_integration_state"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "procurement_review_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("request_id", sa.String(128), nullable=False, unique=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False),
        sa.Column("due_at", sa.Float(), nullable=False),
        sa.Column("lease_owner", sa.String(64)),
        sa.Column("lease_until", sa.Float(), nullable=False),
    )
    op.create_index("ix_procurement_review_due", "procurement_review_jobs", ["state", "due_at"])


def downgrade():
    op.drop_index("ix_procurement_review_due", table_name="procurement_review_jobs")
    op.drop_table("procurement_review_jobs")
