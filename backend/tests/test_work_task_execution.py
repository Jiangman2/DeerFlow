"""Execution discovery survives continuation versions without granting authority."""

import asyncio
import json
import time

import pytest
import pytest_asyncio
import test_work_calendar_batch as batch_tests

from app.integrations.work.auth import Grant, TokenVault
from app.integrations.work.capabilities import Action, Plan, WorkCapabilities
from app.integrations.work.client import WorkError
from app.integrations.work.store import WorkStore
from app.integrations.work.tasks import TaskRef


@pytest_asyncio.fixture(params=["memory", "sqlite", "postgres"])
async def execution_env(request, tmp_path):
    async for environment in batch_tests.batch_env.__wrapped__(request, tmp_path):
        yield environment


@pytest.mark.asyncio
async def test_completed_execution_is_read_only_and_survives_new_generation(execution_env):
    env = execution_env
    assert await env.tools.calendar_task_execution(env.p, env.ref) == {"state": "none", "started": False, "complete": True, "plans": [], "events": []}
    plan = await env.tools.prepare_batch(env.p, [batch_tests.action(1)], task_ref=env.ref)
    assert (await env.tools.calendar_task_execution(env.p, env.ref))["state"] == "none"
    await env.tools.confirm(env.p, plan.id)
    calls = len(env.provider.calls)
    turn = await env.turns.reserve_turn(env.owner, "clarification-after-execution")
    ref = await env.turns.finish_turn(env.owner, turn.turn_id, mode="continue", task={"domain": "calendar"})
    assert ref.task_id == env.ref.task_id and ref.generation != env.ref.generation
    reader = env.tools
    if env.store:
        # No local plan/context survives on this independent service instance.
        other_store = WorkStore(env.store._sf, key=batch_tests.KEY, namespace="synthetic-batch")
        other_vault = TokenVault("synthetic-app", env.vault.repo, None, store=other_store)
        reader = WorkCapabilities(other_vault, env.provider, task_fencing=True)
        assert not reader.plans
    state = await reader.calendar_task_execution(env.p, ref)
    assert state["state"] == "completed" and state["started"] and state["complete"]
    assert state["events"][0]["event_id"] == "synthetic-event-1"
    assert len(env.provider.calls) == calls
    assert "synthetic-A" not in json.dumps(state)
    with pytest.raises(WorkError, match="task_superseded"):
        await env.tools.calendar_task_execution(env.p, env.ref)


@pytest.mark.asyncio
async def test_explicit_new_task_does_not_inherit_prior_task_execution(execution_env):
    env = execution_env
    plan = await env.tools.prepare_batch(env.p, [batch_tests.action(1)], task_ref=env.ref)
    await env.tools.confirm(env.p, plan.id)
    turn = await env.turns.reserve_turn(env.owner, "explicit-new-request")
    ref = await env.turns.finish_turn(env.owner, turn.turn_id, mode="new", task={"domain": "calendar"})
    assert ref.task_id != env.ref.task_id
    state = await env.tools.calendar_task_execution(env.p, ref)
    assert state == {"state": "none", "started": False, "complete": True, "plans": [], "events": []}


@pytest.mark.asyncio
async def test_owner_isolation_holds_even_for_same_task_id_collision(execution_env):
    env = execution_env
    plan = await env.tools.prepare_batch(env.p, [batch_tests.action(1)], task_ref=env.ref)
    await env.tools.confirm(env.p, plan.id)
    p_b = batch_tests.principal("B")
    await env.vault.install(p_b, Grant("synthetic-B", time.monotonic() + 300, batch_tests.SCOPES))
    owner_b = env.vault.principal_key(p_b)
    ref_b = TaskRef.new(task_id=env.ref.task_id)
    if env.store:
        await env.store.create("conversation", owner_b, {"task_ref": ref_b.as_dict(), "task": {}, "context": {}, "context_expires_at": 0}, owner=owner_b, ttl=900)
    else:
        env.turns._rows[owner_b] = {"task_ref": ref_b, "task": {}, "context": {}, "context_expires_at": 0, "expires_at": time.monotonic() + 900}
    state = await env.tools.calendar_task_execution(p_b, ref_b)
    assert state == {"state": "none", "started": False, "complete": True, "plans": [], "events": []}
    with pytest.raises(WorkError, match="task_superseded"):
        await env.tools.calendar_task_execution(p_b, env.ref)


