"""Task fencing uses only disposable databases and synthetic provider calls."""

import asyncio
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.message_bus import InboundMessage
from app.integrations.work.auth import Grant, Principal, TokenVault
from app.integrations.work.capabilities import Action, WorkCapabilities
from app.integrations.work.client import WorkError
from app.integrations.work.store import WorkStore
from app.integrations.work.tasks import TaskRef
from deerflow.persistence.work.model import WorkRecordRow

KEY = "c3ludGhldGljLXRlc3Qta2V5LTMyLWJ5dGVzISEhISE="


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def task_stores(tmp_path, request):
    url = "sqlite+aiosqlite:///" + str(tmp_path / "tasks.db")
    if request.param == "postgres":
        url = os.getenv("WORK_TASK_TEST_POSTGRES_URL", "")
        if not url:
            pytest.skip("Dedicated isolated task PostgreSQL database not configured")
        assert "@127.0.0.1:" in url and (url.rsplit("/", 1)[-1].startswith("work_task_test") or url.rsplit("/", 1)[-1] == "work_isolated_test")
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        existing = await conn.run_sync(lambda c: sa.inspect(c).get_table_names())
        assert not existing, "Task regression requires an empty disposable database"
        await conn.run_sync(WorkRecordRow.__table__.create)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    stores = (WorkStore(sf, key=KEY, namespace="synthetic-task-tests"), WorkStore(sf, key=KEY, namespace="synthetic-task-tests"))
    try:
        yield *stores, sf
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(WorkRecordRow.__table__.drop)
        await engine.dispose()


async def begin(store, owner="synthetic-owner", source="message-1"):
    turn = await store.reserve_turn(owner, source)
    assert turn is not None
    return await store.finish_turn(owner, turn.turn_id, mode="new", task={"domain": "calendar"})


async def pending_plan(store, owner, ref, key="synthetic-plan"):
    record = await store.admit("plan", key, {"id": key}, owner=owner, ttl=300, active={"waiting", "pending"}, limit=256, owner_limit=8, task_ref=ref)
    return await store.change_task_record("plan", key, record, owner=owner, task_ref=ref, status="pending")


@pytest.mark.asyncio
async def test_reservation_dedupes_before_planning_and_latest_turn_wins(task_stores):
    one, two, _ = task_stores
    old = await one.reserve_turn("A", "message-1")
    assert await two.reserve_turn("A", "message-1") is None
    current = await two.reserve_turn("A", "message-2")
    with pytest.raises(WorkError, match="task_superseded"):
        await one.finish_turn("A", old.turn_id, mode="new")
    ref = await two.finish_turn("A", current.turn_id, mode="new")
    await one.assert_task("A", ref)
    with pytest.raises(WorkError, match="task_superseded"):
        await two.finish_turn("A", current.turn_id, mode="new")
    with pytest.raises(WorkError, match="task_superseded"):
        await one.assert_task("B", ref)


@pytest.mark.asyncio
async def test_continue_rebinds_context_but_not_old_authority(task_stores):
    one, two, _ = task_stores
    ref = await begin(one)
    await one.save_task_context("synthetic-owner", ref, {"dialogue": [{"question": "何时？"}], "calendar": {"candidates": [{"event_id": "synthetic-event"}]}})
    before = await one.read("conversation", "synthetic-owner", owner="synthetic-owner")
    # Row revisions change for cache writes; the business generation does not.
    await one.assert_task("synthetic-owner", ref)
    turn = await two.reserve_turn("synthetic-owner", "followup")
    assert turn.context["dialogue"] == [{"question": "何时？"}]
    await one.save_task_context("synthetic-owner", ref, {"dialogue": ["late prior result"]})
    current = await two.finish_turn("synthetic-owner", turn.turn_id, mode="continue", task={"domain": "calendar", "question": "新问题"})
    assert current.task_id == ref.task_id and current.generation != ref.generation
    context = await one.read_task_context("synthetic-owner", current)
    assert context.value == turn.context
    assert context.revision != before.revision
    with pytest.raises(WorkError, match="task_superseded"):
        await one.save_task_context("synthetic-owner", ref, {"dialogue": ["stale"]})
    assert (await two.read_task_context("synthetic-owner", current)).value == turn.context


