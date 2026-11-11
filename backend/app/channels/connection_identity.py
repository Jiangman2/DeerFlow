"""Helpers for attaching persisted channel connection ownership to inbound messages."""

from __future__ import annotations

from typing import Any

from app.channels.identity_resolver import resolve_im_identity
from app.channels.message_bus import InboundMessage


async def attach_connection_identity(
    inbound: InboundMessage,
    *,
    repo: Any,
    provider: str,
    workspace_id: str | None,
    fallback_without_workspace: bool = False,
    binding_repo: Any = None,
    tenant_key: str = "",
    bot_app_id: str = "",
) -> InboundMessage:
    """Attach connection metadata to an inbound message when a persisted binding exists.

    When *binding_repo* is supplied the S08 identity resolver is used: a
    user-level ``im_user_bindings`` row wins over the chat-scoped connection, so
    a bound user is recognised in every group and private chat (not only where
    the ``/connect`` handshake happened). Without it the legacy workspace-scoped
    lookup below is unchanged.
    """
    if binding_repo is not None:
        resolution = await resolve_im_identity(
            binding_repo=binding_repo,
            connection_repo=repo,
            provider=provider,
            external_user_id=inbound.user_id,
            chat_id=workspace_id or inbound.chat_id,
            tenant_key=tenant_key,
            bot_app_id=bot_app_id,
        )
        if resolution.resolved:
            inbound.owner_user_id = resolution.owner_user_id
            if resolution.connection_id:
                inbound.connection_id = resolution.connection_id
        return inbound

    if repo is None:
        return inbound

    workspace_candidates: list[str | None] = []
    if workspace_id:
        workspace_candidates.append(workspace_id)
    if fallback_without_workspace:
        workspace_candidates.append(None)
    if not workspace_candidates:
        return inbound

    for candidate in workspace_candidates:
        connection = await repo.find_connection_by_external_identity(
            provider=provider,
            external_account_id=inbound.user_id,
            workspace_id=candidate,
        )
        if connection is None:
            continue

        inbound.connection_id = connection["id"]
        inbound.owner_user_id = connection["owner_user_id"]
        inbound.workspace_id = connection.get("workspace_id")
        return inbound

    return inbound