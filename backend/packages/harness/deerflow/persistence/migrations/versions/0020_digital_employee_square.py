"""Administrator-controlled public digital-employee presentation.

Revision ID: 0020_digital_employee_square
Revises: 0019_digital_employee_agents
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_digital_employee_square"
down_revision = "0019_digital_employee_agents"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("digital_employee_square"):
        return
    op.create_table(
        "digital_employee_square",
        sa.Column("key", sa.String(256), primary_key=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("hidden", sa.Boolean(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    if sa.inspect(op.get_bind()).has_table("digital_employee_square"):
        op.drop_table("digital_employee_square")
