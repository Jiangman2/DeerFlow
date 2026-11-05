"""Recover only missing, idle targets of an authenticated channel mapping.

No checkpoint contents, credentials or run inputs are loaded. The Gateway owns
new-thread creation; this module conditionally publishes that empty candidate.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import column, select, table, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.channel_connections.model import ChannelConnectionRow, ChannelConversationRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadCreationRow, ThreadMetaRow

# LangGraph owns this table. Reference only its indexed thread identifier, never
# declare an ORM model that could take ownership of its schema or decode blobs.
_checkpoints = table("checkpoints", column("thread_id"))


class ChannelRecoveryRefused(RuntimeError):
    """A bounded, content-free refusal; no fallback to an unverified new thread."""


@dataclass(frozen=True)
class ChannelThreadBinding:
    connection_id: str
    owner_user_id: str
    provider: str
    external_account_id: str
    workspace_id: str
    conversation_id: str
    topic_id: str


@dataclass(frozen=True)
class MappedThread:
    mapping_id: str
    thread_id: str
    updated_at: datetime
    missing: bool
    owned: bool


class ChannelThreadRecovery:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], binding: ChannelThreadBinding) -> None:
        self._sf = session_factory
        self._binding = binding

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[AsyncSession]:
        # No external HTTP or provider work is allowed inside this transaction.
        async with asyncio.timeout(8), self._sf() as session:
            if session.get_bind().dialect.name == "postgresql":
                await session.execute(text("SET LOCAL statement_timeout = '5000'"))
                await session.execute(text("SET LOCAL lock_timeout = '1000'"))
            yield session

    def _connection_conditions(self):
        b = self._binding
        return (
            ChannelConnectionRow.id == b.connection_id,
            ChannelConnectionRow.owner_user_id == b.owner_user_id,
            ChannelConnectionRow.provider == b.provider,
            ChannelConnectionRow.external_account_id == b.external_account_id,
            ChannelConnectionRow.workspace_id == b.workspace_id,
            ChannelConnectionRow.status == "connected",
        )

    def _mapping_conditions(self):
        b = self._binding
        return (
            ChannelConversationRow.connection_id == b.connection_id,
            ChannelConversationRow.owner_user_id == b.owner_user_id,
            ChannelConversationRow.provider == b.provider,
            ChannelConversationRow.external_conversation_id == b.conversation_id,
            ChannelConversationRow.external_topic_id == b.topic_id,
        )

    def _old_target_guards(self, thread_id: str):
        # Even an expired run lease remains protected: recovery is not a run
        # reconciler and must never decide that an apparently busy task is dead.
        return (
            ~select(ThreadMetaRow.thread_id).where(ThreadMetaRow.thread_id == thread_id).exists(),
            ~select(_checkpoints.c.thread_id).where(_checkpoints.c.thread_id == thread_id).exists(),
            ~select(RunRow.run_id).where(RunRow.thread_id == thread_id, RunRow.status.in_(("pending", "running"))).exists(),
            ~select(ThreadCreationRow.thread_id).where(ThreadCreationRow.thread_id == thread_id, ThreadCreationRow.user_id.is_not(None), ThreadCreationRow.user_id != self._binding.owner_user_id).exists(),
        )

    async def inspect(self) -> MappedThread:
        """Read the current mapping, not a previously cached thread id."""
        async with self._session() as session:
            stmt = (
                select(ChannelConversationRow.id, ChannelConversationRow.thread_id, ChannelConversationRow.updated_at, ThreadMetaRow.thread_id, ThreadMetaRow.user_id)
                .join(ChannelConnectionRow, ChannelConnectionRow.id == ChannelConversationRow.connection_id)
                .outerjoin(ThreadMetaRow, ThreadMetaRow.thread_id == ChannelConversationRow.thread_id)
                .where(*self._mapping_conditions(), *self._connection_conditions())
            )
            row = (await session.execute(stmt)).one_or_none()
            if row is None:
                raise ChannelRecoveryRefused("channel_binding_or_mapping_changed")
            mapping_id, thread_id, updated_at, existing_id, owner = row
            if existing_id is not None:
                # Preserve existing threads (including legacy/unowned ones).
                # The normal Gateway owner gate still decides whether a run is
                # allowed. A 404/403 from that gate is never a recovery trigger.
                return MappedThread(mapping_id, thread_id, updated_at, False, owner == self._binding.owner_user_id)
            guards = (await session.execute(select(*self._old_target_guards(thread_id)))).one()
            if not all(guards):
                raise ChannelRecoveryRefused("missing_thread_not_safe_to_replace")
            return MappedThread(mapping_id, thread_id, updated_at, True, False)

    async def recover(self, create: Callable[[], Awaitable[str]]) -> tuple[str, bool]:
        """Publish one replacement mapping; concurrent losers reuse the winner.

        Creation has no model run and occurs outside database transactions. A
        losing candidate is left empty; never delete a thread that another path
        could have started using, and never replay any previous user message.
        """
        before = await self.inspect()
        if not before.missing:
            return before.thread_id, False
        candidate_id = await create()
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id == before.thread_id:
            raise ChannelRecoveryRefused("invalid_replacement_thread")
        async with self._session() as session:
            # Serialize against binding revocation/ownership transfer for this
            # short commit, never across the Gateway thread-create request.
            connection = await session.scalar(select(ChannelConnectionRow.id).where(*self._connection_conditions()).with_for_update())
            if connection is None:
                raise ChannelRecoveryRefused("channel_binding_changed_during_recovery")
            candidate_owned = select(ThreadMetaRow.thread_id).where(ThreadMetaRow.thread_id == candidate_id, ThreadMetaRow.user_id == self._binding.owner_user_id).exists()
            stmt = (
                update(ChannelConversationRow)
                .where(
                    *self._mapping_conditions(),
                    ChannelConversationRow.id == before.mapping_id,
                    ChannelConversationRow.thread_id == before.thread_id,
                    ChannelConversationRow.updated_at == before.updated_at,
                    *self._old_target_guards(before.thread_id),
                    candidate_owned,
                    select(ChannelConnectionRow.id).where(*self._connection_conditions()).exists(),
                )
                .values(thread_id=candidate_id)
                .returning(ChannelConversationRow.thread_id)
                .execution_options(synchronize_session=False)
            )
            accepted = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
        if accepted is not None:
            return accepted, True
        current = await self.inspect()
        if current.thread_id != before.thread_id and not current.missing and current.owned:
            return current.thread_id, False
        raise ChannelRecoveryRefused("channel_recovery_precondition_changed")
