"""Prepare explicit publication dependencies without editing private drafts."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from app.gateway.employee_knowledge import knowledge_resource
from deerflow.config.agent_knowledge_config import AgentKnowledgeScope
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, GrantResource
from deerflow.skills.agent_publication import read_skill_bundle, stage_agent_skills

_PUBLIC_FIELDS = frozenset({"name", "display_name", "description", "model", "tool_groups", "skills", "knowledge_scope", "category_id", "tags", "avatar_id", "model_settings", "thinking_enabled", "reasoning_effort"})


def publication_config(raw):
    # Preserve unknown/private fields in the creator's draft and backups, not
    # in a new cross-user runtime snapshot. Never copy channel installation ids
    # or an unrecognised secret-bearing integration field into the Square.
    return {key: value for key, value in raw.items() if key in _PUBLIC_FIELDS}


@dataclass(frozen=True)
class PreparedResources:
    publication_id: str
    draft_revision: str
    config: dict
    resources: list[GrantResource]
    staging: Path


def prepare_resources(paths, name, publisher, definition, *, staging: Path, app_config=None):
    config = publication_config(definition.config)
    names = config.get("skills")
    if names is None:
        raise ValueError("Publishing requires an explicit Skill selection; choose [] for no Skills")
    digest = stage_agent_skills(staging, names, user_id=publisher, app_config=app_config)
    _, manifest, _ = read_skill_bundle(staging, names=names, digest=digest)
    config["skills_revision"] = digest
    resources = []
    for skill_name, entries in manifest["skills"].items():
        content_digest = hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        resources.append(GrantResource(kind="skill", skill_name=skill_name, content_digest=content_digest))
    scope = AgentKnowledgeScope.model_validate(config.get("knowledge_scope") or {})
    if scope.mode == "selected":
        resources.extend(knowledge_resource(item) for item in scope.items)
    identity = TeamPublicationStore(paths).reserve_identity(name, owner=publisher)
    return PreparedResources(identity, definition.revision, config, resources, staging)


def authorize_prepared(paths, prepared, *, publisher: str, grant_ids: list[str]):
    if len(grant_ids) != len(set(grant_ids)) or len(grant_ids) > 148:
        raise ValueError("Invalid employee resource authorization list")
    store = EmployeeGrantStore(paths)
    expected = set(prepared.resources)
    approved = {}
    for identifier in grant_ids:
        grant = store.get(identifier)
        if grant.resource not in expected or grant.resource in approved:
            raise PermissionError("Authorization does not match the prepared employee")
        store.require(identifier, publication_id=prepared.publication_id, publisher=publisher, resource=grant.resource)
        approved[grant.resource] = grant
    if set(approved) != expected:
        raise PermissionError("Employee resources require owner-approved invocation grants")
    return {
        **prepared.config,
        "resource_policy_version": 1,
        "skill_grants": [approved[r].id for r in prepared.resources if r.kind == "skill"],
        "knowledge_grants": [approved[r].id for r in prepared.resources if r.kind == "knowledge"],
    }


async def _finish_file_operation(function, *args, **kwargs):
    # Cancellation must not remove a staging directory while a worker thread
    # is still copying into it or atomically committing its publication.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


async def publish_reviewed_employee(paths, name, publisher, definition, *, expected_revision, grant_ids, avatar=None):
    from app.gateway.employee_knowledge import EmployeeKnowledgeBroker

    root = paths.team_agents_dir / ".publication-checks"
    if root.is_symlink():
        raise ValueError("Unsafe employee publication staging directory")
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="publish-", dir=root) as temporary:
        staging = Path(temporary)
        prepared = await _finish_file_operation(prepare_resources, paths, name, publisher, definition, staging=staging)
        config = await asyncio.to_thread(authorize_prepared, paths, prepared, publisher=publisher, grant_ids=grant_ids)
        proposed = SimpleNamespace(config=config, directory=staging, publication_id=prepared.publication_id, owner=publisher)
        await asyncio.wait_for(EmployeeKnowledgeBroker(paths).authorize(proposed, publisher, verify_owners=True), timeout=30)
        return await _finish_file_operation(
            TeamPublicationStore(paths).publish,
            name,
            owner=publisher,
            config=config,
            soul=definition.soul,
            avatar=avatar,
            expected_revision=expected_revision,
            skill_bundle=staging,
        )
