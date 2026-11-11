"""IM session routing: pointers, message links, event inbox.

Revision ID: 0023_im_session_routing
Revises: 0022_work_integration_state

Purely additive: three new tables, no existing table or row is touched, and
``downgrade`` only drops the tables this revision created. Rolling back keeps
the legacy ``channel_conversations`` pointers untouched, so a downgrade
returns the deployment to single-thread-per-chat behaviour without deleting
conversation history (the threads themselves live in LangGraph).
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_im_session_routing"
down_revision = "0022_work_integration_state"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("im_session_pointers"):
        op.create_table(
            "im_session_pointers",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("connection_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("channel_type", sa.String(16), nullable=False, server_default="group"),
            sa.Column("external_conversation_id", sa.String(128), nullable=False),
            sa.Column("external_topic_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("thread_id", sa.String(64), nullable=False),
            sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint(
                "connection_id",
                "owner_user_id",
                "channel_type",
                "external_conversation_id",
                "external_topic_id",
                name="uq_im_session_pointer_scope",
            ),
        )
        op.create_index("ix_im_session_pointers_provider", "im_session_pointers", ["provider"])
        op.create_index("ix_im_session_pointers_connection_id", "im_session_pointers", ["connection_id"])
        op.create_index("ix_im_session_pointers_owner_user_id", "im_session_pointers", ["owner_user_id"])
        op.create_index("ix_im_session_pointers_thread_id", "im_session_pointers", ["thread_id"])

    if not inspector.has_table("im_message_links"):
        op.create_table(
            "im_message_links",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("connection_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("external_conversation_id", sa.String(128), nullable=False),
            sa.Column("message_id", sa.String(128), nullable=False),
            sa.Column("direction", sa.String(16), nullable=False),
            sa.Column("message_kind", sa.String(32), nullable=False, server_default=""),
            sa.Column("thread_id", sa.String(64), nullable=False),
            sa.Column("sender_external_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("root_message_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("parent_message_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("task_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("provider", "message_id", name="uq_im_message_link_provider_message"),
        )
        op.create_index("ix_im_message_links_provider", "im_message_links", ["provider"])
        op.create_index("ix_im_message_links_owner_user_id", "im_message_links", ["owner_user_id"])
        op.create_index("ix_im_message_links_external_conversation_id", "im_message_links", ["external_conversation_id"])
        op.create_index("ix_im_message_links_thread_id", "im_message_links", ["thread_id"])
        op.create_index("idx_im_message_links_thread", "im_message_links", ["thread_id", "created_at"])

    if not inspector.has_table("im_event_inbox"):
        op.create_table(
            "im_event_inbox",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("event_key", sa.String(255), nullable=False),
            sa.Column("connection_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("owner_user_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("external_conversation_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("message_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("status", sa.String(32), nullable=False, server_default="RECEIVED"),
            sa.Column("route_reason", sa.String(64), nullable=False, server_default=""),
            sa.Column("thread_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("provider", "event_key", name="uq_im_event_inbox_key"),
        )
        op.create_index("ix_im_event_inbox_provider", "im_event_inbox", ["provider"])


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("im_event_inbox"):
        op.drop_table("im_event_inbox")
    if inspector.has_table("im_message_links"):
        op.drop_table("im_message_links")
    if inspector.has_table("im_session_pointers"):
        op.drop_table("im_session_pointers")