@pytest.mark.asyncio
async def test_topic_exit_cancels_old_jobs_states_and_previews_keeps_grant_and_receipts(task_stores):
    one, two, _ = task_stores
    owner, ref = "synthetic-owner", await begin(one)
    await one.create("grant", owner, {"token": "synthetic-grant"}, owner=owner, ttl=7200, status="active")
    job = await one.admit("job", "ticket", {}, owner=owner, ttl=600, active={"waiting"}, limit=32, owner_limit=4, task_ref=ref)
    job = await one.change_task_record("job", "ticket", job, owner=owner, task_ref=ref, status="authorizing")
    await one.create_task_state("synthetic-state", {"ticket": "ticket", "browser_hash": "synthetic"}, owner=owner, task_ref=ref)
    await pending_plan(one, owner, ref)
    for stage in ("executing", "uncertain", "completed"):
        await one.create("plan", stage, {"receipt": "synthetic-receipt"}, owner=owner, ttl=86400, status=stage)
    await one.save_task_context(owner, ref, {"document": "doxSynthetic123"})
    turn = await two.reserve_turn(owner, "new-general-message")
    assert await two.finish_turn(owner, turn.turn_id, mode="exit") is None
    assert (await one.read("grant", owner, owner=owner)).value["token"] == "synthetic-grant"
    assert (await one.read("job", "ticket", owner=owner)).status == "cancelled"
    assert (await one.read("plan", "synthetic-plan", owner=owner)).status == "cancelled"
    assert (await one.read("state", "synthetic-state")).status == "cancelled"
    for stage in ("executing", "uncertain", "completed"):
        assert (await one.read("plan", stage, owner=owner)).status == stage
    with pytest.raises(WorkError, match="task_superseded"):
        await one.install_authorized("ticket", job, owner=owner, value={"token": "late"}, ttl=7200, task_ref=ref)


@pytest.mark.asyncio
async def test_other_owner_exit_cannot_cancel_another_users_authorization(task_stores):
    one, two, _ = task_stores
    ref_a, ref_b = await begin(one, "A"), await begin(two, "B")
    await one.create_task_state("state-for-A", {"ticket": "A"}, owner="A", task_ref=ref_a)
    turn = await two.reserve_turn("B", "B-exit")
    await two.finish_turn("B", turn.turn_id, mode="exit")
    assert (await one.read("state", "state-for-A")).status == "authorizing"
    await one.assert_task("A", ref_a)
    with pytest.raises(WorkError, match="task_superseded"):
        await one.assert_task("B", ref_b)


@pytest.mark.asyncio
async def test_authorization_install_and_explicit_revoke_share_task_fence(task_stores):
    one, two, _ = task_stores
    owner, ref = "synthetic-owner", await begin(one)
    job = await one.admit("job", "ticket", {}, owner=owner, ttl=600, active={"waiting"}, limit=32, owner_limit=4, task_ref=ref)
    job = await one.change_task_record("job", "ticket", job, owner=owner, task_ref=ref, status="authorizing")
    await two.invalidate_owner(owner)
    with pytest.raises(WorkError, match="task_superseded"):
        await one.install_authorized("ticket", job, owner=owner, value={"token": "late"}, ttl=7200, task_ref=ref)
    assert await one.read("grant", owner, owner=owner) is None


@pytest.mark.asyncio
async def test_current_state_is_consumed_and_grant_installed_once(task_stores):
    one, two, _ = task_stores
    owner, ref = "synthetic-owner", await begin(one)
    job = await one.admit("job", "ticket", {}, owner=owner, ttl=600, active={"waiting"}, limit=32, owner_limit=4, task_ref=ref)
    job = await one.change_task_record("job", "ticket", job, owner=owner, task_ref=ref, status="authorizing")
    state = await one.create_task_state("current-state", {"ticket": "ticket"}, owner=owner, task_ref=ref)
    consumed = await two.change_task_record("state", "current-state", state, owner=owner, task_ref=ref, status="consumed")
    assert consumed.status == "consumed" and consumed.owner == ""
    assert await one.change_task_record("state", "current-state", state, owner=owner, task_ref=ref, status="consumed") is None
    grant, authorized = await two.install_authorized("ticket", job, owner=owner, value={"token": "synthetic-current", "scopes": []}, ttl=7200, task_ref=ref)
    assert grant.status == "active" and authorized.status == "authorized"
    with pytest.raises(WorkError, match="authorization_or_binding_invalid"):
        await one.install_authorized("ticket", job, owner=owner, value={"token": "synthetic-replay"}, ttl=7200, task_ref=ref)
    assert (await one.read("grant", owner, owner=owner)).value["token"] == "synthetic-current"


