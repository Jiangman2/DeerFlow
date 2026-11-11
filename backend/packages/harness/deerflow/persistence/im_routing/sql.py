"""SQL repository for IM session routing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deerflow.persistence.im_routing.model import ImEventInboxRow, ImMessageLinkRow, ImSessionPointerRow


def _new_id() -> str:
    return uuid.uuid4().hex


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class SessionPointer:
    """A resolved current-thread pointer."""

    thread_id: str
    generation: int


@dataclass(frozen=True)
class MessageLink:
    """A resolved platform-message -> thread mapping."""

    provider: str
    message_id: str
    chat_id: str
    owner_user_id: str
    thread_id: str
    direction: str
    message_kind: str
    connection_id: str
    sender_external_id: str


class ImRoutingRepository:
    """Persistence for session pointers, message links and the event inbox.

    All methods are async and safe to call concurrently: pointer writes are
    either ``INSERT`` guarded by a unique key or ``UPDATE ... WHERE
    generation = <read value>`` (compare-and-swap), never read-modify-write.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    # -- session pointers --------------------------------------------------

    async def get_pointer(
        self,
        *,
        provider: str,
        owner_user_id: str,
        chat_id: str,
        channel_type: str = "group",
        connection_id: str = "",
    ) -> SessionPointer | None:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(ImSessionPointerRow).where(
                        ImSessionPointerRow.provider == provider,
                        ImSessionPointerRow.connection_id == connection_id,
                        ImSessionPointerRow.owner_user_id == owner_user_id,
                        ImSessionPointerRow.channel_type == channel_type,
                        ImSessionPointerRow.external_conversation_id == chat_id,
                        ImSessionPointerRow.external_topic_id == "",
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return SessionPointer(thread_id=row.thread_id, generation=row.generation)

    async def insert_pointer_if_absent(
        self,
        *,
        provider: str,
        owner_user_id: str,
        chat_id: str,
        thread_id: str,
        channel_type: str = "group",
        connection_id: str = "",
    ) -> SessionPointer:
        """Create the pointer unless a concurrent writer already did.

        Returns the *effective* pointer — the caller's ``thread_id`` when it won
        the insert, otherwise the winner's. Two racing "first message" workers
        therefore converge on one thread instead of splitting the conversation.

        The scope is the chat, not the Feishu topic: every topic inside one
        group must resolve to the same conversation for a given owner.
        """
        try:
            async with self.session_factory() as session:
                session.add(
                    ImSessionPointerRow(
                        id=_new_id(),
                        provider=provider,
                        connection_id=connection_id,
                        owner_user_id=owner_user_id,
                        channel_type=channel_type,
                        external_conversation_id=chat_id,
                        external_topic_id="",
                        thread_id=thread_id,
                        generation=1,
                    ),
                )
                await session.commit()
            return SessionPointer(thread_id=thread_id, generation=1)
        except IntegrityError:
            existing = await self.get_pointer(
                provider=provider,
                owner_user_id=owner_user_id,
                chat_id=chat_id,
                channel_type=channel_type,
                connection_id=connection_id,
            )
            if existing is None:
                raise
            return existing

    async def cas_switch_pointer(
        self,
        *,
        provider: str,
        owner_user_id: str,
        chat_id: str,
        thread_id: str,
        expected_generation: int,
        channel_type: str = "group",
        connection_id: str = "",
    ) -> bool:
        """Atomically move the pointer from ``expected_generation`` to a new thread.

        Returns ``False`` when another writer moved it first; the caller must
        re-read the pointer and retry rather than blindly overwrite.
        """
        async with self.session_factory() as session:
            result = await session.execute(
                update(ImSessionPointerRow)
                .where(
                    ImSessionPointerRow.provider == provider,
                    ImSessionPointerRow.connection_id == connection_id,
                    ImSessionPointerRow.owner_user_id == owner_user_id,
                    ImSessionPointerRow.channel_type == channel_type,
                    ImSessionPointerRow.external_conversation_id == chat_id,
                    ImSessionPointerRow.external_topic_id == "",
                    ImSessionPointerRow.generation == expected_generation,
                )
                .values(thread_id=thread_id, generation=expected_generation + 1, updated_at=_utc_now()),
            )
            await session.commit()
            return bool(result.rowcount)

    # -- message links -----------------------------------------------------

    async def record_link(
        self,
        *,
        provider: str,
        message_id: str,
        chat_id: str,
        owner_user_id: str,
        thread_id: str,
        direction: str,
        message_kind: str = "",
        connection_id: str = "",
        sender_external_id: str = "",
        root_message_id: str = "",
        parent_message_id: str = "",
        task_id: str = "",
    ) -> None:
        """Record a platform message -> thread link (idempotent)."""
        if not message_id or not thread_id:
            return
        try:
            async with self.session_factory() as session:
                session.add(
                    ImMessageLinkRow(
                        id=_new_id(),
                        provider=provider,
                        connection_id=connection_id,
                        owner_user_id=owner_user_id,
                        external_conversation_id=chat_id,
                        message_id=message_id,
                        direction=direction,
                        message_kind=message_kind,
                        thread_id=thread_id,
                        sender_external_id=sender_external_id,
                        root_message_id=root_message_id,
                        parent_message_id=parent_message_id,
                        task_id=task_id,
                    ),
                )
                await session.commit()
        except IntegrityError:
            # Same platform message seen twice (duplicate delivery, or the
            # outbound card refreshed). The first link wins: it is the one the
            # thread actually ran under.
            return

    async def get_link(self, *, provider: str, message_id: str) -> MessageLink | None:
        if not message_id:
            return None
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(ImMessageLinkRow).where(
                        ImMessageLinkRow.provider == provider,
                        ImMessageLinkRow.message_id == message_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return MessageLink(
                provider=row.provider,
                message_id=row.message_id,
                chat_id=row.external_conversation_id,
                owner_user_id=row.owner_user_id,
                thread_id=row.thread_id,
                direction=row.direction,
                message_kind=row.message_kind,
                connection_id=row.connection_id,
                sender_external_id=row.sender_external_id,
            )

    # -- event inbox -------------------------------------------------------

    async def claim_event(
        self,
        *,
        provider: str,
        event_key: str,
        chat_id: str = "",
        owner_user_id: str = "",
        message_id: str = "",
        connection_id: str = "",
        route_reason: str = "",
    ) -> bool:
        """Claim an inbound event exactly once. ``False`` means duplicate."""
        try:
            async with self.session_factory() as session:
                session.add(
                    ImEventInboxRow(
                        id=_new_id(),
                        provider=provider,
                        event_key=event_key,
                        connection_id=connection_id,
                        owner_user_id=owner_user_id,
                        external_conversation_id=chat_id,
                        message_id=message_id,
                        status="PROCESSING",
                        route_reason=route_reason,
                    ),
                )
                await session.commit()
            return True
        except IntegrityError:
            return False

    async def finish_event(
        self,
        *,
        provider: str,
        event_key: str,
        status: str,
        thread_id: str = "",
        route_reason: str = "",
    ) -> None:
        values: dict[str, object] = {"status": status, "updated_at": _utc_now()}
        if thread_id:
            values["thread_id"] = thread_id
        if route_reason:
            values["route_reason"] = route_reason
        async with self.session_factory() as session:
            await session.execute(
                update(ImEventInboxRow).where(ImEventInboxRow.provider == provider, ImEventInboxRow.event_key == event_key).values(**values),
            )
            await session.commit()

    async def release_event(self, *, provider: str, event_key: str) -> None:
        """Drop a claim so a failed attempt can be retried."""
        async with self.session_factory() as session:
            await session.execute(
                delete(ImEventInboxRow).where(
                    ImEventInboxRow.provider == provider,
                    ImEventInboxRow.event_key == event_key,
                ),
            )
            await session.commit()
