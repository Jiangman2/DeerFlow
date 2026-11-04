"""Draft orchestration exercises real task state with synthetic provider calls."""

import asyncio
import json
import time
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_work_semantic_service import make_service, message

from app.integrations.work.auth import Grant
from app.integrations.work.client import WorkError
from app.integrations.work.drafts import CalendarDraft
from app.integrations.work.routing import WorkTurnDecision
from app.integrations.work.scopes import CALENDAR_AUTH_SCOPE


def draft(**updates):
    data = {
        "operation": "create",
        "cardinality": "finite",
        "changes": {"title": "项目日常例会"},
        "schedule": {"start_date": "2026-11-03", "count": 3, "start_clock": "09:00", "duration_minutes": 180},
    }
    data.update(updates)
    return CalendarDraft.model_validate(data)


def decision(value, route="new_work", question=""):
    return WorkTurnDecision(route=route, domain="calendar", draft=value, question=question)


@pytest.mark.asyncio
async def test_known_daily_slots_survive_clarification_and_missing_dates_precede_oauth():
    partial = draft(schedule={"count": 3, "start_clock": "09:00", "duration_minutes": 180})
    service = make_service(decision(partial, "clarify", "请补充标题和时长"), decision(draft(), "continue_work"))
    try:
        assert await service.handle(message("每天早上9点，项目日常例会，持续3小时"))
        question = service.send.call_args.args[0].text
        assert "具体日期" in question and "标题" not in question and "持续多久" not in question
        assert not service.jobs and "授权并继续" not in question
        await service.handle(message("2026年11月3日开始，连开3天", "second"))
        context = service._turn_planner.call_args.kwargs["task_context"]
        assert context["draft"]["changes"]["title"] == "项目日常例会"
        assert context["draft"]["schedule"]["duration_minutes"] == 180
        assert context["draft"]["schedule"]["count"] == 3
        job = next(iter(service.jobs.values()))
        assert job.protocol == 2 and job.intent["draft"]["schedule"]["count"] == 3
        assert "授权并继续" in service.send.call_args.args[0].text
        service._api.assert_not_awaited()
    finally:
        await service.close()


def provider_calls():
    calls = []

    async def api(operation, **kwargs):
        calls.append(operation)
        if operation == "token":
            return {"access_token": "synthetic-user-token", "expires_in": 7200, "scope": CALENDAR_AUTH_SCOPE}
        if operation == "identity":
            return {"data": {"open_id": "ou-A"}}
        if operation == "calendar_primary":
            return {"data": {"calendars": [{"calendar": {"calendar_id": "primary", "type": "primary", "role": "owner"}}]}}
        if operation == "events":
            return {"data": {"items": [], "has_more": False}}
        raise AssertionError("No write is permitted before an explicit confirmation")

    return calls, api


