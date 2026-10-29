"""Layer 1: trusted identity and per-principal grants, never a shared user token.

Local UAT may use memory. Production loads current encrypted SQL grants on every
use, verifies persisted bindings and supports version-aware revocation. Refresh
tokens are not retained; expiry requires fresh consent, not channel rebinding.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from app.channels.message_bus import InboundMessage

from .client import WorkError
from .scopes import has_scopes


@dataclass(frozen=True)
class Principal:
    app_id: str
    sender: str
    owner: str
    connection: str
    workspace: str
    chat: str

    @classmethod
    def from_message(cls, app_id: str, msg: InboundMessage):
        if msg.channel_name != "feishu" or msg.metadata.get("chat_type") != "p2p" or not all((app_id, msg.user_id, msg.owner_user_id, msg.connection_id, msg.workspace_id, msg.chat_id)):
            raise WorkError("untrusted_identity")
        return cls(app_id, msg.user_id, msg.owner_user_id, msg.connection_id, msg.workspace_id, msg.chat_id)


@dataclass
class Grant:
    token: str = field(repr=False)
    expires_at: float
    scopes: frozenset[str] = frozenset()
    revision: int = field(default=0, repr=False)


class TokenVault:
    def __init__(self, app_id: str, repo: Any, allowed_owners: set[str] | None, runtime_guard=None, *, store=None):
        self.app_id, self.repo, self.allowed_owners = app_id, repo, frozenset(allowed_owners) if allowed_owners is not None else None
        self.store = store
        self._records = {}
        self.runtime_guard = runtime_guard or (lambda: True)
        self.grants: dict[Principal, Grant] = {}

    async def bound(self, p: Principal) -> bool:
        try:
            active = self.runtime_guard() is True
        except Exception:
            active = False
        if not active:
            self.grants.clear()
            return False
        if p.app_id != self.app_id or self.allowed_owners is not None and p.owner not in self.allowed_owners or not self.repo:
            return False
        row = await self.repo.find_connection_by_external_identity(provider="feishu", external_account_id=p.sender, workspace_id=p.workspace)
        valid = bool(row and row.get("id") == p.connection and row.get("owner_user_id") == p.owner and row.get("status") == "connected")
        if not valid:
            self.grants.pop(p, None)
        return valid

    def put(self, p: Principal, grant: Grant):
        self.prune()
        if p.app_id != self.app_id or self.allowed_owners is not None and p.owner not in self.allowed_owners or not self.store and len(self.grants) >= 64 and p not in self.grants:
            raise WorkError("grant_admission")
        self.grants[p] = grant

    async def require(self, p: Principal, scope: str) -> Grant:
        if not await self.bound(p):
            raise WorkError("binding_changed")
        if self.store:
            key = self.principal_key(p)
            record = await self.store.read("grant", key, owner=key)
            if record:
                self._records[p] = record
                value = record.value
                self.grants[p] = Grant(value["token"], time.monotonic() + record.expires_at - time.time(), frozenset(value["scopes"]), record.revision)
            else:
                self.grants.pop(p, None)
                self._records.pop(p, None)
        grant = self.grants.get(p)
        if not grant or grant.expires_at <= time.monotonic():
            self.grants.pop(p, None)
            raise WorkError("authorization_required")
        if not has_scopes(grant.scopes, scope):
            raise WorkError("scope_not_granted")
        return grant

    @staticmethod
    def principal_key(p: Principal):
        import json

        return json.dumps(p.__dict__, sort_keys=True, separators=(",", ":"))

    async def install(self, p: Principal, grant: Grant):
        if not await self.bound(p):
            raise WorkError("binding_changed")
        if self.store:
            key = self.principal_key(p)
            record = await self.store.put("grant", key, {"token": grant.token, "scopes": sorted(grant.scopes)}, owner=key, ttl=max(1, grant.expires_at - time.monotonic()), status="active")
            self._records[p] = record
            grant.revision = record.revision
        self.put(p, grant)

    async def revoke_shared(self, p: Principal, *, expected: Grant | None = None):
        if self.store:
            key = self.principal_key(p)
            if expected is None:
                record = await self.store.read("grant", key, owner=key)
            else:
                record = self._records.get(p)
                # Another in-process require may already have loaded a newer consent.
                if not record or record.revision != expected.revision or record.value.get("token") != expected.token:
                    record = None
            if record:
                await self.store.remove("grant", key, record)
        self.revoke(p, expected=expected)

    def prune(self):
        for p, g in list(self.grants.items()):
            if g.expires_at <= time.monotonic():
                self.grants.pop(p, None)

    def revoke(self, p: Principal, *, expected: Grant | None = None):
        # A slow request using an old token must not revoke a newer consent.
        if expected is None or self.grants.get(p) is expected:
            self.grants.pop(p, None)