@pytest.mark.asyncio
async def test_started_batch_and_later_partial_result_survive_continuation(execution_env):
    env = execution_env
    plan = await env.tools.prepare_batch(env.p, [batch_tests.action(1), batch_tests.action(2)], task_ref=env.ref)
    env.provider.entered, env.provider.release = asyncio.Event(), asyncio.Event()
    confirming = asyncio.create_task(env.tools.confirm(env.p, plan.id))
    try:
        await asyncio.wait_for(env.provider.entered.wait(), 5)
        before = await env.tools.calendar_task_execution(env.p, env.ref)
        assert before["state"] == "started" and before["started"] and not before["events"]
        turn = await env.turns.reserve_turn(env.owner, "changed-request-during-execution")
        ref = await env.turns.finish_turn(env.owner, turn.turn_id, mode="continue", task={"domain": "calendar"})
        env.provider.release.set()
        await confirming
        after = await env.tools.calendar_task_execution(env.p, ref)
        assert after["state"] == "partial" and after["started"]
        assert [item["event_id"] for item in after["events"]] == ["synthetic-event-1"]
        assert len(env.provider.writes) == 1
    finally:
        env.provider.release.set()
        await asyncio.gather(confirming, return_exceptions=True)


@pytest.mark.asyncio
async def test_uncertain_items_never_become_successful_execution_facts(execution_env):
    env = execution_env
    plan = await env.tools.prepare_batch(env.p, [batch_tests.action(1), batch_tests.action(2)], task_ref=env.ref)
    env.provider.fail_at = 2
    with pytest.raises(TimeoutError):
        await env.tools.confirm(env.p, plan.id)
    state = await env.tools.calendar_task_execution(env.p, env.ref)
    assert state["state"] == "uncertain" and state["started"]
    assert [item["event_id"] for item in state["events"]] == ["synthetic-event-1"]


@pytest.mark.asyncio
async def test_old_completed_sql_plan_retains_a_trusted_locator_across_generations(execution_env):
    env = execution_env
    if not env.store:
        pytest.skip("The legacy minimal receipt format is SQL-only")
    conversation = await env.store.read("conversation", env.owner, owner=env.owner)
    value = dict(conversation.value)
    value.pop("execution_index")
    await env.store.change("conversation", env.owner, conversation, value=value)
    await env.store.create("plan", "old-r1-plan", {"id": "old-r1-plan", "operation": "event_create", "receipt": "old-event-51-hours", "target": "personal", "task_ref": env.ref.as_dict()}, owner=env.owner, ttl=3600, status="completed")
    turn = await env.turns.reserve_turn(env.owner, "repair-followup")
    ref = await env.turns.finish_turn(env.owner, turn.turn_id, mode="continue", task={"domain": "calendar"})
    state = await env.tools.calendar_task_execution(env.p, ref)
    assert state["state"] == "completed"
    assert state["events"][0]["event_id"] == "old-event-51-hours"
    assert state["events"][0]["start"] == "" and state["events"][0]["title"] == ""
    assert not env.provider.calls


