"""Finite calendar batches use only synthetic APIs and disposable databases."""

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
from app.integrations.work.capabilities import Action, WorkCapabilities, parse_time
from app.integrations.work.client import WorkError
from app.integrations.work.store import WorkStore
from app.integrations.work.turn_state import MemoryTurns
from deerflow.persistence.work.model import WorkRecordRow

KEY = "c3ludGhldGljLXRlc3Qta2V5LTMyLWJ5dGVzISEhISE="
SCOPES = frozenset({"calendar:calendar", "calendar:calendar:readonly"})


def principal(name="A"):
    message = InboundMessage(
        channel_name="feishu", chat_id="chat-" + name, user_id="ou-" + name, owner_user_id="owner-" + name, connection_id="conn-" + name, workspace_id="chat-" + name, text="synthetic", thread_ts="msg", metadata={"chat_type": "p2p"}
    )
    return Principal.from_message("synthetic-app", message)


def action(index=1, **changes):
    return Action(operation="event_create", title=f"Synthetic {index}", start=f"2026-11-{index + 10:02d}T10:00:00+08:00", end=f"2026-11-{index + 10:02d}T10:30:00+08:00", **changes)


class Provider:
    def __init__(self, token="synthetic-A"):
        self.events, self.writes, self.calls = {}, [], []
        self.token = token
        self.entered, self.release = None, None
        self.fail_at = None

    async def __call__(self, operation, **kwargs):
        assert kwargs["token"] == self.token
        self.calls.append(operation)
        if operation == "calendar_primary":
            return {"data": {"calendars": [{"calendar": {"calendar_id": "primary-A", "type": "primary", "role": "owner"}}]}}
        if operation == "events":
            return {"data": {"items": [], "has_more": False}}
        if operation == "event_attendees":
            return {"data": {"items": [], "has_more": False}}
        if operation == "event_get":
            return {"data": {"event": dict(self.events[kwargs["event_id"]])}}
        if operation in {"event_create", "event_update"}:
            event_id = kwargs.get("event_id") or f"synthetic-event-{len(self.writes) + 1}"
            self.writes.append((operation, dict(kwargs)))
            self.events[event_id] = {"event_id": event_id, "organizer_calendar_id": "primary-A", "status": "confirmed", **self.events.get(event_id, {}), **kwargs["payload"]}
            if self.entered is not None and len(self.writes) == 1:
                self.entered.set()
                await self.release.wait()
            if self.fail_at == len(self.writes):
                raise TimeoutError("synthetic post-send response loss")
            return {"data": {"event": self.events[event_id]}}
        pytest.fail(f"Unexpected synthetic operation {operation}")


@pytest_asyncio.fixture(params=["memory", "sqlite", "postgres"])
async def batch_env(request, tmp_path):
    engine, store = None, None
    if request.param != "memory":
        url = "sqlite+aiosqlite:///" + str(tmp_path / "batch.db")
        if request.param == "postgres":
            url = os.getenv("WORK_BATCH_TEST_POSTGRES_URL", "")
            if not url:
                pytest.skip("Dedicated empty PostgreSQL batch database not configured")
            assert "@127.0.0.1:" in url and url.rsplit("/", 1)[-1].startswith("work_batch_test")
        engine = create_async_engine(url)
        async with engine.begin() as connection:
            assert not await connection.run_sync(lambda conn: sa.inspect(conn).get_table_names()), "Use only an empty disposable database"
            await connection.run_sync(WorkRecordRow.__table__.create)
        store = WorkStore(async_sessionmaker(engine, expire_on_commit=False), key=KEY, namespace="synthetic-batch")
    turns = store or MemoryTurns()

    async def find(**kwargs):
        name = kwargs["external_account_id"].removeprefix("ou-")
        return {"id": "conn-" + name, "owner_user_id": "owner-" + name, "status": "connected"}

    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(side_effect=find))
    vault = TokenVault("synthetic-app", repo, None, store=store)
    p = principal()
    await vault.install(p, Grant("synthetic-A", time.monotonic() + 3600, SCOPES))
    owner = vault.principal_key(p)
    turn = await turns.reserve_turn(owner, "initial")
    ref = await turns.finish_turn(owner, turn.turn_id, mode="new", task={"domain": "calendar"})
    provider = Provider()

    def guard(person, reference):
        return turns.assert_task(vault.principal_key(person), reference)

    tools = WorkCapabilities(vault, provider, task_fencing=True, task_guard=guard)
    try:
        yield SimpleNamespace(tools=tools, provider=provider, vault=vault, p=p, ref=ref, store=store, turns=turns, owner=owner, guard=guard)
    finally:
        if engine:
            async with engine.begin() as connection:
                await connection.run_sync(WorkRecordRow.__table__.drop)
            await engine.dispose()


