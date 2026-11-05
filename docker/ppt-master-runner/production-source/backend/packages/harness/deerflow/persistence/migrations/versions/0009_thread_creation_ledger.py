"""Add an append-only thread creation ledger for historical dashboard counts."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_thread_creation_ledger"
down_revision: str | Sequence[str] | None = "0008_drop_site_navigation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("thread_creations"):
        op.create_table(
            "thread_creations",
            sa.Column("thread_id", sa.String(length=64), nullable=False),
            sa.Column("assistant_id", sa.String(length=128), nullable=True),
            sa.Column("user_id", sa.String(length=64), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("thread_id"),
        )
        op.create_index("ix_thread_creations_assistant_id", "thread_creations", ["assistant_id"])
        op.create_index("ix_thread_creations_user_id", "thread_creations", ["user_id"])
        op.create_index("ix_thread_creations_created_at", "thread_creations", ["created_at"])
        op.create_index("ix_thread_creations_user_time", "thread_creations", ["user_id", "created_at"])

    thread_creations = sa.table(
        "thread_creations",
        sa.column("thread_id", sa.String),
        sa.column("assistant_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    threads_meta = sa.table(
        "threads_meta",
        sa.column("thread_id", sa.String),
        sa.column("assistant_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    runs = sa.table(
        "runs",
        sa.column("thread_id", sa.String),
        sa.column("assistant_id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    # Preserve exact creation times for all currently retained conversations.
    bind.execute(
        sa.insert(thread_creations).from_select(
            ["thread_id", "assistant_id", "user_id", "created_at"],
            sa.select(
                threads_meta.c.thread_id,
                threads_meta.c.assistant_id,
                threads_meta.c.user_id,
                threads_meta.c.created_at,
            ).where(
                ~sa.exists(
                    sa.select(1).where(
                        thread_creations.c.thread_id == threads_meta.c.thread_id,
                    )
                )
            ),
        )
    )

    # Recover previously deleted conversations that still have durable runs.
    # Their first run is the best historical approximation available before
    # this append-only ledger existed.
    bind.execute(
        sa.insert(thread_creations).from_select(
            ["thread_id", "assistant_id", "user_id", "created_at"],
            sa.select(
                runs.c.thread_id,
                sa.func.max(runs.c.assistant_id),
                sa.func.max(runs.c.user_id),
                sa.func.min(runs.c.created_at),
            )
            .where(
                ~sa.exists(
                    sa.select(1).where(
                        thread_creations.c.thread_id == runs.c.thread_id,
                    )
                )
            )
            .group_by(runs.c.thread_id),
        )
    )


def downgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("thread_creations"):
        op.drop_table("thread_creations")
