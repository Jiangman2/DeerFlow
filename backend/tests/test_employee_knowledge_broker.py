"""A Square viewer can ask, but never acquires the resource owner's token."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.gateway.employee_knowledge import EmployeeKnowledgeBroker
from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, GrantResource
from deerflow.persistence.agents.runtime import EmployeePublicationBindings


@pytest.fixture
def broker(tmp_path):
    paths = Paths(base_dir=tmp_path)
    grants = EmployeeGrantStore(paths)
    publications = TeamPublicationStore(paths)
    identity = publications.reserve_identity("employee", owner="alice")
    resource = GrantResource(kind="knowledge", tenant_id="tenant", kb_id="kb", document_id="doc")
    pending = grants.request(publication_id=identity, publisher="alice", resource=resource, requested_by="alice")
    active = grants.decide(pending.id, actor="owner", expected_revision=pending.revision, approve=True, expires_at=datetime.now(UTC) + timedelta(days=1))
    snapshot = publications.publish(
        "employee",
        owner="alice",
        config={"skills": [], "knowledge_scope": {"mode": "selected", "items": [{"type": "file", "id": "doc", "kb_id": "kb", "tenant_id": "tenant"}]}, "knowledge_grants": [active.id]},
        soul="duty",
        avatar=None,
        expected_revision=None,
    )
    EmployeePublicationBindings(paths).admit("thread", user_id="bob", name="employee", source="team")
    calls = []

    async def actor_lookup(uid):
        return SimpleNamespace(id=uid, is_disabled=False)

    async def session_lookup(actor):
        return SimpleNamespace(token="owner-private-token", user={"id": "wk-owner"})

    async def request(method, path, token, **kwargs):
        calls.append((method, path, token, kwargs))
        assert token == "owner-private-token"
        if path == "/auth/me":
            return {"data": {"user": {"id": "wk-owner"}, "memberships": [{"tenant_id": "tenant"}]}}
        if path == "/knowledge-bases/kb":
            return {"data": {"id": "kb", "creator_id": "wk-owner"}}
        if path == "/knowledge/doc":
            return {"data": {"id": "doc", "knowledge_base_id": "kb"}}
        assert path == "/knowledge-search"
        assert kwargs["json"] == {"query": "question", "knowledge_ids": ["doc"]}
        return {
            "data": [{"id": "chunk", "knowledge_base_id": "kb", "knowledge_id": "doc", "content": "answer [source](https://private/download?token=secret)", "knowledge_title": "approved document", "download_url": "https://private/secret"}]
        }

    instance = EmployeeKnowledgeBroker(paths, actor_lookup=actor_lookup, session_lookup=session_lookup, request=request)
    return instance, calls, grants, active, publications, snapshot


@pytest.mark.asyncio
async def test_search_only_exact_document_with_owner_session_confined_to_broker(broker):
    instance, calls, _, _, _, _ = broker
    result = await instance.search(thread_id="thread", viewer_id="bob", query="question")
    assert result["results"][0]["content"].startswith("answer")
    text = str(result)
    for forbidden in ("owner-private-token", "download_url", "https://", "token=secret"):
        assert forbidden not in text
    assert len([c for c in calls if c[1] == "/knowledge-search"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [{"data": None}, {"data": {"user": None}}, {"data": {"user": []}}])
async def test_malformed_identity_denies_without_search(broker, identity):
    instance, calls, _, _, _, _ = broker
    original = instance.request

    async def malformed(method, path, token, **kwargs):
        if path == "/auth/me":
            return identity
        return await original(method, path, token, **kwargs)

    instance.request = malformed
    with pytest.raises(PermissionError):
        await instance.search(thread_id="thread", viewer_id="bob", query="question")
    assert not any(c[1] == "/knowledge-search" for c in calls)


@pytest.mark.asyncio
async def test_revoked_grant_stops_before_any_owner_session_use(broker):
    instance, calls, grants, active, _, _ = broker
    grants.revoke(active.id, actor="owner", expected_revision=active.revision)
    with pytest.raises(PermissionError):
        await instance.search(thread_id="thread", viewer_id="bob", query="question")
    assert calls == []


@pytest.mark.asyncio
async def test_other_viewer_cannot_supply_someone_elses_thread_receipt(broker):
    instance, calls, _, _, _, _ = broker
    with pytest.raises(PermissionError):
        await instance.search(thread_id="thread", viewer_id="stranger", query="question")
    assert calls == []


@pytest.mark.asyncio
async def test_owner_permission_change_or_disabled_account_blocks_retrieval(broker):
    instance, calls, _, _, _, _ = broker
    original = instance.request

    async def no_owner(method, path, token, **kwargs):
        if path == "/knowledge-bases/kb":
            return {"data": {"id": "kb", "creator_id": "someone-else"}}
        return await original(method, path, token, **kwargs)

    instance.request = no_owner
    with pytest.raises(PermissionError):
        await instance.search(thread_id="thread", viewer_id="bob", query="question")
    assert not any(c[1] == "/knowledge-search" for c in calls)


@pytest.mark.asyncio
async def test_off_scope_hits_are_never_returned_and_no_raw_shape_fallback(broker):
    instance, _, _, _, _, _ = broker
    original = instance.request

    async def off_scope(method, path, token, **kwargs):
        if path == "/knowledge-search":
            return {"data": [{"knowledge_base_id": "kb", "knowledge_id": "different", "content": "FORBIDDEN"}, {"content": "UNKNOWN"}]}
        return await original(method, path, token, **kwargs)

    instance.request = off_scope
    result = await instance.search(thread_id="thread", viewer_id="bob", query="question")
    assert result["results"] == []


@pytest.mark.asyncio
async def test_revoke_while_upstream_search_is_running_discards_result(broker):
    instance, _, grants, active, _, _ = broker
    original = instance.request

    async def race(method, path, token, **kwargs):
        response = await original(method, path, token, **kwargs)
        if path == "/knowledge-search":
            grants.revoke(active.id, actor="owner", expected_revision=active.revision)
        return response

    instance.request = race
    with pytest.raises(PermissionError):
        await instance.search(thread_id="thread", viewer_id="bob", query="question")


@pytest.mark.asyncio
async def test_withdrawn_employee_and_oversized_query_are_blocked(broker):
    instance, calls, _, _, publications, snapshot = broker
    with pytest.raises(ValueError):
        await instance.search(thread_id="thread", viewer_id="bob", query="x" * 2001)
    publications.unpublish("employee", owner="alice", expected_revision=snapshot.revision)
    with pytest.raises((PermissionError, FileNotFoundError)):
        await instance.search(thread_id="thread", viewer_id="bob", query="question")
    assert calls == []
