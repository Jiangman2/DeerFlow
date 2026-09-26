"""Version receipts use the authorized thread owner, never a viewer bucket.

Authentication is a local fixture; permission/ownership decorators and the
publication store are real. This is not a production SSO acceptance test.
"""

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from langgraph.store.memory import InMemoryStore

from app.gateway.authz import AuthContext
from app.gateway.routers import employee_resources
from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.persistence.agents.runtime import EmployeePublicationBindings
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore


@pytest.mark.asyncio
async def test_owner_scoped_status_admin_read_and_unrelated_denial(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(employee_resources, "get_paths", lambda: paths)
    monkeypatch.setattr(employee_resources, "_require_enabled", lambda: None)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    publications = TeamPublicationStore(paths)
    first = publications.publish("reports", owner="alice", config={"skills": [], "knowledge_scope": {"mode": "disabled"}}, soul="PRIVATE SOUL", avatar=None, expected_revision=None)
    bindings = EmployeePublicationBindings(paths)
    bindings.admit("thread", user_id="bob", name="reports", source="team")
    current = publications.publish("reports", owner="alice", config={"skills": []}, soul="NEW PRIVATE SOUL", avatar=None, expected_revision=first.revision)

    app = FastAPI()
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    await app.state.thread_store.create("thread", user_id="bob")

    @app.middleware("http")
    async def identity(request: Request, call_next):
        actor = request.headers.get("x-test-actor")
        user = SimpleNamespace(id=actor, system_role="admin" if actor == "admin" else "user") if actor else None
        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=["threads:read"])
        return await call_next(request)

    app.include_router(employee_resources.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for actor in ("bob", "admin"):
            result = await client.get("/api/threads/thread/employee-publication?user_id=alice", headers={"x-test-actor": actor})
            assert result.status_code == 200, result.text
            assert result.json() == {"bound": True, "name": "reports", "revision": first.revision, "current_revision": current.revision, "update_available": True, "knowledge_mode": "disabled"}
            assert "PRIVATE" not in result.text
        for actor in ("alice", "stranger"):
            assert (await client.get("/api/threads/thread/employee-publication?user_id=bob", headers={"x-test-actor": actor})).status_code == 404
        assert (await client.get("/api/threads/thread/employee-publication")).status_code == 401
        assert (await client.get("/api/threads/missing/employee-publication", headers={"x-test-actor": "admin"})).status_code == 404
        publications.unpublish("reports", owner="alice", expected_revision=current.revision)
        assert (await client.get("/api/threads/thread/employee-publication", headers={"x-test-actor": "bob"})).status_code == 404
    assert (paths.thread_dir("thread", user_id="bob") / "employee-publication.json").is_file()
