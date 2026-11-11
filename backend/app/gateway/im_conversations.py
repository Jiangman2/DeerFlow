"""Read-only projection of IM (Feishu) conversations for the Web sidebar.

Every method here is a read: the sidebar must never move a Feishu pointer, and
the HTTP layer above exposes GET routes only. Ownership is scoped by
``owner_user_id``; the caller passes the authorised persistence scope, never a
client-supplied value.

The IM thread registry is the only source of directory membership, so a group
that merely has a catalog row (a name cached from a lifecycle event) but no
thread is invisible here by construction — that is the "no empty directories"
guarantee.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from typing import Any

from deerflow.persistence.im_routing import (
    GroupMeta,
    ImGroupCatalogRepository,
    ImRoutingRepository,
    ImThreadRegistryRepository,
    RegistryThread,
)

PROVIDER = "feishu"
MAX_GROUP_THREADS = 50


def im_session_tree_enabled() -> bool:
    """Whether the server side of S08 is switched on (default off)."""
    return os.environ.get("DEER_FLOW_S08_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def _encode_cursor(thread: RegistryThread) -> str:
    stamp = thread.last_active_at.isoformat() if thread.last_active_at else ""
    raw = f"{stamp}|{thread.thread_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(value: str | None) -> tuple[datetime, str] | None:
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii")).decode("utf-8")
        stamp, _, thread_id = raw.partition("|")
        if not stamp or not thread_id:
            return None
        return datetime.fromisoformat(stamp), thread_id
    except (ValueError, TypeError):
        return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


class ImConversationService:
    """Assemble the sidebar tree from registry/catalog/pointer read models."""

    def __init__(
        self,
        *,
        registry_repo: ImThreadRegistryRepository,
        catalog_repo: ImGroupCatalogRepository,
        routing_repo: ImRoutingRepository,
        enabled: bool,
    ) -> None:
        self._registry_repo = registry_repo
        self._catalog_repo = catalog_repo
        self._routing_repo = routing_repo
        self._enabled = enabled

    def capabilities(self) -> dict[str, Any]:
        return {
            "im_session_tree_enabled": self._enabled,
            "provider": PROVIDER,
            "max_group_threads": MAX_GROUP_THREADS,
            "search_enabled": self._enabled,
        }

    async def _current_threads(self, owner_user_id: str) -> dict[tuple[str, str], str]:
        pointers = await self._routing_repo.list_pointers_for_owner(
            provider=PROVIDER,
            owner_user_id=owner_user_id,
        )
        return {(pointer.channel_type, pointer.external_conversation_id): pointer.thread_id for pointer in pointers}

    @staticmethod
    def _thread_item(
        thread: RegistryThread,
        *,
        chat_name: str | None,
        current_thread_id: str | None,
    ) -> dict[str, Any]:
        return {
            "thread_id": thread.thread_id,
            "title": thread.title,
            "channel_type": thread.channel_type,
            "chat_id": thread.external_conversation_id,
            "chat_name": chat_name,
            "last_active_at": _iso(thread.last_active_at),
            "is_current": bool(current_thread_id and current_thread_id == thread.thread_id),
            "status": thread.status,
        }

    async def session_tree(
        self,
        *,
        owner_user_id: str,
        groups_limit: int,
        threads_per_group: int,
        group_offset: int = 0,
    ) -> dict[str, Any]:
        pointers = await self._current_threads(owner_user_id)
        private_threads = await self._registry_repo.list_private(
            owner_user_id=owner_user_id,
            limit=threads_per_group,
        )
        private_total = await self._registry_repo.count_private(owner_user_id=owner_user_id)

        groups = await self._registry_repo.aggregate_groups(
            owner_user_id=owner_user_id,
            limit=groups_limit,
            offset=group_offset,
        )
        groups_total = await self._registry_repo.count_groups(owner_user_id=owner_user_id)
        chat_ids = [group.external_conversation_id for group in groups]
        catalog = await self._catalog_repo.get_many(chat_ids)
        per_group = await self._registry_repo.first_threads_per_group(
            owner_user_id=owner_user_id,
            chat_ids=chat_ids,
            per_group=threads_per_group,
        )

        nodes = []
        for group in groups:
            chat_id = group.external_conversation_id
            meta = catalog.get(chat_id)
            current_thread_id = pointers.get(("group", chat_id))
            nodes.append(
                {
                    "chat_id": chat_id,
                    "name": meta.display_name if meta else None,
                    "name_source": meta.name_source if meta else "UNKNOWN",
                    "chat_status": meta.chat_status if meta else "UNKNOWN",
                    "thread_count": group.thread_count,
                    "last_active_at": _iso(group.last_active_at),
                    "current_thread_id": current_thread_id,
                    "threads": [
                        self._thread_item(
                            thread,
                            chat_name=meta.display_name if meta else None,
                            current_thread_id=current_thread_id,
                        )
                        for thread in per_group.get(chat_id, [])
                    ],
                }
            )

        return {
            "private": {
                "threads": [
                    self._thread_item(
                        thread,
                        chat_name=None,
                        current_thread_id=pointers.get(("private", thread.external_conversation_id)),
                    )
                    for thread in private_threads
                ],
                "total": private_total,
            },
            "groups": nodes,
            "groups_total": groups_total,
        }

    async def group_threads(
        self,
        *,
        owner_user_id: str,
        chat_id: str,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        threads = await self._registry_repo.list_group_threads(
            owner_user_id=owner_user_id,
            external_conversation_id=chat_id,
            limit=limit + 1,
            cursor=_decode_cursor(cursor),
        )
        has_more = len(threads) > limit
        page = threads[:limit]
        meta: GroupMeta | None = (await self._catalog_repo.get_many([chat_id])).get(chat_id)
        current_thread_id = (await self._current_threads(owner_user_id)).get(("group", chat_id))
        return {
            "threads": [
                self._thread_item(
                    thread,
                    chat_name=meta.display_name if meta else None,
                    current_thread_id=current_thread_id,
                )
                for thread in page
            ],
            "next_cursor": _encode_cursor(page[-1]) if has_more and page else None,
        }

    async def private_threads(
        self,
        *,
        owner_user_id: str,
        limit: int,
        cursor: str | None,
    ) -> dict[str, Any]:
        threads = await self._registry_repo.list_private(
            owner_user_id=owner_user_id,
            limit=limit + 1,
            cursor=_decode_cursor(cursor),
        )
        has_more = len(threads) > limit
        page = threads[:limit]
        pointers = await self._current_threads(owner_user_id)
        return {
            "threads": [
                self._thread_item(
                    thread,
                    chat_name=None,
                    current_thread_id=pointers.get(("private", thread.external_conversation_id)),
                )
                for thread in page
            ],
            "next_cursor": _encode_cursor(page[-1]) if has_more and page else None,
        }

    async def search_threads(
        self,
        *,
        owner_user_id: str,
        query: str,
        limit: int,
        offset: int = 0,
    ) -> dict[str, Any]:
        threads = await self._registry_repo.search(
            owner_user_id=owner_user_id,
            query=query,
            limit=limit,
            offset=offset,
        )
        chat_ids = [thread.external_conversation_id for thread in threads if thread.channel_type == "group"]
        catalog = await self._catalog_repo.get_many(chat_ids)
        pointers = await self._current_threads(owner_user_id)
        results = []
        for thread in threads:
            meta = catalog.get(thread.external_conversation_id)
            current_thread_id = (
                pointers.get(("group", thread.external_conversation_id))
                if thread.channel_type == "group"
                else pointers.get(("private", thread.external_conversation_id))
            )
            results.append(
                self._thread_item(
                    thread,
                    chat_name=meta.display_name if meta else None,
                    current_thread_id=current_thread_id,
                )
            )
        return {"results": results, "limit": limit, "offset": offset}

    async def thread_origin(self, *, thread_id: str) -> dict[str, Any] | None:
        thread = await self._registry_repo.get(thread_id)
        if thread is None:
            return None
        meta = None
        if thread.channel_type == "group":
            meta = (await self._catalog_repo.get_many([thread.external_conversation_id])).get(
                thread.external_conversation_id
            )
        pointers = await self._current_threads(thread.owner_user_id)
        current_thread_id = pointers.get((thread.channel_type, thread.external_conversation_id))
        return {
            "thread_id": thread.thread_id,
            "owner_user_id": thread.owner_user_id,
            "channel_type": thread.channel_type,
            "chat_id": thread.external_conversation_id,
            "chat_name": meta.display_name if meta else None,
            "chat_status": meta.chat_status if meta else None,
            "status": thread.status,
            "is_current": bool(current_thread_id and current_thread_id == thread.thread_id),
            "last_active_at": _iso(thread.last_active_at),
            "thread_exists": thread.thread_exists,
        }