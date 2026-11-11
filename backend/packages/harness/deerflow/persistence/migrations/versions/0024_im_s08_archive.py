"""S08 IM session archive: bindings, group catalog, thread registry.

Revision ID: 0024_im_s08_archive
Revises: 0023_im_session_routing

Purely additive. Three new tables plus one read-path index on
``im_session_pointers``. No column, constraint or row of an existing table is
changed, so ``downgrade`` only removes what this revision created and leaves
the IM routing pointers and message links untouched.
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_im_s08_archive"
down_revision = "0023_im_session_routing"
branch_labels = None
depends_on = None

_ACTIVE = sa.text("status = 'ACTIVE'")


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    if "im_user_bindings" not in existing:
        op.create_table(
            "im_user_bindings",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tenant_key", sa.String(64), nullable=False, server_default=""),
            sa.Column("bot_app_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("feishu_id_type", sa.String(16), nullable=False, server_default="open_id"),
            sa.Column("feishu_user_id", sa.String(128), nullable=False),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
            sa.Column("source", sa.String(24), nullable=False, server_default="CONNECT_CODE"),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_im_user_bindings_owner", "im_user_bindings", ["owner_user_id"])
        op.create_index("ix_im_user_bindings_identity", "im_user_bindings", ["feishu_user_id"])
        op.create_index(
            "uq_im_user_binding_active",
            "im_user_bindings",
            ["tenant_key", "bot_app_id", "feishu_id_type", "feishu_user_id"],
            unique=True,
            sqlite_where=_ACTIVE,
            postgresql_where=_ACTIVE,
        )

    if "im_group_catalog" not in existing:
        op.create_table(
            "im_group_catalog",
            sa.Column("id", sa.String(64), primary_key=True),
            sa.Column("tenant_key", sa.String(64), nullable=False, server_default=""),
            sa.Column("bot_app_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("chat_id", sa.String(128), nullable=False),
            sa.Column("display_name", sa.String(256), nullable=True),
            sa.Column("name_source", sa.String(16), nullable=False, server_default="UNKNOWN"),
            sa.Column("chat_type", sa.String(16), nullable=False, server_default=""),
            sa.Column("chat_status", sa.String(16), nullable=False, server_default="ACTIVE"),
            sa.Column("avatar_ref", sa.String(256), nullable=True),
            sa.Column("member_count", sa.Integer(), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("sync_error", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("tenant_key", "bot_app_id", "chat_id", name="uq_im_group_catalog_scope"),
        )
        op.create_index("ix_im_group_catalog_chat", "im_group_catalog", ["chat_id"])

    if "im_thread_registry" not in existing:
        op.create_table(
            "im_thread_registry",
            sa.Column("thread_id", sa.String(64), primary_key=True),
            sa.Column("provider", sa.String(32), nullable=False, server_default="feishu"),
            sa.Column("connection_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("tenant_key", sa.String(64), nullable=False, server_default=""),
            sa.Column("bot_app_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("owner_user_id", sa.String(64), nullable=False),
            sa.Column("channel_type", sa.String(16), nullable=False),
            sa.Column("external_conversation_id", sa.String(128), nullable=False),
            sa.Column("external_topic_id", sa.String(128), nullable=False, server_default=""),
            sa.Column("source_platform", sa.String(16), nullable=False, server_default="feishu"),
            sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("classification_source", sa.String(24), nullable=False, server_default="RUNTIME"),
            sa.Column("title_snapshot", sa.String(256), nullable=True),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_im_thread_registry_owner_scope",
            "im_thread_registry",
            ["owner_user_id", "channel_type", "external_conversation_id", "last_active_at"],
        )
        op.create_index(
            "ix_im_thread_registry_owner_active",
            "im_thread_registry",
            ["owner_user_id", "last_active_at"],
        )
        op.create_index("ix_im_thread_registry_scope", "im_thread_registry", ["external_conversation_id"])

    if "im_session_pointers" in existing:
        pointer_indexes = {index["name"] for index in inspector.get_indexes("im_session_pointers")}
        if "ix_im_session_pointers_owner_scope" not in pointer_indexes:
            op.create_index(
                "ix_im_session_pointers_owner_scope",
                "im_session_pointers",
                ["owner_user_id", "channel_type", "external_conversation_id"],
            )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("im_session_pointers"):
        pointer_indexes = {index["name"] for index in inspector.get_indexes("im_session_pointers")}
        if "ix_im_session_pointers_owner_scope" in pointer_indexes:
            op.drop_index("ix_im_session_pointers_owner_scope", table_name="im_session_pointers")
    for name in ("im_thread_registry", "im_group_catalog", "im_user_bindings"):
        if sa.inspect(op.get_bind()).has_table(name):
            op.drop_table(name)