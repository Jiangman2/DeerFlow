"""Opt-in company knowledge access through native WeKnora viewer membership.

The organizations and KBs are created and shared by administrators. This
adapter never creates resources, changes roles, or borrows an administrator's
credentials. It joins only pinned, discoverable, approval-free organizations
with the authenticated user's own token and consumer workspace.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException

_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_DEADLINE_SECONDS = 20


def _unavailable(code: str) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": code, "message": "全员共享知识库暂不可用，请联系管理员检查共享空间配置。"})


@dataclass(frozen=True)
class CompanyKnowledge:
    organization_ids: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> CompanyKnowledge | None:
        enabled = os.getenv("WEKNORA_COMPANY_KB_ENABLED", "false").strip().lower()
        if enabled in {"false", "0", "off", "no", ""}:
            return None
        if enabled not in {"true", "1", "on", "yes"}:
            raise _unavailable("COMPANY_KB_CONFIGURATION_INVALID")
        try:
            configured = json.loads(os.getenv("WEKNORA_COMPANY_KB_ORGANIZATION_IDS", "[]"))
        except ValueError:
            raise _unavailable("COMPANY_KB_CONFIGURATION_INVALID") from None
        if not isinstance(configured, list) or not 1 <= len(configured) <= 16:
            raise _unavailable("COMPANY_KB_CONFIGURATION_INVALID")
        values = []
        for raw in configured:
            if not isinstance(raw, str):
                raise _unavailable("COMPANY_KB_CONFIGURATION_INVALID")
            try:
                value = str(UUID(raw))
            except (ValueError, AttributeError):
                raise _unavailable("COMPANY_KB_CONFIGURATION_INVALID") from None
            if raw.lower() != value:
                raise _unavailable("COMPANY_KB_CONFIGURATION_INVALID")
            values.append(value)
        if len(set(values)) != len(values):
            raise _unavailable("COMPANY_KB_CONFIGURATION_INVALID")
        return cls(tuple(values))


async def _request(client: httpx.AsyncClient, method: str, url: str, *, payload: dict | None = None) -> tuple[int, Any]:
    async with client.stream(method, url, json=payload) as response:
        if response.status_code not in {200, 201}:
            # Never forward provider text, credentials, private metadata or redirects.
            return response.status_code, None
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            body.extend(chunk)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise _unavailable("COMPANY_KB_RESPONSE_TOO_LARGE")
        try:
            envelope = json.loads(body)
        except (ValueError, UnicodeError):
            raise _unavailable("COMPANY_KB_PROTOCOL_INVALID") from None
        if not isinstance(envelope, dict) or envelope.get("success") is not True or "data" not in envelope:
            raise _unavailable("COMPANY_KB_PROTOCOL_INVALID")
        return response.status_code, envelope["data"]


def _organization(value: Any, organization_id: str) -> dict:
    if not isinstance(value, dict) or value.get("id") != organization_id:
        raise _unavailable("COMPANY_KB_ORGANIZATION_MISMATCH")
    return value


async def _ensure_membership(client: httpx.AsyncClient, endpoint: Callable[[str], str], organization_id: str) -> None:
    org_url = endpoint("/organizations/" + organization_id)
    status, data = await _request(client, "GET", org_url)
    if status != 200:
        raise _unavailable("COMPANY_KB_ORGANIZATION_UNAVAILABLE")
    org = _organization(data, organization_id)
    role = org.get("my_role")
    if role is not None and not isinstance(role, str):
        raise _unavailable("COMPANY_KB_MEMBERSHIP_INVALID")
    if role not in {"viewer", "editor", "admin"}:
        if role not in {None, ""}:
            raise _unavailable("COMPANY_KB_MEMBERSHIP_INVALID")
        # Fail before a mutation if the native organization requires approval,
        # is private, or still has the default small member cap.
        if org.get("searchable") is not True or org.get("require_approval") is not False or type(org.get("member_limit")) is not int or org["member_limit"] != 0:
            raise _unavailable("COMPANY_KB_ORGANIZATION_NOT_GLOBAL")
        try:
            status, _ = await _request(client, "POST", endpoint("/organizations/join-by-id"), payload={"organization_id": organization_id, "role": "viewer"})
        except httpx.TransportError:
            # A lost response may already have committed. Read once; never
            # replay a possibly committed POST during this provisioning attempt.
            status = None
        if status not in {None, 200, 201}:
            raise _unavailable("COMPANY_KB_JOIN_REJECTED")
        status, data = await _request(client, "GET", org_url)
        if status != 200:
            raise _unavailable("COMPANY_KB_JOIN_UNCONFIRMED")
        org = _organization(data, organization_id)
        role = org.get("my_role")
    if role != "viewer":
        # Do not silently demote an existing native maintainer or accept a
        # provider that grants more than the requested viewer permission.
        raise _unavailable("COMPANY_KB_VIEWER_MEMBERSHIP_REQUIRED")


async def _ensure_access(client: httpx.AsyncClient, endpoint: Callable[[str], str], config: CompanyKnowledge) -> None:
    for organization_id in config.organization_ids:
        await _ensure_membership(client, endpoint, organization_id)
    status, shared = await _request(client, "GET", endpoint("/shared-knowledge-bases"))
    if status != 200 or not isinstance(shared, list):
        raise _unavailable("COMPANY_KB_SHARING_UNAVAILABLE")
    for row in shared:
        if not isinstance(row, dict):
            raise _unavailable("COMPANY_KB_PROTOCOL_INVALID")
        if row.get("organization_id") in config.organization_ids and row.get("permission") != "viewer":
            raise _unavailable("COMPANY_KB_READONLY_SHARE_REQUIRED")
    # Empty spaces are valid: subsequent KBs shared there become available
    # through native RBAC without changing a pinned KB list or redeploying.


async def ensure_company_knowledge_access(session: Any, endpoint: Callable[[str], str], *, administrator: bool) -> None:
    """Run before caching SSO; native RBAC still authorizes every later request.

    Disabling the policy stops future auto-joins; it does not revoke memberships.
    A user who leaves a mandatory company space rejoins on a later SSO session.
    """
    # Native system administrators already retain full workspace authority.
    # Do not block the maintenance UI when the new sharing policy is misconfigured.
    if administrator:
        return
    config = CompanyKnowledge.from_environment()
    if config is None:
        return
    tenant_id = session.tenant.get("id")
    if isinstance(tenant_id, bool) or not str(tenant_id).isdigit() or int(tenant_id) <= 0 or not session.token:
        raise _unavailable("COMPANY_KB_SESSION_INVALID")
    headers = {"Authorization": "Bearer " + session.token, "X-Tenant-ID": str(tenant_id)}
    try:
        async with asyncio.timeout(_DEADLINE_SECONDS):
            async with httpx.AsyncClient(headers=headers, timeout=5, trust_env=False, follow_redirects=False) as client:
                await _ensure_access(client, endpoint, config)
    except (httpx.HTTPError, TimeoutError):
        raise _unavailable("COMPANY_KB_TRANSPORT_FAILED") from None
