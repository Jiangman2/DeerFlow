"""Bounded, fixed-operation knowledge broker for Digital Employee invocation.

Only this Gateway adapter acquires the resource owner's WeKnora session. No
session, general-purpose client, resource URL or owner identity enters tool
arguments, the viewer's runtime context, or Sandbox environment.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import ssl
import tempfile
import time
from pathlib import Path

import httpx

from deerflow.config.agent_knowledge_config import AgentKnowledgeScope
from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import _publication_lock, _read_file, _sync_dir
from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, knowledge_resource, require_publication_grants
from deerflow.persistence.agents.runtime import EmployeePublicationBindings

logger = logging.getLogger(__name__)
_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
_MAX_RESPONSE = 2 * 1024 * 1024


async def _lookup_actor(user_id):
    from app.gateway.deps import get_local_provider

    return await get_local_provider().get_user(user_id)


async def _lookup_session(actor):
    from app.gateway.weknora_identity import ensure_weknora_session_for_deerflow_user

    return await ensure_weknora_session_for_deerflow_user(actor)


async def bounded_knowledge_request(method, path, token, *, tenant_id=None, json=None):
    """Not a generic proxy: allow only identity, metadata and bounded search."""
    from app.gateway.weknora_identity import weknora_endpoint

    if not ((method == "GET" and (path == "/auth/me" or re.fullmatch(r"/(?:knowledge-bases|knowledge)/" + _ID, path))) or (method == "POST" and path == "/knowledge-search")):
        raise PermissionError("Unsupported employee knowledge operation")
    if tenant_id is not None and not re.fullmatch(_ID, tenant_id):
        raise PermissionError("Invalid knowledge tenant")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/json"}
    if tenant_id:
        headers["X-Tenant-ID"] = tenant_id
    verify = await asyncio.to_thread(ssl.create_default_context, cafile=os.getenv("WEKNORA_CA_BUNDLE") or os.getenv("SSL_CERT_FILE") or None)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3), follow_redirects=False, trust_env=False, verify=verify) as client:
            async with client.stream(method, weknora_endpoint(path), headers=headers, json=json) as response:
                if response.status_code != 200:
                    raise PermissionError("Knowledge resource is unavailable or no longer authorized")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > _MAX_RESPONSE:
                        raise ValueError("Knowledge response exceeds the employee retrieval limit")
        result = __import__("json").loads(body)
        if not isinstance(result, dict):
            raise ValueError("Unexpected knowledge response")
        return result
    except httpx.HTTPError:
        # Do not echo URLs, response bodies or request/credential exceptions.
        raise RuntimeError("Employee knowledge service is temporarily unavailable") from None


def require_knowledge_grants(paths, publication):
    scope = AgentKnowledgeScope.model_validate(publication.config.get("knowledge_scope") or {})
    identifiers = publication.config.get("knowledge_grants")
    if scope.mode != "selected" or not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 20 or len(set(identifiers)) != len(identifiers):
        raise PermissionError("Employee has no approved knowledge invocation scope")
    store = EmployeeGrantStore(paths)
    expected = {knowledge_resource(item) for item in scope.items}
    grants = []
    for identifier in identifiers:
        record = store.get(identifier)
        if record.resource not in expected:
            raise PermissionError("Employee knowledge grant does not match its published scope")
        store.require(identifier, publication_id=publication.publication_id, publisher=publication.owner, resource=record.resource)
        grants.append(record)
    if {g.resource for g in grants} != expected or len(grants) != len(expected):
        raise PermissionError("Employee knowledge scope is missing an owner approval")
    return grants


async def verify_knowledge_owner(actor, resource, *, session_lookup=_lookup_session, request=bounded_knowledge_request):
    """Fresh ownership, not a saved is_owner flag or a readable shared KB."""
    if actor is None or getattr(actor, "is_disabled", True):
        raise PermissionError("Knowledge owner is unavailable")
    session = await session_lookup(actor)
    identity = await request("GET", "/auth/me", session.token)
    data = identity.get("data", {})
    session_user = getattr(session, "user", None)
    owner_id = str(session_user.get("id") or "") if isinstance(session_user, dict) else ""
    identity_user = data.get("user") if isinstance(data, dict) else None
    if not owner_id or not isinstance(identity_user, dict) or str(identity_user.get("id")) != owner_id:
        raise PermissionError("Knowledge owner identity could not be verified")
    memberships = data.get("memberships")
    if not isinstance(memberships, list) or not any(isinstance(x, dict) and str(x.get("tenant_id")) == resource.tenant_id for x in memberships):
        raise PermissionError("Knowledge owner tenant access changed")
    kb = (await request("GET", "/knowledge-bases/" + resource.kb_id, session.token, tenant_id=resource.tenant_id)).get("data", {})
    if not isinstance(kb, dict) or str(kb.get("id")) != resource.kb_id or str(kb.get("creator_id")) != owner_id:
        raise PermissionError("Knowledge read permission does not grant onward sharing")
    if resource.document_id:
        doc = (await request("GET", "/knowledge/" + resource.document_id, session.token, tenant_id=resource.tenant_id)).get("data", {})
        if not isinstance(doc, dict) or str(doc.get("id")) != resource.document_id or str(doc.get("knowledge_base_id")) != resource.kb_id:
            raise PermissionError("Knowledge document is no longer in the approved scope")
    return session


def _plain_excerpt(value, limit):
    text = str(value or "")[: limit * 4]
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?:https?|file|resource)://[^\s<>]+", "[link withheld]", text, flags=re.I)
    text = re.sub(r"<[^>]*>", "", text)
    return text[:limit]


class EmployeeKnowledgeBroker:
    def __init__(self, paths: Paths, *, actor_lookup=_lookup_actor, session_lookup=_lookup_session, request=bounded_knowledge_request):
        self.paths = paths
        self.actor_lookup = actor_lookup
        self.session_lookup = session_lookup
        self.request = request

    async def authorize(self, publication, viewer_id, *, verify_owners=False):
        """Admission/turn authorization; no credential leaves this service."""
        grants = await asyncio.to_thread(require_publication_grants, EmployeeGrantStore(self.paths), publication)
        actors = {}
        for uid in dict.fromkeys([viewer_id, publication.owner] + [g.approver for g in grants]):
            actor = await self.actor_lookup(uid)
            if actor is None or getattr(actor, "is_disabled", True) or str(actor.id) != uid:
                raise PermissionError("Employee authorization participant is unavailable")
            actors[uid] = actor
        for grant in grants:
            if grant.resource.kind == "skill" and getattr(actors[grant.approver], "system_role", None) != "admin":
                raise PermissionError("Employee Skill approval is no longer valid")
            if verify_owners and grant.resource.kind == "knowledge":
                await verify_knowledge_owner(actors[grant.approver], grant.resource, session_lookup=self.session_lookup, request=self.request)

    def _admit(self, thread_id, viewer_id):
        publication = EmployeePublicationBindings(self.paths).read(thread_id, user_id=viewer_id)
        if publication is None:
            raise PermissionError("An admitted employee conversation is required")
        return publication, require_knowledge_grants(self.paths, publication)

    def _consume_budget(self, publication_id, viewer_id):
        # Shared-file admission bounds sequential extraction across processes.
        root = self.paths.team_agents_dir / ".knowledge-search-budgets"
        if root.is_symlink():
            raise ValueError("Unsafe knowledge budget directory")
        key = hashlib.sha256((publication_id + "\0" + viewer_id).encode()).hexdigest()
        path = root / (key + ".json")
        with _publication_lock(root / (key + ".lock")):
            now = time.time()
            try:
                current = json.loads(_read_file(path))
            except FileNotFoundError:
                current = {"started": now, "count": 0}
            if not isinstance(current, dict) or not isinstance(current.get("started"), (int, float)) or not isinstance(current.get("count"), int):
                raise ValueError("Invalid employee retrieval budget")
            if now - current["started"] >= 60:
                current = {"started": now, "count": 0}
            if current["count"] >= 30:
                raise PermissionError("Employee knowledge request limit reached; retry later")
            current["count"] += 1
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=root, prefix=".budget-", delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(json.dumps(current).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
                _sync_dir(root)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)

    async def search(self, *, thread_id: str, viewer_id: str, query: str):
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("Employee knowledge query must contain 1–2000 characters")
        try:
            return await asyncio.wait_for(self._search(thread_id, viewer_id, query.strip()), timeout=30)
        except TimeoutError:
            raise RuntimeError("Employee knowledge search timed out; no results were released") from None

    async def _search(self, thread_id, viewer_id, query):
        publication, grants = await asyncio.to_thread(self._admit, thread_id, viewer_id)
        for uid in dict.fromkeys([viewer_id, publication.owner]):
            actor = await self.actor_lookup(uid)
            if actor is None or getattr(actor, "is_disabled", True) or str(actor.id) != uid:
                raise PermissionError("Employee participant is unavailable")
        await asyncio.to_thread(self._consume_budget, publication.publication_id, viewer_id)
        results = []
        # At most 20 selected resources, serial calls share one 30s wall-clock
        # boundary. No unbounded task fan-out remains after a failed request.
        for grant in grants:
            actor = await self.actor_lookup(grant.approver)
            if actor is None or str(actor.id) != grant.approver:
                raise PermissionError("Knowledge authorization owner is unavailable")
            session = await verify_knowledge_owner(actor, grant.resource, session_lookup=self.session_lookup, request=self.request)
            resource = grant.resource
            scope = {"knowledge_ids": [resource.document_id]} if resource.document_id else {"knowledge_base_ids": [resource.kb_id]}
            data = await self.request("POST", "/knowledge-search", session.token, tenant_id=resource.tenant_id, json={"query": query, **scope})
            hits = data.get("data")
            if not isinstance(hits, list):
                raise ValueError("Unexpected employee knowledge result")
            for hit in hits[:100]:
                if not isinstance(hit, dict) or str(hit.get("knowledge_base_id")) != resource.kb_id or not hit.get("knowledge_id"):
                    continue
                if resource.document_id and str(hit["knowledge_id"]) != resource.document_id:
                    continue
                if len(results) < 8:
                    results.append({"citation": len(results) + 1, "title": _plain_excerpt(hit.get("knowledge_title"), 160), "content": _plain_excerpt(hit.get("content"), 1800)})
        # Withdrawal or revocation during upstream I/O discards all fetched
        # text. No stale success is returned from an in-memory grant cache.
        current, _ = await asyncio.to_thread(self._admit, thread_id, viewer_id)
        if (current.publication_id, current.revision, current.digest) != (publication.publication_id, publication.revision, publication.digest):
            raise PermissionError("Employee admission changed during retrieval")
        logger.info("employee_knowledge_search viewer=%s publication=%s revision=%s grants=%s results=%d", viewer_id, publication.publication_id, publication.revision, ",".join(g.id for g in grants), len(results))
        return {"results": results, "total_results": len(results), "access": "employee_question_answer_only"}
