"""Persist isolated PPT consent and official-gate transport state.

Revision ID: 0020_ppt_confirmation
Revises: 0019_digital_employee_agents

Local candidate only: production's installed migration chain must be attested
before packaging. Application rollback retains these additive tables; do NOT
run downgrade as part of an image rollback. No existing run or Skill data changes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_ppt_confirmation"
down_revision = "0019_digital_employee_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ppt_confirmation_tasks",
        sa.Column("task_id", sa.String(128), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(128), nullable=False),
        sa.Column("auto_enabled", sa.Boolean(), nullable=False),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("current_gate_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_ppt_confirmation_task_revision"),
    )
    op.create_index("ix_ppt_confirmation_task_owner_thread", "ppt_confirmation_tasks", ["owner_id", "thread_id", "task_id"])
    op.create_table(
        "ppt_confirmation_gates",
        sa.Column("gate_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(128), sa.ForeignKey("ppt_confirmation_tasks.task_id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("due_at", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.CheckConstraint("revision >= 0", name="ck_ppt_confirmation_gate_revision"),
    )
    op.create_index("ix_ppt_confirmation_gate_task", "ppt_confirmation_gates", ["task_id", "created_at", "gate_id"])
    op.create_index("ix_ppt_confirmation_gate_due", "ppt_confirmation_gates", ["state", "due_at", "gate_id"])


def downgrade() -> None:
    op.drop_index("ix_ppt_confirmation_gate_due", table_name="ppt_confirmation_gates")
    op.drop_index("ix_ppt_confirmation_gate_task", table_name="ppt_confirmation_gates")
    op.drop_table("ppt_confirmation_gates")
    op.drop_index("ix_ppt_confirmation_task_owner_thread", table_name="ppt_confirmation_tasks")
    op.drop_table("ppt_confirmation_tasks")
