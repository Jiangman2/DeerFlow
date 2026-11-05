"""Cross-replica idempotency for the periodic channel audit digest.

Every Gateway replica runs its own digest loop; without a shared claim they all
push the same window's summary into the same chat. This table is that claim: one
row per (channel, window bucket), inserted with a unique-constraint guard so only
the first replica to claim a bucket sends it.

Revision ID: 0023_channel_digest_dedup
Revises: 0022_work_integration_state
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_channel_digest_dedup"
down_revision = "0022_work_integration_state"
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table("channel_digest_dedup"):
        return
    op.create_table(
        "channel_digest_dedup",
        sa.Column("channel_name", sa.String(64), primary_key=True),
        sa.Column("window_bucket", sa.String(64), primary_key=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chat_id", sa.String(128), nullable=True),
        sa.Column("events", sa.Integer(), nullable=True),
    )


def downgrade():
    if sa.inspect(op.get_bind()).has_table("channel_digest_dedup"):
        op.drop_table("channel_digest_dedup")
