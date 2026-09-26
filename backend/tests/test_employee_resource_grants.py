"""Delegation is exact, revocable, and separate from a saved selection."""

from datetime import UTC, datetime, timedelta

import pytest

from deerflow.config.paths import Paths
from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, GrantResource


@pytest.fixture
def store(tmp_path):
    return EmployeeGrantStore(Paths(base_dir=tmp_path))


def resource(**overrides):
    return GrantResource.model_validate({"kind": "knowledge", "tenant_id": "tenant", "kb_id": "kb", "document_id": "doc", **overrides})


def request(store):
    return store.request(publication_id="a" * 32, publisher="alice", resource=resource(), requested_by="alice")


def approve(store, pending):
    return store.decide(pending.id, actor="owner", expected_revision=pending.revision, approve=True, expires_at=datetime.now(UTC) + timedelta(days=30))


def test_pending_is_not_authority_and_exact_scope_is_required(store):
    pending = request(store)
    with pytest.raises(PermissionError):
        store.require(pending.id, publication_id="a" * 32, publisher="alice", resource=resource())
    active = approve(store, pending)
    assert store.require(active.id, publication_id="a" * 32, publisher="alice", resource=resource()).approver == "owner"
    for scope in (resource(document_id=None), resource(document_id="other"), resource(tenant_id="other")):
        with pytest.raises(PermissionError):
            store.require(active.id, publication_id="a" * 32, publisher="alice", resource=scope)
    with pytest.raises(PermissionError):
        store.require(active.id, publication_id="b" * 32, publisher="alice", resource=resource())
    with pytest.raises(PermissionError):
        store.require(active.id, publication_id="a" * 32, publisher="bob", resource=resource())


def test_revocation_is_terminal_and_old_revision_cannot_restore_it(store):
    active = approve(store, request(store))
    revoked = store.revoke(active.id, actor="owner", expected_revision=active.revision)
    assert revoked.state == "revoked" and revoked.resource == active.resource
    with pytest.raises(PermissionError):
        store.require(active.id, publication_id="a" * 32, publisher="alice", resource=resource())
    with pytest.raises(ValueError):
        store.decide(active.id, actor="owner", expected_revision=revoked.revision, approve=True, expires_at=datetime.now(UTC) + timedelta(days=2))
    with pytest.raises(PermissionError):
        store.revoke(active.id, actor="stranger", expected_revision=revoked.revision)


def test_decisions_use_compare_and_swap_and_bounded_expiry(store):
    pending = request(store)
    with pytest.raises(ValueError):
        store.decide(pending.id, actor="owner", expected_revision=pending.revision, approve=True, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    active = approve(store, pending)
    with pytest.raises(ValueError, match="changed"):
        approve(store, pending)
    expired = active.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    assert not expired.is_active()


def test_skill_grant_binds_exact_content_and_knowledge_cannot_be_wildcard(store):
    with pytest.raises(ValueError):
        resource(kb_id="../all")
    with pytest.raises(ValueError):
        resource(kind="skill", skill_name="analysis", content_digest="a" * 64)
    skill = GrantResource(kind="skill", skill_name="analysis", content_digest="b" * 64)
    pending = store.request(publication_id="a" * 32, publisher="alice", resource=skill, requested_by="alice")
    active = approve(store, pending)
    with pytest.raises(PermissionError):
        store.require(active.id, publication_id="a" * 32, publisher="alice", resource=skill.model_copy(update={"content_digest": "c" * 64}))


def test_grants_are_outside_user_data_and_malformed_records_fail_closed(store, tmp_path):
    pending = request(store)
    assert not str(store.directory).startswith(str(tmp_path / "users"))
    path = store.directory / (pending.id + ".json")
    path.write_text("{}")
    with pytest.raises(ValueError):
        store.get(pending.id)
    with pytest.raises(ValueError):
        store.get("../secret")


def test_inventory_is_participant_scoped_without_payload_paths(store):
    pending = request(store)
    assert [x.id for x in store.list_for("alice")] == [pending.id]
    assert store.list_for("stranger") == []
    active = approve(store, pending)
    assert [x.id for x in store.list_for("owner")] == [active.id]
    assert "token" not in active.model_dump() and "path" not in active.model_dump()


def test_duplicate_requests_are_idempotent_until_revoked(store):
    first = request(store)
    assert request(store).id == first.id
    approved = approve(store, first)
    assert request(store).id == first.id
    store.revoke(first.id, actor="alice", expected_revision=approved.revision)
    assert request(store).id != first.id


def test_publication_filter_happens_before_inventory_limit(store):
    request(store)
    wanted = store.request(publication_id="b" * 32, publisher="alice", requested_by="alice", resource=resource())
    assert store.list_for("alice", publication_id="b" * 32, limit=1) == [wanted]


def test_live_publication_policy_requires_all_exact_grants_and_detects_revocation(store):
    from types import SimpleNamespace
    from deerflow.persistence.agents.resource_grants import require_publication_grants

    active = approve(store, request(store))
    publication = SimpleNamespace(
        publication_id="a" * 32,
        owner="alice",
        config={
            "resource_policy_version": 1,
            "skills": [],
            "skill_grants": [],
            "knowledge_scope": {"mode": "selected", "items": [{"type": "file", "id": "doc", "kb_id": "kb", "tenant_id": "tenant"}]},
            "knowledge_grants": [active.id],
        },
    )
    require_publication_grants(store, publication)
    store.revoke(active.id, actor="owner", expected_revision=active.revision)
    with pytest.raises(PermissionError):
        require_publication_grants(store, publication)