@pytest.mark.asyncio
async def test_expired_conversation_cannot_be_recreated_by_stale_writer(task_stores):
    one, _, sf = task_stores
    owner, ref = "synthetic-owner", await begin(one)
    async with sf() as session:
        await session.execute(sa.update(WorkRecordRow).where(*one._where("conversation", owner)).values(expires_at=time.time() - 1))
        await session.commit()
    await one.purge()
    with pytest.raises(WorkError, match="task_superseded"):
        await one.save_task_context(owner, ref, {"document": "stale"})
    assert await one.read("conversation", owner, owner=owner) is None
    current = await begin(one, owner, "new-after-expiry")
    assert ref != current and ref.task_id != current.task_id
    with pytest.raises(WorkError, match="task_superseded"):
        await one.assert_task(owner, ref)


@pytest.mark.asyncio
async def test_claim_and_exit_are_atomic_and_keep_an_admitted_write(task_stores):
    one, two, _ = task_stores
    owner, ref = "synthetic-owner", await begin(one)
    old = await pending_plan(one, owner, ref)
    turn = await two.reserve_turn(owner, "exit")
    results = await asyncio.gather(one.claim_plan("synthetic-plan", old, owner=owner, task_ref=ref, value={"id": "synthetic-plan"}), two.finish_turn(owner, turn.turn_id, mode="exit"), return_exceptions=True)
    final = await one.read("plan", "synthetic-plan", owner=owner)
    assert final.status in {"executing", "cancelled"}
    if isinstance(results[0], WorkError):
        assert results[0].stage == "task_superseded" and final.status == "cancelled"
    else:
        assert results[0] is not None and final.status == "executing"


def trusted_message():
    return InboundMessage(
        channel_name="feishu", user_id="synthetic-sender", owner_user_id="synthetic-owner", connection_id="synthetic-connection", workspace_id="synthetic-chat", chat_id="synthetic-chat", text="修改测试文档", metadata={"chat_type": "p2p"}
    )


async def capability_fixture(store):
    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(return_value={"id": "synthetic-connection", "owner_user_id": "synthetic-owner", "status": "connected"}))
    vault = TokenVault("synthetic-app", repo, None, store=store)
    p = Principal.from_message("synthetic-app", trusted_message())
    await vault.install(p, Grant("synthetic-token", time.monotonic() + 7200, frozenset({"docx:document"})))
    api = AsyncMock()
    return p, api, WorkCapabilities(vault, api, task_fencing=True)


@pytest.mark.asyncio
async def test_switch_while_preview_snapshot_inflight_never_creates_preview(task_stores):
    one, two, _ = task_stores
    p, api, tools = await capability_fixture(one)
    owner = tools.vault.principal_key(p)
    ref = await begin(one, owner)
    started, release = asyncio.Event(), asyncio.Event()

    async def snapshot(*args, **kwargs):
        started.set()
        await release.wait()
        return {"data": {"document": {"title": "old", "revision_id": 7}}}

    api.side_effect = snapshot
    preparing = asyncio.create_task(tools.prepare(p, Action(operation="document_rename", document_id="doxSynthetic123", title="new"), task_ref=ref))
    await started.wait()
    turn = await two.reserve_turn(owner, "exit")
    await two.finish_turn(owner, turn.turn_id, mode="exit")
    release.set()
    with pytest.raises(WorkError, match="task_superseded"):
        await preparing
    assert not tools.plans and api.await_count == 1


@pytest.mark.asyncio
async def test_switch_during_confirmation_discovery_never_dispatches_write(task_stores):
    one, two, _ = task_stores
    p, api, tools = await capability_fixture(one)
    owner = tools.vault.principal_key(p)
    ref = await begin(one, owner)
    api.return_value = {"data": {"document": {"title": "old", "revision_id": 7}}}
    plan = await tools.prepare(p, Action(operation="document_rename", document_id="doxSynthetic123", title="new"), task_ref=ref)
    started, release = asyncio.Event(), asyncio.Event()

    async def snapshot(op, **kwargs):
        assert op == "document_info"
        started.set()
        await release.wait()
        return {"data": {"document": {"title": "old", "revision_id": 7}}}

    api.side_effect = snapshot
    confirming = asyncio.create_task(tools.confirm(p, plan.id))
    await started.wait()
    turn = await two.reserve_turn(owner, "exit")
    await two.finish_turn(owner, turn.turn_id, mode="exit")
    release.set()
    with pytest.raises(WorkError, match="task_superseded"):
        await confirming
    assert all(call.args[0] == "document_info" for call in api.call_args_list)


