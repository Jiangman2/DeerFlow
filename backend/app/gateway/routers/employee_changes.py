"""Creator-only conversational proposal review and version-to-draft restore."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.gateway.deps import get_current_user_from_request
from app.gateway.routers import agents
from app.gateway.routers.employee_resources import _actor, _http_error
from deerflow.config.paths import get_paths
from deerflow.persistence.agents import get_agent_store
from deerflow.persistence.agents.drafts import DraftConflict
from deerflow.persistence.agents.proposals import EmployeeProposalStore
from deerflow.persistence.agents.publications import PublicationForbidden, TeamPublicationStore

router = APIRouter(prefix="/api/agents", tags=["employee-changes"])


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision: str = Field(min_length=1, max_length=80)
    approve: bool


class VersionRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_draft_revision: str = Field(min_length=1, max_length=80)


async def _validate(updates, soul, user):
    request = agents.AgentUpdateRequest(**updates, **({"soul": soul} if soul is not None else {}))
    if "model" in updates:
        await asyncio.to_thread(agents._validate_model_exists, request.model)
    if "skills" in updates:
        request.skills = await asyncio.to_thread(agents.validate_agent_skills, request.skills, user_id=str(user.id))
    if "knowledge_scope" in updates:
        request.knowledge_scope = await agents.validate_creator_knowledge_scope(request.knowledge_scope, user)
    if "category_id" in updates:
        await asyncio.to_thread(agents._ensure_category_exists, request.category_id or agents.DEFAULT_DIGITAL_EMPLOYEE_CATEGORY_ID)
    canonical = request.model_dump(mode="json", exclude_unset=True)
    canonical.pop("soul", None)
    canonical.pop("expected_draft_revision", None)
    return canonical


@router.get("/{name}/proposals")
async def list_proposals(name: str, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    try:
        await asyncio.to_thread(lambda: get_agent_store().read_definition(name, user_id=uid))
        return {"proposals": await asyncio.to_thread(EmployeeProposalStore(get_paths()).list, name, uid)}
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise _http_error(exc) from None


@router.get("/{name}/proposals/{proposal_id}")
async def get_proposal(name: str, proposal_id: str, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    try:
        # Existing employee identity must still be present, including SQL mode.
        current = await asyncio.to_thread(lambda: get_agent_store().read_definition(name, user_id=uid))
        proposal = await asyncio.to_thread(EmployeeProposalStore(get_paths()).get, name, uid, proposal_id)
        return {**proposal, "current_draft_revision": current.revision, "conflict": current.revision not in (proposal["expected_draft_revision"], proposal["target_revision"])}
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise _http_error(exc) from None


@router.post("/{name}/proposals/{proposal_id}/decision")
async def decide_proposal(name: str, proposal_id: str, body: ProposalDecision, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    proposals = EmployeeProposalStore(get_paths())
    try:
        proposal = await asyncio.to_thread(proposals.get, name, uid, proposal_id)
        if proposal["expected_draft_revision"] != body.expected_draft_revision:
            raise DraftConflict("Review changed; reload the proposal")
        if not body.approve:
            return await asyncio.to_thread(proposals.finish, name, uid, proposal_id, state="rejected", current_revision=body.expected_draft_revision)
        updates = await _validate(proposal["updates"], proposal["soul"], user)
        current = await asyncio.to_thread(proposals.apply, name, uid, proposal_id, agent_store=get_agent_store(), validated_updates=updates, validated_soul=proposal["soul"])
        return {"state": "applied", "draft_revision": current.revision, "publication_changed": False}
    except DraftConflict:
        raise HTTPException(409, "Employee was edited after this proposal; no stale changes were applied") from None
    except (ValueError, PermissionError, FileNotFoundError) as exc:
        raise _http_error(exc) from None


@router.post("/{name}/publications/{revision}/restore-draft")
async def restore_publication_draft(name: str, revision: str, body: VersionRestore, user=Depends(get_current_user_from_request)):
    uid = _actor(user)
    try:
        from deerflow.persistence.agents.proposals import EDITABLE_FIELDS

        snapshot = await asyncio.to_thread(TeamPublicationStore(get_paths()).read_revision, name, revision, owner=uid)
        if snapshot.state != "published":
            raise ValueError("A withdrawn receipt is not a restorable definition")
        # Restore managed definition fields, never publication/grant credentials
        # or unknown private fields. Existing avatar file and memory stay intact.
        from deerflow.persistence.agents import parse_agent_config

        # Absent managed fields mean their historical defaults, not whatever
        # a later edit happens to contain. Unknown private fields are excluded
        # from this patch and therefore survive the restoration verbatim.
        restored = parse_agent_config(snapshot.config, name).model_dump(mode="json")
        updates = {key: restored[key] for key in EDITABLE_FIELDS}
        updates = await _validate(updates, snapshot.soul, user)
        current = await asyncio.to_thread(lambda: get_agent_store().patch_definition(name, updates, snapshot.soul, user_id=uid, expected_revision=body.expected_draft_revision))
        return {"draft_revision": current.revision, "restored_from": snapshot.revision, "publication_changed": False, "memory_and_uploaded_avatar": "preserved"}
    except DraftConflict:
        raise HTTPException(409, "Employee draft changed; reload before restoring") from None
    except (ValueError, PermissionError, FileNotFoundError, PublicationForbidden) as exc:
        raise _http_error(exc) from None
