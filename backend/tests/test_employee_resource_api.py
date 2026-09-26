from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.routers import employee_resources
from deerflow.config.paths import Paths
from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, GrantResource


@pytest.fixture
def environment(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(employee_resources, "get_paths", lambda: paths)
    store = EmployeeGrantStore(paths)
    pending = store.request(publication_id="a" * 32, publisher="alice", requested_by="alice", resource=GrantResource(kind="knowledge", tenant_id="tenant", kb_id="kb", document_id="doc"))
    monkeypatch.setattr(employee_resources, "_require_enabled", lambda: None)
    return store, pending


@pytest.mark.asyncio
async def test_read_access_does_not_approve_knowledge_and_leaves_pending(environment, monkeypatch):
    store, pending = environment

    async def refuse(*a, **kw):
        raise PermissionError("not the owner")

    monkeypatch.setattr(employee_resources, "verify_knowledge_owner", refuse)
    with pytest.raises(HTTPException) as exc:
        await employee_resources.decide_grant(
            pending.id, employee_resources.GrantDecision(expected_revision=pending.revision, approve=True, expires_at=datetime.now(UTC) + timedelta(days=1)), user=SimpleNamespace(id="alice", system_role="user", is_disabled=False)
        )
    assert exc.value.status_code == 403
    assert store.get(pending.id).state == "pending"


@pytest.mark.asyncio
async def test_owner_approval_uses_authenticated_actor_and_inventory_does_not_leak(environment, monkeypatch):
    _, pending = environment

    async def owner(*a, **kw):
        return object()

    monkeypatch.setattr(employee_resources, "verify_knowledge_owner", owner)
    result = await employee_resources.decide_grant(
        pending.id, employee_resources.GrantDecision(expected_revision=pending.revision, approve=True, expires_at=datetime.now(UTC) + timedelta(days=1)), user=SimpleNamespace(id="owner", system_role="user", is_disabled=False)
    )
    assert result.approver == "owner" and result.state == "approved"
    assert await employee_resources.list_grants(user=SimpleNamespace(id="stranger", is_disabled=False)) == {"grants": []}


@pytest.mark.asyncio
async def test_unauthorized_revoke_cannot_change_grant(environment):
    store, pending = environment
    with pytest.raises(HTTPException) as exc:
        await employee_resources.revoke_grant(pending.id, employee_resources.GrantRevision(expected_revision=pending.revision), user=SimpleNamespace(id="stranger", is_disabled=False))
    assert exc.value.status_code == 403 and store.get(pending.id).state == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError, RuntimeError])
async def test_upstream_failure_is_bounded_retryable_and_does_not_approve(environment, monkeypatch, failure):
    store, pending = environment

    async def unavailable(*a, **kw):
        raise failure("PRIVATE token=never-echo")

    monkeypatch.setattr(employee_resources, "verify_knowledge_owner", unavailable)
    with pytest.raises(HTTPException) as exc:
        await employee_resources.decide_grant(
            pending.id, employee_resources.GrantDecision(expected_revision=pending.revision, approve=True, expires_at=datetime.now(UTC) + timedelta(days=1)), user=SimpleNamespace(id="owner", system_role="user", is_disabled=False)
        )
    assert exc.value.status_code == 503
    assert "PRIVATE" not in str(exc.value.detail)
    assert store.get(pending.id).state == "pending"