@pytest.mark.asyncio
async def test_admitted_write_retains_receipt_after_topic_exit_and_is_not_retried(task_stores):
    one, two, _ = task_stores
    p, api, tools = await capability_fixture(one)
    owner = tools.vault.principal_key(p)
    ref = await begin(one, owner)
    api.return_value = {"data": {"document": {"title": "old", "revision_id": 7}}}
    plan = await tools.prepare(p, Action(operation="document_rename", document_id="doxSynthetic123", title="new"), task_ref=ref)
    started, release = asyncio.Event(), asyncio.Event()

    async def write(op, **kwargs):
        if op == "block_update":
            started.set()
            await release.wait()
            return {"data": {}}
        return {"data": {"document": {"title": "new" if started.is_set() else "old", "revision_id": 7}}}

    api.side_effect = write
    confirming = asyncio.create_task(tools.confirm(p, plan.id))
    await started.wait()
    turn = await two.reserve_turn(owner, "exit")
    await two.finish_turn(owner, turn.turn_id, mode="exit")
    assert (await one.read("plan", plan.id, owner=owner)).status == "executing"
    release.set()
    assert "已执行" in await confirming
    assert (await one.read("plan", plan.id, owner=owner)).status == "completed"
    with pytest.raises(WorkError):
        await tools.confirm(p, plan.id)
    assert sum(call.args[0] == "block_update" for call in api.call_args_list) == 1
    assert "此前已完成" in await tools.terminal_notice(p, plan.id)


@pytest.mark.asyncio
async def test_new_mode_rejects_legacy_preview_without_task_ref(task_stores):
    one, _, _ = task_stores
    p, api, tools = await capability_fixture(one)
    tools.task_fencing = False
    plan = await tools.prepare(p, Action(operation="document_create", title="legacy"))
    tools.task_fencing = True
    with pytest.raises(WorkError, match="task_reference_required"):
        await tools.confirm(p, plan.id)
    api.assert_not_awaited()


def test_task_reference_is_strict_and_generation_is_not_a_row_revision():
    first, second = TaskRef.new(), TaskRef.new()
    assert first != second and TaskRef.parse(first.as_dict()) == first
    assert TaskRef.parse(None) is None
    with pytest.raises(WorkError):
        TaskRef.parse({**first.as_dict(), "owner": "someone-else"})
    with pytest.raises(WorkError):
        TaskRef.parse({"task_id": first.task_id, "generation": 123})


@pytest.mark.asyncio
async def test_memory_guard_is_rechecked_after_confirmation_snapshot_without_writes():
    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(return_value={"id": "synthetic-connection", "owner_user_id": "synthetic-owner", "status": "connected"}))
    vault = TokenVault("synthetic-app", repo, None)
    p = Principal.from_message("synthetic-app", trusted_message())
    vault.put(p, Grant("synthetic-token", time.monotonic() + 7200, frozenset({"docx:document"})))
    ref = TaskRef.new()
    active = [ref]

    async def guard(principal, expected):
        assert principal == p
        if active[0] != expected:
            raise WorkError("task_superseded")

    api = AsyncMock(return_value={"data": {"document": {"title": "old", "revision_id": 7}}})
    tools = WorkCapabilities(vault, api, task_fencing=True, task_guard=guard)
    plan = await tools.prepare(p, Action(operation="document_rename", document_id="doxSynthetic123", title="new"), task_ref=ref)

    async def snapshot(op, **kwargs):
        active[0] = TaskRef.new()
        return {"data": {"document": {"title": "old", "revision_id": 7}}}

    api.side_effect = snapshot
    with pytest.raises(WorkError, match="task_superseded"):
        await tools.confirm(p, plan.id)
    assert plan.stage == "pending" and all(call.args[0] == "document_info" for call in api.call_args_list)
