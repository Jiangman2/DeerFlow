"""Exercise real start_run/scheduler admission with immutable employee storage.

The external owner lookup and agent execution are doubles. Authorization,
checkpoint validation, config normalization and thread ownership remain real.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from app.gateway import services
from app.gateway.auth_middleware import AUTH_SOURCE_INTERNAL
from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, get_internal_user
from app.gateway.routers.thread_runs import RunCreateRequest
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.persistence.agents.runtime import EmployeePublicationBindings
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus
from deerflow.runtime.user_context import reset_current_user, set_current_user


@pytest_asyncio.fixture
async def run_entry(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr("app.gateway.agent_publications.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    authorize = AsyncMock()
    monkeypatch.setattr("app.gateway.employee_knowledge.EmployeeKnowledgeBroker", lambda _: SimpleNamespace(authorize=authorize))
    publications = TeamPublicationStore(paths)
    config = {"skills": [], "resource_policy_version": 1, "skill_grants": [], "knowledge_grants": []}
    first = publications.publish("reports", owner="alice", config=config, soul="old", avatar=None, expected_revision=None)
    bindings = EmployeePublicationBindings(paths)
    bindings.admit("employee-thread", user_id="bob", name="reports", source="team")
    latest = publications.publish("reports", owner="alice", config=config, soul="new", avatar=None, expected_revision=first.revision)
    thread_store = MemoryThreadMetaStore(InMemoryStore())
    await thread_store.create("employee-thread", user_id="bob", assistant_id="lead_agent")
    checkpointer = InMemorySaver()
    checkpoint = empty_checkpoint()
    checkpoint["id"] = "checkpoint-one"
    await checkpointer.aput({"configurable": {"thread_id": "employee-thread", "checkpoint_ns": ""}}, checkpoint, {"step": 1}, {})
    record = RunRecord(run_id="test-run", thread_id="employee-thread", assistant_id="lead_agent", status=RunStatus.pending, on_disconnect=DisconnectMode.continue_)
    manager = SimpleNamespace(create_or_reject=AsyncMock(return_value=record))
    app = SimpleNamespace(state=SimpleNamespace(stream_bridge=object(), run_manager=manager, thread_store=thread_store, checkpointer=checkpointer))
    monkeypatch.setattr(services, "get_run_context", lambda _: SimpleNamespace(thread_store=thread_store))
    monkeypatch.setattr(services, "resolve_agent_factory", lambda _: object())
    execute = AsyncMock()
    monkeypatch.setattr(services, "run_agent", execute)
    owner = SimpleNamespace(id="bob", system_role="user", is_disabled=False)
    lookup = AsyncMock(return_value=owner)
    monkeypatch.setattr(services, "get_local_provider", lambda: SimpleNamespace(get_user=lookup))
    set_app_config(AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}))
    token = set_current_user(owner)
    try:
        yield SimpleNamespace(paths=paths, publications=publications, bindings=bindings, first=first, latest=latest, owner=owner, app=app, manager=manager, execute=execute, authorize=authorize, lookup=lookup)
    finally:
        reset_current_user(token)
        reset_app_config()


async def invoke(ctx, mode):
    if mode == "scheduled":
        result = await services.launch_scheduled_thread_run(app=ctx.app, owner_user_id="bob", thread_id="employee-thread", assistant_id="lead_agent", prompt="continue")
        record = ctx.manager.create_or_reject.return_value
        await record.task
        assert result["run_id"] == record.run_id
        return record
    internal = mode == "channel"
    request = SimpleNamespace(
        app=ctx.app, headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: "bob"} if internal else {}, state=SimpleNamespace(user=get_internal_user() if internal else ctx.owner, auth_source=AUTH_SOURCE_INTERNAL if internal else "session")
    )
    body = RunCreateRequest(assistant_id="lead_agent", input={"messages": [{"role": "user", "content": "continue"}]})
    if mode == "resume":
        body.command = {"resume": "confirmed"}
    elif mode == "retry":
        body.checkpoint_id = "checkpoint-one"
    elif mode == "explicit":
        body.context = {"agent_name": "reports", "agent_source": "team", "agent_publication_revision": "browser-forgery"}
    record = await services.start_run(body, "employee-thread", request)
    await record.task
    return record


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explicit", "continue", "resume", "retry", "channel", "scheduled"])
async def test_all_run_entries_keep_original_publication_and_check_authority(run_entry, mode):
    ctx = run_entry
    await invoke(ctx, mode)
    ctx.authorize.assert_awaited_once()
    assert ctx.authorize.call_args.args[0].revision == ctx.first.revision
    assert ctx.authorize.call_args.args[1] == "bob"
    config = ctx.execute.call_args.kwargs["config"]
    for section in ("context", "configurable"):
        assert config[section]["agent_name"] == "reports"
        assert config[section]["agent_publication_revision"] == ctx.first.revision
    assert config["context"]["user_id"] == "bob"
    if mode == "resume":
        assert isinstance(ctx.execute.call_args.kwargs["graph_input"], Command)
    if mode == "retry":
        assert config["configurable"]["checkpoint_id"] == "checkpoint-one"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explicit", "continue", "resume", "retry", "channel", "scheduled"])
@pytest.mark.parametrize("failure", ["withdrawn", "revoked"])
async def test_denied_run_entries_never_create_pending_work(run_entry, mode, failure):
    ctx = run_entry
    if failure == "withdrawn":
        ctx.publications.unpublish("reports", owner="alice", expected_revision=ctx.latest.revision)
    else:
        ctx.authorize.side_effect = PermissionError("owner revoked")
    with pytest.raises(HTTPException) as error:
        await invoke(ctx, mode)
    assert error.value.status_code in (403, 409)
    ctx.manager.create_or_reject.assert_not_awaited()
    ctx.execute.assert_not_awaited()
    assert (await ctx.app.state.thread_store.get("employee-thread", user_id="bob"))["status"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["channel", "scheduled"])
@pytest.mark.parametrize("failure", ["missing", "disabled", "wrong-owner"])
async def test_internal_employee_run_cannot_fall_back_to_system_identity(run_entry, mode, failure):
    ctx = run_entry
    ctx.lookup.return_value = None if failure == "missing" else SimpleNamespace(id="other" if failure == "wrong-owner" else "bob", is_disabled=failure == "disabled", system_role="user")
    with pytest.raises(HTTPException) as error:
        await invoke(ctx, mode)
    assert error.value.status_code in (403, 503)
    ctx.manager.create_or_reject.assert_not_awaited()
    ctx.execute.assert_not_awaited()
