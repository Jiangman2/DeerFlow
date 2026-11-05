"""Independent Gateway services continue canonical drafts in shared SQL state."""

from unittest.mock import AsyncMock

import pytest
from test_work_production import principal_message, production_service, stores  # noqa: F401

from app.integrations.work.client import WorkError
from app.integrations.work.drafts import CalendarDraft, expand_create, merge_draft, missing_question
from app.integrations.work.routing import WorkTurnDecision
from app.integrations.work.store import WorkStore
from app.integrations.work.tasks import TaskRef


def decision(draft, *, route="continue_work"):
    return WorkTurnDecision(route=route, domain="calendar", draft=draft)


def partial_draft():
    return CalendarDraft(operation="create", cardinality="finite", schedule={"count": 3})


def add_known_slots(draft):
    return merge_draft(draft, {"changes": {"title": "技术交流"}, "schedule": {"start_clock": "09:00", "duration_minutes": 180}})


def third_store(shared_stores):
    first, _, session_factory = shared_stores
    # WorkStore.namespace is already a hash; its constructor takes the original
    # namespace, exactly as the imported fixture does for its two processes.
    result = WorkStore(session_factory, key=first.key, namespace="production:synthetic-app")
    assert result.namespace == first.namespace
    return result


async def current_task(store, service):
    owner = service.vault.principal_key(service._identity(principal_message()))
    record = await store.read("conversation", owner, owner=owner)
    assert record is not None
    return owner, record


@pytest.mark.asyncio
async def test_three_independent_services_keep_count_time_and_title_then_freeze_three_occurrences_before_oauth(stores):  # noqa: F811 - imported pytest fixture
    first, second, third = [production_service(store) for store in (stores[0], stores[1], third_store(stores))]
    initial, known = partial_draft(), add_known_slots(partial_draft())
    first._turn_planner = AsyncMock(return_value=decision(initial, route="new_work"))

    async def second_turn(_request, **kwargs):
        restored = CalendarDraft.model_validate(kwargs["task_context"]["draft"])
        assert restored == initial
        assert kwargs["task_context"]["original_request"] == "请创建连续三天的日程"
        return decision(add_known_slots(restored))

    async def third_turn(_request, **kwargs):
        restored = CalendarDraft.model_validate(kwargs["task_context"]["draft"])
        assert restored == known
        question = missing_question(restored)
        assert "日期" in question
        assert all(word not in question for word in ("标题", "开始时间", "持续多久"))
        ready = merge_draft(restored, {"schedule": {"start_date": "2026-11-03"}})
        assert not missing_question(ready)
        return decision(ready)

    second._turn_planner = AsyncMock(side_effect=second_turn)
    third._turn_planner = AsyncMock(side_effect=third_turn)
    try:
        assert await first.handle(principal_message(text="请创建连续三天的日程", source="first-pod"))
        owner, first_state = await current_task(stores[1], first)
        first_ref = TaskRef.parse(first_state.value["task_ref"])
        assert CalendarDraft.model_validate(first_state.value["task"]["draft"]) == initial
        assert not first.jobs
        first._api.assert_not_awaited()

        assert await second.handle(principal_message(text="每天9点，标题技术交流，每条3小时", source="second-pod"))
        _, second_state = await current_task(stores[0], second)
        second_ref = TaskRef.parse(second_state.value["task_ref"])
        assert second_ref.task_id == first_ref.task_id and second_ref.generation != first_ref.generation
        assert CalendarDraft.model_validate(second_state.value["task"]["draft"]) == known
        assert "日期" in second.send.call_args.args[0].text
        assert not second.jobs
        second._api.assert_not_awaited()

        assert await third.handle(principal_message(text="从2026年11月3日开始", source="third-pod"))
        _, third_state = await current_task(stores[1], third)
        third_ref = TaskRef.parse(third_state.value["task_ref"])
        assert third_ref.task_id == first_ref.task_id and third_ref.generation != second_ref.generation
        assert len(third.jobs) == 1
        job = next(iter(third.jobs.values()))
        frozen = CalendarDraft.model_validate(job.intent["draft"])
        assert frozen == CalendarDraft.model_validate(third_state.value["task"]["draft"])
        assert job.task_ref == third_ref.as_dict()
        occurrences = expand_create(frozen)
        assert len(occurrences) == 3
        assert [intent.changes.start[:10] for intent in occurrences] == ["2026-11-03", "2026-11-04", "2026-11-05"]
        assert all(intent.changes.title == "技术交流" and intent.changes.start.endswith("T09:00:00+08:00") and intent.changes.end.endswith("T12:00:00+08:00") for intent in occurrences)
        assert job.stage == "awaiting_authorization"
        assert "授权并继续" in third.send.call_args.args[0].text
        third._api.assert_not_awaited()
        assert all(not service.tools.plans for service in (first, second, third))
        with pytest.raises(WorkError, match="task_superseded"):
            await stores[0].assert_task(owner, first_ref)
    finally:
        for service in (first, second, third):
            await service.close()