@pytest.mark.asyncio
async def test_old_history_does_not_block_a_new_indexed_task_or_erase_its_draft(execution_env):
    env = execution_env
    turn = await env.turns.reserve_turn(env.owner, "pending-draft-before-history-scan")
    pending_draft = {"domain": "calendar", "draft": {"operation": "create", "schedule": {"count": 3}}, "question": "请补充日期"}
    env.ref = await env.turns.finish_turn(env.owner, turn.turn_id, mode="continue", task=pending_draft)
    for index in range(101):
        reference = TaskRef.new()
        if env.store:
            await env.store.create(
                "plan", "unrelated-" + str(index), {"id": "unrelated-" + str(index), "operation": "event_create", "receipt": "other-event-" + str(index), "task_ref": reference.as_dict()}, owner=env.owner, ttl=3600, status="completed"
            )
        else:
            env.tools.plans["unrelated-" + str(index)] = Plan(
                "unrelated-" + str(index),
                env.p,
                Action(operation="event_create", title="other", start="2026-11-11T10:00:00+08:00", end="2026-11-11T11:00:00+08:00").model_dump_json(),
                "",
                "{}",
                "",
                time.monotonic() + 300,
                stage="completed",
                receipt="other-event-" + str(index),
                task_ref=reference,
            )
    state = await env.tools.calendar_task_execution(env.p, env.ref)
    assert state["state"] == ("none" if env.store else "unknown")
    assert state["complete"] is bool(env.store)
    assert not state["events"] and not state["plans"]
    if env.store:
        task = (await env.store.read("conversation", env.owner, owner=env.owner)).value["task"]
        assert task == pending_draft
        # An actual pre-index legacy conversation remains conservative.
        conversation = await env.store.read("conversation", env.owner, owner=env.owner)
        value = dict(conversation.value)
        value.pop("execution_index")
        await env.store.change("conversation", env.owner, conversation, value=value)
        legacy = await env.tools.calendar_task_execution(env.p, env.ref)
        assert legacy["state"] == "unknown" and legacy["complete"] is False


@pytest.mark.asyncio
async def test_index_is_claim_atomic_continues_without_context_save_and_resets_for_new_task(execution_env):
    env = execution_env
    if not env.store:
        pytest.skip("The atomic execution index is SQL-only")
    pending = await env.tools.prepare(env.p, batch_tests.action(1), task_ref=env.ref)
    old = await env.store.read("plan", pending.id, owner=env.owner)
    # Claim only: simulate a process stopping before provider I/O or any cache save.
    assert await env.store.claim_plan(pending.id, old, owner=env.owner, task_ref=env.ref, value={"id": pending.id, "operation": "event_create", "target": "personal"})
    turn = await env.turns.reserve_turn(env.owner, "continue-after-claim")
    ref = await env.turns.finish_turn(env.owner, turn.turn_id, mode="continue", task={"domain": "calendar", "draft": {"operation": "create"}})
    index = (await env.store.read("conversation", env.owner, owner=env.owner)).value["execution_index"]
    assert index["started"] and index["task_id"] == ref.task_id and index["plan_ids"] == [pending.id]
    other_store = WorkStore(env.store._sf, key=batch_tests.KEY, namespace="synthetic-batch")
    other = WorkCapabilities(TokenVault("synthetic-app", env.vault.repo, None, store=other_store), env.provider, task_fencing=True)
    state = await other.calendar_task_execution(env.p, ref)
    assert state["state"] == "started" and state["started"]
    assert not env.provider.writes
    turn = await env.turns.reserve_turn(env.owner, "new-after-claim")
    fresh = await env.turns.finish_turn(env.owner, turn.turn_id, mode="new", task={"draft": {"operation": "create"}})
    assert (await other.calendar_task_execution(env.p, fresh))["state"] == "none"
    assert (await env.store.read("plan", pending.id, owner=env.owner)).status == "executing"


@pytest.mark.asyncio
async def test_execution_index_truncation_is_explicit_and_cannot_erase_started_fact(execution_env):
    env = execution_env
    if not env.store:
        pytest.skip("The atomic execution index is SQL-only")
    for index in range(11):
        key = "indexed-plan-" + str(index)
        record = await env.store.admit("plan", key, {}, owner=env.owner, task_ref=env.ref, ttl=300, active={"pending"}, limit=100, owner_limit=20)
        pending = await env.store.change_task_record("plan", key, record, owner=env.owner, task_ref=env.ref, status="pending")
        assert await env.store.claim_plan(key, pending, owner=env.owner, task_ref=env.ref, value={"id": key, "operation": "event_create", "target": "personal"})
    state = await env.tools.calendar_task_execution(env.p, env.ref)
    assert state["started"] and state["complete"] is False and len(state["plans"]) == 10
    assert state["state"] == "started"
