"""Shared encrypted Work OAuth and operation state.

Revision ID: 0020_work_integration_state
Revises: 0019_digital_employee_agents
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_work_integration_state"
down_revision = "0019_digital_employee_agents"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("work_integration_records"):
        return
    op.create_table(
        "work_integration_records",
        sa.Column("namespace", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(24), primary_key=True),
        sa.Column("key_hash", sa.String(64), primary_key=True),
        sa.Column("owner_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
    )
    op.create_index("ix_work_records_expiry", "work_integration_records", ["namespace", "kind", "expires_at"])
    op.create_index("ix_work_records_namespace_expiry", "work_integration_records", ["namespace", "expires_at"])


def downgrade():
    if sa.inspect(op.get_bind()).has_table("work_integration_records"):
        op.drop_table("work_integration_records")
