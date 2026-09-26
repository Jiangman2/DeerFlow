"""Invocation discovery exposes names, never a publisher's Skill source."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
import test_agent_skill_publication as fixtures
from fastapi import FastAPI
from langgraph.store.memory import InMemoryStore

from app.gateway.routers import employee_resources
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.persistence.agents.runtime import EmployeePublicationBindings
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.skills.agent_publication import stage_agent_skills

stores = fixtures.stores


@pytest.fixture
def catalog(stores, tmp_path, monkeypatch):
    extensions = tmp_path / "extensions.json"
    extensions.write_text("{}")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions))
    monkeypatch.setattr("deerflow.config.extensions_config._extensions_config", None)
    paths, config, owner, viewer = stores
    monkeypatch.setattr(employee_resources, "get_paths", lambda: paths)
    monkeypatch.setattr(employee_resources, "_require_enabled", lambda: None)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    fixtures.skill(owner.get_user_custom_root(), "analysis", "PRIVATE SOURCE")
    fixtures.skill(viewer.get_user_custom_root(), "viewer-only", "PRIVATE VIEWER")
    staging = tmp_path / "staging"
    digest = stage_agent_skills(staging, ["analysis"], user_id="alice", app_config=config)
    publications = TeamPublicationStore(paths)
    first = publications.publish("reports", owner="alice", config={"skills": ["analysis"], "skills_revision": digest}, soul="PRIVATE SOUL", avatar=None, expected_revision=None, skill_bundle=staging)
    EmployeePublicationBindings(paths).admit("thread", user_id="bob", name="reports", source="team")
    return paths, publications, first


@pytest.mark.asyncio
async def test_invocation_names_are_pinned_owner_scoped_and_read_only(catalog, monkeypatch):
    paths, publications, first = catalog
    app = FastAPI()
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    await app.state.thread_store.create("thread", user_id="bob")
    user = SimpleNamespace(id="bob", is_disabled=False)
    app.dependency_overrides[employee_resources.get_current_user_from_request] = lambda: user
    app.include_router(employee_resources.router)
    before = sorted(str(p.relative_to(paths.base_dir)) for p in paths.base_dir.rglob("*") if p.is_file())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = "/api/agents/reports/invocation-skills?source=team"
        new = await client.get(url)
        assert new.status_code == 200, new.text
        assert [s["name"] for s in new.json()["skills"]] == ["analysis"]
        assert set(new.json()["skills"][0]) == {"name", "description", "enabled"}
        assert new.json()["revision"] == first.revision
        assert new.headers["cache-control"] == "private, no-store"
        assert "PRIVATE" not in new.text and "alice" not in new.text
        assert sorted(str(p.relative_to(paths.base_dir)) for p in paths.base_dir.rglob("*") if p.is_file()) == before
        # A new head may change dependencies; the old conversation retains its
        # own catalog, never the latest employee or viewer's installed Skills.
        current = publications.publish("reports", owner="alice", config={"skills": []}, soul="NEW", avatar=None, expected_revision=first.revision)
        old = await client.get(url + "&thread_id=thread")
        assert old.status_code == 200, old.text
        assert old.json() == new.json()
        assert (await client.get(url + "&thread_id=missing")).status_code == 404
        assert (await client.get("/api/agents/reports/invocation-skills?source=invalid")).status_code == 422
        assert (await client.get("/api/agents/other/invocation-skills?source=team&thread_id=thread")).status_code == 409
        assert (await client.get("/api/agents/reports/invocation-skills?source=personal&thread_id=thread")).status_code == 409
        user.id = "stranger"
        assert (await client.get(url + "&thread_id=thread")).status_code == 404
        user.id = "bob"
        publications.unpublish("reports", owner="alice", expected_revision=current.revision)
        assert (await client.get(url + "&thread_id=thread")).status_code == 404
        assert (await client.get(url)).status_code == 404
        user.is_disabled = True
        assert (await client.get(url)).status_code == 401


@pytest.mark.asyncio
async def test_catalog_rejects_corrupted_bundle_and_does_not_create_thread(catalog):
    paths, _, first = catalog
    app = FastAPI()
    app.dependency_overrides[employee_resources.get_current_user_from_request] = lambda: SimpleNamespace(id="bob", is_disabled=False)
    app.include_router(employee_resources.router)
    (first.directory / "skills/analysis/SKILL.md").write_text("tampered")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/api/agents/reports/invocation-skills?source=team")
        assert result.status_code == 422
        assert "tampered" not in result.text
    assert not (paths.thread_dir("new", user_id="bob") / "employee-publication.json").exists()


def test_same_name_personal_and_square_catalogs_stay_separate(catalog, stores, monkeypatch):
    from deerflow.persistence.agents.file import FileAgentStore

    paths, _, _ = catalog
    _, _, _, viewer = stores
    monkeypatch.setattr("deerflow.persistence.agents.get_agent_store", lambda: FileAgentStore())
    monkeypatch.setattr("deerflow.skills.storage.get_or_new_user_skill_storage", lambda uid: viewer)
    directory = paths.user_agent_dir("bob", "reports")
    directory.mkdir(parents=True)
    config = directory / "config.yaml"
    config.write_text("name: reports\nskills: [viewer-only]\n")
    assert [s["name"] for s in employee_resources._invocation_catalog("reports", "bob", "personal", None)["skills"]] == ["viewer-only"]
    assert [s["name"] for s in employee_resources._invocation_catalog("reports", "bob", "team", None)["skills"]] == ["analysis"]
    config.write_text("name: reports\nskills: []\n")
    assert employee_resources._invocation_catalog("reports", "bob", "personal", None)["skills"] == []


def test_resource_revocation_blocks_pinned_invocation_discovery(catalog):
    from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, GrantResource

    paths, publications, first = catalog
    manifest = json.loads((first.directory / "skills.manifest.json").read_text())
    digest = hashlib.sha256(json.dumps(manifest["skills"]["analysis"], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    grants = EmployeeGrantStore(paths)
    pending = grants.request(publication_id=first.publication_id, publisher="alice", requested_by="alice", resource=GrantResource(kind="skill", skill_name="analysis", content_digest=digest))
    approved = grants.decide(pending.id, actor="skill-reviewer", expected_revision=pending.revision, approve=True, expires_at=datetime.now(UTC) + timedelta(days=1))
    config = {**first.config, "resource_policy_version": 1, "skill_grants": [approved.id], "knowledge_scope": {"mode": "disabled"}, "knowledge_grants": []}
    publications.publish("reports", owner="alice", expected_revision=first.revision, config=config, soul="", avatar=None, skill_bundle=first.directory)
    EmployeePublicationBindings(paths).admit("granted-thread", user_id="bob", name="reports", source="team")
    assert employee_resources._invocation_catalog("reports", "bob", "team", "granted-thread")["skills"][0]["name"] == "analysis"
    grants.revoke(approved.id, actor="skill-reviewer", expected_revision=approved.revision)
    with pytest.raises(PermissionError):
        employee_resources._invocation_catalog("reports", "bob", "team", "granted-thread")
