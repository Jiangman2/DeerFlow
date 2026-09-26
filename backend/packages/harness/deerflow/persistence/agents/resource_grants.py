"""Server-owned invocation grants, never bearer credentials or browse rights.

Gateway must verify the approver's *live* resource ownership before decide().
The store enforces exact scope, bounded expiry, CAS and terminal revocation;
it deliberately cannot infer authority from the publisher's read permission.
Records are outside all user-data/Sandbox mounts. There is no delete API.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import _publication_lock, _read_file, _sync_dir

Identifier = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")]
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]
Identity = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{32}$")]


class GrantResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["skill", "knowledge"]
    skill_name: Identifier | None = None
    content_digest: Digest | None = None
    tenant_id: Identifier | None = None
    kb_id: Identifier | None = None
    document_id: Identifier | None = None

    @model_validator(mode="after")
    def exact_scope(self):
        if self.kind == "skill":
            if not self.skill_name or not self.content_digest or any((self.tenant_id, self.kb_id, self.document_id)):
                raise ValueError("Skill authorization requires exactly one immutable package")
        elif not self.tenant_id or not self.kb_id or self.skill_name or self.content_digest:
            raise ValueError("Knowledge authorization requires an exact tenant and knowledge base")
        return self


class EmployeeGrant(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    id: Identity
    revision: Identity
    publication_id: Identity
    publisher: Identifier
    resource: GrantResource
    requested_by: Identifier
    approver: Identifier | None = None
    state: Literal["pending", "approved", "rejected", "revoked"] = "pending"
    purpose: Literal["employee_invocation_only"] = "employee_invocation_only"
    audience: Literal["authenticated_square_users"] = "authenticated_square_users"
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    previous_revision: Identity | None = None

    @model_validator(mode="after")
    def consistent_state(self):
        for moment in (self.created_at, self.updated_at, self.expires_at):
            if moment is not None and moment.tzinfo is None:
                raise ValueError("Authorization timestamps must be timezone-aware")
        if self.state == "approved" and (not self.approver or not self.expires_at):
            raise ValueError("Approved authorization is incomplete")
        return self

    def is_active(self) -> bool:
        return self.state == "approved" and self.expires_at is not None and self.expires_at > datetime.now(UTC)


class EmployeeGrantStore:
    MAX_PUBLICATION_RECORDS = 1000
    MAX_INVENTORY_RECORDS = 20000

    def __init__(self, paths: Paths):
        # The team volume is shared across Gateway processes, but never exposed
        # by artifact routes or a Skill mount. Dot-directory is not an Agent.
        self.directory = paths.team_agents_dir / ".resource-grants"

    def _path(self, grant_id: str) -> Path:
        if not isinstance(grant_id, str) or not re.fullmatch(r"[a-f0-9]{32}", grant_id):
            raise ValueError("Invalid authorization id")
        if self.directory.is_symlink() or getattr(self.directory, "is_junction", lambda: False)():
            raise ValueError("Unsafe authorization directory")
        return self.directory / (grant_id + ".json")

    def get(self, grant_id: str) -> EmployeeGrant:
        item = EmployeeGrant.model_validate_json(_read_file(self._path(grant_id)))
        if item.id != grant_id:
            raise ValueError("Authorization identity mismatch")
        return item

    def _commit(self, record: EmployeeGrant) -> EmployeeGrant:
        destination = self._path(record.id)
        self.directory.mkdir(parents=True, exist_ok=True)
        # A revision audit contains only scope and identity, never query text,
        # retrieved content, access tokens or the approver's credentials.
        audit = self.directory / ".history" / record.id
        audit.mkdir(parents=True, exist_ok=True)
        content = record.model_dump_json().encode("utf-8")
        with (audit / (record.revision + ".json")).open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_dir(audit)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".grant-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            _sync_dir(self.directory)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return record

    def request(self, *, publication_id: str, publisher: str, resource: GrantResource, requested_by: str) -> EmployeeGrant:
        if publisher != requested_by:
            raise PermissionError("Only the publisher may request resource authorization")
        now = datetime.now(UTC)
        record = EmployeeGrant(id=uuid.uuid4().hex, revision=uuid.uuid4().hex, publication_id=publication_id, publisher=publisher, resource=resource, requested_by=requested_by, created_at=now, updated_at=now)
        with _publication_lock(self.directory / ".grants.lock"):
            existing = self.list_for(publisher, publication_id=publication_id, limit=self.MAX_PUBLICATION_RECORDS)
            for item in existing:
                if item.resource == resource and (item.state == "pending" or item.is_active()):
                    return item
            if len(existing) >= self.MAX_PUBLICATION_RECORDS:
                raise ValueError("Employee authorization history limit reached; administrator review required")
            return self._commit(record)

    def decide(self, grant_id: str, *, actor: str, expected_revision: str, approve: bool, expires_at: datetime) -> EmployeeGrant:
        """Call only after Gateway freshly verifies resource-owner authority."""
        path = self._path(grant_id)
        now = datetime.now(UTC)
        if expires_at.tzinfo is None or not now < expires_at <= now + timedelta(days=366):
            raise ValueError("Authorization expiry must be within one year")
        with _publication_lock(path.with_suffix(".lock")):
            current = self.get(grant_id)
            if current.revision != expected_revision:
                raise ValueError("Authorization changed; reload before deciding")
            if current.state != "pending":
                raise ValueError("Authorization decision is terminal; request a new grant")
            updated = {**current.model_dump(), "revision": uuid.uuid4().hex, "previous_revision": current.revision, "state": "approved" if approve else "rejected", "approver": actor, "expires_at": expires_at, "updated_at": now}
            return self._commit(EmployeeGrant.model_validate(updated))

    def revoke(self, grant_id: str, *, actor: str, expected_revision: str) -> EmployeeGrant:
        path = self._path(grant_id)
        with _publication_lock(path.with_suffix(".lock")):
            current = self.get(grant_id)
            if actor not in (current.publisher, current.approver):
                raise PermissionError("Only an authorization participant may revoke it")
            if current.revision != expected_revision:
                raise ValueError("Authorization changed; reload before revoking")
            if current.state == "revoked":
                return current
            return self._commit(EmployeeGrant.model_validate({**current.model_dump(), "revision": uuid.uuid4().hex, "previous_revision": current.revision, "state": "revoked", "updated_at": datetime.now(UTC)}))

    def require(self, grant_id: str, *, publication_id: str, publisher: str, resource: GrantResource) -> EmployeeGrant:
        record = self.get(grant_id)
        if not record.is_active() or record.publication_id != publication_id or record.publisher != publisher or record.resource != resource:
            raise PermissionError("Employee resource authorization is unavailable or no longer valid")
        return record

    def list_for(self, actor: str, *, limit: int = 100, publication_id: str | None = None) -> list[EmployeeGrant]:
        if not 1 <= limit <= (self.MAX_PUBLICATION_RECORDS if publication_id else 200):
            raise ValueError("Authorization list limit exceeded")
        if not self.directory.exists():
            return []
        result = []
        for index, path in enumerate(self.directory.glob("*.json")):
            if index >= self.MAX_INVENTORY_RECORDS:
                raise ValueError("Authorization inventory requires administrator archival; no records were deleted")
            record = self.get(path.stem)
            if actor in (record.publisher, record.approver) and (publication_id is None or record.publication_id == publication_id):
                result.append(record)
        # Filter first: unrelated employees must never hide a valid approval.
        # Current approvals/pending requests precede terminal audit history.
        result.sort(key=lambda r: (r.is_active(), r.state == "pending", r.updated_at, r.id), reverse=True)
        return result[:limit]


def knowledge_resource(item) -> GrantResource:
    return GrantResource(kind="knowledge", tenant_id=item.tenant_id, kb_id=item.id if item.type == "kb" else item.kb_id, document_id=item.id if item.type == "file" else None)


def _require_exact_set(store, publication, expected, identifiers):
    if not isinstance(identifiers, list) or len(identifiers) > 148 or any(not isinstance(x, str) for x in identifiers) or len(set(identifiers)) != len(identifiers):
        raise PermissionError("Invalid employee resource authorization list")
    grants = []
    for identifier in identifiers:
        grant = store.get(identifier)
        if grant.resource not in expected:
            raise PermissionError("Authorization is outside the published employee scope")
        store.require(identifier, publication_id=publication.publication_id, publisher=publication.owner, resource=grant.resource)
        grants.append(grant)
    if len(grants) != len(expected) or {g.resource for g in grants} != set(expected):
        raise PermissionError("Employee resource scope is missing an owner approval")
    return grants


def require_publication_grants(store: EmployeeGrantStore, publication):
    """Live status checks for every entry point, including pinned old revisions."""
    from deerflow.config.agent_knowledge_config import AgentKnowledgeScope

    config = publication.config
    if "resource_policy_version" not in config:
        return []  # Historical definitions are retained, not silently re-granted.
    if config["resource_policy_version"] != 1:
        raise PermissionError("Unsupported employee resource authorization policy")
    names = config.get("skills")
    if not isinstance(names, list) or len(names) > 128 or any(not isinstance(x, str) for x in names) or len(set(names)) != len(names):
        raise PermissionError("Published employee requires an explicit Skill set")
    expected_skills = set()
    if names:
        manifest = json.loads(_read_file(publication.directory / "skills.manifest.json"))
        canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        if not isinstance(manifest, dict) or hashlib.sha256(canonical).hexdigest() != config.get("skills_revision") or set(manifest.get("skills", {})) != set(names):
            raise PermissionError("Employee Skill dependency manifest changed")
        for name, entries in manifest["skills"].items():
            digest = hashlib.sha256(json.dumps(entries, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            expected_skills.add(GrantResource(kind="skill", skill_name=name, content_digest=digest))
    grants = _require_exact_set(store, publication, expected_skills, config.get("skill_grants"))
    scope = AgentKnowledgeScope.model_validate(config.get("knowledge_scope") or {})
    expected_knowledge = {knowledge_resource(item) for item in scope.items}
    return grants + _require_exact_set(store, publication, expected_knowledge, config.get("knowledge_grants"))
