"""Explicit publication checks and owner-approved resource invocation grants."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.gateway.agent_knowledge import validate_creator_knowledge_scope
from app.gateway.authz import require_permission
from app.gateway.deps import get_authorized_read_scope_user_id, get_current_user_from_request, get_thread_store
from app.gateway.employee_knowledge import verify_knowledge_owner
from app.gateway.employee_publication import prepare_resources
from deerflow.config.agent_knowledge_config import AgentKnowledgeScope
from deerflow.config.agents_config import is_personal_agent, load_agent_config, validate_agent_name, validate_agent_source
from deerflow.config.paths import get_paths
from deerflow.persistence.agents import get_agent_store
from deerflow.persistence.agents.publications import PublicationConflict, PublicationForbidden
from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, GrantResource
from deerflow.persistence.agents.runtime import EmployeePublicationBindings

router = APIRouter(prefix="/api", tags=["employee-resources"])


def _require_enabled():
    from app.gateway.routers.agents import _require_agents_api_enabled

    _require_agents_api_enabled()


def _actor(user):
    _require_enabled()
    if user is None or not getattr(user, "id", None) or getattr(user, "is_disabled", True):
        raise HTTPException(401, "Authenticated active employee participant required")
    return str(user.id)


class GrantRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern=r"^[a-f0-9]{32}$")


class GrantDecision(GrantRevision):
    approve: bool
    expires_at: datetime
    acknowledge_onward_sharing: bool = False


class PublicationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision: str = Field(min_length=1, max_length=80)


class ResourceRequest(PublicationCheck):
    resource: GrantResource


def _invocation_catalog(name, uid, source, thread_id):
    """Read metadata only; do not admit a run, install or project a Skill."""
    from deerflow.skills.agent_publication import read_skill_bundle
    from deerflow.skills.storage import get_or_new_user_skill_storage

    name = validate_agent_name(name)
    source = validate_agent_source(source)
    paths = get_paths()
    bindings = EmployeePublicationBindings(paths)
    publication = bindings.read(thread_id, user_id=uid) if thread_id else None
    if publication is not None:
        if publication.name != name or source not in (None, "team"):
            raise PublicationConflict("Employee conversation identity differs")
    elif source == "team" or (source is None and not is_personal_agent(name, user_id=uid) and bindings.publications.has_record(name)):
        if thread_id:
            raise PublicationConflict("Shared conversation has no admitted employee")
        publication = bindings._authorize_resources(bindings.publications.read(name))
    if publication is not None and "skills_revision" in publication.config:
        names = publication.config.get("skills")
        read_skill_bundle(publication.directory, names=names, digest=publication.config["skills_revision"])
        # Recheck the head/grants after reading the bundle. Revocation must not
        # silently turn discovery into the viewer's personal Skill catalog.
        bindings._authorize_resources(bindings.publications.read_admitted(name, publication_id=publication.publication_id, revision=publication.revision, digest=publication.digest))
        return {"revision": publication.revision, "skills": [{"name": item, "description": "", "enabled": True} for item in names]}
    config = publication.config if publication is not None else load_agent_config(name, user_id=uid, source=source).model_dump()
    allowed = config.get("skills")
    skills = get_or_new_user_skill_storage(uid).load_skills(enabled_only=True)
    return {"revision": publication.revision if publication is not None else None, "skills": [{"name": skill.name, "description": skill.description[:1000], "enabled": True} for skill in skills if allowed is None or skill.name in allowed]}


@router.get("/agents/{name}/invocation-skills")
async def employee_invocation_skills(name: str, request: Request, response: Response, source: str | None = None, thread_id: str | None = Query(default=None, min_length=1, max_length=128), user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    response.headers["Cache-Control"] = "private, no-store"
    if thread_id:
        # Invocation UI has no cross-owner admin exception. Never accept a
        # browser-supplied owner/revision or use its own arbitrary file bucket.
        record = await get_thread_store(request).get(thread_id, user_id=uid)
        if not record or str(record.get("user_id")) != uid:
            raise HTTPException(404, "Employee conversation is unavailable")
    try:
        return await asyncio.to_thread(_invocation_catalog, name, uid, source, thread_id)
    except (ValueError, PermissionError, FileNotFoundError, PublicationConflict, TimeoutError, RuntimeError) as exc:
        raise _http_error(exc) from None


@router.get("/threads/{thread_id}/employee-publication")
@require_permission("threads", "read", owner_check=True, require_existing=True)
async def thread_publication(thread_id: str, request: Request):
    _require_enabled()
    scope = await get_authorized_read_scope_user_id(request)
    record = await get_thread_store(request).get(thread_id, user_id=scope)
    if not record or not record.get("user_id"):
        raise HTTPException(404, "Employee conversation owner is unavailable")
    try:
        return await asyncio.to_thread(EmployeePublicationBindings(get_paths()).describe, thread_id, user_id=str(record["user_id"]))
    except (ValueError, PermissionError, FileNotFoundError, PublicationConflict) as exc:
        raise _http_error(exc) from None


async def _read_definition(name, user, expected_revision):
    name = validate_agent_name(name)
    if not name:
        raise HTTPException(422, "Employee name required")
    uid = _actor(user)
    definition = await asyncio.to_thread(lambda: get_agent_store().read_definition(name, user_id=uid))
    if definition.revision != expected_revision:
        raise PublicationConflict("Employee draft changed; reload its resource selection")
    config = dict(definition.config)
    scope = AgentKnowledgeScope.model_validate(config.get("knowledge_scope") or {})
    scope = await validate_creator_knowledge_scope(scope, user)
    config["knowledge_scope"] = scope.model_dump(mode="json")
    return name, SimpleNamespace(config=config, soul=definition.soul, revision=definition.revision)


def _prepare(paths, name, uid, definition, consume):
    root = paths.team_agents_dir / ".publication-checks"
    if root.is_symlink():
        raise ValueError("Unsafe employee publication staging directory")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="review-", dir=root) as temporary:
        prepared = prepare_resources(paths, name, uid, definition, staging=Path(temporary))
        return consume(prepared)


def _http_error(exc):
    if isinstance(exc, (TimeoutError, RuntimeError)):
        return HTTPException(503, "Resource authorization service is temporarily unavailable; no approval was changed")
    if isinstance(exc, (PermissionError, PublicationForbidden)):
        return HTTPException(403, "Resource owner approval is required for this employee operation")
    if isinstance(exc, FileNotFoundError):
        return HTTPException(404, "Employee or resource authorization is unavailable")
    if isinstance(exc, PublicationConflict):
        return HTTPException(409, "Employee changed; reload before continuing")
    return HTTPException(422, "Employee resource request is invalid or no longer matches its draft")


@router.post("/agents/{name}/publication-check")
async def check_publication(name: str, body: PublicationCheck, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    paths = get_paths()
    try:
        name, definition = await _read_definition(name, user, body.expected_draft_revision)

        def inspect(prepared):
            grants = EmployeeGrantStore(paths).list_for(uid, publication_id=prepared.publication_id, limit=1000)
            return {
                "publication_id": prepared.publication_id,
                "draft_revision": prepared.draft_revision,
                "resources": [
                    {"resource": resource.model_dump(mode="json", exclude_none=True), "grants": [g.model_dump(mode="json") for g in grants if g.publication_id == prepared.publication_id and g.resource == resource]}
                    for resource in prepared.resources
                ],
            }

        return await asyncio.to_thread(_prepare, paths, name, uid, definition, inspect)
    except (ValueError, PermissionError, FileNotFoundError, PublicationConflict, PublicationForbidden, TimeoutError, RuntimeError) as exc:
        raise _http_error(exc) from None


@router.post("/agents/{name}/resource-grants")
async def request_grant(name: str, body: ResourceRequest, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    paths = get_paths()
    try:
        name, definition = await _read_definition(name, user, body.expected_draft_revision)

        def submit(prepared):
            if body.resource not in prepared.resources:
                raise PermissionError("Resource is not part of the creator's current employee selection")
            return EmployeeGrantStore(paths).request(publication_id=prepared.publication_id, publisher=uid, resource=body.resource, requested_by=uid)

        return await asyncio.to_thread(_prepare, paths, name, uid, definition, submit)
    except (ValueError, PermissionError, FileNotFoundError, PublicationConflict, PublicationForbidden, TimeoutError, RuntimeError) as exc:
        raise _http_error(exc) from None


@router.get("/employee-resource-grants")
async def list_grants(user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    return {"grants": await asyncio.to_thread(EmployeeGrantStore(get_paths()).list_for, uid, limit=200)}


async def _verify_approver(user, resource, *, acknowledge=False):
    if resource.kind == "knowledge":
        await asyncio.wait_for(verify_knowledge_owner(user, resource), timeout=20)
    elif getattr(user, "system_role", None) != "admin" or not acknowledge:
        # Installed/downloaded is NOT an onward-sharing grant. This admin
        # approval represents an explicit platform policy decision, not a
        # claim that SkillHub granted the installer source ownership.
        raise PermissionError("An explicit platform Skill sharing approval is required")


@router.get("/employee-resource-grants/{grant_id}")
async def get_grant(grant_id: str, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    try:
        record = await asyncio.to_thread(EmployeeGrantStore(get_paths()).get, grant_id)
        can_decide = False
        try:
            await _verify_approver(user, record.resource, acknowledge=getattr(user, "system_role", None) == "admin")
            can_decide = record.state == "pending"
        except PermissionError:
            if uid not in (record.publisher, record.approver):
                raise
        return {"grant": record, "can_decide": can_decide, "can_revoke": uid in (record.publisher, record.approver) and record.state != "revoked"}
    except (ValueError, PermissionError, FileNotFoundError, TimeoutError, RuntimeError) as exc:
        raise _http_error(exc) from None


@router.post("/employee-resource-grants/{grant_id}/decision")
async def decide_grant(grant_id: str, body: GrantDecision, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    store = EmployeeGrantStore(get_paths())
    try:
        record = await asyncio.to_thread(store.get, grant_id)
        await _verify_approver(user, record.resource, acknowledge=body.acknowledge_onward_sharing or not body.approve)
        return await asyncio.to_thread(store.decide, grant_id, actor=uid, expected_revision=body.expected_revision, approve=body.approve, expires_at=body.expires_at)
    except (ValueError, PermissionError, FileNotFoundError, TimeoutError, RuntimeError) as exc:
        if isinstance(exc, ValueError) and "changed" in str(exc):
            raise HTTPException(409, "Authorization changed; reload before deciding") from None
        raise _http_error(exc) from None


@router.post("/employee-resource-grants/{grant_id}/revoke")
async def revoke_grant(grant_id: str, body: GrantRevision, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    try:
        return await asyncio.to_thread(EmployeeGrantStore(get_paths()).revoke, grant_id, actor=uid, expected_revision=body.expected_revision)
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise _http_error(exc) from None