@pytest.mark.asyncio
async def test_batch_preview_confirms_once_and_retains_owner_bound_successful_receipts(batch_env):
    env = batch_env
    plan = await env.tools.prepare_batch(env.p, [action(1), action(2)], task_ref=env.ref)
    assert "共 2 条" in plan.preview and "30 分钟" in plan.preview
    assert not env.provider.writes
    with pytest.raises(WorkError):
        await env.tools.confirm(principal("B"), plan.id)
    assert not env.provider.writes
    result = await env.tools.confirm(env.p, plan.id)
    assert "成功 2 / 2" in result and len(env.provider.writes) == 2
    assert env.provider.writes[0][1]["params"]["idempotency_key"] != env.provider.writes[1][1]["params"]["idempotency_key"]
    with pytest.raises(WorkError):
        await env.tools.confirm(env.p, plan.id)
    receipts = await env.tools.recent_calendar_receipts(env.p)
    assert {row["event_id"] for row in receipts} == {"synthetic-event-1", "synthetic-event-2"}
    assert all(row["status"] == "completed" for row in receipts)
    assert await env.tools.recent_calendar_receipts(principal("B")) == []


@pytest.mark.asyncio
async def test_topic_exit_stops_unstarted_items_but_keeps_already_claimed_success(batch_env):
    env = batch_env
    plan = await env.tools.prepare_batch(env.p, [action(1), action(2)], task_ref=env.ref)
    env.provider.entered, env.provider.release = asyncio.Event(), asyncio.Event()
    confirming = asyncio.create_task(env.tools.confirm(env.p, plan.id))
    await asyncio.wait_for(env.provider.entered.wait(), 5)
    turn = await env.turns.reserve_turn(env.owner, "topic-exit")
    await env.turns.finish_turn(env.owner, turn.turn_id, mode="exit")
    env.provider.release.set()
    result = await confirming
    assert len(env.provider.writes) == 1 and "成功 1 / 2" in result and "未执行" in result
    assert env.tools.plans[plan.id].stage == "partial"
    assert len(await env.tools.recent_calendar_receipts(env.p)) == 1
    assert "成功 1 / 2" in await env.tools.terminal_notice(env.p, plan.id)


@pytest.mark.asyncio
async def test_response_loss_is_not_retried_and_reports_partial_and_unexecuted_items(batch_env):
    env = batch_env
    plan = await env.tools.prepare_batch(env.p, [action(1), action(2), action(3)], task_ref=env.ref)
    env.provider.fail_at = 2
    with pytest.raises(TimeoutError):
        await env.tools.confirm(env.p, plan.id)
    with pytest.raises(WorkError):
        await env.tools.confirm(env.p, plan.id)
    notice = await env.tools.terminal_notice(env.p, plan.id)
    assert "成功 1 / 3" in notice and "结果不确定" in notice and "未执行" in notice
    assert len(env.provider.writes) == 2
    assert len(await env.tools.recent_calendar_receipts(env.p)) == 1


@pytest.mark.asyncio
async def test_duplicate_confirms_on_independent_instances_cannot_repeat_batch(batch_env):
    env = batch_env
    if env.store is None:
        pytest.skip("Independent-process authority requires shared SQL")
    plan = await env.tools.prepare_batch(env.p, [action(1), action(2)], task_ref=env.ref)
    second = WorkCapabilities(env.vault, env.provider, task_fencing=True, task_guard=env.guard)
    result = await asyncio.gather(env.tools.confirm(env.p, plan.id), second.confirm(env.p, plan.id), return_exceptions=True)
    assert sum(isinstance(value, str) for value in result) == 1
    assert len(env.provider.writes) == 2
    assert len(await second.recent_calendar_receipts(env.p)) == 2


@pytest.mark.asyncio
async def test_batch_rejects_duplicate_or_out_of_scope_operations_before_writes(batch_env):
    env = batch_env
    with pytest.raises(WorkError, match="batch_duplicate_target"):
        await env.tools.prepare_batch(env.p, [action(1), action(1)], task_ref=env.ref)
    with pytest.raises(WorkError, match="batch_calendar_only"):
        await env.tools.prepare_batch(env.p, [Action(operation="document_create", title="not allowed")], task_ref=env.ref)
    with pytest.raises(ValueError):
        await env.tools.prepare_batch(env.p, [action(index) for index in range(1, 12)], task_ref=env.ref)
    assert not env.provider.writes


@pytest.mark.asyncio
async def test_old_fenced_preview_requires_new_protocol_without_promoting_it(batch_env):
    env = batch_env
    plan = await env.tools.prepare(env.p, action(1), task_ref=env.ref)
    plan.protocol = 0
    plan.signature = env.tools._sign(env.p, plan.payload_json, plan.snapshot, plan.preview, env.ref)
    if env.store:
        old = await env.store.read("plan", plan.id, owner=env.owner)
        value = env.tools._plan_data(plan)
        value.pop("protocol")
        await env.store.change("plan", plan.id, old, value=value)
    with pytest.raises(WorkError, match="plan_protocol_upgrade_required"):
        await env.tools.confirm(env.p, plan.id)
    assert not env.provider.writes


