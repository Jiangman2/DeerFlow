"""Real HTTP routes, multi-actor identity, immutable Skills and live revoke.

Only identity provisioning/WeKnora transport are local fixtures. The API,
stores, publication checks, grants, snapshots and projection are real code.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request

from app.gateway import deps, employee_knowledge
from app.gateway.routers import agents, employee_resources
from deerflow.config.agents_api_config import load_agents_api_config_from_dict
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.paths import Paths
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.persistence.agents.file import FileAgentStore
from deerflow.persistence.agents.runtime import EmployeePublicationBindings
from deerflow.runtime.user_context import reset_current_user, set_current_user
from deerflow.skills.agent_publication import get_thread_published_skill_storage
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage


@pytest.fixture
def application(tmp_path, monkeypatch):
    extensions = tmp_path / "extensions_config.json"
    extensions.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions))
    paths = Paths(base_dir=tmp_path / "state")
    config = AppConfig(models=[], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), skills={"path": str(tmp_path / "public-skills")})
    set_app_config(config)
    load_agents_api_config_from_dict({"enabled": True})
    for module in ("deerflow.config.paths", "deerflow.config.agents_config", "deerflow.skills.agent_publication", "app.gateway.routers.agents", "app.gateway.routers.employee_resources"):
        monkeypatch.setattr(module + ".get_paths", lambda: paths)
    store = FileAgentStore()
    monkeypatch.setattr(agents, "get_agent_store", lambda: store)
    monkeypatch.setattr(employee_resources, "get_agent_store", lambda: store)
    people = {name: SimpleNamespace(id=name, system_role="admin" if name == "reviewer" else "user", is_disabled=False) for name in ("alice", "bob", "reviewer", "stranger")}
    storages = {name: UserScopedSkillStorage(name, app_config=config) for name in people}
    for module in ("deerflow.skills.agent_publication", "deerflow.skills.agent_selection"):
        monkeypatch.setattr(module + ".get_or_new_user_skill_storage", lambda uid, **kw: storages[uid])
    package = storages["alice"].get_user_custom_root() / "analysis"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text("---\nname: analysis\ndescription: Test report helper\n---\nALICE_APPROVED_CONTENT", encoding="utf-8")
    (package / "template.txt").write_text("IMMUTABLE_TEMPLATE", encoding="utf-8")
    store.create("reporter", {"skills": ["analysis"], "description": "Report helper", "knowledge_scope": {"mode": "disabled"}, "private_future_field": "KEEP"}, "PRIVATE_DRAFT_SOUL", user_id="alice")
    real_broker = employee_knowledge.EmployeeKnowledgeBroker

    async def actor(uid):
        return people.get(uid)

    monkeypatch.setattr(employee_knowledge, "EmployeeKnowledgeBroker", lambda p: real_broker(p, actor_lookup=actor))
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        person = people.get(request.headers.get("x-test-actor"))
        request.state.user = person
        token = set_current_user(person)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)

    async def current(request: Request):
        return request.state.user

    app.dependency_overrides[deps.get_current_user_from_request] = current
    app.include_router(agents.router)
    app.include_router(employee_resources.router)
    yield app, paths, store, storages, package
    reset_app_config()
    load_agents_api_config_from_dict({})


@pytest.mark.asyncio
async def test_http_grant_publish_cross_user_projection_and_revoke(application):
    app, paths, store, storages, package = application
    draft = store.read_definition("reporter", user_id="alice")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:

        async def post(path, actor, body):
            return await client.post("/api/" + path, headers={"x-test-actor": actor}, json=body)

        review = await post("agents/reporter/publication-check", "alice", {"expected_draft_revision": draft.revision})
        assert review.status_code == 200, review.text
        resource = review.json()["resources"][0]["resource"]
        denied = await post("agents/reporter/publish", "alice", {"expected_draft_revision": draft.revision})
        assert denied.status_code == 403
        request = await post("agents/reporter/resource-grants", "alice", {"expected_draft_revision": draft.revision, "resource": resource})
        assert request.status_code == 200, request.text
        grant = request.json()
        decision = {"expected_revision": grant["revision"], "approve": True, "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat(), "acknowledge_onward_sharing": True}
        assert (await post(f"employee-resource-grants/{grant['id']}/decision", "alice", decision)).status_code == 403
        approved = await post(f"employee-resource-grants/{grant['id']}/decision", "reviewer", decision)
        assert approved.status_code == 200, approved.text
        published = await post("agents/reporter/publish", "alice", {"expected_draft_revision": draft.revision, "grant_ids": [grant["id"]]})
        assert published.status_code == 200, published.text
        assert (await client.get(f"/api/employee-resource-grants/{grant['id']}", headers={"x-test-actor": "stranger"})).status_code == 403
        EmployeePublicationBindings(paths).admit("bob-thread", user_id="bob", name="reporter", source="team")
        projection = get_thread_published_skill_storage("bob-thread", user_id="bob", app_config=storages["bob"]._app_config)
        selected = projection.load_skills(enabled_only=True)
        assert [s.name for s in selected] == ["analysis"]
        assert "ALICE_APPROVED_CONTENT" in selected[0].skill_file.read_text()
        assert (selected[0].skill_dir / "template.txt").read_text() == "IMMUTABLE_TEMPLATE"
        assert storages["bob"].load_skills() == []
        with pytest.raises(PermissionError):
            projection.read_custom_skill("analysis")
        # Later creator changes cannot mutate the runtime's frozen package.
        (package / "template.txt").write_text("CHANGED", encoding="utf-8")
        assert (projection.load_skills()[0].skill_dir / "template.txt").read_text() == "IMMUTABLE_TEMPLATE"
        revoked = await post(f"employee-resource-grants/{grant['id']}/revoke", "reviewer", {"expected_revision": approved.json()["revision"]})
        assert revoked.status_code == 200
        with pytest.raises(PermissionError):
            projection.load_skills()
        assert store.read_definition("reporter", user_id="alice") == draft
