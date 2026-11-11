"""Resolve the DeerFlow owner behind an inbound IM identity (S08).

The legacy binding model stored the owner inside ``channel_connections`` keyed by
``(provider, external_account_id, workspace_id)``, where ``workspace_id`` was
written as the chat id of the ``/connect`` handshake. That makes an owner
resolvable only inside the one chat where the user happened to connect, so the
same person in another group - or in a private chat - looked like an unknown
user.

S08 adds ``im_user_bindings``: a user-level identity binding, independent of any
chat. Resolution order (architecture D-01):

1. an ``ACTIVE`` row in ``im_user_bindings`` for this provider identity;
2. a ``channel_connections`` row scoped to the current chat (legacy path);
3. a ``channel_connections`` row without a workspace scope (pre-V2 rows);
4. none.

The resolver is read-only. It never creates a connection or a binding: writing a
binding is the ``/connect`` handshake's job, so an inbound message can never
self-bind an owner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

IdentitySource = Literal["binding", "connection_chat", "connection_workspace", "none"]

#: Providers that can create user-level bindings today. Other channels keep
#: using only their connection rows.
BINDING_PROVIDERS = frozenset({"feishu"})


@dataclass(frozen=True)
class IdentityResolution:
    """The owner (and connection, when known) behind an inbound identity."""

    owner_user_id: str | None
    connection_id: str | None
    tenant_key: str
    bot_app_id: str
    source: IdentitySource

    @property
    def resolved(self) -> bool:
        return bool(self.owner_user_id)


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


async def _find_connection(
    connection_repo: Any,
    *,
    provider: str,
    external_user_id: str,
    workspace_id: str | None,
) -> dict[str, Any] | None:
    if connection_repo is None:
        return None
    finder = getattr(connection_repo, "find_connection_by_external_identity", None)
    if finder is None:
        return None
    connection = await finder(
        provider=provider,
        external_account_id=external_user_id,
        workspace_id=workspace_id,
    )
    return connection if isinstance(connection, dict) else None


async def resolve_im_identity(
    *,
    binding_repo: Any | None,
    connection_repo: Any | None,
    provider: str,
    external_user_id: str,
    chat_id: str,
    tenant_key: str = "",
    bot_app_id: str = "",
) -> IdentityResolution:
    """Resolve the DeerFlow owner for an inbound platform identity.

    Args:
        binding_repo: ``ImUserBindingRepository`` (or a compatible fake), optional.
        connection_repo: ``ChannelConnectionRepository`` (or fake), optional.
        provider: Channel name, e.g. ``"feishu"``.
        external_user_id: Platform user id (Feishu ``open_id``), never a DeerFlow id.
        chat_id: Conversation the message arrived in.
        tenant_key: Feishu tenant, used to keep identities isolated per tenant.
        bot_app_id: Feishu app id, used to keep identities isolated per bot.
    """
    user_id = _clean(external_user_id)
    chat = _clean(chat_id)
    tenant = _clean(tenant_key)
    app_id = _clean(bot_app_id)

    if not user_id:
        return IdentityResolution(None, None, tenant, app_id, "none")

    if binding_repo is not None and provider in BINDING_PROVIDERS:
        finder = getattr(binding_repo, "find_active", None)
        if finder is not None:
            owner = await finder(
                feishu_user_id=user_id,
                tenant_key=tenant,
                bot_app_id=app_id,
            )
            if isinstance(owner, str) and owner:
                # The binding is authoritative for the owner, but the adapter
                # still wants the chat-scoped connection id when one exists.
                scoped = await _find_connection(
                    connection_repo,
                    provider=provider,
                    external_user_id=user_id,
                    workspace_id=chat or None,
                )
                return IdentityResolution(
                    owner_user_id=owner,
                    connection_id=_clean(scoped.get("id")) if scoped else None,
                    tenant_key=tenant,
                    bot_app_id=app_id,
                    source="binding",
                )

    if chat:
        scoped = await _find_connection(
            connection_repo,
            provider=provider,
            external_user_id=user_id,
            workspace_id=chat,
        )
        if scoped is not None:
            return IdentityResolution(
                owner_user_id=_clean(scoped.get("owner_user_id")) or None,
                connection_id=_clean(scoped.get("id")) or None,
                tenant_key=tenant,
                bot_app_id=app_id,
                source="connection_chat",
            )

    unscoped = await _find_connection(
        connection_repo,
        provider=provider,
        external_user_id=user_id,
        workspace_id=None,
    )
    if unscoped is not None:
        return IdentityResolution(
            owner_user_id=_clean(unscoped.get("owner_user_id")) or None,
            connection_id=_clean(unscoped.get("id")) or None,
            tenant_key=tenant,
            bot_app_id=app_id,
            source="connection_workspace",
        )

    return IdentityResolution(None, None, tenant, app_id, "none")