@pytest.mark.asyncio
async def test_legacy_successful_receipt_can_only_supply_a_locator_for_requery(batch_env):
    env = batch_env
    if env.store is None:
        pytest.skip("Legacy minimal completed records are SQL-only")
    await env.store.create("plan", "old-completed-plan", {"id": "old-completed-plan", "operation": "event_create", "receipt": "old-event-id", "target": "personal"}, owner=env.owner, ttl=3600, status="completed")
    receipts = await env.tools.recent_calendar_receipts(env.p)
    assert receipts == [{"event_id": "old-event-id", "title": "", "start": "", "end": "", "operation": "event_create", "plan_id": "old-completed-plan", "batch_group": "", "status": "completed"}]
    assert not env.provider.calls


@pytest.mark.asyncio
async def test_every_edit_snapshot_is_checked_before_the_first_batch_write(batch_env):
    env = batch_env
    original = action(2)
    env.provider.events["existing-A"] = {
        "event_id": "existing-A",
        "summary": "Original",
        "description": "",
        "start_time": {"timestamp": str(parse_time(original.start)), "timezone": "Asia/Shanghai"},
        "end_time": {"timestamp": str(parse_time(original.end)), "timezone": "Asia/Shanghai"},
        "organizer_calendar_id": "primary-A",
        "status": "confirmed",
    }
    env.tools.remember_events(env.p, ["existing-A"])
    edit = Action(operation="event_update", event_id="existing-A", title="New name", start=original.start, end=original.end, update_fields=("summary",))
    plan = await env.tools.prepare_batch(env.p, [action(1), edit], task_ref=env.ref)
    assert "Original" in plan.preview and "New name" in plan.preview and "summary" in plan.preview
    env.provider.events["existing-A"]["summary"] = "Changed elsewhere"
    with pytest.raises(WorkError, match="target_changed_request_new_preview"):
        await env.tools.confirm(env.p, plan.id)
    assert not env.provider.writes


@pytest.mark.asyncio
async def test_legacy_direct_capability_profile_can_still_read_its_old_signed_preview():
    p = principal()
    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(return_value={"id": p.connection, "owner_user_id": p.owner, "status": "connected"}))
    vault = TokenVault("synthetic-app", repo, None)
    vault.put(p, Grant("synthetic-A", time.monotonic() + 300, SCOPES))
    provider = Provider()
    tools = WorkCapabilities(vault, provider)  # Existing direct API has no new service protocol requirement.
    plan = await tools.prepare(p, action(1))
    plan.protocol = 0
    plan.signature = tools._sign(p, plan.payload_json, plan.snapshot, plan.preview)
    assert "已执行" in await tools.confirm(p, plan.id)
    assert len(provider.writes) == 1


@pytest.mark.asyncio
async def test_sql_batches_do_not_block_another_owner_but_serialize_the_same_owner(batch_env):
    env = batch_env
    if env.store is None:
        pytest.skip("SQL owner-scoped leases are the multi-user authority")
    p_b, provider_b = principal("B"), Provider("synthetic-B")
    await env.vault.install(p_b, Grant("synthetic-B", time.monotonic() + 300, SCOPES))
    turn = await env.turns.reserve_turn(env.vault.principal_key(p_b), "B-new")
    ref_b = await env.turns.finish_turn(env.vault.principal_key(p_b), turn.turn_id, mode="new", task={"domain": "calendar"})

    async def api(operation, **kwargs):
        return await (env.provider if kwargs["token"] == "synthetic-A" else provider_b)(operation, **kwargs)

    env.tools.api = api
    first = await env.tools.prepare_batch(env.p, [action(1)], task_ref=env.ref)
    same_owner = await env.tools.prepare_batch(env.p, [action(2)], task_ref=env.ref)
    other_owner = await env.tools.prepare_batch(p_b, [action(3)], task_ref=ref_b)
    env.provider.entered, env.provider.release = asyncio.Event(), asyncio.Event()
    pending = asyncio.create_task(env.tools.confirm(env.p, first.id))
    try:
        await asyncio.wait_for(env.provider.entered.wait(), 5)
        with pytest.raises(WorkError, match="another_write_in_progress"):
            await env.tools.confirm(env.p, same_owner.id)
        assert "成功 1 / 1" in await asyncio.wait_for(env.tools.confirm(p_b, other_owner.id), 5)
        assert len(provider_b.writes) == 1 and not pending.done()
    finally:
        env.provider.release.set()
        await pending