@pytest.mark.asyncio
async def test_transient_model_timeout_rotates_authority_but_keeps_partial_draft_for_another_service(stores):  # noqa: F811 - imported pytest fixture
    first, failed, resumed = [production_service(store) for store in (stores[0], stores[1], third_store(stores))]
    initial = partial_draft()
    first._turn_planner = AsyncMock(return_value=decision(initial, route="new_work"))
    failed._turn_planner = AsyncMock(side_effect=WorkError("turn_model_timeout"))

    async def retry_turn(_request, **kwargs):
        restored = CalendarDraft.model_validate(kwargs["task_context"]["draft"])
        assert restored == initial  # A failed model did not invent or accept the lost turn's fields.
        return decision(add_known_slots(restored))

    resumed._turn_planner = AsyncMock(side_effect=retry_turn)
    try:
        assert await first.handle(principal_message(text="请创建连续三天的日程", source="partial-pod"))
        owner, before = await current_task(stores[0], first)
        old_ref = TaskRef.parse(before.value["task_ref"])
        assert await failed.handle(principal_message(text="每天9点，标题技术交流，每条3小时", source="timeout-pod"))
        _, after = await current_task(stores[0], failed)
        new_ref = TaskRef.parse(after.value["task_ref"])
        assert new_ref.task_id == old_ref.task_id and new_ref.generation != old_ref.generation
        assert after.value["task"] == before.value["task"]
        assert after.value["context"] == before.value["context"]
        assert CalendarDraft.model_validate(after.value["task"]["draft"]) == initial
        assert "已保留之前核对的条件" in failed.send.call_args.args[0].text
        assert "重发本条补充" in failed.send.call_args.args[0].text
        with pytest.raises(WorkError, match="task_superseded"):
            await stores[1].assert_task(owner, old_ref)
        await stores[0].assert_task(owner, new_ref)

        assert await resumed.handle(principal_message(text="每天9点，标题技术交流，每条3小时", source="resumed-pod"))
        _, continued = await current_task(stores[1], resumed)
        assert CalendarDraft.model_validate(continued.value["task"]["draft"]) == add_known_slots(initial)
        assert continued.value["task"]["original_request"] == "请创建连续三天的日程"
        assert "日期" in resumed.send.call_args.args[0].text
        assert all(not service.jobs and not service.tools.plans for service in (first, failed, resumed))
        for service in (first, failed, resumed):
            service._api.assert_not_awaited()
    finally:
        for service in (first, failed, resumed):
            await service.close()


@pytest.mark.asyncio
async def test_explicit_user_date_proof_crosses_services_but_is_not_inherited_by_a_reset_task(stores):  # noqa: F811 - imported pytest fixture
    first, second, fresh = [production_service(store) for store in (stores[0], stores[1], third_store(stores))]
    initial = partial_draft()
    complete = merge_draft(add_known_slots(initial), {"schedule": {"start_date": "2026-11-03"}})
    first._turn_planner = AsyncMock(return_value=decision(initial, route="new_work"))
    second._turn_planner = AsyncMock(return_value=decision(complete))
    invented_old_date = merge_draft(complete, {"changes": {"title": "另一个任务"}})
    reset = decision(invented_old_date, route="new_work")
    reset._calendar_task_reset = True
    assert reset.calendar_task_reset_validated is True
    fresh._turn_planner = AsyncMock(return_value=reset)
    try:
        assert await first.handle(principal_message(text="请从2026-11-03开始连续三天新建日程", source="explicit-date-pod"))
        owner, partial = await current_task(stores[1], first)
        assert "2026-11-03" in partial.value["context"]["verified_calendar_days"]
        assert CalendarDraft.model_validate(partial.value["task"]["draft"]).schedule.count == 3
        assert not first.jobs
        first._api.assert_not_awaited()

        # A different Gateway sees a date-less current user turn. Its proposal
        # is accepted only because the earlier same-task user date is verified.
        assert await second.handle(principal_message(text="每天9点，标题技术交流，每条3小时", source="date-proof-reused-pod"))
        _, ready = await current_task(stores[0], second)
        ready_ref = TaskRef.parse(ready.value["task_ref"])
        assert CalendarDraft.model_validate(ready.value["task"]["draft"]) == complete
        assert "2026-11-03" in ready.value["context"]["verified_calendar_days"]
        assert len(second.jobs) == 1
        ticket, job = next(iter(second.jobs.items()))
        assert job.stage == "awaiting_authorization"
        assert len(expand_create(CalendarDraft.model_validate(job.intent["draft"]))) == 3
        second._api.assert_not_awaited()

        # A server-validated new task resets date authority as well as its ID.
        # The current message supplies no date; a model cannot borrow Nov 3.
        new_request = "另外新建连续三天的日程，每天9点，标题另一个任务，每条3小时"
        assert await fresh.handle(principal_message(text=new_request, source="new-task-no-date-pod"))
        _, restarted = await current_task(stores[1], fresh)
        new_ref = TaskRef.parse(restarted.value["task_ref"])
        assert new_ref.task_id != ready_ref.task_id
        new_draft = CalendarDraft.model_validate(restarted.value["task"]["draft"])
        assert new_draft.schedule.count == 3
        assert not new_draft.schedule.start_date and not new_draft.schedule.dates
        assert missing_question(new_draft)
        assert "2026-11-03" not in restarted.value["context"].get("verified_calendar_days", [])
        assert restarted.value["task"]["original_request"] == new_request
        assert not fresh.jobs
        fresh._api.assert_not_awaited()
        old_job = await stores[0].read("job", ticket, owner=owner)
        assert old_job.status == "cancelled"
        with pytest.raises(WorkError, match="task_superseded"):
            await stores[0].assert_task(owner, ready_ref)
        assert all(not service.tools.plans for service in (first, second, fresh))
    finally:
        for service in (first, second, fresh):
            await service.close()
