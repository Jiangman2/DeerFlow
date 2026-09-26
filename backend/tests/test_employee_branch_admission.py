"""Real branch HTTP handler keeps a same-owner immutable employee receipt.

Only authentication and the upstream owner lookup are fixtures; thread storage,
checkpoints, permission decorators, publication/grant storage and branching are real.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore

from app.gateway.authz import AuthContext
from app.gateway.routers import threads
from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.persistence.agents.runtime import EmployeePublicationBindings
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.user_context import reset_current_user, set_current_user


@pytest_asyncio.fixture
async def branch_app(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(threads, "get_paths", lambda: paths)
    monkeypatch.setattr("app.gateway.agent_publications.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    authorize = AsyncMock()
    monkeypatch.setattr("app.gateway.employee_knowledge.EmployeeKnowledgeBroker", lambda _: SimpleNamespace(authorize=authorize))
    publications = TeamPublicationStore(paths)
    config = {"skills": [], "resource_policy_version": 1, "skill_grants": [], "knowledge_grants": []}
    first = publications.publish("reports", owner="alice", config=config, soul="version one", avatar=None, expected_revision=None)
    bindings = EmployeePublicationBindings(paths)
    bindings.admit("parent", user_id="bob", name="reports", source="team")
    latest = publications.publish("reports", owner="alice", config=config, soul="version two", avatar=None, expected_revision=first.revision)
    app = FastAPI()
    app.state.thread_store = MemoryThreadMetaStore(InMemoryStore())
    app.state.checkpointer = InMemorySaver()
    await app.state.thread_store.create("parent", user_id="bob", assistant_id="lead-agent")
    history = []
    for index in range(1, 3):
        checkpoint = empty_checkpoint()
        checkpoint["id"] = str(index)
        history.extend([HumanMessage(id="human-" + str(index), content="question"), AIMessage(id="answer-" + str(index), content="answer")])
        checkpoint["channel_values"] = {"messages": list(history)}
        checkpoint["channel_versions"] = {"messages": index}
        await app.state.checkpointer.aput({"configurable": {"thread_id": "parent", "checkpoint_ns": ""}}, checkpoint, {"step": index, "source": "loop"}, checkpoint["channel_versions"])

    @app.middleware("http")
    async def identity(request: Request, call_next):
        name = request.headers.get("x-test-actor", "bob")
        user = SimpleNamespace(id=name, system_role="admin" if name == "admin" else "user", is_disabled=False)
        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=["threads:write", "threads:read"])
        token = set_current_user(user)
        try:
            return await call_next(request)
        finally:
            reset_current_user(token)

    app.include_router(threads.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(app=app, client=client, paths=paths, publications=publications, bindings=bindings, first=first, latest=latest, authorize=authorize)


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["answer-1", "answer-2"])
async def test_branch_preserves_pinned_employee_and_checks_live_authorization(branch_app, message):
    ctx = branch_app
    response = await ctx.client.post("/api/threads/parent/branches", json={"message_id": message})
    assert response.status_code == 200, response.text
    child = response.json()["thread_id"]
    assert ctx.bindings.read(child, user_id="bob").revision == ctx.first.revision
    record = await ctx.app.state.thread_store.get(child, user_id="bob")
    assert record["metadata"]["agent_name"] == "reports"
    assert record["metadata"]["agent_source"] == "team"
    assert ctx.bindings.admit(child, user_id="bob", name=None, source=None).revision == ctx.first.revision
    ctx.authorize.assert_awaited_once()
    assert ctx.authorize.call_args.args[0].revision == ctx.first.revision
    assert ctx.authorize.call_args.args[1] == "bob"
    assert not (ctx.paths.thread_dir(child, user_id="bob") / "agent-skills").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("actor", ["alice", "stranger", "admin"])
async def test_other_identity_cannot_copy_published_thread_into_own_bucket(branch_app, actor):
    ctx = branch_app
    response = await ctx.client.post("/api/threads/parent/branches", json={"message_id": "answer-1"}, headers={"x-test-actor": actor})
    assert response.status_code in (403, 404), response.text
    ctx.authorize.assert_not_awaited()
    assert not ctx.paths.thread_dir("unused", user_id=actor).parent.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["withdrawn", "revoked", "corrupt"])
async def test_invalid_parent_cannot_create_branch_or_use_newer_head(branch_app, failure):
    ctx = branch_app
    if failure == "withdrawn":
        ctx.publications.unpublish("reports", owner="alice", expected_revision=ctx.latest.revision)
    elif failure == "revoked":
        ctx.authorize.side_effect = PermissionError("owner revoked")
    else:
        (ctx.paths.thread_dir("parent", user_id="bob") / "employee-publication.json").write_text("broken", encoding="utf-8")
    response = await ctx.client.post("/api/threads/parent/branches", json={"message_id": "answer-1"})
    assert response.status_code in (403, 409), response.text
    records = await ctx.app.state.thread_store.search(user_id="bob")
    assert [item["thread_id"] for item in records] == ["parent"]