@pytest.mark.asyncio
async def test_oauth_resumes_frozen_daily_draft_and_previews_three_separate_meetings():
    service = make_service(decision(draft()))
    calls, api = provider_calls()
    service._api.side_effect = api
    try:
        await service.handle(message("2026年11月3日起连续三天，每天9点到12点，项目日常例会"))
        ticket = next(iter(service.jobs))
        start = await service.authorize(ticket, "")
        state = parse_qs(urlsplit(start["authorize_url"]).query)["state"][0]
        await service.callback(state=state, code="synthetic-code", browser_session=start["browser_session"])
        await service.drain()
        service._turn_planner.assert_awaited_once()
        plan = next(iter(service.tools.plans.values()))
        payload = json.loads(plan.payload_json)
        assert payload["operation"] == "calendar_batch" and len(payload["items"]) == 3
        for index, action in enumerate(payload["items"], 3):
            assert action["start"] == f"2026-11-{index:02d}T09:00:00+08:00"
            assert action["end"] == f"2026-11-{index:02d}T12:00:00+08:00"
        assert plan.stage == "pending" and "/work 确认" in service.send.call_args.args[0].text
        assert set(calls) <= {"token", "identity", "calendar_primary", "events"}
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_topic_change_cancels_batch_preview_keeps_grant_and_old_confirmation_cannot_write():
    service = make_service(decision(draft()), WorkTurnDecision(route="general"))
    calls, api = provider_calls()
    service._api.side_effect = api
    principal = service._identity(message("seed"))
    service.vault.put(principal, Grant("synthetic-token", time.monotonic() + 600, frozenset(CALENDAR_AUTH_SCOPE.split())))
    try:
        await service.handle(message("2026年11月3日起连续三天，每天9点到12点"))
        await service.drain()
        plan = next(iter(service.tools.plans.values()))
        assert await service.handle(message("解释一下光合作用", "next")) is False
        await service.handle(message("/work 确认 " + plan.id, "confirm"))
        assert plan.stage == "cancelled"
        assert (await service.vault.require(principal, CALENDAR_AUTH_SCOPE)).token == "synthetic-token"
        assert "event_create" not in calls and "event_update" not in calls
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_delete_draft_stays_disabled_before_oauth_or_provider_access():
    value = CalendarDraft(operation="delete", selector={"start": "2026-11-03T00:00:00+08:00", "end": "2026-11-04T00:00:00+08:00"})
    service = make_service(decision(value))
    try:
        await service.handle(message("帮我取消明天的日程"))
        assert "尚未开放" in service.send.call_args.args[0].text
        assert not service.jobs and not service.tools.plans
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_transient_model_failure_keeps_verified_slots_with_a_new_task_generation():
    partial = draft(schedule={"count": 3, "start_clock": "09:00", "duration_minutes": 180})
    service = make_service(decision(partial, "clarify", "请确认具体日期"))
    try:
        await service.handle(message("三天每天9点开会3小时"))
        owner = service.vault.principal_key(service._identity(message("identity")))
        old = await service._turn_state.reserve_turn(owner, "inspect-original")
        service._turn_planner.side_effect = WorkError("turn_model_timeout")
        await service.handle(message("明天开始", "answer-timeout"))
        current = await service._turn_state.reserve_turn(owner, "inspect-retained")
        assert current.task_ref != old.task_ref and current.task["draft"] == old.task["draft"]
        assert current.task["draft"]["schedule"]["count"] == 3
        assert "保留之前核对的条件" in service.send.call_args.args[0].text
        assert not service.jobs
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_queue_busy_retains_only_the_previous_verified_draft_and_does_not_claim_to_save_the_new_input():
    partial = draft(schedule={"count": 3, "start_clock": "09:00", "duration_minutes": 180})
    service = make_service(decision(partial, "clarify", "请确认具体日期"))
    try:
        await service.handle(message("三天每天9点开会3小时"))
        owner = service.vault.principal_key(service._identity(message("identity")))
        old = await service._turn_state.reserve_turn(owner, "inspect-original")
        service.settings = replace(service.settings, turn_queue_timeout=0.01)
        service._turn_semaphore = asyncio.Semaphore(0)
        assert await service.handle(message("明天开始", "answer-busy"))
        current = await service._turn_state.reserve_turn(owner, "inspect-retained")
        assert current.task_ref != old.task_ref and current.task["draft"] == old.task["draft"]
        text = service.send.call_args.args[0].text
        assert "繁忙" in text and "保留之前核对的条件" in text and "重发本条补充" in text
        assert "已保存本条" not in text and "已保留当前请求" not in text
        assert service._turn_planner.await_count == 1
        assert not service.jobs
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["completed", "partial", "uncertain", "started", "unknown"])
async def test_executed_task_cannot_reuse_create_draft_even_when_model_says_new_work(state):
    partial = draft(schedule={"count": 3, "start_clock": "09:00", "duration_minutes": 180})
    service = make_service(decision(partial, "clarify", "从哪天开始？"), decision(draft()), decision(draft()))
    execution = {"state": state, "started": True, "complete": state != "unknown", "events": []}
    service.tools.calendar_task_execution = AsyncMock(return_value=execution)
    try:
        await service.handle(message("安排三天例会"))
        await service.handle(message("改成10点", "change-executed"))
        assert "不会重复创建" in service.send.call_args.args[0].text or "不能把原新建草稿再次提交" in service.send.call_args.args[0].text
        await service.handle(message("还是10点", "clarify-executed"))
        assert service._turn_planner.call_args.kwargs["task_context"]["calendar_execution"] == execution
        assert not service.jobs and not service.tools.plans
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_explicit_new_task_clarification_uses_new_task_id_and_does_not_inherit_old_execution():
    partial = draft(schedule={"count": 3, "start_clock": "09:00", "duration_minutes": 180})
    reset = decision(partial, "clarify", "新的安排从哪天开始？")
    reset._calendar_task_reset = True  # Only the real local tool can set this marker.
    service = make_service(decision(partial, "clarify", "从哪天开始？"), reset, decision(draft(), "continue_work"))
    service.tools.calendar_task_execution = AsyncMock(
        side_effect=[
            {"state": "completed", "started": True, "complete": True, "events": []},
            {"state": "none", "started": False, "complete": True, "events": []},
            {"state": "none", "started": False, "complete": True, "events": []},
        ]
    )
    try:
        await service.handle(message("安排三天例会"))
        owner = service.vault.principal_key(service._identity(message("identity")))
        old = await service._turn_state.reserve_turn(owner, "inspect-old-task")
        await service.handle(message("另外新建三天例会", "explicit-new"))
        current = await service._turn_state.reserve_turn(owner, "inspect-new-task")
        assert current.task_ref.task_id != old.task_ref.task_id
        assert current.task["stage"] == "awaiting_input" and not service.jobs
        await service.handle(message("从2026年11月3日开始", "new-dates"))
        assert len(service.jobs) == 1 and "授权并继续" in service.send.call_args.args[0].text
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_guessed_start_day_is_removed_before_oauth_without_losing_daily_slots():
    partial = CalendarDraft(operation="create", cardinality="finite", schedule={"count": 3})
    service = make_service(decision(partial), decision(draft(), "continue_work"), decision(draft(), "continue_work"))
    try:
        await service.handle(message("未来三天都有会议，帮我安排日程"))
        await service.handle(message("每天早上9点，项目日常例会，持续3小时", "slots-without-day"))
        assert not service.jobs and not service.tools.plans
        owner = service.vault.principal_key(service._identity(message("identity")))
        state = await service._turn_state.reserve_turn(owner, "inspect-grounded-slots")
        retained = CalendarDraft.model_validate(state.task["draft"])
        assert not retained.schedule.start_date and not retained.schedule.dates
        assert retained.schedule.count == 3 and retained.schedule.start_clock == "09:00" and retained.schedule.duration_minutes == 180
        assert retained.changes.title == "项目日常例会" and "日期" in service.send.call_args.args[0].text
        await service.handle(message("从2026年11月3日开始", "explicit-start-day"))
        assert len(service.jobs) == 1
        frozen = CalendarDraft.model_validate(next(iter(service.jobs.values())).intent["draft"])
        assert frozen.schedule.start_date == "2026-11-03" and frozen.schedule.count == 3
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_old_confirmation_claim_during_model_wait_cannot_become_a_second_create_job():
    partial = CalendarDraft(operation="create", cardinality="finite", schedule={"count": 3})
    service = make_service(decision(partial), decision(draft(), "new_work"))
    service.tools.calendar_task_execution = AsyncMock(
        side_effect=[
            {"state": "none", "started": False, "complete": True, "events": []},
            {"state": "started", "started": True, "complete": True, "events": []},
        ]
    )
    try:
        await service.handle(message("创建三天的会议"))
        owner = service.vault.principal_key(service._identity(message("identity")))
        old = await service._turn_state.reserve_turn(owner, "inspect-before-claim-race")
        await service.handle(message("2026年11月3日起每天9点开3小时，项目日常例会", "model-wait-race"))
        current = await service._turn_state.reserve_turn(owner, "inspect-after-claim-race")
        assert current.task_ref.task_id == old.task_ref.task_id
        assert current.task_ref.generation != old.task_ref.generation
        assert service.tools.calendar_task_execution.await_count == 2
        assert "不会重复创建" in service.send.call_args.args[0].text
        assert not service.jobs and not service.tools.plans
        service._api.assert_not_awaited()
    finally:
        await service.close()
